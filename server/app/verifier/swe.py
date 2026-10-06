"""工程修复任务（issue_resolution）程序判定。

流程（契约见协议第 8.1 节）：
1. 按 repo_url + base_commit 供给仓库纯净快照（GitHub 归档，本地缓存）
2. 复制为本次判定的工作目录，创建独立 venv 并按 environment.setup 安装依赖
3. 应用 judge 侧测试补丁（evaluation.apply）
4. 从 solver 回答提取 unified diff 补丁并应用
5. 运行 FAIL_TO_PASS / PASS_TO_PASS 测试（pytest + JUnit XML 结构化采集）
"""

from __future__ import annotations

import logging
import shlex
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import patch_ng

from ..config import settings
from ..toolchain import ToolchainUnavailable
from ..toolchain.python_env import create_venv, ensure_python, pip_install, venv_python
from ..toolchain.repos import fetch_repo_snapshot, materialize_workspace
from .base import VerifyOutcome, run_command, tail
from .extract import extract_patch

logger = logging.getLogger(__name__)

# patch_ng.apply 内部使用 os.chdir(root)（进程级状态），并发应用会互相干扰，
# 必须以进程级互斥串行化（补丁应用本身是毫秒级操作，无性能损失）。
_apply_lock = threading.Lock()


def _apply_patch(repo_dir: Path, patch_text: str) -> tuple[bool, str]:
    """应用 unified diff。patch-ng 会自动剥离 git 风格 a//b 前缀（strip=0）；
    对老式手工 diff（路径带 a//b 但无 git 头）回退 strip=1。"""
    with _apply_lock:
        for strip in (0, 1):
            try:
                patchset = patch_ng.fromstring(patch_text.encode("utf-8"))
            except Exception as exc:  # patch-ng 解析异常类型不统一
                return False, f"补丁解析失败: {exc}"
            if not patchset:
                return False, "补丁内容为空或无法识别"
            try:
                ok = patchset.apply(root=str(repo_dir), strip=strip, fuzz=True)
            except Exception as exc:
                return False, f"补丁应用异常: {exc}"
            if ok:
                return True, "ok"
        return False, "补丁应用失败（hunk 不匹配）"


def _run_setup_steps(venv_py: Path, repo_dir: Path, steps: list[str]) -> tuple[bool, str]:
    """执行 environment.setup：git 步骤由框架快照供给替代；pip 步骤经 uv；其余原样执行。"""
    logs: list[str] = []
    for step in steps:
        command = step.strip()
        if not command:
            continue
        if command.startswith(("git clone", "git checkout", "cd ")):
            continue  # 仓库快照与 cwd 已由框架准备
        if command.startswith("pip install"):
            args = shlex.split(command)[2:]
            try:
                pip_install(venv_py, args, cwd=repo_dir)
            except ToolchainUnavailable as exc:
                return False, f"依赖安装失败（{command}）: {exc}"
            logs.append(f"$ {command} -> ok")
            continue
        result = run_command(command, cwd=repo_dir, timeout_s=settings.verify_step_timeout_s, env=None)
        logs.append(f"$ {command}\n{result.stdout}{result.stderr}")
        if result.returncode != 0:
            return False, f"setup 步骤失败（{command}）"
    return True, "\n".join(logs)


def _junit_nodeid(classname: str, name: str) -> str:
    """把 JUnit 的 classname/name 还原为 pytest nodeid。

    classname 形如 "tests.test_utils"（模块级测试）或 "tests.test_x.TestFoo"（类内测试）；
    模块路径按惯例全小写，第一个大写开头的段起为类名。参数化后缀（[param]）保留在 name 中。
    """
    parts = classname.split(".") if classname else []
    split_at = len(parts)
    for i, part in enumerate(parts):
        if part[:1].isupper():
            split_at = i
            break
    module_path = "/".join(parts[:split_at])
    classes = parts[split_at:]
    nodeid = f"{module_path}.py" if module_path else ""
    for cls in classes:
        nodeid += f"::{cls}"
    return f"{nodeid}::{name}" if nodeid else name


def _parse_junit(report: Path) -> dict[str, str]:
    """解析 pytest JUnit XML，返回 {nodeid: passed/failed/error/skipped}。

    参数化用例的 nodeid 形如 test_x[param]；契约声明的裸 nodeid
    （test_x.py::test_y）应聚合其全部参数化实例：全过才 passed，
    任一失败/错误即 failed/error。"""
    outcomes: dict[str, str] = {}
    if not report.is_file():
        return outcomes
    try:
        tree = ET.parse(report)
    except ET.ParseError:
        return outcomes
    for case in tree.getroot().iter("testcase"):
        nodeid = _junit_nodeid(case.get("classname", ""), case.get("name", ""))
        if case.find("failure") is not None:
            outcomes[nodeid] = "failed"
        elif case.find("error") is not None:
            outcomes[nodeid] = "error"
        elif case.find("skipped") is not None:
            outcomes[nodeid] = "skipped"
        else:
            outcomes[nodeid] = "passed"
    # 聚合参数化实例到其裸 nodeid（取最差结果）
    severity = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    aggregated = dict(outcomes)
    for nodeid, outcome in outcomes.items():
        base = nodeid.split("[")[0] if "[" in nodeid else None
        if base:
            prev = aggregated.get(base)
            if prev is None or severity[outcome] > severity[prev]:
                aggregated[base] = outcome
    return aggregated


