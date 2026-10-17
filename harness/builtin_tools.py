"""内置演示工具：全部无副作用、可安全启用，用来跑通 agent 工具循环。

想加自己的工具：用 @tool 装饰一个带类型注解 + docstring 的函数即可（见 README）。
"""
from __future__ import annotations

import ast
import datetime as _dt
import math
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .tools import tool

# 允许出现在表达式里的常量/简单函数（白名单，禁用任意代码执行）
_ALLOWED_NAMES = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "pow": math.pow,
}

_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}


def _eval_expr(node, depth: int = 0) -> float:
    if depth > 8:
        raise ValueError("表达式嵌套过深")
    if isinstance(node, ast.Expression):
        return _eval_expr(node.body, depth)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_expr(node.left, depth + 1), _eval_expr(node.right, depth + 1))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval_expr(node.operand, depth + 1)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.Name) and node.id in _ALLOWED_NAMES:
        return _ALLOWED_NAMES[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_NAMES:
        fn = _ALLOWED_NAMES[node.func.id]
        args = [_eval_expr(a, depth + 1) for a in node.args]
        return fn(*args)
    raise ValueError(f"表达式含不支持的语法：{ast.dump(node)}")


@tool(description="做一次只允许四则运算、幂、开方、abs/round/min/max 的数学计算（安全，不会执行任意代码）。")
def calculator(expression: str) -> str:
    """计算一个数学表达式并返回结果。

    Args:
        expression (str): 数学表达式，例如 "((1+2)*3-4)/2"、"sqrt(16)+2**10"、"abs(-5)"。
    """
    try:
        tree = ast.parse(expression, mode="eval")
        value = _eval_expr(tree.body)
        return f"{expression} = {value}"
    except (SyntaxError, ValueError, ZeroDivisionError, TypeError) as e:
        raise ValueError(f"无法计算 {expression!r}：{e}") from e


@tool(description="获取当前本地日期和时间，可指定 IANA 时区。")
def get_current_time(timezone: str = "local") -> str:
    """返回指定时区的当前时间字符串。

    Args:
        timezone (str): IANA 时区名，如 "Asia/Shanghai"、"UTC"、"America/New_York"；
            缺省 "local" 表示本机时区。
    """
    if timezone == "local":
        now = _dt.datetime.now()
        zone = _dt.datetime.now().astimezone().tzinfo
    else:
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            raise ValueError(f"未知的 IANA 时区：{timezone!r}") from None
        now = _dt.datetime.now(zone)
    return now.strftime(f"%Y-%m-%d %H:%M:%S %z (%Z，{timezone})")


@tool(description="获取当前日期（年-月-日）。")
def get_current_date() -> str:
    """返回今天的日期字符串。

    Returns:
        形如 2026-09-04 的日期。
    """
    return _dt.date.today().isoformat()


def get_builtins() -> list:
    """返回内置工具列表（含 calculator / get_current_time / get_current_date）。"""
    from .tools import REGISTRY

    names = ("calculator", "get_current_time", "get_current_date")
    return [REGISTRY.get(n) for n in names if n in REGISTRY]
