"""价格解析与成本计算。

来源优先级：
1. 用户对具体 Deployment 的手动 override（输入价与输出价同时给出才算）
2. models.dev
3. LiteLLM cost map
4. Unknown

每次实际 Run 必须把解析结果固化为 PricingSnapshot；历史 cost 绝不按未来价格重算。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import httpx
import litellm

from ..config import settings
from ..models import Deployment, Provider

logger = logging.getLogger(__name__)

MODELS_DEV_API = "https://models.dev/api.json"
_MODELS_DEV_TTL_S = 3600.0


@dataclass
class ResolvedPricing:
    source: str  # manual_override / models_dev / litellm_cost_map / unknown
    price_input_per_mtok: float | None
    price_output_per_mtok: float | None
    price_cached_input_per_mtok: float | None
    price_cache_write_per_mtok: float | None = None
    currency: str = "USD"
    raw: dict | None = None


_models_dev_cache: tuple[float, dict] | None = None


def _fetch_models_dev() -> dict | None:
    """获取 models.dev 价格目录。内存缓存 + 磁盘缓存，失败返回 None（离线安全）。"""
    global _models_dev_cache
    now = time.time()
    if _models_dev_cache and now - _models_dev_cache[0] < _MODELS_DEV_TTL_S:
        return _models_dev_cache[1]

    cache_path = settings.data_dir / "models_dev_cache.json"
    try:
        resp = httpx.get(MODELS_DEV_API, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        _models_dev_cache = (now, data)
        try:
            cache_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        return data
    except Exception as exc:
        logger.info("models.dev 获取失败，尝试磁盘缓存: %s", exc)

    if cache_path.is_file():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            _models_dev_cache = (now, data)
            return data
        except Exception:
            return None
    return None


def _find_models_dev_model(api_model_name: str) -> dict | None:
    """在 models.dev 目录中按模型 id 查找条目。"""
    data = _fetch_models_dev()
    if not data:
        return None
    for provider_entry in data.values():
        models = (provider_entry or {}).get("models") or {}
        entry = models.get(api_model_name)
        if entry:
            return entry
    return None


def _from_models_dev(api_model_name: str) -> ResolvedPricing | None:
    entry = _find_models_dev_model(api_model_name)
    if entry is None:
        return None
    cost = entry.get("cost") or {}
    price_in = cost.get("input")
    price_out = cost.get("output")
    if price_in is None or price_out is None:
        return None
    return ResolvedPricing(
        source="models_dev",
        price_input_per_mtok=float(price_in),
        price_output_per_mtok=float(price_out),
        price_cached_input_per_mtok=(
            float(cost["cache_read"]) if cost.get("cache_read") is not None else None
        ),
        price_cache_write_per_mtok=(
            float(cost["cache_write"]) if cost.get("cache_write") is not None else None
        ),
        raw={"model_id": api_model_name, "cost": cost},
    )


def _from_litellm_cost_map(provider_type: str, api_model_name: str) -> ResolvedPricing | None:
    keys = [
        api_model_name,
        f"{provider_type}/{api_model_name}",
    ]
    for key in keys:
        entry = litellm.model_cost.get(key)
        if not entry:
            continue
        price_in = entry.get("input_cost_per_token")
        price_out = entry.get("output_cost_per_token")
        if price_in is None or price_out is None:
            continue
        cached = entry.get("cache_read_input_token_cost")
        write = entry.get("cache_creation_input_token_cost", entry.get("cache_write_input_token_cost"))
        return ResolvedPricing(
            source="litellm_cost_map",
            price_input_per_mtok=float(price_in) * 1e6,
            price_output_per_mtok=float(price_out) * 1e6,
            price_cached_input_per_mtok=float(cached) * 1e6 if cached is not None else None,
            price_cache_write_per_mtok=float(write) * 1e6 if write is not None else None,
            raw={"key": key},
        )
    return None


def resolve_pricing_for(
    *,
    provider_type: str,
    api_model_name: str,
    manual_input: float | None = None,
    manual_output: float | None = None,
    manual_cached_input: float | None = None,
    manual_cache_write: float | None = None,
) -> ResolvedPricing:
    """按优先级解析价格（输入价与输出价同时填写才视为手动 override）。"""
    if manual_input is not None and manual_output is not None:
        return ResolvedPricing(
            source="manual_override",
            price_input_per_mtok=manual_input,
            price_output_per_mtok=manual_output,
            price_cached_input_per_mtok=manual_cached_input,
            price_cache_write_per_mtok=manual_cache_write,
        )
    for resolved in (
        _from_models_dev(api_model_name),
        _from_litellm_cost_map(provider_type, api_model_name),
    ):
        if resolved is not None:
            return resolved
    return ResolvedPricing(
        source="unknown",
        price_input_per_mtok=None,
        price_output_per_mtok=None,
        price_cached_input_per_mtok=None,
    )


def resolve_pricing(deployment: Deployment, provider: Provider) -> ResolvedPricing:
    """按优先级解析 Deployment 当前价格。"""
    return resolve_pricing_for(
        provider_type=provider.type,
        api_model_name=deployment.api_model_name,
        manual_input=deployment.price_input_per_mtok,
        manual_output=deployment.price_output_per_mtok,
        manual_cached_input=deployment.price_cached_input_per_mtok,
        manual_cache_write=deployment.price_cache_write_per_mtok,
    )


def model_capabilities(api_model_name: str) -> dict | None:
    """从 models.dev 条目提取模型能力，供界面按模型调整可配置项。

    返回 None 表示目录中没有该模型（能力未知，界面不做限制）。
    """
    entry = _find_models_dev_model(api_model_name)
    if entry is None:
        return None
    limit = entry.get("limit") or {}
    return {
        "source": "models_dev",
        "reasoning": entry.get("reasoning"),
        "tool_call": entry.get("tool_call"),
        "attachment": entry.get("attachment"),
        "context_limit": limit.get("context"),
        "output_limit": limit.get("output"),
        "modalities": entry.get("modalities"),
    }


def compute_cost(
    pricing: ResolvedPricing,
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
    cache_write_tokens: int | None = None,
) -> float | None:
    """按快照价格计算单次调用成本。价格或 token 数缺失时返回 None。"""
    if pricing.price_input_per_mtok is None or pricing.price_output_per_mtok is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None

    cached = cached_input_tokens or 0
    write = cache_write_tokens or 0
    uncached = max(0, input_tokens - cached)
    cached_rate = (
        pricing.price_cached_input_per_mtok
        if pricing.price_cached_input_per_mtok is not None
        else pricing.price_input_per_mtok
    )
    write_rate = (
        pricing.price_cache_write_per_mtok
        if pricing.price_cache_write_per_mtok is not None
        else pricing.price_input_per_mtok
    )
    return (
        uncached * pricing.price_input_per_mtok / 1e6
        + cached * cached_rate / 1e6
        + write * write_rate / 1e6
        + output_tokens * pricing.price_output_per_mtok / 1e6
    )
