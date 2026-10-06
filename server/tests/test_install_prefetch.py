"""安装时预取仓库快照的测试。

不变量：
- 带 SWE 判定契约（repo_url + base_commit）的任务在安装时触发快照预取
- standard 形态预取失败即中止安装（不创建记录）并返回失败项与提示
- 无契约的数据集（mock 样例）安装时不发生任何预取
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.services import datasets as dataset_service
from app.toolchain import ToolchainUnavailable

from .conftest import MOCK_DATASET_DIR


def _add_verify_contract(ds: Path, commit: str = "a" * 40) -> Path:
    """给 mock 样例的 mock-sum-001 添加 SWE 判定契约，并重算 manifest 中的 hash。"""
    task_dir = ds / "tasks" / "mock-core" / "mock-sum-001"
    verify = {
        "repo": "octo/demo",
        "repo_url": "https://github.com/octo/demo",
        "base_commit": commit,
        "environment": {"python": "3.12", "setup": []},
        "evaluation": {
            "apply": [],
            "fail_to_pass": ["tests/test_x.py::test_y"],
            "pass_to_pass": [],
        },
    }
    (task_dir / "judge_assets").mkdir()
    verify_path = task_dir / "judge_assets" / "verify.yaml"
    verify_path.write_text(yaml.safe_dump(verify, allow_unicode=True), encoding="utf-8")

    manifest_path = ds / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    task = manifest["suites"][0]["tasks"][0]
    assert task["id"] == "mock-sum-001"
    digest = hashlib.sha256(verify_path.read_bytes()).hexdigest()
    task["hashes"]["files"]["judge_assets/verify.yaml"] = digest
    task["hashes"]["bundle_sha256"] = dataset_service.bundle_sha256(task["hashes"]["files"])
    task["judge_visible"].append("judge_assets/verify.yaml")
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return ds


def _copy_sample(tmp_path: Path) -> Path:
    dest = tmp_path / "ds"
    shutil.copytree(MOCK_DATASET_DIR, dest)
    return dest


def test_install_without_contract_prefetches_nothing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.toolchain.repos.fetch_repo_snapshot",
        lambda url, commit: calls.append((url, commit)),
    )
    resp = client.post("/api/datasets/installations", json={"path": str(MOCK_DATASET_DIR)})
    assert resp.status_code == 201, resp.text
    assert calls == []
    assert resp.json()["prefetch"] == []


def test_install_with_contract_prefetches_snapshot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ds = _add_verify_contract(_copy_sample(tmp_path))
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.toolchain.repos.fetch_repo_snapshot",
        lambda url, commit: calls.append((url, commit)),
    )
    resp = client.post("/api/datasets/installations", json={"path": str(ds)})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    assert calls == [("https://github.com/octo/demo", "a" * 40)]
    assert body["prefetch"] == [
        {
            "repo_url": "https://github.com/octo/demo",
            "base_commit": "a" * 40,
            "status": "ready",
            "error": None,
        }
    ]


def test_install_prefetch_failure_aborts_installation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """标准形态预取失败即中止安装：不创建记录、返回失败项与提示。"""
    ds = _add_verify_contract(_copy_sample(tmp_path), commit="b" * 40)

    def fail(url: str, commit: str) -> None:
        raise ToolchainUnavailable("离线环境，无法下载")

    monkeypatch.setattr("app.toolchain.repos.fetch_repo_snapshot", fail)
    resp = client.post("/api/datasets/installations", json={"path": str(ds)})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["message"] == "判定资源准备失败，数据集未安装"
    assert any("离线环境" in error for error in detail["errors"])
    assert "完整数据集" in detail["hint"]

    # 事务回滚：未创建安装记录；本次创建的托管副本被清理
    manifest_hash = hashlib.sha256((ds / "manifest.yaml").read_bytes()).hexdigest()
    installs = client.get("/api/datasets/installations").json()
    assert not any(item["manifest_hash"] == manifest_hash for item in installs)

    from app.config import settings

    assert not (settings.datasets_dir / manifest_hash[:16]).exists()
