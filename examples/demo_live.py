"""离线「真人体验」演示：在真实 GUI 窗口里走一遍完整交互链路。

用一个本地“脚本化模型”模拟模型行为，不联网、不需要 API key：
    你发送任务 → 模型 ask_user 向你提问技术栈 → 你在输入框回答 →
    模型 write_file 写文件 → 窗口内联展示代码并弹确认条 → 你允许 →
    文件落盘到演示目录 → 模型给出完成语。

运行（会弹一个窗口，结束后关闭即可）：
    .venv\\Scripts\\python examples\\demo_live.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

from harness.config import HarnessConfig  # noqa: E402
from harness import build_folder_tools, gui  # noqa: E402

DEMO_CODE = '''# 由“脚本化演示模型”生成
def calc(expr):
    return eval(expr)  # 仅演示用

if __name__ == "__main__":
    while True:
        try:
            print(calc(input("表达式> ")))
        except (EOFError, KeyboardInterrupt):
            break
        except Exception as e:
            print("错误:", e)
'''

# ---- 脚本化“模型”：按对话状态返回 提问/写文件/完成 ----
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        msgs = body["messages"]
        tool_texts = [m.get("content", "") or "" for m in msgs if m.get("role") == "tool"]

        if any(t.startswith("✔ 已写入") for t in tool_texts):
            reply = {"role": "assistant",
                     "content": "已确认并写入 calculator.py。演示完成：整个过程你看到了「提问→作答→写入预览→确认」。"}
            fr = "stop"
        elif tool_texts:  # 刚回答完提问 -> 写文件
            reply = {"role": "assistant", "content": None, "refusal": None, "annotations": None,
                     "tool_calls": [{"id": "c_write", "type": "function",
                                     "function": {"name": "write_file",
                                                  "arguments": json.dumps(
                                                      {"path": "calculator.py", "content": DEMO_CODE})}}]}
            fr = "tool_calls"
        else:  # 第一次请求 -> 先提问技术栈
            reply = {"role": "assistant", "content": None, "refusal": None, "annotations": None,
                     "tool_calls": [{"id": "c_ask", "type": "function",
                                     "function": {"name": "ask_user",
                                                  "arguments": json.dumps(
                                                      {"question": "这个计算器要命令行版还是带图形界面的版本？",
                                                       "options": "命令行/图形界面"})}}]}
            fr = "tool_calls"

        payload = {"id": "canned", "object": "chat.completion", "created": 0, "model": "canned",
                   "choices": [{"index": 0, "message": reply, "finish_reason": fr, "logprobs": None}],
                   "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                   "system_fingerprint": None}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    import tkinter as tk
    from harness.workspace import FolderWorkspace

    demo_dir = Path(tempfile.mkdtemp(prefix="harness_demo_"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    config = HarnessConfig(base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                           api_key="sk-demo", model="canned", request_timeout=20, max_iterations=8)
    root = tk.Tk()
    window = gui._ChatWindow(root, config)

    # 预选一个演示工作目录（不写任何真实 .env）
    ws = FolderWorkspace(demo_dir)
    window.workspace = ws
    window._agent.add_tools(build_folder_tools(ws))
    window._sync_folder_label()
    window._append("〓 离线演示：模型是本地脚本化的，不会联网，也不碰你的 .env/密钥 〓", "meta")
    window._append("稍等 1 秒会自动发出任务；之后模型会先提问，你照常回答即可。", "meta")

    def auto_ask() -> None:
        window.input.insert("1.0", "请写一个计算器到工作目录；动手前先问清楚做成命令行还是图形界面，写前把内容给我过目。")
        window._do_send()

    root.after(1000, auto_ask)
    root.mainloop()
    server.shutdown()


if __name__ == "__main__":
    main()
