"""价格解析优先级与成本计算测试。"""

from __future__ import annotations

from app.models import Deployment, Model, Provider
from app.services.pricing import ResolvedPricing, compute_cost, resolve_pricing


def _make_deployment(price_in=None, price_out=None, api_model_name="mock-strong") -> tuple[Deployment, Provider]:
    provider = Provider(name="Mock", type="mock")
    model = Model(canonical_id="mock/model", display_name="Mock")
    deployment = Deployment(
        name="d",
        model_id=model.id,
        provider_id=provider.id,
        api_model_name=api_model_name,
        price_input_per_mtok=price_in,
        price_output_per_mtok=price_out,
    )
    deployment.model = model
    deployment.provider = provider
    return deployment, provider


def test_manual_override_has_top_priority() -> None:
    deployment, provider = _make_deployment(price_in=3.0, price_out=15.0)
    resolved = resolve_pricing(deployment, provider)
    assert resolved.source == "manual_override"
    assert resolved.price_input_per_mtok == 3.0
    assert resolved.price_output_per_mtok == 15.0


def test_unknown_when_no_price_available() -> None:
    # mock provider 不在任何价格目录中，且测试环境 models.dev 已禁用
    deployment, provider = _make_deployment(api_model_name="no-such-model-xyz")
    resolved = resolve_pricing(deployment, provider)
    assert resolved.source == "unknown"
    assert resolved.price_input_per_mtok is None


def test_litellm_cost_map_fallback() -> None:
    # gpt-4o-mini 在 litellm cost map 中存在（本地数据，无需网络）
    provider = Provider(name="OpenAI", type="openai")
    model = Model(canonical_id="openai/gpt-4o-mini", display_name="GPT-4o mini")
    deployment = Deployment(
        name="d", model_id=model.id, provider_id=provider.id, api_model_name="gpt-4o-mini"
    )
    deployment.model = model
    deployment.provider = provider
    resolved = resolve_pricing(deployment, provider)
    assert resolved.source == "litellm_cost_map"
    assert resolved.price_input_per_mtok and resolved.price_input_per_mtok > 0


def test_compute_cost_basic() -> None:
    pricing = ResolvedPricing("manual_override", 1.0, 2.0, None)
    cost = compute_cost(pricing, input_tokens=1_000_000, cached_input_tokens=0, output_tokens=500_000)
    assert cost == 1.0 + 1.0


def test_compute_cost_with_cached_tokens() -> None:
    pricing = ResolvedPricing("manual_override", 1.0, 2.0, 0.5)
    cost = compute_cost(pricing, input_tokens=1_000_000, cached_input_tokens=400_000, output_tokens=0)
    assert cost == 0.6 * 1.0 + 0.4 * 0.5


def test_compute_cost_unknown_returns_none() -> None:
    pricing = ResolvedPricing("unknown", None, None, None)
    assert compute_cost(pricing, input_tokens=1, cached_input_tokens=0, output_tokens=1) is None
