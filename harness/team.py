"""多模型协作 ——「主答 + 审查改进」。

流程：主模型（可调工具）完整作答 → 审查模型（轻量/快速，无工具）检验是否满足任务；
不满意则给出具体修改意见，把意见连同任务打回主模型重做；最多 max_rounds 轮。

两个角色共用同一家厂商/密钥（key 只一份），但可以是不同模型，
如 DeepSeek 的 deepseek-v4-pro（主答）+ deepseek-v4-flash（审查）。
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

from . import providers
from .agent import Agent
from .config import HarnessConfig

CRITIC_SYSTEM_PROMPT = (
    "你是严格的质检员。用户把任务交给一个智能体（它可调用工具），你负责检验它的最终回答"
    "是否真正满足任务要求。\n"
    "规则：若回答已正确、完整、可交付，第一行只输出：PASS\n"
    "否则第一行输出：REVISE，随后给出具体、可执行的修改意见（指出哪里错/缺什么/怎么做）。\n"
    "不要使用任何工具，也不要复述任务本身。"
)

DEFAULT_MAX_ROUNDS = 3


def parse_verdict(text: str) -> tuple[bool, str]:
    """解析审查输出：返回 (是否通过, 修改意见/说明)。"""
    text = (text or "").strip()
    if not text:
        return False, "（审查模型无输出）"
    lines = text.splitlines()
    head = lines[0].strip()
    rest = text[len(head):].strip()
    if head.upper().startswith("PASS"):
        return True, rest or "已通过"
    return False, text if not rest else rest


class Collaboration:
    """「主答 + 审查改进」的两模型协作器。

    run() 可被连续调用（多轮对话）；主答与审查的对话历史各自累积。
    """

    def __init__(
        self,
        config: HarnessConfig,
        *,
        critic_model: Optional[str] = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        on_event: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, dict], None]] = None,
        on_tool_end: Optional[Callable[[str, object], None]] = None,
        confirm_handler: Optional[Callable[[str, dict], bool]] = None,
        ask_handler: Optional[Callable[[str, dict], str]] = None,
    ):
        self.config = config
        self.max_rounds = max(1, int(max_rounds))
        self.on_event = on_event or (lambda msg: None)
        critic = (critic_model or providers.critic_default_for(config.base_url) or config.model)
        self.main_model = config.model
        self.critic_model = critic

        # 主答：带工具，走完整 agent 循环
        self.main = Agent(
            config,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            confirm_handler=confirm_handler,
            ask_handler=ask_handler,
        )
        # 审查：同一厂商 + 审查专用人设，无工具
        critic_config = replace(config, model=critic, system_prompt=CRITIC_SYSTEM_PROMPT)
        self.critic = Agent(critic_config, tools=[], system_prompt=CRITIC_SYSTEM_PROMPT)

    def reset(self) -> None:
        self.main.reset()
        self.critic.reset()

    def add_tools(self, tools) -> None:
        """运行期给协作的"主答"模型追加工具（如工作目录文件工具）。"""
        self.main.add_tools(tools)

    # ---------------------------------------------------------- 主流程
    def run(self, user_input: str) -> str:
        """让两模型协作完成一条用户请求，返回最终可交付回答。"""
        user_input = (user_input or "").strip()
        if not user_input:
            raise ValueError("user_input 不能为空")

        self.on_event(f"协作：主答 {self.main_model} ＋ 审查 {self.critic_model}，最多 {self.max_rounds} 轮")
        answer = self.main.run(user_input)
        if not answer:
            answer = "（主模型未给出文本回答）"

        for round_no in range(1, self.max_rounds + 1):
            self.on_event(f"—— 第 {round_no} 轮：审查模型复核 ——")
            verdict = self.critic.run(
                f"用户任务：\n{user_input}\n\n主模型给出的回答：\n{answer}"
            )
            passed, comments = parse_verdict(verdict)
            if passed:
                self.on_event("审查通过，输出最终回答。")
                return answer

            self.on_event(f"审查未通过：{_short(comments, 220)}")
            answer = self.main.run(
                f"你的上一版回答未通过质检。\n--- 审查意见 ---\n{comments}\n"
                "请据此修改并重新回答（需要时可再次调用工具核实）。"
            )
            if not answer:
                answer = "（主模型重做后仍无文本回答）"

        self.on_event(f"已达 {self.max_rounds} 轮，采纳最后一版。")
        return answer


def _short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def guess_critic_model(config: HarnessConfig) -> Optional[str]:
    """根据 .env/配置推断审查模型：优先显式配置，否则同厂商默认轻量模型。"""
    return config.collab_critic_model or providers.critic_default_for(config.base_url)
