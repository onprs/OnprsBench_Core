"""端到端测试：mock Provider 全流程（建 Run → Solver → 多 Judge → 结果 → 重评）。

守护的不变量：
- Solver / Judge 原始记录完整保存（prompt、response、raw output、usage、pricing snapshot）。
- Judge prompt 不泄露 Solver 身份（匿名化）。
- 重新 Judge 只新增 JudgeExecution，不改写 Solver 原始事实。
- 价格快照随 Run 冻结，成本区分 solver_cost / judge_cost。
"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from .conftest import wait_run_finished


def test_end_to_end_run(client: TestClient, completed_run: dict, mock_setup: dict) -> None:
    run = completed_run

    # 2 solver × 2 task = 4 solver executions；4 × 2 judge = 8 judge executions
    assert run["solver_execution_count"] == 4
    assert run["judge_execution_count"] == 8
    assert run["solver_wall_time_s"] > 0
    assert run["judge_wall_time_s"] > 0
    assert run["total_wall_time_s"] >= run["solver_wall_time_s"]
    assert run["solver_cost"] > 0
    assert run["judge_cost"] > 0
    assert run["total_cost"] == run["solver_cost"] + run["judge_cost"]

    # 追溯字段完整冻结
    assert run["framework_version"]
    assert run["dataset_id"] == "onprs-mock-protocol-sample"
    assert run["dataset_revision"] == "mock-rev-1"
    assert len(run["manifest_hash"]) == 64

    results = client.get(f"/api/runs/{run['id']}/results").json()
    assert len(results["targets"]) == 2

    by_label = {t["label"]: t for t in results["targets"]}
    strong = next(v for k, v in by_label.items() if "Mock Strong" in k)
    weak = next(v for k, v in by_label.items() if "Mock Weak" in k)

    # mock-weak 故意答错 → fatal_error → 0 分；mock-strong 得分显著更高
    assert weak["score_mean"] == 0.0
    assert strong["score_mean"] > 50.0
    assert strong["total_cost"] > 0
    assert strong["latency_mean_s"] > 0

    # 每个 task 都有 judge 聚合统计（mean/median/stddev/min/max）
    for target in results["targets"]:
        for entry in target_entries(results, target):
            summary = entry["judge_score_summary"]
            assert summary["count"] == 2
            assert summary["mean"] is not None
            assert summary["stddev"] is not None


def target_entries(results: dict, target: dict) -> list[dict]:
    key = f"{target['deployment_id']}:{target['reasoning_profile_id'] or ''}"
    return results["entries_by_target"][key]


def test_judge_anonymization(client: TestClient, completed_run: dict) -> None:
    """Judge prompt 不得包含 solver deployment/provider 名称，候选回答匿名引用。"""
    from app.db import SessionLocal
    from app.models import JudgeExecution
    import sqlalchemy as sa

    with SessionLocal() as session:
        judges = session.scalars(
            sa.select(JudgeExecution).where(JudgeExecution.run_id == completed_run["id"])
        ).all()
        assert judges
        for judge in judges:
            prompt_text = "\n".join(m["content"] for m in judge.prompt_json)
            assert "Candidate Response #A" in prompt_text
            assert "Mock Strong" not in prompt_text
            assert "Mock Weak" not in prompt_text
            assert "Mock Provider" not in prompt_text
            # 原始输出必须保存
            assert judge.raw_output_text
            assert judge.rubric_version == "1"


def test_usage_and_pricing_snapshot_frozen(client: TestClient, completed_run: dict) -> None:
    usage = client.get(f"/api/runs/{completed_run['id']}/usage").json()
    assert len(usage) == 12  # 4 solver + 8 judge
    for record in usage:
        assert record["input_tokens"] > 0
        assert record["output_tokens"] > 0
        assert record["pricing"] is not None
        assert record["pricing"]["source"] == "manual_override"
        assert record["cost"] is not None and record["cost"] > 0


def test_rejudge_preserves_raw_facts(client: TestClient, completed_run: dict) -> None:
    run_id = completed_run["id"]
    before = client.get(f"/api/runs/{run_id}/results").json()

    before_responses = {}
    for key, entries in before["entries_by_target"].items():
        for e in entries:
            before_responses[e["solver_execution_id"]] = e["response_text"]

    resp = client.post(f"/api/runs/{run_id}/rejudge", json={})
    assert resp.status_code == 202, resp.text

    # 等待 rejudge 后台任务真正完成（而非仅行已创建）
    from app.services.runner import run_manager

    deadline = time.time() + 30
    while time.time() < deadline and run_manager.is_active(run_id):
        time.sleep(0.05)
    assert not run_manager.is_active(run_id), "rejudge 超时"
    assert client.get(f"/api/runs/{run_id}").json()["judge_execution_count"] == 16

    after = client.get(f"/api/runs/{run_id}/results").json()
    after_responses = {}
    for key, entries in after["entries_by_target"].items():
        for e in entries:
            after_responses[e["solver_execution_id"]] = e["response_text"]

    # Solver 原始回答不变（immutable raw facts）
    assert before_responses == after_responses

    # mock judge 是确定性的：同一回答重评分数一致，且每个 task 现在有 4 个 judge 结果
    for target in after["targets"]:
        for entry in target_entries(after, target):
            assert entry["judge_score_summary"]["count"] == 4


def test_compare_runs(client: TestClient, completed_run: dict, mock_setup: dict) -> None:
    # 同数据集同 suite 的第二次 run → Comparable
    body = {
        "name": "second run",
        "installation_id": mock_setup["installation"]["id"],
        "suite_id": "mock-core",
        "solvers": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
        "judges": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
    }
    run2 = client.post("/api/runs", json=body).json()
    wait_run_finished(client, run2["id"])

    verdict = client.get(f"/api/runs/compare?ids={completed_run['id']},{run2['id']}").json()
    assert verdict["comparable"], verdict

    # 不存在的 run → 不可比
    verdict = client.get(f"/api/runs/compare?ids={completed_run['id']},{'0' * 32}").json()
    assert not verdict["comparable"]


def test_timeseries(client: TestClient, completed_run: dict) -> None:
    points = client.get("/api/analytics/timeseries").json()
    assert points
    for p in points:
        assert p["score_mean"] is not None
        assert p["run_at"]
        assert p["label"]
