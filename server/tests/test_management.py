"""管理操作测试：编辑/删除/引用保护/Run 取消。

守护的不变量：
- 被历史 Run 引用的 Provider/Deployment/ReasoningProfile/数据集安装不可删除（可追溯性）。
- 未被引用的记录可正常删除。
- Run 可取消，进行中的执行记录置为 cancelled，已完成的原始事实保留。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from .conftest import MOCK_DATASET_DIR, wait_run_finished


@pytest.fixture(scope="session")
def scratch(client: TestClient) -> dict:
    """一套未参与任何 Run 的记录 + 一套被 Run 引用的记录。"""
    provider = client.post("/api/providers", json={"name": "管理测试", "type": "mock"}).json()
    model = client.post(
        "/api/models", json={"canonical_id": "mgmt/model", "display_name": "Mgmt Model"}
    ).json()

    def make_dep(name: str, latency: float | None = None) -> dict:
        body: dict = {
            "name": name,
            "model_id": model["id"],
            "provider_id": provider["id"],
            "api_model_name": "mock-strong",
        }
        if latency is not None:
            body["custom_options"] = {"mock_latency_s": latency}
        return client.post("/api/deployments", json=body).json()

    dep_free = make_dep("空闲 Deployment")
    dep_used = make_dep("被引用 Deployment", latency=0.0)
    profile_free = client.post(
        "/api/reasoning-profiles", json={"deployment_id": dep_free["id"], "name": "free"}
    ).json()
    profile_used = client.post(
        "/api/reasoning-profiles", json={"deployment_id": dep_used["id"], "name": "used"}
    ).json()

    install = client.post("/api/datasets/installations", json={"path": str(MOCK_DATASET_DIR)}).json()

    run = client.post(
        "/api/runs",
        json={
            "name": "mgmt run",
            "installation_id": install["installation"]["id"],
            "suite_id": "mock-core",
            "solvers": [{"deployment_id": dep_used["id"], "reasoning_profile_id": profile_used["id"]}],
            "judges": [{"deployment_id": dep_used["id"]}],
        },
    ).json()
    wait_run_finished(client, run["id"])

    return {
        "provider": provider,
        "dep_free": dep_free,
        "dep_used": dep_used,
        "profile_free": profile_free,
        "profile_used": profile_used,
        "installation": install["installation"],
        "run": run,
    }


def test_provider_edit_and_key_rotation(client: TestClient, scratch: dict) -> None:
    pid = scratch["provider"]["id"]
    updated = client.patch(f"/api/providers/{pid}", json={"name": "管理测试-改名"}).json()
    assert updated["name"] == "管理测试-改名"
    # mock provider 也允许补 key（编辑接口通用性）
    updated = client.patch(f"/api/providers/{pid}", json={"api_key": "sk-test-123"}).json()
    assert updated["has_credential"] is True


def test_provider_delete_blocked_by_deployment(client: TestClient, scratch: dict) -> None:
    resp = client.delete(f"/api/providers/{scratch['provider']['id']}")
    assert resp.status_code == 409


def test_deployment_edit(client: TestClient, scratch: dict) -> None:
    did = scratch["dep_free"]["id"]
    updated = client.patch(
        f"/api/deployments/{did}",
        json={"name": "改名后", "price_input_per_mtok": 2.0, "price_output_per_mtok": 4.0},
    ).json()
    assert updated["name"] == "改名后"
    assert updated["price_input_per_mtok"] == 2.0


def test_delete_used_deployment_blocked(client: TestClient, scratch: dict) -> None:
    assert client.delete(f"/api/deployments/{scratch['dep_used']['id']}").status_code == 409
    assert client.delete(f"/api/reasoning-profiles/{scratch['profile_used']['id']}").status_code == 409


def test_delete_free_records(client: TestClient, scratch: dict) -> None:
    assert client.delete(f"/api/reasoning-profiles/{scratch['profile_free']['id']}").status_code == 204
    # 删除 deployment 时级联清掉其未被引用的 profiles
    assert client.delete(f"/api/deployments/{scratch['dep_free']['id']}").status_code == 204


def test_installation_delete_blocked_by_run(client: TestClient, scratch: dict) -> None:
    assert client.delete(f"/api/datasets/installations/{scratch['installation']['id']}").status_code == 409


def test_cancel_completed_run_rejected(client: TestClient, scratch: dict) -> None:
    assert client.post(f"/api/runs/{scratch['run']['id']}/cancel").status_code == 409


def test_cancel_running_run(client: TestClient, mock_setup: dict) -> None:
    # 用高延迟 mock deployment 让 run 保持运行中，便于取消
    provider = client.post("/api/providers", json={"name": "慢速 Mock", "type": "mock"}).json()
    model = client.post(
        "/api/models", json={"canonical_id": "slow/model", "display_name": "Slow Model"}
    ).json()
    dep = client.post(
        "/api/deployments",
        json={
            "name": "Slow Mock",
            "model_id": model["id"],
            "provider_id": provider["id"],
            "api_model_name": "mock-strong",
            "custom_options": {"mock_latency_s": 5.0},
        },
    ).json()

    run = client.post(
        "/api/runs",
        json={
            "name": "cancel me",
            "installation_id": mock_setup["installation"]["id"],
            "suite_id": "mock-core",
            "solvers": [{"deployment_id": dep["id"]}],
            "judges": [{"deployment_id": dep["id"]}],
        },
    ).json()
    assert client.post(f"/api/runs/{run['id']}/cancel").status_code == 202

    deadline = time.time() + 15
    while time.time() < deadline:
        state = client.get(f"/api/runs/{run['id']}").json()
        if state["status"] == "cancelled":
            break
        time.sleep(0.1)
    else:
        raise TimeoutError("取消未生效")
    assert state["status"] == "cancelled"

    # 进行中的执行记录应为 cancelled
    results = client.get(f"/api/runs/{run['id']}/results").json()
    for entries in results["entries_by_target"].values():
        for entry in entries:
            assert entry["status"] in ("completed", "cancelled")
