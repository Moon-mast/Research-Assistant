"""LLM 客户端封装：基于 openai 官方 SDK，指向任意 OpenAI 兼容的 base_url。

模型调用方式仿 DeepSeek Harness（@deepseek-ai/dsh-llm-deepseek + dsh-llm-retry）：
- 请求体注入 thinking / output_config.effort / max_tokens（模型 id 直通，新模型无需登记）；
  DeepSeek 端点原生支持这些扩展字段；其它厂商不认识会被拒，故默认 auto 模式只在
  DeepSeek 端点注入（LLM_THINKING=enabled/disabled 可强制全局开/关）。
- 失败重试按 dsh-llm-retry normal 模式分类：RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT /
  EMPTY_RESPONSE，指数退避 500ms→10s、10% 抖动、默认 5 次；非可重试错误立即抛出。
"""
from __future__ import annotations

import random
import time

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


def build_wire_extra(config: HarnessConfig) -> dict:
    """仿 dsh-llm-deepseek 构造请求扩展参数：thinking / output_config / max_tokens。

    dsh 的规则：thinking 取 enabled|disabled（disabled 时 effort 锁 off）；
    effort 取 off|low|high|max（默认 high），经 output_config 下发；
    max_tokens 是每请求输出上限（dsh 默认 256000，模型自身上限优先）。
    LLM_THINKING=auto（默认）时仅在 DeepSeek 端点注入，避免其它厂商拒绝未知参数。
    """
    extra: dict = {}
    base = (config.base_url or "").lower()
    is_deepseek = "deepseek" in base
    thinking = (config.llm_thinking or "auto").lower()
    if thinking == "auto" and not is_deepseek:
        return extra
    effort = (config.llm_reasoning_effort or "high").lower()
    if effort not in ("off", "low", "high", "max"):
        effort = "high"
    if thinking == "disabled" and effort != "off":
        effort = "off"
    extra["thinking"] = {"type": "disabled" if effort == "off" else "enabled"}
    if effort != "off":
        extra["output_config"] = {"effort": effort}
    if config.llm_max_tokens and config.llm_max_tokens > 0:
        extra["max_tokens"] = int(config.llm_max_tokens)
    return extra


def _classify_error(e: Exception) -> str | None:
    """按 dsh-llm-retry 的错误分类；返回 None 表示不可重试。"""
    try:
        import openai as _oa
    except Exception:  # pragma: no cover
        return None
    if isinstance(e, _oa.RateLimitError):
        return "RATE_LIMIT"
    if isinstance(e, _oa.APITimeoutError):
        return "TIMEOUT"
    if isinstance(e, _oa.APIConnectionError):
        return "TRANSPORT"
    if isinstance(e, _oa.InternalServerError):
        return "SERVER"
    st = getattr(e, "status_code", None)
    if isinstance(st, int) and 500 <= st < 600:
        return "SERVER"
    return None


class _EmptyResponse(Exception):
    """choices 里既无 content 又无 tool_calls（dsh 的 EMPTY_RESPONSE 分类）。"""


def chat_completion(
    client: "_OpenAIClient",
    *,
    messages: list,
    tools: list[Tool],
    model: str,
    temperature: float | None,
    verbose: bool = False,
    wire_extra: dict | None = None,
    retry_max: int = 5,
    backoff_initial_ms: int = 500,
    backoff_max_ms: int = 10000,
):
    """发起一次 chat.completions 请求（非流式，工具循环最稳），带 dsh 式分类重试。

    返回 response 对象，由 Agent 负责读取 message / tool_calls。
    可重试：RATE_LIMIT / SERVER(5xx) / TIMEOUT / TRANSPORT / EMPTY_RESPONSE；
    退避指数增长（初始 backoff_initial_ms，封顶 backoff_max_ms）并加 10% 抖动。
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
    if wire_extra:
        kwargs.update(wire_extra)

    attempt = 0
    delay_s = max(backoff_initial_ms, 1) / 1000.0
    cap_s = max(backoff_max_ms, delay_s * 1000) / 1000.0
    while True:
        try:
            resp = client.chat.completions.create(**kwargs)
            choices = getattr(resp, "choices", None)
            if not choices:
                raise _EmptyResponse("no choices")
            msg = choices[0].message
            if not (getattr(msg, "content", None) or getattr(msg, "tool_calls", None)):
                raise _EmptyResponse("no content and no tool_calls")
            if verbose:
                print(f"[request] model={model} messages={len(messages)} tools={len(tools)}")
            return resp
        except _EmptyResponse:
            attempt += 1
            if attempt > max(0, retry_max):
                return resp  # 耗尽后交还原（空）响应，由上层按空回答处理，不炸 agent 循环
            pause = min(delay_s, cap_s) * (1 + random.uniform(-0.1, 0.1))
            if verbose:
                print(f"[retry] EMPTY_RESPONSE 第 {attempt}/{retry_max} 次，{pause:.2f}s 后重试")
            time.sleep(pause)
            delay_s = min(delay_s * 2, cap_s)
        except Exception as e:
            kind = _classify_error(e)
            attempt += 1
            if kind is None or attempt > max(0, retry_max):
                raise
            pause = min(delay_s, cap_s) * (1 + random.uniform(-0.1, 0.1))
            if verbose:
                print(f"[retry] {kind} 第 {attempt}/{retry_max} 次，{pause:.2f}s 后重试")
            time.sleep(pause)
            delay_s = min(delay_s * 2, cap_s)
