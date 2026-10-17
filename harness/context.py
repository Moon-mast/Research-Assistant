"""上下文压缩的纯逻辑：token 估算 / 预算 / 轮次拆分 / gist 折叠 / 会话回顾 / 可检索存档(CCR-lite)。

参考 headroom(https://github.com/headroomlabs-ai/headroom) 的思路，但实现是**零依赖、确定性**的，
不调用任何模型、不需要外部 tokenizer：

- 多轮对话超过预算时，只把"更早的、已完成"的轮次折成一条很短的 gist 摘要（免费确定性折叠）；
  被折叠轮次的**原文**存入 Agent 的内存存档，模型需要精确细节时可调用 ``recall_history`` 取回
  —— 即 headroom 的 "Compress-Cache-Retrieve"（CCR）思路的精简版。
- 单轮内已发送过的消息**永不改写** → 请求前缀保持稳定，命中 DeepSeek / 通义 / Kimi / GLM 等
  厂商的自动前缀 KV 缓存（前缀相同部分按缓存价计费），从而把"每轮重发整段历史"的开销压下去。

本模块只含纯函数/常量，不 import harness 其它模块（对 tools 鸭子类型），全部可离线单测。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 折叠出的"会话回顾"system 消息用此前缀标记，便于识别/重建。
_DIGEST_PREFIX = "【会话回顾】"
_RECALL_HINT = (
    "当你需要精确原文（工具输出 / 文件路径 / 数字 / 代码片段）时，请调用 recall_history "
    "用关键词检索，不要臆造细节。"
)
_NO_RECALL_HINT = "本会话未启用原文检索工具，请基于下列要点回答，不要臆造细节。"

_MAX_DIGEST_ENTRIES = 12    # 会话回顾里最多保留几条 gist（更早的原文仍在存档可检索）
_MAX_ARCHIVE_TURNS = 40     # 内存存档最多保留多少轮原文（超出丢最旧的）
_GIST_USER_CHARS = 120      # 每条 gist 里用户问题的截断长度
_GIST_ANSWER_CHARS = 240    # 每条 gist 里最终结论的截断长度
_RECALL_MAX_MATCHES = 3
_RECALL_RADIUS = 200        # 命中文前后各取多少字符

_MSG_OVERHEAD = 4           # 每条消息的 role/结构开销估算
_ASCII_TOK_PER_CHAR = 0.27  # 非 CJK 字符到 token 的粗略换算
_SCHEMA_OVERHEAD = 200      # 请求体里 tools 之外的固定开销余量

# 预置模型的大致上下文窗口（tokens）。不确定时取偏保守值：宁小勿大——预算过小只会提前折叠，
# 不会让请求超窗报错。
_MODEL_WINDOWS: dict[str, int] = {
    "moonshot-v1-8k": 8192,
    "moonshot-v1-32k": 32768,
    "moonshot-v1-128k": 131072,
    "kimi-k2-0711-preview": 128000,
    "deepseek-v4-pro": 128000,
    "deepseek-v4-flash": 128000,
    "deepseek-chat": 128000,
    "deepseek-reasoner": 64000,
    "deepseek-ai/deepseek-v3": 65536,
    "glm-4-flash": 131072,
    "glm-4-plus": 131072,
    "glm-4-air": 131072,
    "glm-4-long": 131072,   # 实际更大，这里保守
    "thudm/glm-4-9b-chat": 8192,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4.1": 128000,
    "gpt-4.1-mini": 128000,
    "qwen-max": 131072,
    "qwen-plus": 131072,
    "qwen-turbo": 131072,
    "qwen2.5-72b-instruct": 131072,
    "qwen/qwen3-235b-a22b-instruct-2507": 131072,
}
_FALLBACK_WINDOW = 32768
_DEFAULT_RATIO = 0.55       # 默认预算取窗口的 ~55%（留出当轮工具循环增长空间）
_MIN_BUDGET = 4000
_MAX_BUDGET = 60000

_KK_RE = re.compile(r"(\d{2,5})(?:k)", re.I)  # 模型名里的 8k / 128k 后缀


# ---------------------------------------------------------------------------
# token 估算（确定性启发式，足够用于预算闸门）
# ---------------------------------------------------------------------------
def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3000 <= o <= 0x303F) or (0x3400 <= o <= 0x9FFF) or (0xF900 <= o <= 0xFAFF)


def estimate_text_tokens(text) -> int:
    """粗略估算一段文本的 token 数：CJK≈1 token/字，其余≈0.27 token/字符。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = max(0, len(text) - cjk)
    return max(1, int(round(cjk + other * _ASCII_TOK_PER_CHAR)))


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 多模态 content 数组
        parts = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(parts)
    return ""


