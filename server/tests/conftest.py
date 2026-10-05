"""pytest 公共夹具：独立临时数据目录 + TestClient。

必须在导入 app 模块前设置 ONPRSBENCH_DATA_DIR（settings 在 import 时定型）。
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

_TMP_DATA = tempfile.mkdtemp(prefix="onprs-test-")
os.environ["ONPRSBENCH_DATA_DIR"] = _TMP_DATA

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from app.services import pricing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_DATASET_DIR = REPO_ROOT / "protocol" / "examples" / "mock-protocol-sample"

# 测试环境默认离线：models.dev 解析直接落空（litellm cost map 仍可本地命中）
pricing._fetch_models_dev = lambda: None  # noqa: SLF001


@pytest.fixture(scope="session")
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


def wait_run_finished(client: TestClient, run_id: str, timeout_s: float = 60.0) -> dict:
    """轮询直到 run 进入终态。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("completed", "failed"):
            return run
        time.sleep(0.1)
    raise TimeoutError(f"run {run_id} 未在 {timeout_s}s 内结束")


@pytest.fixture(scope="session")
def mock_setup(client: TestClient) -> dict:
    """建立 mock provider / model / 两个 deployment（强/弱）/ reasoning profile / 数据集安装。"""
    provider = client.post(
        "/api/providers", json={"name": "Mock Provider", "type": "mock"}
    ).json()

    model = client.post(
        "/api/models",
        json={"canonical_id": "mock.family/model", "display_name": "Mock Model"},
    ).json()

    dep_strong = client.post(
        "/api/deployments",
        json={
            "name": "Mock Strong",
            "model_id": model["id"],
            "provider_id": provider["id"],
            "api_model_name": "mock-strong",
            "price_input_per_mtok": 1.0,
            "price_output_per_mtok": 2.0,
        },
    ).json()
    dep_weak = client.post(
        "/api/deployments",
        json={
            "name": "Mock Weak",
            "model_id": model["id"],
            "provider_id": provider["id"],
            "api_model_name": "mock-weak",
            "price_input_per_mtok": 0.5,
            "price_output_per_mtok": 1.0,
        },
    ).json()

    profile = client.post(
        "/api/reasoning-profiles",
        json={"deployment_id": dep_strong["id"], "name": "low", "reasoning_effort": "low"},
    ).json()

    install = client.post("/api/datasets/installations", json={"path": str(MOCK_DATASET_DIR)}).json()

    return {
        "provider": provider,
        "model": model,
        "dep_strong": dep_strong,
        "dep_weak": dep_weak,
        "profile": profile,
        "installation": install["installation"],
    }


@pytest.fixture(scope="session")
def completed_run(client: TestClient, mock_setup: dict) -> dict:
    """端到端完成一次 Run：2 Solver × 2 Judge × 2 Task。"""
    body = {
        "name": "e2e mock run",
        "installation_id": mock_setup["installation"]["id"],
        "suite_id": "mock-core",
        "solvers": [
            {"deployment_id": mock_setup["dep_strong"]["id"], "reasoning_profile_id": mock_setup["profile"]["id"]},
            {"deployment_id": mock_setup["dep_weak"]["id"]},
        ],
        "judges": [
            {"deployment_id": mock_setup["dep_strong"]["id"]},
            {"deployment_id": mock_setup["dep_weak"]["id"]},
        ],
    }
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 201, resp.text
    run = resp.json()
    finished = wait_run_finished(client, run["id"])
    assert finished["status"] == "completed", finished.get("error")
    return finished
