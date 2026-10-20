"""配置模块：由环境变量 + 可选的 .env 文件驱动，无需改代码即可切换厂商。

约定的环境变量（见项目根目录 .env.example）：
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_TEMPERATURE
    AGENT_MAX_ITERATIONS / SYSTEM_PROMPT / LLM_REQUEST_TIMEOUT / TOOL_TIMEOUT
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 项目根目录（harness/ 的上一级），用于定位 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = Path(os.environ.get("HARNESS_ENV_FILE", _PROJECT_ROOT / ".env"))


def load_env_file() -> None:
    """把 .env 载入 os.environ。若同名的真实环境变量已存在则保留真实值。"""
    if _ENV_FILE.is_file():
        load_dotenv(_ENV_FILE, override=False)


load_env_file()


@dataclass
class HarnessConfig:
    """一次 LLM 接入所需的全部配置。

    通过 ``HarnessConfig.from_env()`` 从环境变量构建；也可以直接实例化覆盖。
    """

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: Optional[float] = None
    max_iterations: int = 40           # 单轮问题 agent 循环最多几步（防死循环；科研长任务建议 60~200）
    system_prompt: str = (
        "你是一个善于使用工具的智能体。需要实时信息、精确计算或外部数据时，"
        "请调用可用工具；否则直接、简洁地回答用户。用中文回答。"
    )
    request_timeout: float = 90.0      # 单次请求超时（秒）
    tool_timeout: float = 30.0         # 单个工具执行超时（秒）
    verbose: bool = False
    # —— "主答 + 审查改进"多模型协作 ——
    collab_enabled: bool = False            # 是否启用协作（主答模型+审查模型配合）
    collab_critic_model: Optional[str] = None  # 审查角色的模型；None=用厂商默认轻量模型
    # —— 上下文压缩（headroom 思路，省 token）——
    context_compress: bool = True           # 总开关：多轮超预算时把更早的已完成轮次折叠成小结
    context_budget_tokens: int = 0         # 触发折叠的估算 token 上限；0=按模型窗口自动
    context_mode: str = "gist"              # 小结生成方式：现实现 "gist"（免费确定性）；预留 "off"
    context_recall: bool = True             # 折叠后是否注册 recall_history 工具（可取回折叠前原文）
    context_keep_recent: int = 1            # 保留最近几个"已完成"轮次/回合的全文不折叠
    context_compress_mid: bool = True       # 单轮任务中途超预算时，也自动折叠更早的已消费工具回合

    @classmethod
    def from_env(cls) -> "HarnessConfig":
        def _f(name: str, default=None):
            v = os.environ.get(name)
            return v if v not in (None, "") else default

        def _flag(name: str, default: bool) -> bool:
            v = _f(name)
            if v is None:
                return default
            return str(v).lower() in ("1", "true", "yes", "on")

        def _int(name: str, default: int) -> int:
            v = _f(name)
            if not v:
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        max_iter = _f("AGENT_MAX_ITERATIONS")
        request_timeout = _f("LLM_REQUEST_TIMEOUT")
        tool_timeout = _f("TOOL_TIMEOUT")
        temperature = _f("LLM_TEMPERATURE")
        collab_enabled = _flag("COLLAB_ENABLED", False)
        collab_critic = _f("COLLAB_CRITIC_MODEL")

        ctx_mode = (_f("CONTEXT_MODE") or "gist").strip().lower()
        if ctx_mode not in ("gist", "off"):
            ctx_mode = "gist"

        return cls(
            base_url=(_f("LLM_BASE_URL") or cls.base_url).rstrip("/"),
            api_key=_f("LLM_API_KEY") or "",
            model=_f("LLM_MODEL") or "gpt-4o-mini",
            temperature=float(temperature) if temperature else None,
            max_iterations=int(max_iter) if max_iter else 40,
            system_prompt=_f("SYSTEM_PROMPT") or cls.system_prompt,
            request_timeout=float(request_timeout) if request_timeout else 90.0,
            tool_timeout=float(tool_timeout) if tool_timeout else 30.0,
            verbose=_flag("HARNESS_VERBOSE", False),
            collab_enabled=collab_enabled,
            collab_critic_model=collab_critic,
            context_compress=_flag("CONTEXT_COMPRESS", True),
            context_budget_tokens=_int("CONTEXT_BUDGET_TOKENS", 0),
            context_mode=ctx_mode,
            context_recall=_flag("CONTEXT_RECALL", True),
            context_keep_recent=_int("CONTEXT_KEEP_RECENT", 1),
            context_compress_mid=_flag("CONTEXT_COMPRESS_MID", True),
        )

    def problems(self) -> list[str]:
        """返回配置缺失项的中文提示；空列表表示就绪。"""
        out: list[str] = []
        if not self.api_key:
            out.append(
                "未检测到 LLM_API_KEY。请把项目根目录的 .env.example 复制为 .env "
                "并填入你的密钥（本地 Ollama 除外，可填任意占位如 ollama）。"
            )
        if not self.base_url:
            out.append("未设置 LLM_BASE_URL。")
        if not self.model:
            out.append("未设置 LLM_MODEL。")
        return out

    def summary(self) -> str:
        key = (self.api_key[:6] + "…" + self.api_key[-4:]) if self.api_key else "（未设置）"
        collab = f"on（主答 {self.model} / 审查 {self.collab_critic_model or '默认'}）" if self.collab_enabled else "off"
        return (
            f"base_url = {self.base_url}\n"
            f"api_key  = {key}\n"
            f"model    = {self.model}\n"
            f"collab   = {collab}\n"
            f"temperature = {self.temperature}\n"
            f"max_iterations = {self.max_iterations}"
        )
