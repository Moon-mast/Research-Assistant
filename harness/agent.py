"""Agent：一个跑通「模型 <-> 工具」闭环的最小 harness。

循环逻辑：发消息给模型 -> 模型若要求调用工具，逐个执行并回填结果 -> 再次请求，
直到模型直接给出文本回答（或达到最大迭代步数）。
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from . import builtin_tools  # noqa: F401  # 保证默认注册内置工具
from . import context as _context
from .client import build_client, chat_completion
from .config import HarnessConfig
from .tools import REGISTRY, Tool, ToolResult, make_tool


class IterationLimitError(RuntimeError):
    """agent 循环达到最大步数仍无最终文本回答。"""


class Agent:
    def __init__(
        self,
        config: Optional[HarnessConfig] = None,
        *,
        tools: Optional[list] = None,
        system_prompt: Optional[str] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_end: Optional[Callable[[str, ToolResult], None]] = None,
        confirm_handler: Optional[Callable[[str, dict], "bool | str"]] = None,
        ask_handler: Optional[Callable[[str, dict], str]] = None,
        verbose: Optional[bool] = None,
    ):
        self.config = config or HarnessConfig.from_env()
        self.system_prompt = system_prompt or self.config.system_prompt
        self.max_iterations = self.config.max_iterations
        self.verbose = self.config.verbose if verbose is None else verbose
        self.tools: list[Tool] = REGISTRY.resolve(tools)
        # 回调钩子，便于 CLI 展示工具执行过程
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        # 破坏性工具（如 write_file）执行前的用户确认回调。
        # 返回 True=允许；False=拒绝；字符串=拒绝并附上用户理由（会一并回传模型）。
        self.confirm_handler = confirm_handler
        # ask_user 提问工具：返回用户对问题的文字回答（交由宿主弹内联提问）。
        self.ask_handler = ask_handler

        self.messages: list[dict] = []   # 跨轮保存的对话历史
        self._extra_system: list[str] = []  # 运行时追加的 system 片段（如技能清单/预载说明）
        # —— 上下文压缩（headroom 思路，省 token）——
        self._archive: list[dict] = []   # 被折叠轮次的原文存档（recall_history 取回用）
        self._digest_entries: list[str] = []  # 会话回顾里保留的 gist 行
        self.context_stats: dict = {}    # 最近一次压缩统计（供展示/离线测试断言）
        self._recall_tool: Optional[Tool] = None  # 惰性注册的 recall_history 工具
        self._client = None
        self._by_name = {t.name: t for t in self.tools}

    # ---- 基础设施 ----
    @property
    def client(self):
        if self._client is None:
            self._client = build_client(self.config)
        return self._client

    def tool_schemas(self) -> list[dict]:
        return [t.openai_schema() for t in self.tools]

    def tool_list(self) -> list[str]:
        return [f"{t.signature_preview()} —— {t.description.splitlines()[0]}" for t in self.tools]

    def add_tools(self, tools) -> list["Tool"]:
        """运行期追加工具（如选定工作目录后注入的文件工具）；已存在的同名工具被忽略。"""
        added: list[Tool] = []
        for t in tools:
            if isinstance(t, Tool) and t.name not in self._by_name:
                self.tools.append(t)
                self._by_name[t.name] = t
                added.append(t)
        return added

    def reset(self) -> None:
        """清空本轮历史与压缩存档（会话回顾/可检索原文一并清空），并摘除 recall_history 工具。"""
        self.messages = []
        self._archive = []
        self._digest_entries = []
        self.context_stats = {}
        if self._recall_tool is not None:
            name = self._recall_tool.name
            self.tools = [t for t in self.tools if t.name != name]
            self._by_name.pop(name, None)
            self._recall_tool = None

    def inject_system(self, text: str) -> None:
        """运行时向 agent 追加一段 system 说明（如技能清单 / 预载技能全文）。

        对话尚未开始时，片段并入首条 system 消息；已有历史时，追加一条 system
        消息到末尾（在下一个 user 消息之前生效）。reset() 只清历史，片段随实例保留。
        """
        text = (text or "").strip()
        if not text:
            return
        self._extra_system.append(text)
        if self.messages:
            self.messages.append({"role": "system", "content": text})

    # ---- 主入口 ----
    def run(self, user_input: str) -> str:
        """处理一条用户输入，跑完整个工具循环，返回最终文本回答。

        对话历史保存在 self.messages 里，连续调用 run 即多轮对话。
        """
        user_input = (user_input or "").strip()
        if not user_input:
            raise ValueError("user_input 不能为空")

        if not self.messages:
            parts = [self.system_prompt] + list(self._extra_system)
            content = "\n\n".join(p for p in parts if (p or "").strip())
            self.messages = [{"role": "system", "content": content}]
        else:
            # 已有历史：先愈合可能残留的悬空工具块，再视预算把更早的已完成轮次折叠成小结
            # （只在轮次边界压缩，绝不在工具循环中途动消息；已发消息保持不变以命中前缀缓存）
            self._heal_dangling_tail()
            self._maybe_compact_before_turn()
        self.messages.append({"role": "user", "content": user_input})

        for step in range(1, self.max_iterations + 1):
            if self.verbose:
                print(f"[agent] step {step}/{self.max_iterations}，历史消息 {len(self.messages)} 条")

            response = chat_completion(
                self.client,
                messages=self.messages,
                tools=self.tools,
                model=self.config.model,
                temperature=self.config.temperature,
                verbose=self.verbose,
            )
            message = response.choices[0].message
            assistant_msg = self._assistant_to_param(message)
            self.messages.append(assistant_msg)

            tool_calls = assistant_msg.get("tool_calls")
            if not tool_calls:
                return (assistant_msg.get("content") or "").strip()

            # 模型要求调用工具：逐个执行并回填（可能同一轮并行调多个工具）
            for tc in tool_calls:
                tool_result = self._handle_tool_call(tc)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": tool_result.display,
                    }
                )
            # 单轮中途：这轮历史若已超预算，自动折叠更早的已消费工具回合，再发下一次请求
            self._maybe_compact_mid_turn()

        raise IterationLimitError(
            f"已达最大迭代步数 {self.max_iterations}，模型仍未给出最终回答"
            "（可能工具调用陷入循环）。可用 agent.reset() 或 CLI 的 /clear 重来。"
        )

    # ---- 内部 ----
    def _handle_tool_call(self, tc: dict) -> ToolResult:
        name = tc["function"]["name"]
        args_raw = tc["function"].get("arguments", "")
        tool = self._by_name.get(name)

        if tool is None:
            return ToolResult(False, f"模型要求调用未知工具 {name!r}（可用工具：{', '.join(self._by_name)}）")

        # 解析参数（用于展示 / 确认）
        try:
            parsed = json.loads(args_raw) if args_raw.strip() else {}
            if not isinstance(parsed, dict):
                parsed = {"_raw": args_raw}
        except json.JSONDecodeError:
            parsed = {"_raw": args_raw}

        # ask_user：问题交给宿主作答（不真正执行工具函数；不提“调用中”以免重复问题）
        if tool.kind == "ask":
            if self.ask_handler is None:
                result = ToolResult(False, "当前环境不支持向用户提问（未配置 ask_handler）")
            else:
                reply = self.ask_handler(name, parsed)
                result = ToolResult(True, str(reply) if reply is not None else "")
            if self.on_tool_end:
                self.on_tool_end(name, result)
            return result

        # 触发「工具开始」回调
        if self.on_tool_start:
            self.on_tool_start(name, parsed)

        # 破坏性工具（如 write_file）在执行前询问用户；拒绝则不落盘并回传模型
        if tool.confirm and self.confirm_handler is not None:
            decision = self.confirm_handler(name, parsed)
            if isinstance(decision, bool):
                allowed, note = decision, None
            else:
                allowed, note = False, str(decision).strip() or None
            if not allowed:
                text = (
                    f"用户拒绝了调用 {name}。请勿直接重试相同的写入；"
                    "如需继续请按用户意见调整后再征求同意。"
                )
                if note:
                    text += f"（用户说明：{note}）"
                result = ToolResult(False, text)
            else:
                result = tool.invoke(args_raw)
        else:
            result = tool.invoke(args_raw)

        if self.on_tool_end:
            self.on_tool_end(name, result)
        return result

    @staticmethod
    def _assistant_to_param(message) -> dict:
        """把 SDK 返回的 assistant message 转成干净的参数字典（跨厂商稳定）。"""
        content = getattr(message, "content", None)
        raw_calls = getattr(message, "tool_calls", None) or []
        tool_calls = None
        if raw_calls:
            tool_calls = []
            for tc in raw_calls:
                fn = getattr(tc, "function", None)
                tool_calls.append(
                    {
                        "id": getattr(tc, "id", None),
                        "type": getattr(tc, "type", None) or "function",
                        "function": {
                            "name": getattr(fn, "name", None),
                            "arguments": getattr(fn, "arguments", None) or "{}",
                        },
                    }
                )
        param: dict = {"role": "assistant", "content": content}
        if tool_calls:
            param["tool_calls"] = tool_calls
        return param

    # ---- 上下文压缩（省 token；headroom 思路的精简版）----
    def _heal_dangling_tail(self) -> None:
        """摘掉历史末尾"未闭合"的工具块（上一轮 IterationLimitError / 中断遗留）。

        保证下一轮追加 user 后仍是合法的 chat 工具序列：tool 消息必须紧跟其引用的
        assistant.tool_calls，且不能被 user 打断。末尾被注入的技能 system 片段会被临时
        挪开再放回（它们不是悬空块，不应被丢掉）。
        """
        msg = self.messages
        if not msg:
            return
        trailing_sys: list[dict] = []
        while msg and msg[-1].get("role") == "system" and not _context.is_digest_message(msg[-1]):
            trailing_sys.insert(0, msg.pop())
        while msg and _context.is_open_tail(msg[-1]):
            msg.pop()
        msg.extend(trailing_sys)

    def _maybe_compact_before_turn(self) -> None:
        """新一轮开始前：历史估算超预算时，把更早的、已完成的轮次折叠成"会话回顾"小结。

        只在 run() 开头调用，**绝不打断当前工具循环**。被折叠轮次的原文存入 self._archive，
        模型需要时可经 recall_history 取回（CCR-lite）；单轮内已发消息不改写，前缀缓存可命中。
        """
        cfg = self.config
        self.context_stats = {}
        if not (cfg.context_compress and cfg.context_mode != "off"):
            return
        if len(self.messages) < 2:
            return

        messages = self.messages
        budget = cfg.context_budget_tokens or _context.default_budget_tokens(cfg.model)
        est = (
            _context.estimate_messages_tokens(messages)
            + _context.estimate_tool_schema_tokens(self.tools)
            + _context.SCHEMA_OVERHEAD
        )
        self.context_stats.update(estimated=est, budget=budget)
        if est <= budget:
            return

        fold = _context.select_fold_turns(messages, keep_recent=cfg.context_keep_recent)
        if not fold:
            self.context_stats.update(triggered=False, reason="无更早可折叠的已完成轮次")
            if self.verbose:
                print(f"[context] 历史估算 ≈{est} token > 预算 {budget}，但没有可折叠的更早轮次，保持原样")
            return

        before = _context.estimate_messages_tokens(messages)
        removed_idx = _context.fold_indices(messages, fold)

        # 收集折叠后的正文 / 中途 system / 会话回顾条目（先只算不落，确认"真能省"再应用）
        sticky: list[dict] = []
        kept: list[dict] = []
        for i, m in enumerate(messages):
            if i == 0 or i in removed_idx:
                continue
            if _context.is_digest_message(m):
                continue  # 旧会话回顾会被下方重建取代
            if m.get("role") == "system":
                sticky.append(m)  # 中途注入的技能说明等：保留并统一放到回顾之后
                continue
            kept.append(m)

        new_entries = list(self._digest_entries)
        for t in fold:
            new_entries.append(_context.turn_to_gist(t))
        new_entries = new_entries[-_context.MAX_DIGEST_ENTRIES:]

        digest_text = _context.digest_content(new_entries, mention_recall=cfg.context_recall)
        candidate = [messages[0]]
        if digest_text:
            candidate.append({"role": "system", "content": digest_text})
        candidate += sticky + kept

        after = _context.estimate_messages_tokens(candidate)
        if after >= before:
            # 折叠反而不省（如都是很短的轮次，回顾 header 的固定开销占主导）：保持原样
            self.context_stats.update(triggered=False, reason="折叠不省 token，跳过", before=before, after=after)
            if self.verbose:
                print(f"[context] 历史估算 ≈{est} > 预算 {budget}，但折叠不省 token（{before}→{after}），保持原样")
            return

        # 应用：旧轮原文入档、gist 入会话回顾、替换消息（只在此刻动 self.messages）
        for t in fold:
            self._archive.append(_context.archive_entry_for(messages, t))
        if len(self._archive) > _context.MAX_ARCHIVE_TURNS:
            self._archive = self._archive[-_context.MAX_ARCHIVE_TURNS:]
        self._digest_entries = new_entries
        self.messages = candidate

        self.context_stats.update(
            triggered=True,
            folded_turns=len(fold),
            archived_messages=len(removed_idx),
            before=before,
            after=after,
            saved=before - after,
        )
        if self.verbose:
            print(
                f"[context] 历史估算 ≈{est} > 预算 {budget}：折叠 {len(fold)} 个旧轮"
                f"（{len(removed_idx)} 条消息），消息 token 估算 {before} → {after}"
                f"（省 ~{before - after}）"
            )
        self._ensure_recall_tool()

    def _maybe_compact_mid_turn(self) -> None:
        """单轮任务中途：当前这轮累计超预算时，自动折叠"更早、已被模型消费"的工具回合。

        只动当前轮内**已完成**的旧工具回合（保留最近 keep_recent 个 + 正在进行的这步），
        绝不折叠还没被模型回复的在途回合，也不破坏任何 tool_calls<->tool 配对；
        被折回合的原文存入 self._archive，模型可经 recall_history 取回。
        若没得折或折了不省就保持原样（防御）。
        """
        cfg = self.config
        if not (cfg.context_compress and cfg.context_mode != "off"):
            return
        if not cfg.context_compress_mid:
            return

        messages = self.messages
        last_user = -1
        for i, m in enumerate(messages):
            if m.get("role") == "user":
                last_user = i
        if last_user < 0 or last_user == len(messages) - 1:
            return  # 还没有任何工具回合

        budget = cfg.context_budget_tokens or _context.default_budget_tokens(cfg.model)
        est = (
            _context.estimate_messages_tokens(messages)
            + _context.estimate_tool_schema_tokens(self.tools)
            + _context.SCHEMA_OVERHEAD
        )
        if est <= budget:
            return

        exchanges = _context.split_exchanges(messages, last_user + 1)
        fold = _context.select_fold_exchanges(exchanges, keep_recent=max(1, cfg.context_keep_recent))
        if not fold:
            return

        removed: set[int] = set()
        for e in fold:
            removed.update(range(e.start, e.end))
        before = _context.estimate_messages_tokens(messages)
        candidate = [m for i, m in enumerate(messages) if i not in removed]
        after = _context.estimate_messages_tokens(candidate)
        if after >= before:
            return  # 只删不加，理论上不会发生；防御

        for e in fold:
            self._archive.append(_context.archive_exchange_entry(messages, e))
        if len(self._archive) > _context.MAX_ARCHIVE_TURNS:
            self._archive = self._archive[-_context.MAX_ARCHIVE_TURNS:]
        self.messages = candidate

        self.context_stats.update(
            triggered=True,
            kind="mid-turn",
            folded_exchanges=len(fold),
            archived_messages=len(removed),
            before=before,
            after=after,
            saved=before - after,
        )
        if self.verbose:
            print(
                f"[context] 单轮中途 ≈{est} > 预算 {budget}：折叠 {len(fold)} 个已消费工具回合"
                f"（{len(removed)} 条消息），token 估算 {before} → {after}（省 ~{before - after}）"
            )
        self._ensure_recall_tool()

    def _ensure_recall_tool(self) -> None:
        """存档非空且开启 recall 时，把 recall_history 工具接到 Agent（幂等）。"""
        if not (self.config.context_recall and self._archive):
            return
        if self._recall_tool is not None:
            if self._recall_tool.name not in self._by_name:
                self.tools.append(self._recall_tool)
                self._by_name[self._recall_tool.name] = self._recall_tool
            return
        existing = self._by_name.get("recall_history")
        if existing is not None:
            self._recall_tool = existing
            return
        tool = make_tool(
            self._recall_history,
            name="recall_history",
            description=_context.make_recall_description(),
        )
        self._recall_tool = tool
        self.tools.append(tool)
        self._by_name[tool.name] = tool

    def _recall_history(self, query: str) -> str:
        """检索本会话更早、被折叠成小结的轮次的原始对话片段。

        当会话回顾里的要点不够精确，或用户问起早先某次操作的细节（工具输出 / 路径 / 数字 /
        代码）而当前上下文没有时，调用它取回原文。

        Args:
            query (str): 要检索的关键词或短语。
        """
        return _context.recall_search(self._archive, query)