def estimate_message_tokens(msg: dict) -> int:
    """估算单条消息的 token 数（含 tool_calls 的 name/arguments）。"""
    n = _MSG_OVERHEAD + estimate_text_tokens(_content_text(msg.get("content")))
    if msg.get("role") == "assistant":
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
            if isinstance(fn, dict):
                n += estimate_text_tokens(fn.get("name")) + estimate_text_tokens(fn.get("arguments"))
    return n


def estimate_messages_tokens(messages: list) -> int:
    return sum(estimate_message_tokens(m) for m in messages)


def estimate_tool_schema_tokens(tools) -> int:
    """估算全部工具 schema 占用的 token（请求里每次都会带上）。"""
    total = 0
    for t in tools or []:
        try:
            total += estimate_text_tokens(json.dumps(t.openai_schema(), ensure_ascii=False))
        except Exception:
            continue
    return total


# ---------------------------------------------------------------------------
# 预算 / 模型窗口
# ---------------------------------------------------------------------------
def model_context_window(model: str) -> int:
    """按模型名估算上下文窗口；匹配不上用保守回退值。"""
    name = (model or "").strip().lower()
    if name in _MODEL_WINDOWS:
        return _MODEL_WINDOWS[name]
    hits = sorted({int(k) for k in _KK_RE.findall(name)}, reverse=True)
    if hits:
        return max(hits) * 1000
    return _FALLBACK_WINDOW


def default_budget_tokens(model: str) -> int:
    """默认上下文预算 ≈ 窗口 × 55%，夹在 [4000, 60000]。"""
    return max(_MIN_BUDGET, min(_MAX_BUDGET, int(model_context_window(model) * _DEFAULT_RATIO)))


# ---------------------------------------------------------------------------
# 消息结构：识别会话回顾(digest)消息、定位正文起点、切分轮次
# ---------------------------------------------------------------------------
def is_digest_message(m) -> bool:
    """是否为 Agent 写入的"会话回顾"system 消息。"""
    return bool(
        m and m.get("role") == "system" and isinstance(m.get("content"), str)
        and m["content"].startswith(_DIGEST_PREFIX)
    )


def digest_content(entries: list[str], *, mention_recall: bool = True) -> Optional[str]:
    """把 gist 条目拼成一条"会话回顾"消息的完整文本；空则返回 None。

    mention_recall=False 时（未注册 recall_history 工具），提示语不指向一个不存在的工具。
    """
    lines = [e for e in entries if e]
    if not lines:
        return None
    head = _DIGEST_PREFIX + "更早轮次已被折叠成要点以节省 token。"
    head += _RECALL_HINT if mention_recall else _NO_RECALL_HINT
    return head + "\n" + "\n".join(lines[-_MAX_DIGEST_ENTRIES:])


def content_start(messages: list) -> int:
    """返回真正"对话正文"的起点下标：越过 messages[0] 的 system 及随后的
    会话回顾/连续 system 片段，落在第一个 user 消息处。"""
    i, n = 0, len(messages)
    if i < n and messages[i].get("role") == "system":
        i += 1
    while i < n and messages[i].get("role") == "system":
        i += 1
    return i


@dataclass
class Turn:
    """从某条 user 消息开始的一段轮次（到下一条 user 或结尾为止）。"""

    start: int                # messages 中 user 消息下标
    end: int                  # 轮次结束下标（不含）
    user_text: str = ""
    final_text: Optional[str] = None
    completed: bool = False   # 以一段最终文本回答收尾（无悬空工具块）
    tool_calls: int = 0       # 该轮产生的 tool 结果消息数


def is_open_tail(m) -> bool:
    """消息是否为"未闭合"的工具块尾部（tool 消息，或带 tool_calls 的 assistant）。"""
    role = m.get("role")
    if role == "tool":
        return True
    if role == "assistant":
        return bool(m.get("tool_calls"))
    return False


