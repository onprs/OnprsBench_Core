"""Agent 统一形态、轮次预算、截断处理与新派生字段的测试。

覆盖：
- Reasoning Profile 参数（reasoning_effort / reasoning_budget / max_output_tokens /
  temperature / provider_params / 墙钟上限）在 agent 每轮调用中生效；
- 轮次预算：正整数上限、0/None 不限制、收尾提示后强制结束；
- 输出预算耗尽（finish_reason=length）的续行提示与截断事实；
- 竞赛代码任务的自由工作区执行与解答收集；
- 花费轮次（turns）与截断标记落库、结果 API 返回、逐轮 usage 记录；
- 启动时恢复中断的 Run；
- 可比性判定纳入 judge prompt / 聚合算法版本；
- metadata-only（空）suite 拒绝创建 Run。
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import yaml
from fastapi.testclient import TestClient

from app.agents.loop import run_agent_loop
from app.runtime.base import ModelRequest, ToolCall, ToolStep, UsageInfo
from app.services.runner import collect_workspace_solution
from app.verifier import algorithm as algo_mod

from .conftest import wait_run_finished

# ---------------------------------------------------------------------------
# 测试客户端与工具
# ---------------------------------------------------------------------------


class RecordingClient:
    """记录每次请求并返回预设步骤的测试客户端。"""

    def __init__(self, steps: list[ToolStep]) -> None:
        self.steps = list(steps)
        self.requests: list[ModelRequest] = []
        self._cursor = 0

    async def complete_with_tools(self, request: ModelRequest, tools: list[dict[str, Any]]) -> ToolStep:
        self.requests.append(request)
        step = self.steps[min(self._cursor, len(self.steps) - 1)]
        self._cursor += 1
        return step


def _step(
    content: str = "",
    *,
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
) -> ToolStep:
    now = datetime.now(timezone.utc)
    return ToolStep(
        content=content,
        tool_calls=tool_calls or [],
        assistant_message={"role": "assistant", "content": content},
        usage=UsageInfo(input_tokens=10, output_tokens=5, cached_input_tokens=1, reasoning_tokens=2),
        finish_reason=finish_reason,
        started_at=now,
        finished_at=now,
        total_latency_s=0.01,
        ttft_s=0.005,
    )


@pytest.fixture(autouse=True)
def _pin_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """算法判定的解释器固定为当前 Python，避免测试触发下载。"""
    monkeypatch.setattr(algo_mod, "ensure_python", lambda spec: Path(sys.executable))


# ---------------------------------------------------------------------------
# Agent 循环：参数透传、轮次预算、截断
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_passes_profile_request(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client = RecordingClient([_step("完成")])
    template = ModelRequest(
        messages=[],
        reasoning_effort="high",
        reasoning_budget=8192,
        max_output_tokens=4096,
        temperature=0.2,
        top_p=0.9,
        seed=7,
        provider_params={"extra": True},
        timeout_s=100.0,
        call_timeout_s=200.0,
    )
    result = await run_agent_loop(
        client, problem="题目", workspace=workspace, request_template=template, max_turns=5
    )

    assert result.turns == 1 and result.stop_reason == "completed"
    request = client.requests[0]
    assert request.reasoning_effort == "high"
    assert request.reasoning_budget == 8192
    assert request.max_output_tokens == 4096
    assert request.temperature == 0.2
    assert request.top_p == 0.9
    assert request.seed == 7
    assert request.provider_params == {"extra": True}
    assert request.call_timeout_s == 200.0
    assert request.messages[0]["role"] == "system"
    assert request.messages[1]["content"] == "题目"


@pytest.mark.asyncio
async def test_agent_loop_recovers_from_truncation(tmp_path: Path) -> None:
    """一轮被截断后给出续行提示，下一轮得到最终回答。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client = RecordingClient([_step("思考中…", finish_reason="length"), _step("最终回答：42")])
    result = await run_agent_loop(client, problem="题目", workspace=workspace, max_turns=5)

    assert result.stop_reason == "completed"
    assert result.turns == 2
    assert result.truncated_turns == 1
    assert result.finish_reason == "stop"
    assert result.final_text == "最终回答：42"
    # 第二轮请求中包含截断续行提示
    assert "被截断" in client.requests[1].messages[-1]["content"]
    assert result.turn_records[0]["truncated"] is True