def _run_pytest(venv_py: Path, repo_dir: Path, targets: list[str], report: Path) -> tuple[dict[str, str], str]:
    """运行 pytest 并返回 ({nodeid: outcome}, 日志)。"""
    result = run_command(
        [
            str(venv_py), "-m", "pytest",
            *targets,
            "--junitxml", str(report),
            "-p", "no:cacheprovider",
            "-q", "--tb=short",
        ],
        cwd=repo_dir,
        timeout_s=settings.verify_step_timeout_s,
    )
    log = result.stdout + ("\n" + result.stderr if result.stderr else "")
    if result.timed_out:
        log += f"\n[verifier] pytest 超时（{settings.verify_step_timeout_s}s）"
    return _parse_junit(report), tail(log)


def verify_swe(task_dir: Path, verify: dict, response_text: str, work_dir: Path) -> VerifyOutcome:
    """执行 SWE 判定契约，返回判定事实。"""
    started = time.perf_counter()
    work_dir = work_dir.resolve()  # uv 等子进程对相对路径的解析依赖 cwd，统一绝对化
    repo_url = verify.get("repo_url") or f"https://github.com/{verify.get('repo', '')}"
    base_commit = verify.get("base_commit")
    evaluation = verify.get("evaluation") or {}
    env_spec = verify.get("environment") or {}
    python_spec = str(env_spec.get("python") or settings.verifier_default_python)
    setup_steps = [str(s) for s in env_spec.get("setup") or []]
    test_patches = [str(p) for p in evaluation.get("apply") or []]
    fail_to_pass = [str(t) for t in evaluation.get("fail_to_pass") or []]
    pass_to_pass = [str(t) for t in evaluation.get("pass_to_pass") or []]

    facts: dict = {
        "mode": "issue_resolution",
        "repo_url": repo_url,
        "base_commit": base_commit,
        "python": python_spec,
        "fail_to_pass": fail_to_pass,
        "pass_to_pass": pass_to_pass,
    }
    environment: dict = {"python_spec": python_spec}
    logs: list[str] = []

    if not base_commit:
        return VerifyOutcome("failed", "swe_issue", facts, environment, error="verify 契约缺少 base_commit")

    # 0. 先提取补丁：提取失败时无需准备环境即可确定判定结果（基线 bug 未修，F2P 必然失败）
    solver_patch = extract_patch(response_text)
    facts["patch_extracted"] = solver_patch is not None
    facts["patch_applied"] = False
    if solver_patch is None:
        facts["patch_error"] = "未从 solver 回答中提取到 unified diff 补丁"
        facts["fail_to_pass_results"] = None
        facts["pass_to_pass_summary"] = None
        facts["verdict"] = {"fail_to_pass_ok": False, "pass_to_pass_ok": False}
        facts["wall_time_s"] = round(time.perf_counter() - started, 3)
        return VerifyOutcome("completed", "swe_issue", facts, environment)

    # 1. 仓库快照与 venv
    snapshot = fetch_repo_snapshot(repo_url, base_commit)
    repo_dir = materialize_workspace(snapshot, work_dir / "repo")
    python_path = ensure_python(python_spec)
    environment["python_executable"] = str(python_path)
    venv_py = create_venv(python_path, work_dir / ".venv")
    ok, setup_log = _run_setup_steps(venv_py, repo_dir, setup_steps)
    logs.append(setup_log)
    if not ok:
        return VerifyOutcome("failed", "swe_issue", facts, environment, tail("\n".join(logs)), error=setup_log)

    # 2. judge 侧测试补丁
    for rel in test_patches:
        patch_file = task_dir / rel
        if not patch_file.is_file():
            return VerifyOutcome("failed", "swe_issue", facts, environment, error=f"测试补丁缺失: {rel}")
        applied, note = _apply_patch(repo_dir, patch_file.read_text(encoding="utf-8"))
        if not applied:
            return VerifyOutcome("failed", "swe_issue", facts, environment, error=f"测试补丁应用失败: {rel}: {note}")
    facts["test_patch_applied"] = True

    # 3. solver 补丁
    applied, note = _apply_patch(repo_dir, solver_patch)
    facts["patch_applied"] = applied
    if not applied:
        facts["patch_error"] = note

    # 4. 测试判定
    f2p_outcomes: dict[str, str] = {}
    if fail_to_pass:
        f2p_outcomes, f2p_log = _run_pytest(venv_py, repo_dir, fail_to_pass, work_dir / "f2p.xml")
        logs.append(f"[FAIL_TO_PASS]\n{f2p_log}")
    facts["fail_to_pass_results"] = {
        nodeid: f2p_outcomes.get(nodeid, "not_found") for nodeid in fail_to_pass
    }
    facts["fail_to_pass_all_passed"] = all(
        v == "passed" for v in facts["fail_to_pass_results"].values()
    ) and bool(fail_to_pass)

    p2p_summary = {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0}
    if pass_to_pass:
        p2p_outcomes, p2p_log = _run_pytest(venv_py, repo_dir, pass_to_pass, work_dir / "p2p.xml")
        logs.append(f"[PASS_TO_PASS]\n{p2p_log}")
        for outcome in p2p_outcomes.values():
            p2p_summary["total"] += 1
            p2p_summary[outcome] = p2p_summary.get(outcome, 0) + 1
    facts["pass_to_pass_summary"] = p2p_summary
    facts["pass_to_pass_no_regression"] = p2p_summary["failed"] == 0 and p2p_summary["error"] == 0

    facts["verdict"] = {
        "fail_to_pass_ok": facts["fail_to_pass_all_passed"],
        "pass_to_pass_ok": facts["pass_to_pass_no_regression"],
    }
    facts["wall_time_s"] = round(time.perf_counter() - started, 3)
    return VerifyOutcome("completed", "swe_issue", facts, environment, tail("\n".join(logs)))
