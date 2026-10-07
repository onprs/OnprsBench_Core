"""Provider 模型列表缓存测试。

不变量：
- 首次拉取写入缓存，之后默认返回缓存（from_cache=true），refresh=true 强制重拉
- Provider 列表返回已拉取摘要（数量与时间），供界面展示“再次拉取”
- 删除 Provider 时缓存记录一并移除
"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi.testclient import TestClient


def test_model_catalog_cached_and_refreshable(client: TestClient, mock_setup: dict) -> None:
    provider_id = mock_setup["provider"]["id"]

    first = client.get(f"/api/providers/{provider_id}/models").json()
    assert first["from_cache"] is False
    assert first["models"] == ["mock-strong", "mock-weak"]
    assert first["fetched_at"]

    cached = client.get(f"/api/providers/{provider_id}/models").json()
    assert cached["from_cache"] is True
    assert cached["models"] == first["models"]

    refreshed = client.get(
        f"/api/providers/{provider_id}/models", params={"refresh": True}
    ).json()
    assert refreshed["from_cache"] is False
    assert refreshed["models"] == first["models"]

    row = next(p for p in client.get("/api/providers").json() if p["id"] == provider_id)
    assert row["model_catalog_count"] == 2
    assert row["model_catalog_fetched_at"]


def test_model_selection_toggle(client: TestClient, mock_setup: dict) -> None:
    """渠道页勾选/取消模型：持久化并在列表接口返回。"""
    from app.api import providers as providers_api

    provider_id = mock_setup["provider"]["id"]
    first = client.get(f"/api/providers/{provider_id}/models").json()
    assert first["selected_models"] == []

    toggled = client.post(
        f"/api/providers/{provider_id}/models/toggle", json={"model": "mock-strong"}
    )
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["selected_models"] == ["mock-strong"]

    again = client.get(f"/api/providers/{provider_id}/models").json()
    assert again["selected_models"] == ["mock-strong"]

    untoggled = client.post(
        f"/api/providers/{provider_id}/models/toggle", json={"model": "mock-strong"}
    )
    assert untoggled.json()["selected_models"] == []

    # 不在已拉取列表中的模型不能选择
    invalid = client.post(
        f"/api/providers/{provider_id}/models/toggle", json={"model": "no/such-model"}
    )
    assert invalid.status_code == 400

    # 重新拉取后，已下架的模型从已选列表移除
    client.post(f"/api/providers/{provider_id}/models/toggle", json={"model": "mock-weak"})
    monkeypatch_target = providers_api._fetch_provider_models
    providers_api._fetch_provider_models = lambda provider: ["mock-strong"]
    try:
        refreshed = client.get(
            f"/api/providers/{provider_id}/models", params={"refresh": True}
        ).json()
    finally:
        providers_api._fetch_provider_models = monkeypatch_target
    assert refreshed["models"] == ["mock-strong"]
    assert refreshed["selected_models"] == []


def test_model_catalog_removed_with_provider(client: TestClient) -> None:
    from app.db import SessionLocal
    from app.models import ProviderModelCatalog

    provider = client.post("/api/providers", json={"name": "Catalog Temp", "type": "mock"}).json()
    client.get(f"/api/providers/{provider['id']}/models")

    with SessionLocal() as session:
        assert session.scalar(
            sa.select(sa.func.count())
            .select_from(ProviderModelCatalog)
            .where(ProviderModelCatalog.provider_id == provider["id"])
        ) == 1

    assert client.delete(f"/api/providers/{provider['id']}").status_code == 204

    with SessionLocal() as session:
        assert session.scalar(
            sa.select(sa.func.count())
            .select_from(ProviderModelCatalog)
            .where(ProviderModelCatalog.provider_id == provider["id"])
        ) == 0

    # 新建 Provider 不继承旧缓存
    provider2 = client.post("/api/providers", json={"name": "Catalog Temp 2", "type": "mock"}).json()
    row = next(p for p in client.get("/api/providers").json() if p["id"] == provider2["id"])
    assert row["model_catalog_count"] == 0
    assert row["model_catalog_fetched_at"] is None