@pytest.mark.asyncio
async def test_agent_loop_truncation_hint_limit(tmp_path: Path) -> None:
    """连续截断达到提示上限后接受可见内容，不无限循环。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    client = RecordingClient([_step("残片内容", finish_reason="length")])
    result = await run_agent_loop(client, problem="题目", workspace=workspace, max_turns=None)

    assert result.stop_reason == "truncated"
    assert result.turns == 3  # 首次 + 两轮续行提示
    assert result.truncated_turns == 3
    assert result.final_text == "残片内容"


@pytest.mark.asyncio
async def test_agent_loop_unlimited_turns(tmp_path: Path) -> None:
    """max_turns=None 表示不限制：跑完脚本给出的全部动作。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool_calls = [ToolCall(id=f"c{i}", name="list_files", arguments={}) for i in range(5)]
    client = RecordingClient([_step(tool_calls=[call], finish_reason="tool_calls") for call in tool_calls] + [_step("完成")])
    result = await run_agent_loop(client, problem="题目", workspace=workspace, max_turns=None)

    assert result.turns == 6
    assert result.stop_reason == "completed"


@pytest.mark.asyncio
async def test_agent_loop_closing_hint_ends_loop(tmp_path: Path) -> None:
    """收尾提示发出后模型仍调用工具时直接结束，不再空转。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    steps = [_step(tool_calls=[ToolCall(id=f"c{i}", name="list_files", arguments={})], finish_reason="tool_calls") for i in range(10)]
    client = RecordingClient(steps)
    result = await run_agent_loop(client, problem="题目", workspace=workspace, max_turns=6)

    # 第 3 轮后（remaining == 3）发出收尾提示，第 4 轮模型仍调工具 → 立即结束
    assert result.stop_reason == "max_turns"
    assert result.turns == 4


# ---------------------------------------------------------------------------
# 工作区解答收集
# ---------------------------------------------------------------------------


def test_collect_workspace_solution_prefers_named_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "assets").mkdir(parents=True)
    (workspace / "assets" / "helper.py").write_text("print('asset')\n", encoding="utf-8")
    (workspace / "scratch.cpp").write_text("int main() {}\n", encoding="utf-8")
    (workspace / "solution.py").write_text("print('solution')\n", encoding="utf-8")

    found = collect_workspace_solution(workspace)
    assert found is not None
    assert found["path"] == "solution.py"
    assert found["language"] == "python"


def test_collect_workspace_solution_falls_back_to_code_file(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "problem.md").write_text("题面", encoding="utf-8")
    (workspace / "answer.py").write_text("print('ok')\n", encoding="utf-8")

    found = collect_workspace_solution(workspace)
    assert found is not None and found["path"] == "answer.py"


def test_collect_workspace_solution_ignores_assets_only(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "assets").mkdir(parents=True)
    (workspace / "assets" / "helper.py").write_text("print('asset')\n", encoding="utf-8")
    assert collect_workspace_solution(workspace) is None


# ---------------------------------------------------------------------------
# 竞赛代码任务：统一 agent 形态端到端
# ---------------------------------------------------------------------------


_REFERENCE_PY = """\
import sys
data = sys.stdin.read().split()
t = int(data[0])
out = []
i = 1
for _ in range(t):
    out.append(str(int(data[i]) + int(data[i + 1])))
    i += 2
print("\\n".join(out))
"""

_GENERATOR_PY = """\
import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
cases = int(sys.argv[2]) if len(sys.argv) > 2 else 10
rng = random.Random(seed)
print(cases)
for _ in range(cases):
    print(rng.randint(1, 100), rng.randint(1, 100))
