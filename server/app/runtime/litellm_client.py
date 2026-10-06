"""LiteLLM driver：把框架的 ModelRequest 翻译为 litellm.acompletion 调用。

业务层不直接依赖 LiteLLM；所有 LiteLLM 相关转换集中在本模块。
"""

from __future__ import annotations

from typing import Any

import litellm

from .base import CallTimer, ModelRequest, ModelResult, ToolCall, ToolStep, UsageInfo

# LiteLLM 不允许同时传 temperature/top_p 与部分 reasoning 参数等由 provider 决定，
# 这里只做 None 过滤，具体兼容性交给 LiteLLM 与各 provider。
_OPTIONAL_PARAMS = ("temperature", "top_p", "seed")


class LiteLLMClient:
    """面向单个 Deployment 的 LiteLLM 客户端。"""

    def __init__(
        self,
        *,
        litellm_model: str,
        api_base: str | None = None,
        api_key: str | None = None,
        custom_options: dict[str, Any] | None = None,
    ) -> None:
        self._model = litellm_model
        self._api_base = api_base
        self._api_key = api_key
        self._custom_options = custom_options or {}

    async def complete(self, request: ModelRequest) -> ModelResult:
        kwargs = self._base_kwargs(request)
        timer = CallTimer()
        response = await litellm.acompletion(**kwargs)
        finished_at, total_latency = timer.finish()

        choice = response.choices[0]
        text = choice.message.content or ""
        usage = self._extract_usage(response)
        raw = self._to_dict(response)

        return ModelResult(
            text=text,
            raw_response=raw,
            usage=usage,
            started_at=timer.started_at,
            finished_at=finished_at,
            total_latency_s=total_latency,
            ttft_s=None,  # 非流式调用无 TTFT，见 base.ModelResult 注释
            generation_time_s=None,
        )

    async def complete_with_tools(self, request: ModelRequest, tools: list[dict[str, Any]]) -> ToolStep:
        """带 function calling 的一次调用（agent 循环用）。"""
        import json

        kwargs = self._base_kwargs(request)
        kwargs["tools"] = tools
        response = await litellm.acompletion(**kwargs)

        message = response.choices[0].message
        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

        return ToolStep(
            content=message.content or "",
            tool_calls=tool_calls,
            assistant_message=message.model_dump(exclude_none=True),
            usage=self._extract_usage(response),
            raw_response=self._to_dict(response),
        )

    def _base_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        """组装 litellm 调用参数（complete / complete_with_tools 共用）。"""
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
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.reasoning_effort is not None:
            kwargs["reasoning_effort"] = request.reasoning_effort

        # Deployment 级与 Profile 级 provider 专属参数
        kwargs.update(self._custom_options)
        kwargs.update(request.provider_params)
        return kwargs

    @staticmethod
    def _extract_usage(response: Any) -> UsageInfo:
        usage = getattr(response, "usage", None)
        if usage is None:
            return UsageInfo()

        cached = None
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details is not None:
            cached = getattr(prompt_details, "cached_tokens", None)

        reasoning = None
        completion_details = getattr(usage, "completion_tokens_details", None)
        if completion_details is not None:
            reasoning = getattr(completion_details, "reasoning_tokens", None)

        return UsageInfo(
            input_tokens=getattr(usage, "prompt_tokens", None),
            cached_input_tokens=cached,
            output_tokens=getattr(usage, "completion_tokens", None),
            reasoning_tokens=reasoning,
        )

    @staticmethod
    def _to_dict(response: Any) -> dict[str, Any] | None:
        for method in ("model_dump", "dict"):
            fn = getattr(response, method, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    continue
        return None


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
