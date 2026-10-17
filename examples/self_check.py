"""离线自检：不依赖 API key、不发任何网络请求。

验证：
  1. 内置工具的 OpenAI schema 结构是否正确；
  2. 工具调用路径（含参数校验 / 未知工具 / 业务异常 / 结果序列化）；
  3. SDK assistant message -> 干净参数字典 的转换。

运行：.venv\\Scripts\\python examples\\self_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

from harness import Agent, get_builtins
from harness.config import HarnessConfig
from harness.tools import ToolResult


class _FakeToolCall:
    def __init__(self, cid, name, args):
        self.id = cid
        self.type = "function"
        self.function = type("Fn", (), {"name": name, "arguments": args})()


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def main() -> None:
    agent = Agent(HarnessConfig(api_key="sk-dummy"))  # 只做离线逻辑，绝不联网

    # 1) schema 结构
    print("== 1. 内置工具 schema ==")
    schemas = agent.tool_schemas()
    assert len(schemas) == 3, schemas
    names = {s["function"]["name"] for s in schemas}
    assert names == {"calculator", "get_current_time", "get_current_date"}, names
    calc = next(s for s in schemas if s["function"]["name"] == "calculator")
    assert calc["type"] == "function"
    assert "expression" in calc["function"]["parameters"]["properties"]
    assert calc["function"]["parameters"]["required"] == ["expression"]
    tc = next(s for s in schemas if s["function"]["name"] == "get_current_time")
    assert "timezone" not in (tc["function"]["parameters"].get("required") or []), "带默认值应非必填"
    for s in schemas:
        print("  -", s["function"]["name"], json.dumps(s["function"]["parameters"], ensure_ascii=False)[:120])

    # 2) 工具调用路径
    print("\n== 2. 工具执行 ==")
    r: ToolResult = agent._handle_tool_call(
        {"id": "c1", "function": {"name": "calculator", "arguments": '{"expression": "(1+2)*3-4"}'}}
    )
    assert r.ok and "(1+2)*3-4 = 5" in r.content, r
    print("  正常计算：", r.display)

    r = agent._handle_tool_call({"id": "c2", "function": {"name": "calculator", "arguments": '{"expression": "1/0"}'}})
    assert not r.ok and "无法计算" in r.content, r
    print("  业务异常（除零被回传为文本）：", r.display[:80])

    r = agent._handle_tool_call({"id": "c3", "function": {"name": "calculator", "arguments": '{"expr": 1}'}})
    assert not r.ok and "校验失败" in r.content, r
    print("  参数名错误（extra=forbid 拦截）：", r.display[:80])

    r = agent._handle_tool_call({"id": "c4", "function": {"name": "no_such_tool", "arguments": "{}"}})
    assert not r.ok and "未知工具" in r.content, r
    print("  未知工具：", r.display[:80])

    r = agent._handle_tool_call({"id": "c5", "function": {"name": "get_current_date", "arguments": ""}})
    assert r.ok and len(r.content) == 10, r
    print("  无参工具（空 arguments）：", r.display)

    # 3) assistant message -> param dict
    print("\n== 3. assistant message 转换 ==")
    msgs = _FakeMessage(
        content=None,
        tool_calls=[_FakeToolCall("call_A", "calculator", '{"expression": "1+1"}')],
    )
    param = Agent._assistant_to_param(msgs)
    assert param["role"] == "assistant" and param["content"] is None
    assert param["tool_calls"][0]["function"]["name"] == "calculator"
    print("  含工具调用：", json.dumps(param, ensure_ascii=False))
    assert Agent._assistant_to_param(_FakeMessage(content="你好")) == {"role": "assistant", "content": "你好"}
    print("  纯文本消息转换正常")

    print("\n✔ 全部离线自检通过。")
    print("接下来：复制 .env.example 为 .env 填好密钥，再跑 .venv\\Scripts\\python -m harness.cli 做端到端验证。")


if __name__ == "__main__":
    main()
