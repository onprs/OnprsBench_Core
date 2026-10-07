"""LiteLLM driver：把框架的 ModelRequest 翻译为 litellm 流式调用。

为什么流式：
- 推理模型（reasoning_effort）思考时间长，同步长连接易被中间网关判定挂起而断开（5xx）；
  流式数据持续流动，连接保活。
- 流式才能采集 TTFT（首 token 时间）与 generation_time（首→末 token 时长），
  这两个字段是数据模型的一级记录项。

业务层不直接依赖 LiteLLM；所有 LiteLLM 相关转换集中在本模块。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import litellm

from .base import ModelRequest, ModelResult, ToolCall, ToolStep, UsageInfo

logger = logging.getLogger(__name__)

# LiteLLM 不允许同时传 temperature/top_p 与部分 reasoning 参数等由 provider 决定，
# 这里只做 None 过滤，具体兼容性交给 LiteLLM 与各 provider。
_OPTIONAL_PARAMS = ("temperature", "top_p", "seed")

# 网关瞬时错误（5xx、连接错误、流中断）的重试策略：最多 3 次尝试（首次 + 2 次重试）
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_S = (5.0, 15.0)

# 思考预算到 provider 参数的映射。只映射 LiteLLM 有明确定义的 provider；
# 无映射的 provider 不做臆测参数，未生效的字段由调用方记录进原始事实。
_BUDGET_PARAM_BUILDERS = {
    "anthropic": lambda n: {"thinking": {"type": "enabled", "budget_tokens": n}},
    "gemini": lambda n: {"thinking": {"type": "enabled", "budget_tokens": n}},
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _stream_completion(kwargs: dict[str, Any]) -> "_StreamAggregate":
    """发起流式调用并聚合全部 chunk。流中途出错同样抛出（由重试层处理）。"""
    kwargs["stream"] = True
    kwargs["stream_options"] = {"include_usage": True}

    started = _utcnow()
    t0 = time.perf_counter()
    ttft_s: float | None = None
    last_chunk_at: float | None = None

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_parts: dict[int, dict[str, Any]] = {}
    usage: UsageInfo = UsageInfo()
    finish_reason: str | None = None
    chunk_count = 0

    stream = await litellm.acompletion(**kwargs)
    async for chunk in stream:
        chunk_count += 1
        now = time.perf_counter()
        if ttft_s is None and _chunk_has_payload(chunk):
            ttft_s = now - t0
        last_chunk_at = now

        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = _extract_usage_from(chunk_usage)

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        choice = choices[0]
        if getattr(choice, "finish_reason", None):
            finish_reason = choice.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        if getattr(delta, "content", None):
            content_parts.append(delta.content)
        if getattr(delta, "reasoning_content", None):
            reasoning_parts.append(delta.reasoning_content)
        for tc in getattr(delta, "tool_calls", None) or []:
            slot = tool_parts.setdefault(tc.index, {"id": None, "name": None, "arguments": []})
            if tc.id:
                slot["id"] = tc.id
            function = getattr(tc, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    slot["name"] = function.name
                if getattr(function, "arguments", None):
                    slot["arguments"].append(function.arguments)

    finished = _utcnow()
    total_s = time.perf_counter() - t0
    generation_s = (last_chunk_at - t0 - ttft_s) if (ttft_s is not None and last_chunk_at) else None

    tool_calls = [
        ToolCall(
            id=slot["id"] or f"call-{index}",
            name=slot["name"] or "",
            arguments=_parse_tool_arguments("".join(slot["arguments"])),
        )
        for index, slot in sorted(tool_parts.items())
    ]
    return _StreamAggregate(
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts),
        tool_calls=tool_calls,
        usage=usage,
        finish_reason=finish_reason,
        started_at=started,
        finished_at=finished,
        total_latency_s=total_s,
        ttft_s=ttft_s,
        generation_time_s=generation_s,
        chunk_count=chunk_count,
        model=str(kwargs.get("model", "")),
    )


def _chunk_has_payload(chunk: Any) -> bool:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return False
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return False
    return bool(
        getattr(delta, "content", None)
        or getattr(delta, "reasoning_content", None)
        or getattr(delta, "tool_calls", None)
    )


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _extract_usage_from(usage: Any) -> UsageInfo:
    cached = None
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    if prompt_details is not None:
        cached = getattr(prompt_details, "cached_tokens", None)
    # 缓存写入：Anthropic 风格为顶层字段，部分网关放在 prompt_tokens_details
    cache_write = getattr(usage, "cache_creation_input_tokens", None)
    if cache_write is None and prompt_details is not None:
        cache_write = getattr(prompt_details, "cache_creation_tokens", None)
    reasoning = None
    completion_details = getattr(usage, "completion_tokens_details", None)
    if completion_details is not None:
        reasoning = getattr(completion_details, "reasoning_tokens", None)
    return UsageInfo(
        input_tokens=getattr(usage, "prompt_tokens", None),
        cached_input_tokens=cached,
        cache_write_tokens=cache_write,
        output_tokens=getattr(usage, "completion_tokens", None),
        reasoning_tokens=reasoning,
    )


class _StreamAggregate:
    """一次流式调用的聚合结果。"""

    attempts: int = 1  # 实际发起次数（重试后由调用方写入）

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


async def _stream_with_retry(
    kwargs: dict[str, Any], *, call_timeout_s: float | None = None
) -> "_StreamAggregate":
    """对瞬时性 API 错误做有限重试（指数退避）；客户端错误（4xx）不重试。

    墙钟上限（call_timeout_s）用于终止长时间无进展的请求（如模型持续思考）：
    它是确定性策略，超时后不重试，直接抛出。
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        backoff = _RETRY_BACKOFF_S[attempt] if attempt < len(_RETRY_BACKOFF_S) else None
        try:
            call = _stream_completion(kwargs)
            if call_timeout_s and call_timeout_s > 0:
                agg = await asyncio.wait_for(call, timeout=call_timeout_s)
            else:
                agg = await call
            agg.attempts = attempt + 1
            return agg
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"模型调用超过墙钟上限 {call_timeout_s:.0f}s") from exc
        except Exception as exc:
            last_exc = exc
            status = getattr(exc, "status_code", None)
            # 4xx 为确定性错误（鉴权/参数），重试无意义
            if status is not None and 400 <= int(status) < 500:
                raise
            if backoff is not None:
                logger.warning("模型调用瞬时失败（%s），%.0fs 后重试", exc, backoff)
                await asyncio.sleep(backoff)
    assert last_exc is not None
    raise last_exc


