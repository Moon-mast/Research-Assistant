"""最小程序化用法：注册一个自定义工具 -> Agent.run() 完成一次工具循环。

运行前先把 .env.example 复制为 .env 并填写（任选一家 OpenAI 兼容厂商）。
    py -3.12 -m venv .venv
    .venv\\Scripts\\python -m pip install -r requirements.txt
    .venv\\Scripts\\python examples\\quickstart.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 项目根目录

from harness import Agent, get_builtins, tool


# --- 自定义工具：带类型注解 + docstring，schema 自动生成 ---
@tool
def add(a: float, b: float) -> str:
    """把两个数字相加。

    Args:
        a (float): 第一个加数。
        b (float): 第二个加数。
    """
    return f"{a:g} + {b:g} = {a + b:g}"


def main() -> None:
    # 注册进 Agent：内置工具 + 自定义的 add
    agent = Agent(tools=[*get_builtins(), add])

    print("=== 已注册工具 ===")
    for line in agent.tool_list():
        print("  -", line)
    print()

    answer = agent.run("请调用工具分别计算 12.3 + 4.5 和 99 + 1，然后把两次结果用一句话告诉我。")
    print("\n最终回答：", answer)


if __name__ == "__main__":
    main()