"""


def _build_algorithm_dataset(tmp_path: Path, *, write_reference: bool = True) -> Path:
    """构造含一个 code_generation 契约任务的最小数据集。

    write_reference=False 时参考解文件不存在（模拟判定资产缺失导致的判定失败）。
    """
    from app.services.datasets import bundle_sha256

    root = tmp_path / "algo-ds"
    task_dir = root / "tasks" / "algo-suite" / "algo-demo-sum"
    (task_dir / "judge_assets").mkdir(parents=True)
    (task_dir / "problem.md").write_text(
        "# 求和\n\n读入 t 与 t 组 a b，输出每组 a+b。", encoding="utf-8"
    )
    (task_dir / "rubric.yaml").write_text(
        yaml.safe_dump(
            {
                "rubric_version": 1,
                "dimensions": [
                    {
                        "id": "correctness",
                        "weight": 1.0,
                        "description": "程序 verifier 判定：官方样例与应力测试通过。",
                        "anchors": [
                            {"score": 0.0, "description": "未通过"},
                            {"score": 0.5, "description": "部分通过"},
                            {"score": 1.0, "description": "全部通过"},
                        ],
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "meta.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "algo-demo-sum",
                "revision": 1,
                "title": "演示求和任务",
                "suite": "algo-suite",
                "type": "code_generation",
                "tags": ["demo"],
                "lifecycle": {"status": "active", "stage": "fresh"},
                "freshness": {"class": "F0", "created_at": "2026-10-07"},
                "difficulty": {"author": "easy"},
                "source": {"kind": "synthetic", "license": "MIT"},
                "contamination": {"risk": "none"},
                "license": "MIT",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "judge_assets" / "verify.yaml").write_text(
        yaml.safe_dump(
            {
                "source": {"limits": {"time": "2s"}},
                "evaluation": {
                    "mode": "程序判题",
                    "reference_solution": "judge_assets/reference_solution.py",
                    "harness": {"generator": "judge_assets/generator.py"},
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "judge_assets" / "reference_solution.py").write_text(_REFERENCE_PY, encoding="utf-8")
    (task_dir / "judge_assets" / "generator.py").write_text(_GENERATOR_PY, encoding="utf-8")
    (task_dir / "judge_assets" / "samples.json").write_text(
        json.dumps([{"input": "2\n1 2\n3 4", "output": "3\n7"}]), encoding="utf-8"
    )

    judge_visible = [
        "judge_assets/verify.yaml",
        "judge_assets/generator.py",
        "judge_assets/samples.json",
        "rubric.yaml",
    ]
    if write_reference:
        judge_visible.insert(1, "judge_assets/reference_solution.py")
    else:
        (task_dir / "judge_assets" / "reference_solution.py").unlink(missing_ok=True)

    files = {
        p.relative_to(task_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(task_dir.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "protocol_version": "1",
        "dataset": {
            "id": "mock-algo-ds",
            "name": "Mock Algorithm Dataset",
            "version": "0.1.0",
            "release_date": "2026-10-07",
            "revision": "mock-algo-rev-1",
            "description": "算法 agent 集成测试样例",
            "license": "MIT",
        },
        "suites": [
            {
                "id": "algo-suite",
                "name": "Algorithm Demo Suite",
                "description": "算法 agent 链路测试",
                "layer": "derived",
                "adapter": None,
                "tasks": [
                    {
                        "id": "algo-demo-sum",
                        "revision": 1,
                        "title": "演示求和任务",
                        "type": "code_generation",
                        "tags": ["demo"],
                        "status": "active",
                        "path": "tasks/algo-suite/algo-demo-sum",
                        "solver_visible": ["problem.md"],
                        "judge_visible": judge_visible,
                        "metadata": {
                            "difficulty": "easy",
                            "freshness": "F0",
                            "contamination": "none",
                            "flagship": False,
                        },
                        "hashes": {"bundle_sha256": bundle_sha256(files), "files": files},
                    }
                ],
            }
        ],
    }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return root


def _build_empty_suite_dataset(tmp_path: Path) -> Path:
    """构造只含 metadata-only（空）suite 的最小数据集。"""
    root = tmp_path / "empty-ds"
    root.mkdir(parents=True)
    manifest = {
        "protocol_version": "1",
        "dataset": {
            "id": "mock-empty-ds",
            "name": "Mock Empty Dataset",
            "version": "0.1.0",
            "release_date": "2026-10-07",
            "revision": "mock-empty-rev-1",
            "description": "空 suite 测试样例",
            "license": "MIT",
        },
        "suites": [
            {
                "id": "empty-suite",
                "name": "Empty Suite",
                "description": "metadata-only 坐标系",
                "layer": "external",
                "adapter": "demo",
                "tasks": [],
            }
        ],
    }
    (root / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return root


def test_algorithm_task_runs_as_agent(
    client: TestClient, mock_setup: dict, tmp_path: Path
) -> None:
    from app.db import SessionLocal
    from app.models import SolverExecution, UsageRecord

    install = client.post(
        "/api/datasets/installations", json={"path": str(_build_algorithm_dataset(tmp_path))}
    )
    assert install.status_code == 201, install.text
    installation = install.json()["installation"]

    solution_code = _REFERENCE_PY
    script = [
        {"tool": "list_files", "arguments": {}},
        {"tool": "write_file", "arguments": {"path": "solution.py", "content": solution_code}},
        {"tool": "run_command", "arguments": {"command": f"{sys.executable} -m py_compile solution.py"}},
        {"text": "已给出 O(t) 的求和实现"},
    ]
    dep = client.post(
        "/api/deployments",
        json={
            "name": "Mock Algo Agent",
            "model_id": mock_setup["model"]["id"],
            "provider_id": mock_setup["provider"]["id"],
            "api_model_name": "mock-agent",
            "custom_options": {"agent_script": script},
        },
    ).json()

    run = client.post(
        "/api/runs",
        json={
            "name": "algo agent e2e",
            "installation_id": installation["id"],
            "suite_id": "algo-suite",
            "solvers": [{"deployment_id": dep["id"]}],
            "judges": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
        },
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    finished = wait_run_finished(client, run_id)
    assert finished["status"] == "completed", finished.get("error")

    with SessionLocal() as session:
        se = session.scalars(
            sa.select(SolverExecution).where(SolverExecution.run_id == run_id)
        ).one()
        assert se.raw_response_json["solver_mode"] == "agent"
        assert se.raw_response_json["solution_file"] == "solution.py"
        assert se.turns == 4
        assert se.finish_reason == "stop"
        assert se.truncated is False
        assert "```python" in se.response_text
        # 逐轮 usage：agent 每一轮调用一条记录
        usage_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(UsageRecord)
            .where(UsageRecord.owner_id == se.id)
        )
        assert usage_count == 4

    results = client.get(f"/api/runs/{run_id}/results").json()
    entry = next(iter(results["entries_by_target"].values()))[0]
    assert entry["turns"] == 4
    assert entry["solver_mode"] == "agent"
    assert entry["truncated"] is False
    assert entry["verifier"]["facts"]["verdict"]["samples_ok"] is True
    assert entry["verifier"]["facts"]["verdict"]["stress_ok"] is True
    # Solver 侧元信息注入判定事实，便于 judge 区分截断与答错
    assert entry["verifier"]["facts"]["solver"]["turns"] == 4
    # 目标汇总包含花费轮次对比字段
    target = results["targets"][0]
    assert target["turns_mean"] == 4
    assert target["turns_total"] == 4
    assert target["truncated_count"] == 0


def test_rejudge_backfills_failed_verifier(
    client: TestClient, mock_setup: dict, tmp_path: Path
) -> None:
    """历史判定失败（判定资产缺失）时，重新评分先补齐程序判定事实。"""
    import time

    from app.db import SessionLocal
    from app.models import JudgeExecution, VerifierExecution
    from app.services.runner import run_manager

    install = client.post(
        "/api/datasets/installations",
        json={"path": str(_build_algorithm_dataset(tmp_path, write_reference=False))},
    )
    assert install.status_code == 201, install.text
    installation = install.json()["installation"]

    script = [
        {"tool": "write_file", "arguments": {"path": "solution.py", "content": _REFERENCE_PY}},
        {"text": "已给出解答"},
    ]
    dep = client.post(
        "/api/deployments",
        json={
            "name": "Mock Backfill Agent",
            "model_id": mock_setup["model"]["id"],
            "provider_id": mock_setup["provider"]["id"],
            "api_model_name": "mock-agent",
            "custom_options": {"agent_script": script},
        },
    ).json()
    run = client.post(
        "/api/runs",
        json={
            "name": "rejudge backfill",
            "installation_id": installation["id"],
            "suite_id": "algo-suite",
            "solvers": [{"deployment_id": dep["id"]}],
            "judges": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
        },
    ).json()
    wait_run_finished(client, run["id"])
    run_id = run["id"]

    with SessionLocal() as session:
        first = session.scalars(
            sa.select(VerifierExecution).where(VerifierExecution.run_id == run_id)
        ).one()
        assert first.status == "failed"
        assert (first.facts_json or {}).get("verdict") is None
        judges_before = session.scalar(
            sa.select(sa.func.count()).select_from(JudgeExecution).where(JudgeExecution.run_id == run_id)
        )

    # 补齐缺失的判定资产（模拟环境修复），再重新评分
    reference = (
        Path(installation["source_path"])
        / "tasks"
        / "algo-suite"
        / "algo-demo-sum"
        / "judge_assets"
        / "reference_solution.py"
    )
    reference.write_text(_REFERENCE_PY, encoding="utf-8")

    resp = client.post(f"/api/runs/{run_id}/rejudge", json={})
    assert resp.status_code == 202, resp.text
    deadline = time.time() + 120
    while time.time() < deadline and run_manager.is_active(run_id):
        time.sleep(0.05)
    assert not run_manager.is_active(run_id)

    with SessionLocal() as session:
        rows = session.scalars(
            sa.select(VerifierExecution).where(VerifierExecution.run_id == run_id)
        ).all()
        # 原始失败记录保留（immutable）+ 补齐的新判定
        assert len(rows) == 2
        assert any(
            row.status == "completed" and (row.facts_json or {}).get("verdict", {}).get("samples_ok")
            for row in rows
        )
        judges_after = session.scalar(
            sa.select(sa.func.count()).select_from(JudgeExecution).where(JudgeExecution.run_id == run_id)
        )
        assert judges_after == judges_before * 2


# ---------------------------------------------------------------------------
# 生命周期恢复与可比性判定
# ---------------------------------------------------------------------------


def test_recover_interrupted_runs(client: TestClient, mock_setup: dict) -> None:
    from app.db import SessionLocal
    from app.models import ConfigSnapshot, Run, SolverExecution, TaskCache
    from app.services.runner import recover_interrupted_runs

    with SessionLocal() as session:
        task = session.scalars(sa.select(TaskCache)).first()
        assert task is not None
        snapshot = ConfigSnapshot(payload={"suite_id": "mock-core", "solvers": [], "judges": []})
        session.add(snapshot)
        session.flush()
        run = Run(
            name="interrupted run",
            status="running",
            framework_version="test",
            framework_commit="test",
            dataset_id="mock",
            dataset_version="0",
            dataset_revision="0",
            manifest_hash="0" * 64,
            suite_id="mock-core",
            installation_id=mock_setup["installation"]["id"],
            config_snapshot_id=snapshot.id,
        )
        session.add(run)
        session.flush()
        execution = SolverExecution(
            run_id=run.id,
            task_cache_id=task.id,
            task_id=task.task_id,
            task_revision=task.revision,
            task_hash=task.task_hash,
            deployment_id=mock_setup["dep_strong"]["id"],
            deployment_label="Mock Strong",
            status="running",
        )
        session.add(execution)
        session.flush()
        run_id, execution_id = run.id, execution.id
        session.commit()

    recovered = recover_interrupted_runs()
    assert recovered >= 1

    with SessionLocal() as session:
        assert session.get(Run, run_id).status == "failed"
        assert session.get(Run, run_id).error.startswith("服务重启")
        assert session.get(SolverExecution, execution_id).status == "cancelled"


def test_compare_flags_judge_prompt_version(
    client: TestClient, completed_run: dict, mock_setup: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import prompts

    monkeypatch.setattr(prompts, "JUDGE_PROMPT_VERSION", "judge-test-next")
    body = {
        "name": "next prompt version",
        "installation_id": mock_setup["installation"]["id"],
        "suite_id": "mock-core",
        "solvers": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
        "judges": [{"deployment_id": mock_setup["dep_strong"]["id"]}],
    }
    run2 = client.post("/api/runs", json=body).json()
    wait_run_finished(client, run2["id"])

    verdict = client.get(f"/api/runs/compare?ids={completed_run['id']},{run2['id']}").json()
    assert verdict["comparable"] is False
    assert any("judge prompt 版本" in reason for reason in verdict["reasons"])


def test_empty_suite_rejected(client: TestClient, tmp_path: Path) -> None:
    install = client.post(
        "/api/datasets/installations", json={"path": str(_build_empty_suite_dataset(tmp_path))}
    )
    assert install.status_code == 201, install.text
    installation = install.json()["installation"]

    dep = client.get("/api/deployments").json()[0]

    run = client.post(
        "/api/runs",
        json={
            "name": "empty suite run",
            "installation_id": installation["id"],
            "suite_id": "empty-suite",
            "solvers": [{"deployment_id": dep["id"]}],
            "judges": [{"deployment_id": dep["id"]}],
        },
    )
    assert run.status_code == 422
    assert "没有可运行的任务" in run.text
