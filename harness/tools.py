"""工具系统：把任意带类型注解 + docstring 的 Python 函数变成可被 LLM 调用的工具。

- ``@tool`` 装饰器注册一个工具（默认注册到全局 REGISTRY，内置工具即如此）。
- 工具入参 schema 用 pydantic 从函数签名 + docstring 自动推导；入参由 pydantic 校验，
  出错会以清晰的文本回传给模型，让它自行纠错。
- 工具在独立线程中带超时执行，返回值统一序列化为字符串。
"""
from __future__ import annotations

import inspect
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Type, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

_SAFE_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class ToolError(Exception):
    """工具定义或调用层面的错误（区别于工具运行时业务异常）。"""


@dataclass
class ToolResult:
    """一次工具调用的结果，由 Agent 拼进 {role: tool} 消息。"""

    ok: bool
    content: str

    @property
    def display(self) -> str:
        return self.content if self.ok else f"[错误] {self.content}"


@dataclass
class Tool:
    """一个可被模型调用的工具。"""

    func: Callable[..., Any]
    name: str
    description: str
    parameters_schema: dict
    arg_model: Type[BaseModel]
    timeout: float = field(default=30.0)
    confirm: bool = field(default=False)  # 是否需要用户确认后才执行（写文件等破坏性操作）
    kind: str = field(default="call")     # "call"=普通工具；"ask"=向用户提问(ask_user)

    # ---------- schema ----------
    def openai_schema(self) -> dict:
        """组装成 OpenAI function-calling 的 tools 元素。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

    # ---------- 执行 ----------
    def invoke(self, arguments_json: str) -> ToolResult:
        """解析并校验参数 -> 在线程中超时执行 -> 序列化返回。任何失败都转为文本。"""
        try:
            raw = json.loads(arguments_json) if arguments_json.strip() else {}
            if not isinstance(raw, dict):
                return ToolResult(
                    False,
                    f"参数必须是 JSON 对象，收到的却是 {type(raw).__name__}：{arguments_json[:200]}",
                )
        except json.JSONDecodeError as e:
            return ToolResult(False, f"参数 JSON 解析失败：{e}。原始内容：{arguments_json[:200]}")

        # pydantic 校验（extra=forbid：多余的参数名也会报错，便于模型纠错）
        try:
            validated = self.arg_model.model_validate(raw)
        except ValidationError as e:
            return ToolResult(False, _format_validation_error(e))

        kwargs = validated.model_dump()
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="tool") as pool:
                future = pool.submit(_call_safe, self.func, kwargs)
                result = future.result(timeout=self.timeout)
        except FutureTimeout:
            return ToolResult(False, f"工具 {self.name} 执行超过 {self.timeout:g}s，已超时中止。")
        except Exception as e:  # 业务异常
            return ToolResult(False, f"工具 {self.name} 抛出异常：{type(e).__name__}: {e}")
        return ToolResult(True, _format_result(result))

    # ---------- 人类可读 ----------
    def signature_preview(self) -> str:
        sig = ", ".join(
            p for p, info in self.parameters_schema.get("properties", {}).items()
        )
        return f"{self.name}({sig})"


def _call_safe(func: Callable, kwargs: dict) -> Any:
    return func(**kwargs)


def _format_validation_error(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        msg = err["msg"].replace("Value error, ", "")
        parts.append(f"字段 {loc}: {msg}")
    return "参数校验失败：" + "；".join(parts)


_MAX_RESULT_CHARS = 4000


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[已截断，共 {len(text)} 字符]"


def _short_repr(value: Any, cap: int = 500) -> str:
    """单个元素的紧凑 JSON 表示（超长截断），用于结构化预览。"""
    try:
        s = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(value)
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _list_preview(items: list, limit: int) -> str:
    """超长列表的结构化预览（headroom SmartCrusher 思路的简化版）：

    保留前若干项逐字内容 + 行数/字段摘要，替代纯尾部截断——让模型知道"有多少行、哪些列"，
    首行数据仍是真实内容，不捏造。
    """
    n = len(items)
    if n == 0:
        return "[]"
    keys = None
    head: list[str] = []
    used = 0
    for idx, item in enumerate(items):
        if idx >= 8:  # 预览最多尝试前 8 行，避免单轮循环过久
            break
        s = _short_repr(item, cap=600)
        need = len(s) + 2
        if used and used + need + 60 > limit:  # 预留"… 摘要"的后缀空间
            break
        head.append(s)
        used += len(s)
        if keys is None:
            keys = list(item.keys()) if isinstance(item, dict) else None
    if not head:
        # 连一行都放不下，交给调用方按纯文本截断
        return ""
    shown = len(head)
    notes = []
    if keys is not None:
        notes.append("字段：" + ", ".join(keys))
    if n > shown:
        notes.append(f"共 {n} 项，已省略 {n - shown} 项")
    notes.append("（仅列前几行；需要更多再针对性读取）")
    out = "[ " + ", ".join(head) + " … " + "，".join(notes) + " ]"
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _dict_preview(obj: dict, limit: int) -> str:
    """超长 dict 的结构化预览：逐个键列内容，超长列表/字符串值给"项数/长度"而非全文。"""
    parts: list[str] = []
    used = 0
    omitted: list[str] = []
    for k, v in obj.items():
        if isinstance(v, list) and len(v) > 6:
            s = f"{k}: [list 共 {len(v)} 项]"
        elif isinstance(v, str) and len(v) > 1200:
            s = f"{k}: {v[:800]}…[字符串共 {len(v)} 字符]"
        else:
            s = f"{k}: {_short_repr(v)}"
        need = len(s) + 1
        if used and used + need + 50 > limit:
            omitted.append(k)
            continue
        parts.append(s)
        used += len(s)
    if not parts:
        return ""
    out = "\n".join(parts)
    if omitted:
        out += f"\n… 省略 {len(omitted)} 个键：{', '.join(omitted)}"
    return out if len(out) <= limit else out[: limit - 40] + "…"


def _structural_preview(value: Any, limit: int) -> str | None:
    """对超长 list/dict 返回结构化预览文本（长度 ≤ limit）；不适合则返回 None 走原截断。"""
    if isinstance(value, list):
        return _list_preview(value, limit)
    if isinstance(value, dict):
        return _dict_preview(value, limit)
    return None


def _format_result(value: Any) -> str:
    """把工具返回值序列化成文本；过长则截断（超长列表/字典先走结构化瘦身）。"""
    if isinstance(value, str):
        return _clip(value, _MAX_RESULT_CHARS)
    if isinstance(value, BaseModel):
        return _clip(value.model_dump_json(), _MAX_RESULT_CHARS)
    if isinstance(value, bytes):
        return _clip(value.decode("utf-8", errors="replace"), _MAX_RESULT_CHARS)
    if value is None:
        return "None"

    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > _MAX_RESULT_CHARS:
        if isinstance(value, (list, dict)):
            preview = _structural_preview(value, _MAX_RESULT_CHARS)
            if preview:
                return preview
    return _clip(text, _MAX_RESULT_CHARS)


# --------------------------------------------------------------------------
# docstring 解析（Google/主流风格的最小子集）
# --------------------------------------------------------------------------
_ARGS_HEADINGS = ("args:", "arguments:", "parameters:", "参数:")
_OTHER_SECTIONS = (
    "returns:", "return:", "raises:", "raise:", "example:", "examples:",
    "note:", "notes:", "注意:", "示例:", "返回:", "异常:",
)


def _parse_docstring(doc: Optional[str]) -> tuple[str, dict[str, str]]:
    """返回 (工具总描述, {参数名: 描述})。"""
    if not doc:
        return "", {}
    summary: list[str] = []
    param_descs: dict[str, str] = {}
    section: Optional[str] = None
    indent_prefixes = ("    ", "\t", "  ")
    last_param: Optional[str] = None
    for raw in doc.strip().splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        low = stripped.lower()
        if not stripped:
            last_param = None
            continue
        if low in _OTHER_SECTIONS:
            section = None
            last_param = None
            continue
        if low in _ARGS_HEADINGS:
            section = "args"
            last_param = None
            continue
        if section == "args":
            m = re.match(r"^([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.+)$", stripped)
            if m:
                param_descs[m.group(1)] = m.group(2).strip()
                last_param = m.group(1)
                continue
            # 参数描述的续行（缩进更深），追加到上一个参数
            if last_param and line.startswith(indent_prefixes):
                param_descs[last_param] += " " + stripped
                continue
            # 出现其他无缩进文本 → 本段结束
            section = None
        summary.append(stripped)
    description = " ".join(summary).strip() or (doc.strip().splitlines()[0].strip() if doc else "")
    return description, param_descs


# --------------------------------------------------------------------------
# 签名 -> pydantic 参数模型 -> OpenAI JSON Schema
# --------------------------------------------------------------------------
def make_tool(
    func: Callable,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    timeout: Optional[float] = None,
    confirm: bool = False,
) -> Tool:
    """把任意函数包装成 Tool（不注册）。注解缺省视为 str，默认值 → 可选参数。"""
    if not callable(func):
        raise ToolError("make_tool 需要一个可调用对象")

    tool_name = name or func.__name__
    if not _SAFE_NAME.match(tool_name):
        raise ToolError(
            f"工具名 {tool_name!r} 非法：仅允许字母/数字/-/_，长度 1-64（对应各厂商约束）"
        )

    doc_desc, param_descs = _parse_docstring(inspect.getdoc(func))
    tool_desc = (description or doc_desc or f"工具 {tool_name}。").strip()
    if len(tool_desc) > 1024:
        tool_desc = tool_desc[:1024] + "…"

    sig = inspect.signature(func)
    hints: dict[str, Any] = {}
    try:
        hints = {k: v for k, v in inspect.get_type_hints(func).items() if k != "return"}
    except Exception:  # 个别函数拿不到类型提示时退化为按注解原样
        for p in sig.parameters.values():
            if p.annotation is not inspect.Parameter.empty:
                hints[p.name] = p.annotation

    fields: dict[str, Any] = {}
    for pname, p in sig.parameters.items():
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise ToolError(f"工具 {tool_name} 不支持 *args/**kwargs：参数 {pname}")
        if p.name == "self" or p.name == "cls":
            continue
        ann = hints.get(pname, p.annotation if p.annotation is not inspect.Parameter.empty else str)
        if ann is inspect.Parameter.empty:
            ann = str
        has_default = p.default is not inspect.Parameter.empty
        default = None if p.default is inspect.Parameter.empty else p.default
        field_desc = param_descs.get(pname)
        if has_default:
            if default is None and not _is_optional(ann):
                ann = Optional[ann]
            if field_desc:
                fields[pname] = (ann, Field(default, description=field_desc))
            else:
                fields[pname] = (ann, default)
        else:
            fields[pname] = (ann, Field(..., description=field_desc) if field_desc else ...)

    if not fields:
        fields["_dummy"] = (Optional[str], None)  # 无参工具：pydantic 需要至少一个字段

    model_name = re.sub(r"\W", "_", tool_name) + "__args"
    arg_model = create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)

    schema = arg_model.model_json_schema()
    if "_dummy" in schema.get("properties", {}):
        schema["properties"].pop("_dummy")
    params_schema = {"type": "object", "properties": schema.get("properties", {})}
    required = schema.get("required", [])
    if required:
        params_schema["required"] = required

    return Tool(
        func=func,
        name=tool_name,
        description=tool_desc,
        parameters_schema=params_schema,
        arg_model=arg_model,
        timeout=timeout if timeout is not None else 30.0,
        confirm=confirm,
    )


def _is_optional(ann: Any) -> bool:
    origin = getattr(ann, "__origin__", None)
    return origin is Union and type(None) in getattr(ann, "__args__", ())


# --------------------------------------------------------------------------
# 注册表 + @tool 装饰器
# --------------------------------------------------------------------------
class Registry:
    """按名字保存 Tool 的注册表（按注册顺序）。"""

    def __init__(self) -> None:
        self._by_name: dict[str, Tool] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()

    def register(self, tool: Tool) -> None:
        with self._lock:
            if tool.name in self._by_name:
                raise ToolError(f"工具名冲突：{tool.name!r} 已存在（{self._by_name[tool.name].func}）")
            self._by_name[tool.name] = tool
            self._order.append(tool.name)

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str) -> Optional[Tool]:
        return self._by_name.get(name)

    def names(self) -> list[str]:
        return list(self._order)

    def snapshot(self) -> list[Tool]:
        return [self._by_name[n] for n in self._order]

    def resolve(self, tools_arg: Optional[list] = None) -> list[Tool]:
        """把用户传入的 tools 参数规整成 Tool 列表。

        tools_arg 为 None -> 返回注册表快照；元素可以是 Tool 或任意带注解的函数。
        """
        if tools_arg is None:
            return self.snapshot()
        resolved: dict[str, Tool] = {}
        for item in tools_arg:
            if isinstance(item, Tool):
                t = item
            elif callable(item):
                t = make_tool(item)
            else:
                raise ToolError(f"无法识别工具条目：{item!r}（应为 Tool 或函数）")
            if t.name in resolved:
                raise ToolError(f"工具名冲突：{t.name!r}")
            resolved[t.name] = t
        return list(resolved.values())


REGISTRY = Registry()


def tool(*args, **kwargs):
    """把函数注册为工具。支持两种用法：

    @tool
    def f(...): ...

    @tool(name="别名", description="覆盖描述")
    def f(...): ...
    """
    name = kwargs.get("name")
    description = kwargs.get("description")
    timeout = kwargs.get("timeout")

    if len(args) == 1 and callable(args[0]) and not name and not description and not timeout:
        t = make_tool(args[0])
        REGISTRY.register(t)
        return args[0]

    def deco(func: Callable) -> Callable:
        t = make_tool(func, name=name, description=description, timeout=timeout)
        REGISTRY.register(t)
        return func

    return deco


def build_ask_tool() -> Tool:
    """生成 "ask_user" 提问工具：模型中途调用它向你提问，由宿主的 ask_handler 作答。

    该工具不真正执行函数体，Agent 见 kind=="ask" 会把问题交给交互层，用户回答作为
    工具结果回传给模型后继续。
    """

    def _stub(question: str, options: str = ""):
        """向用户提问并等待回答，得到回答后再继续任务。

        仅在确实需要澄清、补充信息或让用户在几个方案中做选择时才调用；
        问题要具体，必要时在 options 里用 / 分隔给出可选项。返回用户的文字回答。

        Args:
            question (str): 要问用户的问题。
            options (str): 可选答案，用 / 分隔（可省略）；用户仍可自由输入。
        """
        raise NotImplementedError("ask_user 由宿主交互层处理")

    t = make_tool(
        _stub,
        name="ask_user",
        description=(
            "任务中途需要澄清、补充信息或让用户做选择时，调用此工具向用户提问并等待其文字回答，"
            "拿到回答后继续完成原任务。不要把可以自行判断的事情拿来问用户。"
        ),
    )
    t.kind = "ask"
    return t