def split_turns(messages: list, start: int = 0) -> list[Turn]:
    """把 messages[start:] 切成轮次。轮次=每条 user 消息到下一个 user 为止。

    completed 判定向后越过夹在轮次末尾的 system（中途注入的技能说明），
    看最后一个真实消息是否为"最终文本回答"。
    """
    n = len(messages)
    user_idx = [i for i in range(start, n) if messages[i].get("role") == "user"]
    turns: list[Turn] = []
    for j, u in enumerate(user_idx):
        end = user_idx[j + 1] if j + 1 < len(user_idx) else n
        seg_last = end - 1
        while seg_last > u and messages[seg_last].get("role") == "system":
            seg_last -= 1
        t = Turn(start=u, end=end, user_text=_content_text(messages[u].get("content")))
        last = messages[seg_last] if seg_last >= u else None
        if last is not None and last.get("role") == "assistant":
            if not last.get("tool_calls") and (last.get("content") not in (None, "")):
                t.completed = True
                t.final_text = last.get("content")
        t.tool_calls = sum(
            1 for k in range(u, end) if messages[k].get("role") == "tool"
        )
        turns.append(t)
    return turns


def select_fold_turns(messages: list, *, keep_recent: int = 1) -> list[Turn]:
    """选出应折叠的轮次：**已完成** 且比最近 keep_recent 个已完成轮次更旧的。

    返回 [] 表示当前没有可折叠的更早轮次（不足、都是最近/未完成）。
    """
    start = content_start(messages)
    turns = split_turns(messages, start)
    completed = [t for t in turns if t.completed]
    if len(completed) <= max(1, keep_recent):
        return []
    return completed[: -max(1, keep_recent)]


def fold_indices(messages: list, fold_turns: list[Turn]) -> set[int]:
    """待删除的消息下标：被折叠轮次里的非 system 消息（system 一律保留并提升）。"""
    removed: set[int] = set()
    for t in fold_turns:
        for k in range(t.start, t.end):
            if messages[k].get("role") != "system":
                removed.add(k)
    return removed


# ---------------------------------------------------------------------------
# 单轮中途压缩：对"当前轮内更早、已被消费"的工具回合(exchange)做折叠
# ---------------------------------------------------------------------------
@dataclass
class Exchange:
    """一次工具回合：一条带 tool_calls 的 assistant + 紧随其后的若干 tool 结果。"""

    start: int            # messages 中 assistant(tool_calls) 下标
    end: int              # 结束下标（不含，即下一条 assistant 或结尾）
    completed: bool = False  # 其后已有 assistant 消息（模型已消费本回合结果）才算完成
    tool_messages: int = 0


def split_exchanges(messages: list, start: int = 0) -> list[Exchange]:
    """把 messages[start:] 切成"工具回合"。回合 = assistant(tool_calls) + 其 tool 结果们。

    completed 表示该回合结果已被模型"消费"（后面跟着下一条 assistant）；
    处于消息末尾、还没有 assistant 回复的回合是"进行中"，不得折叠。
    """
    n = len(messages)
    exchanges: list[Exchange] = []
    i = start
    while i < n:
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            j = i + 1
            while j < n and messages[j].get("role") == "tool":
                j += 1
            ex = Exchange(start=i, end=j, tool_messages=j - (i + 1))
            ex.completed = j < n and messages[j].get("role") == "assistant"
            exchanges.append(ex)
            i = j
        else:
            i += 1
    return exchanges


def select_fold_exchanges(exchanges: list[Exchange], keep_recent: int = 1) -> list[Exchange]:
    """选出应折叠的工具回合：**已消费完成**且比最近 keep_recent 个完成回合更旧的。"""
    completed = [e for e in exchanges if e.completed]
    if len(completed) <= max(1, keep_recent):
        return []
    return completed[: -max(1, keep_recent)]


def archive_exchange_entry(messages: list, ex: Exchange) -> dict:
    """为被折叠的工具回合生成一条存档（含工具名+参数，供 recall_history 检索）。"""
    labels: list[str] = []
    for k in range(ex.start, ex.end):
        m = messages[k]
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") if isinstance(tc, dict) else {}
            labels.append(f"{fn.get('name')}({fn.get('arguments')})")
    return {
        "user": (" | ".join(labels))[:200] or "（工具回合）",
        "final": "",
        "messages": messages[ex.start:ex.end],
    }


# ---------------------------------------------------------------------------
# gist 折叠 / 会话回顾 / 存档 / 检索
# ---------------------------------------------------------------------------
def one_line(text, limit: int) -> str:
    """压成单行并按需截断。"""
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)].rstrip() + "…"


