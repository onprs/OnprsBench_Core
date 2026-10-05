"""程序判定（verifier）测试。

- 算法判定：本地构造竞赛任务夹具，真实 g++ 编译（本机/toolchain 供给）。
  解释器经 monkeypatch 固定为当前 Python，避免网络下载。
- SWE 判定：本地构造含 bug 的微型 Python 包，真实应用补丁 + 跑 pytest。
  仓库快照/解释器/venv 经 monkeypatch 固定，避免网络依赖。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.verifier import service as verifier_service
from app.verifier import swe as swe_mod
from app.verifier import algorithm as algo_mod


# ---------------------------------------------------------------------------
# 算法判定夹具：a+b 求和题
# ---------------------------------------------------------------------------

GENERATOR_SRC = """\
import random
import sys

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 1
cases = int(sys.argv[2]) if len(sys.argv) > 2 else 10
rng = random.Random(seed)
print(cases)
for _ in range(cases):
    print(rng.randint(1, 100), rng.randint(1, 100))
"""

REFERENCE_CPP = """\
#include <iostream>
int main() {
    int t;
    std::cin >> t;
    while (t--) { long long a, b; std::cin >> a >> b; std::cout << a + b << "\\n"; }
    return 0;
}
"""

SOLVER_CPP_OK = """\
#include <iostream>
int main() {
    int t; std::cin >> t;
    while (t--) { long long a, b; std::cin >> a >> b; std::cout << a + b << "\\n"; }
}
"""

SOLVER_CPP_WRONG = """\
#include <iostream>
int main() {
    int t; std::cin >> t;
    while (t--) { long long a, b; std::cin >> a >> b; std::cout << a - b << "\\n"; }
}
"""

SOLVER_PY_OK = """\
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


@pytest.fixture()
def algo_task(tmp_path: Path) -> Path:
    task_dir = tmp_path / "task"
    (task_dir / "judge_assets").mkdir(parents=True)
    (task_dir / "judge_assets" / "generator.py").write_text(GENERATOR_SRC, encoding="utf-8")
    (task_dir / "judge_assets" / "reference_solution.cpp").write_text(REFERENCE_CPP, encoding="utf-8")
    (task_dir / "judge_assets" / "samples.json").write_text(
        json.dumps([{"input": "2\n1 2\n3 4", "output": "3\n7"}]), encoding="utf-8"
    )
    return task_dir


ALGO_VERIFY = {
    "source": {"limits": {"time": "2s"}},
    "evaluation": {
        "mode": "程序判题",
        "reference_solution": "judge_assets/reference_solution.cpp",
        "harness": {"generator": "judge_assets/generator.py", "brute_force": "judge_assets/brute_force.py"},
    },
}


@pytest.fixture(autouse=True)
def _pin_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """解释器固定为当前 Python，避免测试触发下载。"""
    monkeypatch.setattr(algo_mod, "ensure_python", lambda spec: Path(sys.executable))
    monkeypatch.setattr(swe_mod, "ensure_python", lambda spec: Path(sys.executable))


def test_algorithm_correct_cpp_passes(algo_task: Path, tmp_path: Path) -> None:
    pytest.importorskip("shutil")
    try:
        from app.toolchain.compilers import ensure_cpp_compiler

        ensure_cpp_compiler()
    except Exception:
        pytest.skip("无可用 C++ 编译器")
    outcome = algo_mod.verify_algorithm(
        algo_task, ALGO_VERIFY, f"```cpp\n{SOLVER_CPP_OK}\n```", tmp_path / "work1"
    )
    assert outcome.status == "completed"
    assert outcome.facts["compiled"] is True
    assert outcome.facts["samples"]["passed"] == outcome.facts["samples"]["total"] == 1
    assert outcome.facts["stress"]["passed_batches"] == outcome.facts["stress"]["batches"]
    assert outcome.facts["verdict"] == {"samples_ok": True, "stress_ok": True}


def test_algorithm_wrong_answer_fails(algo_task: Path, tmp_path: Path) -> None:
    try:
        from app.toolchain.compilers import ensure_cpp_compiler

        ensure_cpp_compiler()
    except Exception:
        pytest.skip("无可用 C++ 编译器")
    outcome = algo_mod.verify_algorithm(
        algo_task, ALGO_VERIFY, f"```cpp\n{SOLVER_CPP_WRONG}\n```", tmp_path / "work2"
    )
    assert outcome.status == "completed"
    assert outcome.facts["verdict"]["samples_ok"] is False
    assert outcome.facts["verdict"]["stress_ok"] is False


