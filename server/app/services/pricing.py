"""价格解析与成本计算。

来源优先级：
1. 用户对具体 Deployment 的手动 override
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


def _from_models_dev(deployment: Deployment, provider: Provider) -> ResolvedPricing | None:
    data = _fetch_models_dev()
    if not data:
        return None

    # models.dev api.json: {provider_id: {"models": {model_id: {"cost": {...}}}}}
    # provider_id 与我们的 provider.type 没有稳定映射，遍历所有 provider 查找 model id。
    candidates = {deployment.api_model_name}
    for provider_entry in data.values():
        models = (provider_entry or {}).get("models") or {}
        for model_id in list(candidates):
            entry = models.get(model_id)
            if not entry:
                continue
            cost = entry.get("cost") or {}
            price_in = cost.get("input")
            price_out = cost.get("output")
            if price_in is None or price_out is None:
                continue
            return ResolvedPricing(
                source="models_dev",
                price_input_per_mtok=float(price_in),
                price_output_per_mtok=float(price_out),
                price_cached_input_per_mtok=(
                    float(cost["cache_read"]) if cost.get("cache_read") is not None else None
                ),
                raw={"model_id": model_id, "cost": cost},
            )
    return None


def _from_litellm_cost_map(deployment: Deployment, provider: Provider) -> ResolvedPricing | None:
    keys = [
        deployment.api_model_name,
        f"{provider.type}/{deployment.api_model_name}",
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
        return ResolvedPricing(
            source="litellm_cost_map",
            price_input_per_mtok=float(price_in) * 1e6,
            price_output_per_mtok=float(price_out) * 1e6,
            price_cached_input_per_mtok=float(cached) * 1e6 if cached is not None else None,
            raw={"key": key},
        )
    return None


def resolve_pricing(deployment: Deployment, provider: Provider) -> ResolvedPricing:
    """按优先级解析 Deployment 当前价格。"""
    if deployment.price_input_per_mtok is not None and deployment.price_output_per_mtok is not None:
        return ResolvedPricing(
            source="manual_override",
            price_input_per_mtok=deployment.price_input_per_mtok,
            price_output_per_mtok=deployment.price_output_per_mtok,
            price_cached_input_per_mtok=None,
        )
    for resolver in (_from_models_dev, _from_litellm_cost_map):
        resolved = resolver(deployment, provider)
        if resolved is not None:
            return resolved
    return ResolvedPricing(
        source="unknown",
        price_input_per_mtok=None,
        price_output_per_mtok=None,
        price_cached_input_per_mtok=None,
    )


def compute_cost(
    pricing: ResolvedPricing,
    *,
    input_tokens: int | None,
    cached_input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """按快照价格计算单次调用成本。价格或 token 数缺失时返回 None。"""
    if pricing.price_input_per_mtok is None or pricing.price_output_per_mtok is None:
        return None
    if input_tokens is None or output_tokens is None:
        return None

    cached = cached_input_tokens or 0
    uncached = max(0, input_tokens - cached)
    cached_rate = (
        pricing.price_cached_input_per_mtok
        if pricing.price_cached_input_per_mtok is not None
        else pricing.price_input_per_mtok
    )
    return (
        uncached * pricing.price_input_per_mtok / 1e6
        + cached * cached_rate / 1e6
        + output_tokens * pricing.price_output_per_mtok / 1e6
    )
