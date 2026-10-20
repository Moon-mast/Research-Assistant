"""受确认门控的 Python 执行工具：让 agent 能跑一次性脚本（数据处理、zlib 解压、格式转换等）。

- 任意代码都能干 PDF/OCR 之外的事，是"环境能力"的兜底：模型可先 write_file 落一个
  脚本再执行，或直接 python_exec 一段短代码。
- 安全模型：与 write_file 相同的 confirm 流程——执行前必须经用户确认（GUI 弹确认条 /
  CLI y/n）；运行在工具执行器的独立线程里，超时由 Tool.timeout 统一强制。
- 隔离：在受限命名空间中 exec，捕获 stdout 返回；不持久化任何状态，不注入敏感变量。
"""
from __future__ import annotations

import io
import contextlib

from .tools import Tool, make_tool

MAX_OUTPUT_CHARS = 8000


def _run_python(code: str) -> str:
    """执行一段 Python 代码，返回 stdout（超长截断）。异常以文本形式返回给模型纠错。"""
    code = (code or "").strip()
    if not code:
        raise ValueError("code 为空")
    if len(code) > 20_000:
        raise ValueError("代码过长（>2 万字符），请精简或分段执行")

    buf = io.StringIO()
    env = {"__name__": "__exec__", "__builtins__": __builtins__}
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(compile(code, "<python_exec>", "exec"), env, env)  # noqa: S102 —— 受确认门控的设计能力
    except Exception as e:
        out = buf.getvalue()
        err = f"{type(e).__name__}: {e}"
        head = (out[: MAX_OUTPUT_CHARS // 2] + ("\n…[输出截断]" if len(out) > MAX_OUTPUT_CHARS // 2 else "")) if out else ""
        return f"[执行出错] {err}" + (f"\n-- 出错前的输出 --\n{head}" if head else "")
    out = buf.getvalue().rstrip("\n")
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + f"\n…[输出截断，共 {len(out)} 字符]"
    return out if out else "（无输出——如需看到结果请用 print()）"


def build_python_tools() -> list[Tool]:
    """生成 python_exec 工具（进程内执行，受确认门控）。"""
    return [
        make_tool(
            _run_python,
            name="python_exec",
            description=(
                "执行一段 Python 脚本并返回 print 输出（用于数据处理、格式转换、解压、"
                "批量重命名、调用已安装的第三方库等）。代码在用户确认后运行；"
                "只回传 stdout，需要看到什么就 print 什么。"
            ),
            confirm=True,
            timeout=120.0,
        ),
    ]