class LiteLLMClient:
    """面向单个 Deployment 的 LiteLLM 客户端（流式）。"""

    def __init__(
        self,
        *,
        litellm_model: str,
        provider_type: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        custom_options: dict[str, Any] | None = None,
    ) -> None:
        self._model = litellm_model
        self._provider_type = provider_type
        self._api_base = api_base
        self._api_key = api_key
        self._custom_options = custom_options or {}

    async def complete(self, request: ModelRequest) -> ModelResult:
        kwargs, ignored = self._base_kwargs(request)
        agg = await _stream_with_retry(kwargs, call_timeout_s=request.call_timeout_s)
        return ModelResult(
            text=agg.content,
            raw_response={
                "streamed": True,
                "model": agg.model,
                "finish_reason": agg.finish_reason,
                "chunks": agg.chunk_count,
                "reasoning_chars": len(agg.reasoning_content),
                "attempts": agg.attempts,
                **({"ignored_params": ignored} if ignored else {}),
            },
            usage=agg.usage,
            started_at=agg.started_at,
            finished_at=agg.finished_at,
            total_latency_s=agg.total_latency_s,
            ttft_s=agg.ttft_s,
            generation_time_s=agg.generation_time_s,
            finish_reason=agg.finish_reason,
        )

    async def complete_with_tools(self, request: ModelRequest, tools: list[dict[str, Any]]) -> ToolStep:
        """带 function calling 的一次调用（agent 循环用），流式聚合。"""
        kwargs, ignored = self._base_kwargs(request)
        kwargs["tools"] = tools
        agg = await _stream_with_retry(kwargs, call_timeout_s=request.call_timeout_s)

        assistant_message: dict[str, Any] = {"role": "assistant", "content": agg.content}
        if agg.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                }
                for tc in agg.tool_calls
            ]

        return ToolStep(
            content=agg.content,
            tool_calls=agg.tool_calls,
            assistant_message=assistant_message,
            usage=agg.usage,
            raw_response={
                "streamed": True,
                "model": agg.model,
                "finish_reason": agg.finish_reason,
                "chunks": agg.chunk_count,
                "ttft_s": agg.ttft_s,
                "attempts": agg.attempts,
                **({"ignored_params": ignored} if ignored else {}),
            },
            finish_reason=agg.finish_reason,
            started_at=agg.started_at,
            finished_at=agg.finished_at,
            total_latency_s=agg.total_latency_s,
            ttft_s=agg.ttft_s,
            generation_time_s=agg.generation_time_s,
        )

    def _base_kwargs(self, request: ModelRequest) -> tuple[dict[str, Any], list[str]]:
        """组装 litellm 调用参数（complete / complete_with_tools 共用）。

        返回 (kwargs, 未生效参数列表)：未生效参数会写入原始事实，
        避免“用户配置了但实际被静默丢弃”。
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": request.messages,
            "timeout": request.timeout_s,
        }
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._api_key:
            kwargs["api_key"] = self._api_key

        for name in _OPTIONAL_PARAMS:
            value = getattr(request, name)
            if value is not None:
                kwargs[name] = value
        # max_output_tokens <= 0 统一按“不限制”处理：不传 max_tokens，由上游默认值决定
        if request.max_output_tokens is not None and request.max_output_tokens > 0:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.reasoning_effort is not None:
            kwargs["reasoning_effort"] = request.reasoning_effort

        ignored: list[str] = []
        if request.reasoning_budget is not None:
            builder = _BUDGET_PARAM_BUILDERS.get(self._provider_type or "")
            if builder is not None:
                kwargs.update(builder(int(request.reasoning_budget)))
            else:
                ignored.append("reasoning_budget")

        # Deployment 级与 Profile 级 provider 专属参数（可覆盖以上同名参数）
        kwargs.update(self._custom_options)
        kwargs.update(request.provider_params)
        return kwargs, ignored


def build_litellm_model_string(provider_type: str, api_model_name: str) -> str:
    """按 provider 类型映射 LiteLLM model 字符串。

    同一模型的不同 Provider 渠道通过该映射区分，互不影响。
    """
    match provider_type:
        case "openai":
            return api_model_name
        case "openai_compatible":
            return f"openai/{api_model_name}"
        case "openrouter":
            return f"openrouter/{api_model_name}"
        case "anthropic":
            return f"anthropic/{api_model_name}"
        case "gemini":
            return f"gemini/{api_model_name}"
        case "deepseek":
            return f"deepseek/{api_model_name}"
        case "ollama":
            return f"ollama/{api_model_name}"
        case _:
            # 其他类型按 openai_compatible 处理，依赖 base_url
            return f"openai/{api_model_name}"
