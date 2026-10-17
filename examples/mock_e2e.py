"""离线端到端演示：用标准库起一个假的 OpenAI 兼容服务，跑完整条 agent 循环。

不发任何真实请求、不需要 API key。用来验证：HTTP 请求 -> 模型要调用工具 ->
逐个执行回填 -> 再次请求 -> 拿到最终文本回答。

运行：.venv\\Scripts\\python examples\\mock_e2e.py
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

from harness import Agent  # noqa: E402
from harness.config import HarnessConfig  # noqa: E402

SEEN: list[dict] = []  # 记录收到的每次请求体，供断言


def _chat_json(message: dict, finish_reason: str) -> dict:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "created": 0,
        "model": "mock-model",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
                "logprobs": None,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        "system_fingerprint": None,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        SEEN.append(body)

        step = len(SEEN)
        if step == 1:
            # 第一轮：模型说“要调用 calculator(6*7)”
            payload = _chat_json(
                {
                    "role": "assistant",
                    "content": None,
                    "refusal": None,
                    "annotations": None,
                    "tool_calls": [
                        {
                            "id": "call_mock_1",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": '{"expression": "6*7"}'},
                        }
                    ],
                },
                "tool_calls",
            )
        else:
            # 第二轮：拿到工具结果后，模型给出最终文本
            payload = _chat_json(
                {"role": "assistant", "content": "6*7 = 42", "refusal": None, "annotations": None},
                "stop",
            )

        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    config = HarnessConfig(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="sk-mock",
        model="mock-model",
        request_timeout=15,
        max_iterations=5,
    )
    agent = Agent(config)  # 默认内置工具

    print(f"mock 服务已就绪 @ {config.base_url}")
    answer = agent.run("算一下 6 乘 7 等于几？")

    server.shutdown()
    print("最终回答：", answer)

    # --- 断言真实走过了完整循环 ---
    assert len(SEEN) == 2, f"应发生 2 次请求，实际 {len(SEEN)}"
    assert "tools" in SEEN[0] and len(SEEN[0]["tools"]) == 3, "首轮应携带工具 schema"
    history = SEEN[1]["messages"]
    roles = [m["role"] for m in history]
    assert roles == ["system", "user", "assistant", "tool"], f"第二轮历史角色异常：{roles}"
    tool_msg = history[-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_mock_1", tool_msg
    assert "42" in tool_msg["content"], tool_msg
    assert answer == "6*7 = 42", answer

    print("✔ 端到端循环验证通过：2 次请求、工具结果成功回填、拿到最终回答。")


if __name__ == "__main__":
    main()
