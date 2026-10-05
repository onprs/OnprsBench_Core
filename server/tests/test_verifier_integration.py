"""verifier 编排集成测试：判定事实落库、注入 judge prompt、rejudge 复用、降级路径。

verifier 本身由 test_verifier.py 真实执行覆盖；本文件用假 outcome 隔离编排逻辑，
守护 runner 层不变量：
- 有 verify 契约的任务在 solver 完成后自动判定，facts 作为 immutable raw facts 落库
- judge prompt 包含判定事实段落，且不泄露 solver 身份
- rejudge 复用历史判定事实，不重新执行 verifier
- 工具链不可用时判定为 unavailable，judge 照常完成（优雅降级）
"""

from __future__ import annotations

import time

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import JudgeExecution, VerifierExecution
from app.services.runner import run_manager
from app.toolchain import ToolchainUnavailable
from app.verifier.base import VerifyOutcome

from .conftest import wait_run_finished

FAKE_FACTS = {
    "mode": "code_generation",
    "language": "cpp",
    "compiled": True,
    "samples": {"total": 1, "passed": 1},
    "verdict": {"samples_ok": True, "stress_ok": True},
}


def _start_run(client: TestClient, mock_setup: dict, name: str) -> dict:
    body = {
        "name": name,
        "installation_id": mock_setup["installation"]["id"],
        "suite_id": "mock-core",
        "solvers": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
        "judges": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
    }
    resp = client.post("/api/runs", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _wait_idle(run_id: str, timeout_s: float = 30.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline and run_manager.is_active(run_id):
        time.sleep(0.05)


def test_verifier_facts_stored_and_injected(
    client: TestClient, mock_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import runner as runner_mod

    monkeypatch.setattr(runner_mod.verifier_service, "has_verify_contract", lambda payload: True)

    def fake_verify(**kwargs) -> VerifyOutcome:
        return VerifyOutcome(
            status="completed",
            verifier_kind="algorithm",
            facts=dict(FAKE_FACTS),
            environment={"cpp_compiler": "g++ 14.2.0"},
            log_tail="ok",
        )

    monkeypatch.setattr(runner_mod.verifier_service, "verify_task", fake_verify)

    run = _start_run(client, mock_setup, "verifier integration")
    finished = wait_run_finished(client, run["id"])
    assert finished["status"] == "completed"
    assert finished["verifier_wall_time_s"] is not None

    with SessionLocal() as session:
        verifiers = session.scalars(
            sa.select(VerifierExecution).where(VerifierExecution.run_id == run["id"])
        ).all()
        # 1 solver × 2 task = 2 次判定
        assert len(verifiers) == 2
        for ve in verifiers:
            assert ve.status == "completed"
            assert ve.facts_json["verdict"]["samples_ok"] is True
            assert ve.environment_json["cpp_compiler"] == "g++ 14.2.0"

        judges = session.scalars(
            sa.select(JudgeExecution).where(JudgeExecution.run_id == run["id"])
        ).all()
        assert len(judges) == 2
        for judge in judges:
            prompt_text = "\n".join(m["content"] for m in judge.prompt_json)
            assert "【程序判定事实】" in prompt_text
            assert '"samples_ok": true' in prompt_text
            # 匿名化不变量保持
            assert "Mock Strong" not in prompt_text

    # rejudge 复用判定事实，不新增 VerifierExecution
    resp = client.post(f"/api/runs/{run['id']}/rejudge", json={})
    assert resp.status_code == 202
    _wait_idle(run["id"])
    with SessionLocal() as session:
        count_after = session.scalar(
            sa.select(sa.func.count()).select_from(VerifierExecution).where(
                VerifierExecution.run_id == run["id"]
            )
        )
        assert count_after == 2
        judges = session.scalars(
            sa.select(JudgeExecution).where(JudgeExecution.run_id == run["id"])
        ).all()
        assert len(judges) == 4  # 2 原始 + 2 重评
        for judge in judges:
            prompt_text = "\n".join(m["content"] for m in judge.prompt_json)
            assert "【程序判定事实】" in prompt_text


def test_verifier_unavailable_degrades_gracefully(
    client: TestClient, mock_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import runner as runner_mod

    monkeypatch.setattr(runner_mod.verifier_service, "has_verify_contract", lambda payload: True)

    def raise_unavailable(**kwargs):
        raise ToolchainUnavailable("无法供给 Python 3.14（离线环境）")

    monkeypatch.setattr(runner_mod.verifier_service, "verify_task", raise_unavailable)

    run = _start_run(client, mock_setup, "verifier unavailable")
    finished = wait_run_finished(client, run["id"])
    # 判定降级不阻塞 Run
    assert finished["status"] == "completed"
    assert finished["judge_execution_count"] == 2

    with SessionLocal() as session:
        verifiers = session.scalars(
            sa.select(VerifierExecution).where(VerifierExecution.run_id == run["id"])
        ).all()
        assert len(verifiers) == 2
        for ve in verifiers:
            assert ve.status == "unavailable"
            assert "无法供给" in ve.error

        judges = session.scalars(
            sa.select(JudgeExecution).where(JudgeExecution.run_id == run["id"])
        ).all()
        for judge in judges:
            prompt_text = "\n".join(m["content"] for m in judge.prompt_json)
            # 无判定事实时 prompt 不含该段落，judge 纯文本评审
            assert "【程序判定事实】" not in prompt_text
