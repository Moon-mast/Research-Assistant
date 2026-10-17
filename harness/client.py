"""LLM 客户端封装：基于 openai 官方 SDK，指向任意 OpenAI 兼容的 base_url。"""
from __future__ import annotations

try:
    from openai import OpenAI as _OpenAIClient
except Exception:  # openai 未安装时保持可 import（构建 client 时才报错）
    _OpenAIClient = None  # type: ignore[assignment,misc]

from .config import HarnessConfig
from .tools import Tool


def build_client(config: HarnessConfig) -> "_OpenAIClient":
    if _OpenAIClient is None:
        raise RuntimeError("未安装 openai 包，请先执行：pip install -r requirements.txt")
    return _OpenAIClient(
        base_url=config.base_url,
        api_key=config.api_key,
        timeout=config.request_timeout,
        max_retries=2,
    )


def chat_completion(
    client: "_OpenAIClient",
    *,
    messages: list,
    tools: list[Tool],
    model: str,
    temperature: float | None,
    verbose: bool = False,
):
    """发起一次 chat.completions 请求（非流式，工具循环最稳）。

    返回 response 对象，由 Agent 负责读取 message / tool_calls。
    """
    kwargs: dict = {
        "messages": messages,
        "model": model,
    }
    if tools:
        kwargs["tools"] = [t.openai_schema() for t in tools]
        kwargs["tool_choice"] = "auto"
    if temperature is not None:
        kwargs["temperature"] = temperature
    if verbose:
        print(f"[request] model={model} messages={len(messages)} tools={len(tools)}")
    return client.chat.completions.create(**kwargs)
