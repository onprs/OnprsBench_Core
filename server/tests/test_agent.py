"""Agent 形态 Solver 测试：工作区工具、diff 生成、多轮循环、全链路集成。"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.diff import diff_workspace
from app.agents.loop import run_agent_loop
from app.agents.tools import WorkspaceTools
from app.runtime.mock_client import MockClient

# ---------------------------------------------------------------------------
# 工具与 diff 单测
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text('"""pkg"""\n', encoding="utf-8")
    (root / "pkg" / "calc.py").write_text("def add(a, b):\n    return a - b  # BUG\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        "from pkg.calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    return root


@pytest.mark.asyncio
async def test_workspace_tools_read_write_search(repo: Path) -> None:
    tools = WorkspaceTools(repo)
    assert "return a - b" in await tools.dispatch("read_file", {"path": "pkg/calc.py"})
    await tools.dispatch("write_file", {"path": "pkg/calc.py", "content": "def add(a, b):\n    return a + b\n"})
    assert "return a + b" in (repo / "pkg" / "calc.py").read_text(encoding="utf-8")
    hits = await tools.dispatch("search_text", {"pattern": "return a + b"})
    assert "pkg/calc.py:2" in hits
    listing = await tools.dispatch("list_files", {"path": "."})
    assert "pkg/calc.py" in listing and "tests/test_calc.py" in listing


@pytest.mark.asyncio
async def test_workspace_tools_reject_escape(repo: Path) -> None:
    tools = WorkspaceTools(repo)
    result = await tools.dispatch("read_file", {"path": "../../etc/passwd"})
    assert "路径越界" in result
    result = await tools.dispatch("write_file", {"path": "../evil.py", "content": "x"})
    assert "路径越界" in result


@pytest.mark.asyncio
async def test_workspace_run_command(repo: Path) -> None:
    tools = WorkspaceTools(repo)
    out = await tools.dispatch("run_command", {"command": f"{sys.executable} -m pytest tests/ -q"})
    assert "failed" in out.lower()  # bug 未修时测试失败
    await tools.dispatch("write_file", {"path": "pkg/calc.py", "content": "def add(a, b):\n    return a + b\n"})
    out = await tools.dispatch("run_command", {"command": f"{sys.executable} -m pytest tests/ -q"})
    assert "passed" in out.lower()


def test_diff_workspace_modified_new_deleted(repo: Path, tmp_path: Path) -> None:
    pristine = tmp_path / "pristine"
    shutil.copytree(repo, pristine)
    # 修改 + 新增 + 删除
    (repo / "pkg" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "pkg" / "sub.py").write_text("def sub(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "pkg" / "__init__.py").unlink()

    patch = diff_workspace(pristine, repo)
    assert "diff --git a/pkg/calc.py b/pkg/calc.py" in patch
    assert "-    return a - b  # BUG" in patch and "+    return a + b" in patch
    assert "+++ b/pkg/sub.py" in patch and "--- /dev/null" in patch
    assert "--- a/pkg/__init__.py" in patch and "+++ /dev/null" in patch

    # diff 可被 patch-ng 应用（与 verifier 同一应用路径）
    import patch_ng

    target = tmp_path / "applied"
    shutil.copytree(pristine, target)
    patchset = patch_ng.fromstring(patch.encode("utf-8"))
    assert patchset and patchset.apply(root=str(target), strip=0)
    assert (target / "pkg" / "calc.py").read_text(encoding="utf-8").strip().endswith("return a + b")
    assert (target / "pkg" / "sub.py").is_file()
    assert not (target / "pkg" / "__init__.py").exists()


def test_diff_workspace_no_change(repo: Path, tmp_path: Path) -> None:
    pristine = tmp_path / "pristine"
    shutil.copytree(repo, pristine)
    assert diff_workspace(pristine, repo) == ""


# ---------------------------------------------------------------------------
# 多轮循环（mock 脚本驱动）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_loop_repairs_and_terminates(repo: Path, tmp_path: Path) -> None:
    pristine = tmp_path / "pristine"
    shutil.copytree(repo, pristine)
    script = [
        {"tool": "read_file", "arguments": {"path": "pkg/calc.py"}},
        {"tool": "write_file", "arguments": {"path": "pkg/calc.py", "content": "def add(a, b):\n    return a + b\n"}},
        {"tool": "run_command", "arguments": {"command": f"{sys.executable} -m pytest tests/ -q"}},
        {"text": "已修复 add 的符号错误并通过测试"},
    ]
    client = MockClient("mock-agent", latency_s=0, agent_script=script)
    result = await run_agent_loop(client, problem="修复 calc.add", workspace=repo, max_turns=10)

    assert result.stop_reason == "completed"
    assert result.turns == 4
    assert "已修复" in result.final_text
    assert result.input_tokens and result.input_tokens > 0
    # 轨迹完整：system+user + 4 assistant + 3 tool
    assert len(result.messages) == 9
    patch = diff_workspace(pristine, repo)
    assert "+    return a + b" in patch


@pytest.mark.asyncio
async def test_agent_loop_max_turns(repo: Path) -> None:
    script = [{"tool": "list_files", "arguments": {}}] * 5
    client = MockClient("mock-agent", latency_s=0, agent_script=script)
    result = await run_agent_loop(client, problem="探索", workspace=repo, max_turns=3)
    assert result.stop_reason == "max_turns"
    assert result.turns == 3


# ---------------------------------------------------------------------------
# 全链路集成：agent solver → 工作区 diff → 程序判定 → judge
# ---------------------------------------------------------------------------


def _build_swe_dataset(tmp_path: Path) -> Path:
    """构造含一个 SWE 契约任务的最小数据集。"""
    import hashlib

    import yaml

    from app.services.datasets import bundle_sha256

    root = tmp_path / "swe-ds"
    task_dir = root / "tasks" / "swe-suite" / "swe-demo-bug"
    (task_dir / "judge_assets").mkdir(parents=True)
    (task_dir / "problem.md").write_text("# 修复 add 函数\n\npkg/calc.py 的 add 实现成了减法，请修复。", encoding="utf-8")
    (task_dir / "rubric.yaml").write_text(
        yaml.safe_dump(
            {
                "rubric_version": 1,
                "dimensions": [
                    {
                        "id": "patch_correctness",
                        "weight": 1.0,
                        "description": "程序 verifier 判定：FAIL_TO_PASS 通过。",
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
                "id": "swe-demo-bug",
                "revision": 1,
                "title": "演示修复任务",
                "suite": "swe-suite",
                "type": "issue_resolution",
                "tags": ["demo"],
                "lifecycle": {"status": "active", "stage": "fresh"},
                "freshness": {"class": "F0", "created_at": "2026-10-06"},
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
                "repo": "local/demo",
                "repo_url": "https://github.com/local/demo",
                "base_commit": "c" * 40,
                "environment": {"python": "3.12", "setup": []},
                "evaluation": {
                    "apply": [],
                    "fail_to_pass": ["tests/test_calc.py::test_add"],
                    "pass_to_pass": ["tests/test_calc.py"],
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (task_dir / "reference").mkdir()
    (task_dir / "reference" / "canonical.md").write_text("# 参考：把减法改回加法", encoding="utf-8")

    files = {
        p.relative_to(task_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(task_dir.rglob("*"))
        if p.is_file()
    }
    manifest = {
        "protocol_version": "1",
        "dataset": {
            "id": "mock-swe-ds",
            "name": "Mock SWE Dataset",
            "version": "0.1.0",
            "release_date": "2026-10-06",
            "revision": "mock-swe-rev-1",
            "description": "agent 集成测试样例",
            "license": "MIT",
        },
        "suites": [
            {
                "id": "swe-suite",
                "name": "SWE Demo Suite",
                "description": "agent 链路测试",
                "layer": "derived",
                "adapter": None,
                "tasks": [
                    {
                        "id": "swe-demo-bug",
                        "revision": 1,
                        "title": "演示修复任务",
                        "type": "issue_resolution",
                        "tags": ["demo"],
                        "status": "active",
                        "path": "tasks/swe-suite/swe-demo-bug",
                        "solver_visible": ["problem.md"],
                        "judge_visible": ["judge_assets/verify.yaml", "rubric.yaml", "reference/canonical.md"],
                        "metadata": {"difficulty": "easy", "freshness": "F0", "contamination": "none", "flagship": False},
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


def test_agent_solver_end_to_end(
    client: TestClient,
    mock_setup: dict,
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.db import SessionLocal
    from app.models import SolverExecution, VerifierExecution
    from app.services import runner as runner_mod
    from app.verifier import swe as swe_mod
    from .conftest import wait_run_finished
    import sqlalchemy as sa

    # 仓库快照与 venv 固定为本地夹具，pytest 用当前解释器
    monkeypatch.setattr("app.toolchain.repos.fetch_repo_snapshot", lambda url, commit: repo)
    monkeypatch.setattr(swe_mod, "fetch_repo_snapshot", lambda url, commit: repo)
    monkeypatch.setattr(swe_mod, "ensure_python", lambda spec: Path(sys.executable))
    monkeypatch.setattr(swe_mod, "create_venv", lambda py, dest: Path(sys.executable))

    ds = _build_swe_dataset(tmp_path)
    install = client.post("/api/datasets/installations", json={"path": str(ds)})
    assert install.status_code == 201, install.text
    installation = install.json()["installation"]

    # solver 的 mock 注入修复脚本：读文件 → 修复 → 跑测试 → 总结
    script = [
        {"tool": "read_file", "arguments": {"path": "pkg/calc.py"}},
        {"tool": "write_file", "arguments": {"path": "pkg/calc.py", "content": "def add(a, b):\n    return a + b\n"}},
        {"tool": "run_command", "arguments": {"command": f"{sys.executable} -m pytest tests/ -q"}},
        {"text": "已修复 add 的符号错误"},
    ]
    agent_dep = client.post(
        "/api/deployments",
        json={
            "name": "Mock Agent",
            "model_id": mock_setup["model"]["id"],
            "provider_id": mock_setup["provider"]["id"],
            "api_model_name": "mock-agent",
            "custom_options": {"agent_script": script},
        },
    ).json()

    run = client.post(
        "/api/runs",
        json={
            "name": "agent e2e",
            "installation_id": installation["id"],
            "suite_id": "swe-suite",
            "solvers": [{"deployment_id": agent_dep["id"]}],
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
        # agent 形态：原始事实完整
        assert se.raw_response_json["solver_mode"] == "agent"
        assert se.raw_response_json["turns"] == 4
        assert "```diff" in se.response_text
        assert "return a + b" in se.response_text

        # 程序判定接力：diff 被提取、应用并通过测试
        ve = session.scalars(
            sa.select(VerifierExecution).where(VerifierExecution.solver_execution_id == se.id)
        ).one()
        assert ve.status == "completed"
        assert ve.facts_json["patch_applied"] is True
        assert ve.facts_json["verdict"]["fail_to_pass_ok"] is True
        assert ve.facts_json["verdict"]["pass_to_pass_ok"] is True

    # judge 照常完成且 prompt 带判定事实
    results = client.get(f"/api/runs/{run_id}/results").json()
    entries = next(iter(results["entries_by_target"].values()))
    assert entries[0]["judges"][0]["weighted_total"] is not None
    assert entries[0]["verifier"]["facts"]["verdict"]["fail_to_pass_ok"] is True
