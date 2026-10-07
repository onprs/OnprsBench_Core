"""价格预览与缓存价格测试。

- /pricing/preview 返回匹配到的价格与模型能力（models.dev 目录）
- 未知模型返回 unknown 且能力为 None
- Deployment 的缓存读取/写入价格可创建、可更新、可回读
- compute_cost 对缓存读取与缓存写入分别计价
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services import pricing
from app.services.pricing import ResolvedPricing, compute_cost

_FAKE_MODELS_DEV = {
    "test-provider": {
        "models": {
            "test/model-x": {
                "cost": {"input": 1.0, "output": 2.0, "cache_read": 0.1, "cache_write": 1.25},
                "reasoning": True,
                "tool_call": True,
                "limit": {"context": 128000, "output": 16000},
            }
        }
    }
}


def test_pricing_preview_matches_models_dev(
    client: TestClient, mock_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pricing, "_fetch_models_dev", lambda: _FAKE_MODELS_DEV)
    resp = client.get(
        "/api/pricing/preview",
        params={"provider_id": mock_setup["provider"]["id"], "api_model_name": "test/model-x"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "models_dev"
    assert body["price_input_per_mtok"] == 1.0
    assert body["price_output_per_mtok"] == 2.0
    assert body["price_cached_input_per_mtok"] == 0.1
    assert body["price_cache_write_per_mtok"] == 1.25
    assert body["capabilities"]["reasoning"] is True
    assert body["capabilities"]["context_limit"] == 128000


def test_pricing_preview_unknown_model(
    client: TestClient, mock_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pricing, "_fetch_models_dev", lambda: _FAKE_MODELS_DEV)
    resp = client.get(
        "/api/pricing/preview",
        params={"provider_id": mock_setup["provider"]["id"], "api_model_name": "no/such-model"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "unknown"
    assert body["price_input_per_mtok"] is None
    assert body["capabilities"] is None


def test_pricing_preview_unknown_provider(client: TestClient) -> None:
    resp = client.get(
        "/api/pricing/preview",
        params={"provider_id": "missing-provider", "api_model_name": "test/model-x"},
    )
    assert resp.status_code == 404


def test_deployment_cache_prices_roundtrip(client: TestClient, mock_setup: dict) -> None:
    model = client.post(
        "/api/models", json={"canonical_id": "test/cache-model", "display_name": "Cache Model"}
    ).json()
    created = client.post(
        "/api/deployments",
        json={
            "name": "Cache Deployment",
            "model_id": model["id"],
            "provider_id": mock_setup["provider"]["id"],
            "api_model_name": "test/cache-model",
            "price_input_per_mtok": 1.0,
            "price_output_per_mtok": 2.0,
            "price_cached_input_per_mtok": 0.1,
            "price_cache_write_per_mtok": 1.25,
        },
    )
    assert created.status_code == 201, created.text
    dep = created.json()
    assert dep["price_cached_input_per_mtok"] == 0.1
    assert dep["price_cache_write_per_mtok"] == 1.25

    updated = client.patch(
        f"/api/deployments/{dep['id']}",
        json={"price_cached_input_per_mtok": 0.2},
    )
    assert updated.status_code == 200
    assert updated.json()["price_cached_input_per_mtok"] == 0.2
    assert updated.json()["price_cache_write_per_mtok"] == 1.25


def test_compute_cost_with_cache_read_and_write() -> None:
    resolved = ResolvedPricing(
        source="manual_override",
        price_input_per_mtok=1.0,
        price_output_per_mtok=2.0,
        price_cached_input_per_mtok=0.1,
        price_cache_write_per_mtok=1.25,
    )
    cost = compute_cost(
        resolved,
        input_tokens=1000,
        cached_input_tokens=400,
        cache_write_tokens=100,
        output_tokens=200,
    )
    # 600*1.0 + 400*0.1 + 100*1.25 + 200*2.0 = 1165（每百万 token 计价）
    assert cost == pytest.approx(1165 / 1e6)


def test_compute_cost_falls_back_to_input_price() -> None:
    resolved = ResolvedPricing(
        source="models_dev",
        price_input_per_mtok=1.0,
        price_output_per_mtok=2.0,
        price_cached_input_per_mtok=None,
        price_cache_write_per_mtok=None,
    )
    cost = compute_cost(
        resolved,
        input_tokens=1000,
        cached_input_tokens=400,
        cache_write_tokens=100,
        output_tokens=200,
    )
    # 无缓存单价时按输入价计价：600*1.0 + 400*1.0 + 100*1.0 + 200*2.0 = 1500
    assert cost == pytest.approx(1500 / 1e6)