def test_algorithm_python_solution_passes(algo_task: Path, tmp_path: Path) -> None:
    try:
        from app.toolchain.compilers import ensure_cpp_compiler

        ensure_cpp_compiler()
    except Exception:
        pytest.skip("无可用 C++ 编译器")
    outcome = algo_mod.verify_algorithm(
        algo_task, ALGO_VERIFY, f"```python\n{SOLVER_PY_OK}\n```", tmp_path / "work3"
    )
    assert outcome.status == "completed"
    assert outcome.facts["language"] == "python"
    assert outcome.facts["verdict"]["samples_ok"] is True


def test_algorithm_no_code_extracted(algo_task: Path, tmp_path: Path) -> None:
    outcome = algo_mod.verify_algorithm(algo_task, ALGO_VERIFY, "只有文字没有代码", tmp_path / "work4")
    assert outcome.status == "completed"
    assert outcome.facts["code_extracted"] is False
    assert outcome.facts["verdict"]["samples_ok"] is False


# ---------------------------------------------------------------------------
# SWE 判定夹具：含 bug 的微型 Python 包
# ---------------------------------------------------------------------------

CALC_BUGGY = "def add(a, b):\n    return a - b  # BUG\n"
CALC_TEST = """\
from pkg.calc import add


def test_add():
    assert add(2, 3) == 5


def test_add_zero():
    assert add(0, 0) == 0
"""

FIX_PATCH = """\
diff --git a/pkg/calc.py b/pkg/calc.py
--- a/pkg/calc.py
+++ b/pkg/calc.py
@@ -1,2 +1,2 @@
-def add(a, b):
-    return a - b  # BUG
+def add(a, b):
+    return a + b
"""


@pytest.fixture()
def swe_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "pristine"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "calc.py").write_text(CALC_BUGGY, encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(CALC_TEST, encoding="utf-8")
    return repo


SWE_VERIFY = {
    "repo": "local/pkg",
    "repo_url": "https://github.com/local/pkg",
    "base_commit": "0" * 40,
    "environment": {"python": "3.12", "setup": []},
    "evaluation": {
        "apply": [],
        "fail_to_pass": ["tests/test_calc.py::test_add"],
        "pass_to_pass": ["tests/test_calc.py"],
    },
}


@pytest.fixture(autouse=True)
def _pin_swe_toolchain(monkeypatch: pytest.MonkeyPatch, swe_repo: Path) -> None:
    """仓库快照与 venv 固定为本地夹具，避免网络与依赖安装。"""
    monkeypatch.setattr(swe_mod, "fetch_repo_snapshot", lambda url, commit: swe_repo)
    monkeypatch.setattr(swe_mod, "create_venv", lambda py, dest: Path(sys.executable))


def test_swe_correct_patch_passes(swe_repo: Path, tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outcome = swe_mod.verify_swe(
        task_dir, SWE_VERIFY, f"```diff\n{FIX_PATCH}```", tmp_path / "w1"
    )
    assert outcome.status == "completed", outcome.error
    assert outcome.facts["patch_applied"] is True
    assert outcome.facts["fail_to_pass_results"]["tests/test_calc.py::test_add"] == "passed"
    assert outcome.facts["verdict"]["fail_to_pass_ok"] is True
    assert outcome.facts["verdict"]["pass_to_pass_ok"] is True


def test_swe_missing_patch_fails(swe_repo: Path, tmp_path: Path) -> None:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    outcome = swe_mod.verify_swe(task_dir, SWE_VERIFY, "我无法修复这个问题", tmp_path / "w2")
    assert outcome.status == "completed"
    assert outcome.facts["patch_extracted"] is False
    assert outcome.facts["patch_applied"] is False
    # 补丁提取失败时短路：不准备环境，判定直接为不通过
    assert outcome.facts["fail_to_pass_results"] is None
    assert outcome.facts["verdict"] == {"fail_to_pass_ok": False, "pass_to_pass_ok": False}


def test_verify_task_dispatch(swe_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """service 按契约形状分发到正确判定器。"""
    swe_payload = {"id": "t1", "verify": SWE_VERIFY}
    algo_payload = {"id": "t2", "verify": ALGO_VERIFY}
    text_payload = {"id": "t3", "verify": None}

    monkeypatch.setattr(swe_mod, "fetch_repo_snapshot", lambda url, commit: swe_repo)
    monkeypatch.setattr(swe_mod, "create_venv", lambda py, dest: Path(sys.executable))
    monkeypatch.setattr(swe_mod, "ensure_python", lambda spec: Path(sys.executable))

    assert verifier_service.has_verify_contract(swe_payload)
    assert verifier_service.has_verify_contract(algo_payload)
    assert not verifier_service.has_verify_contract(text_payload)
    assert verifier_service.verify_task(
        task_payload=text_payload, task_dir=tmp_path, response_text="", work_dir=tmp_path / "wx"
    ) is None