def turn_to_gist(turn: Turn) -> str:
    """把一个已完成的旧轮折成一行要点：用户问了什么 → 结论是什么（+工具次数）。"""
    u = one_line(turn.user_text, _GIST_USER_CHARS) or "（该轮无文本）"
    parts = [f"用户：{u}"]
    if turn.completed and turn.final_text:
        parts.append(f"→ 结论：{one_line(turn.final_text, _GIST_ANSWER_CHARS)}")
    else:
        parts.append("→ （未完成，未采纳）")
    if turn.tool_calls:
        parts.append(f"（调用了 {turn.tool_calls} 次工具）")
    return "  ".join(parts)


def archive_entry_for(messages: list, turn: Turn) -> dict:
    """为被折叠轮次生成一条存档（保留完整原文供 recall_history 取回）。"""
    return {
        "user": one_line(turn.user_text, 200),
        "final": one_line(turn.final_text or "", 300),
        "messages": messages[turn.start:turn.end],
    }


def _entry_block(entry: dict) -> str:
    parts = [f"[用户] {entry.get('user') or ''}"]
    for m in entry.get("messages") or []:
        role = m.get("role")
        if role == "user":
            continue
        content = _content_text(m.get("content"))
        if role == "tool":
            parts.append(f"[工具结果] {content}")
        elif role == "assistant" and m.get("tool_calls"):
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") if isinstance(tc, dict) else {}
                parts.append(f"[调用 {fn.get('name')}({fn.get('arguments')})]")
        elif content:
            parts.append(content)
    if entry.get("final"):
        parts.append(f"[最终回答] {entry['final']}")
    return "\n".join(parts)


def _snippet(text: str, needle: str, radius: int = _RECALL_RADIUS) -> str:
    if not text:
        return "（空）"
    low = text.lower()
    pos = low.find((needle or "").lower()) if needle else -1
    if len(text) <= radius * 2:
        return text
    if pos >= 0:
        a, b = max(0, pos - radius), min(len(text), pos + len(needle) + radius)
        return ("…" if a > 0 else "") + text[a:b] + ("…" if b < len(text) else "")
    return text[:radius] + "…"


def recall_search(archive: list, query: str, max_matches: int = _RECALL_MAX_MATCHES) -> str:
    """在折叠轮次存档里按关键词检索，返回若干段原文片段（供 recall_history 工具展示）。"""
    q = (query or "").strip().lower()
    if not archive:
        return "（暂无归档：本会话尚未发生过历史压缩，细节都在当前对话里，无需检索。）"
    tokens = [t for t in re.split(r"[\s,，;；、:：|/]+", q) if len(t) >= 2]
    if not q:
        return "（请提供关键词再检索。）"

    scored = []
    for i, entry in enumerate(archive):
        block = _entry_block(entry)
        low = block.lower()
        score = 3 if q in low else 0
        for tok in tokens:
            if tok in low:
                score += 1
        if score:
            scored.append((score, i, block, entry))
    scored.sort(key=lambda x: (-x[0], x[1]))

    picks = scored[:max_matches]
    if not picks:
        return f"存档中没有检索到与「{query}」相关的内容。"
    lines = [f"找到 {len(picks)} 段被折叠轮次的原文片段（会话内档案）：", ""]
    needle = q or (tokens[0] if tokens else "")
    for idx, (_, _i, block, entry) in enumerate(picks, start=1):
        head = one_line(entry.get("user") or "", 120)
        lines.append(f"[{idx}] 用户问：{head or '（无）'}")
        lines.append(_snippet(block, needle))
        lines.append("-" * 24)
    return "\n".join(lines)


def make_recall_description() -> str:
    return (
        "检索本会话更早、已被压缩成小结的轮次的**原始对话与工具输出**，用于精确回忆某次工具调用的结果、"
        "文件路径、数字、代码等。当你在会话回顾中看到某条要点、但需要其完整原文时调用；"
        "也可在用户问起早先细节而当前上下文没有时调用。参数 query 填关键词或短语即可。"
    )


# 供 Agent / 宿主使用的公共别名
SCHEMA_OVERHEAD = _SCHEMA_OVERHEAD
MAX_DIGEST_ENTRIES = _MAX_DIGEST_ENTRIES
MAX_ARCHIVE_TURNS = _MAX_ARCHIVE_TURNS
