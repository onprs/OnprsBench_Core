"""竞赛代码任务（code_generation）程序判定。

流程（契约见协议第 8.2 节）：
1. 从 solver 回答提取代码（cpp / python）
2. 编译参考解（按内容 hash 缓存）与 solver 代码（C++ 时）
3. 官方样例回归（samples.json）
4. 生成器应力测试：generator.py 出输入，参考解出期望输出，逐一比对

输出比对采用 token 级归一化（忽略空白差异），与竞赛惯例一致。
解释型语言时限按题目时限 ×3 放宽（框架约定，见协议第 10 节）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from ..config import settings
from ..toolchain.compilers import compiler_version, ensure_cpp_compiler
from ..toolchain.python_env import ensure_python
from .base import VerifyOutcome, run_command, step_timeout, tail
from .extract import extract_code

logger = logging.getLogger(__name__)

_INTERPRETED_FACTOR = 3  # 解释型语言时限放宽倍数（框架约定）


def _parse_time_limit(verify: dict) -> float:
    raw = str(((verify.get("source") or {}).get("limits") or {}).get("time", "2s"))
    match = re.match(r"([\d.]+)\s*(s|ms)?", raw)
    if not match:
        return 2.0
    value = float(match.group(1))
    return value / 1000.0 if match.group(2) == "ms" else value


def _tokens_equal(actual: str, expected: str) -> bool:
    return actual.split() == expected.split()


def _compile(compiler: Path, source: Path, output: Path, deadline_at: float | None = None) -> tuple[bool, str]:
    result = run_command(
        [str(compiler), "-O2", "-std=c++17", "-o", str(output), str(source)],
        timeout_s=step_timeout(deadline_at, settings.verify_step_timeout_s),
    )
    if result.returncode != 0:
        return False, tail(result.stderr or result.stdout)
    return True, ""


def _cached_reference_binary(
    compiler: Path, source: Path, cache_root: Path, deadline_at: float | None = None
) -> tuple[Path, str]:
    """编译参考解（按源码 hash 缓存）。返回 (可执行文件路径, 编译器版本)。"""
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:24]
    version = compiler_version(compiler)
    cache_dir = cache_root / digest
    binary = cache_dir / ("ref.exe" if os.name == "nt" else "ref")
    if not binary.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        ok, log = _compile(compiler, source, binary, deadline_at)
        if not ok:
            raise RuntimeError(f"参考解编译失败: {log}")
    return binary, version


_CPP_SUFFIXES = {".cpp", ".cc", ".cxx", ".c"}
_PY_SUFFIXES = {".py"}


def _reference_command(
    ref_source: Path, environment: dict, deadline_at: float | None = None
) -> tuple[list[str], float]:
    """按参考解语言返回运行命令与单用例时限。

    C++：编译（产物按内容 hash 缓存），时限按题目值；
    Python：直接解释运行，时限 ×3（与 solver 侧一致的框架约定）。
    """
    suffix = ref_source.suffix.lower()
    if suffix in _CPP_SUFFIXES:
        compiler = ensure_cpp_compiler()
        environment.setdefault("cpp_compiler", compiler_version(compiler))
        binary, _ = _cached_reference_binary(compiler, ref_source, settings.cache_dir / "refbin", deadline_at)
        return [str(binary)], 0.0  # 时限由调用方按题目值给定
    if suffix in _PY_SUFFIXES:
        python_path = ensure_python(settings.verifier_default_python)
        environment.setdefault("reference_python", str(python_path))
        return [str(python_path), str(ref_source)], 0.0
    raise RuntimeError(f"不支持的参考解类型: {ref_source.name}")


def _run_solution(cmd: list[str], stdin_text: str, timeout_s: float) -> tuple[str, str, float, bool]:
    """运行解答程序。返回 (stdout, stderr, 耗时, 是否超时)。"""
    result = run_command(cmd, timeout_s=timeout_s, stdin_text=stdin_text)
    return result.stdout, result.stderr, result.duration_s, result.timed_out or result.returncode != 0


def verify_algorithm(
    task_dir: Path,
    verify: dict,
    response_text: str,
    work_dir: Path,
    deadline_at: float | None = None,
) -> VerifyOutcome:
    """执行竞赛判定契约，返回判定事实。"""
    started = time.perf_counter()
    evaluation = verify.get("evaluation") or {}
    harness = evaluation.get("harness") or {}
    generator_rel = harness.get("generator")
    reference_rel = evaluation.get("reference_solution")
    time_limit = _parse_time_limit(verify)

    facts: dict = {
        "mode": "code_generation",
        "time_limit_s": time_limit,
        "stress_seeds": settings.algorithm_stress_seeds,
        "stress_cases_per_seed": settings.algorithm_stress_cases,
    }
    environment: dict = {}
    logs: list[str] = []

    extracted = extract_code(response_text)
    facts["code_extracted"] = extracted is not None
    if extracted is None:
        facts["verdict"] = {"samples_ok": False, "stress_ok": False}
        facts["error"] = "未从 solver 回答中提取到代码块"
        return VerifyOutcome("completed", "algorithm", facts, environment, error=facts["error"])

    language, code = extracted
    facts["language"] = language
    facts["code_chars"] = len(code)
    limit = time_limit * (_INTERPRETED_FACTOR if language == "python" else 1)
    facts["applied_time_limit_s"] = limit

    work_dir.mkdir(parents=True, exist_ok=True)

    # 1. 准备 solver 运行命令
    if language == "cpp":
        compiler = ensure_cpp_compiler()
        environment["cpp_compiler"] = compiler_version(compiler)
        solver_src = work_dir / "solution.cpp"
        solver_src.write_text(code, encoding="utf-8")
        solver_bin = work_dir / ("solution.exe" if os.name == "nt" else "solution")
        ok, log = _compile(compiler, solver_src, solver_bin, deadline_at)
        facts["compiled"] = ok
        if not ok:
            facts["compile_error"] = log
            facts["verdict"] = {"samples_ok": False, "stress_ok": False}
            return VerifyOutcome("completed", "algorithm", facts, environment, tail(log))
        solver_cmd = [str(solver_bin)]
    else:
        python_path = ensure_python(settings.verifier_default_python)
        environment["python_executable"] = str(python_path)
        solver_py = work_dir / "solution.py"
        solver_py.write_text(code, encoding="utf-8")
        solver_cmd = [str(python_path), str(solver_py)]
        facts["compiled"] = True

    # 2. 参考解（期望输出来源；C++ 编译并缓存，Python 直接解释运行）
    ref_source = task_dir / str(reference_rel)
    if not ref_source.is_file():
        return VerifyOutcome("failed", "algorithm", facts, environment, error=f"参考解缺失: {reference_rel}")
    ref_is_python = ref_source.suffix.lower() in _PY_SUFFIXES
    ref_limit = time_limit * (_INTERPRETED_FACTOR if ref_is_python else 1)
    try:
        ref_cmd, _ = _reference_command(ref_source, environment, deadline_at)
    except RuntimeError as exc:
        return VerifyOutcome("failed", "algorithm", facts, environment, error=str(exc))

    generator_path = task_dir / str(generator_rel) if generator_rel else None
    tool_python = ensure_python(settings.verifier_default_python)

    def run_case(input_text: str) -> tuple[bool, str, str, float]:
        expected_out, _, _, ref_bad = _run_solution(
            ref_cmd, input_text, step_timeout(deadline_at, ref_limit * 2)
        )
        if ref_bad:
            raise RuntimeError("参考解运行失败（数据集契约问题）")
        actual_out, err, duration, bad = _run_solution(
            solver_cmd, input_text, step_timeout(deadline_at, limit)
        )
        if bad:
            return False, "", err or "超时或崩溃", duration
        return _tokens_equal(actual_out, expected_out), actual_out, "", duration

    # 3. 官方样例（契约声明路径，兼容旧版默认位置）
    samples_rel = evaluation.get("samples") or "judge_assets/samples.json"
    samples_file = task_dir / str(samples_rel)
    samples = json.loads(samples_file.read_text(encoding="utf-8")) if samples_file.is_file() else []
    sample_results: list[dict] = []
    for idx, sample in enumerate(samples):
        passed, actual, err, duration = run_case(str(sample["input"]))
        sample_results.append(
            {
                "index": idx,
                "passed": passed,
                "duration_s": round(duration, 3),
                **({"error": tail(err, 500)} if err else {}),
                **({"actual_tail": tail(actual, 500)} if not passed and not err else {}),
            }
        )
    facts["samples"] = {
        "total": len(sample_results),
        "passed": sum(1 for r in sample_results if r["passed"]),
        "details": sample_results,
    }

    # 4. 应力测试（生成器批量出题，参考解给期望）
    stress_total = 0
    stress_passed = 0
    first_failure: dict | None = None
    if generator_path is not None and generator_path.is_file():
        for seed in range(1, settings.algorithm_stress_seeds + 1):
            gen = run_command(
                [str(tool_python), str(generator_path), str(seed), str(settings.algorithm_stress_cases)],
                timeout_s=step_timeout(deadline_at, 120),
            )
            if gen.returncode != 0 or not gen.stdout.strip():
                return VerifyOutcome("failed", "algorithm", facts, environment, error=f"生成器运行失败: {tail(gen.stderr)}")
            stress_input = gen.stdout
            expected_out, _, _, ref_bad = _run_solution(
                ref_cmd, stress_input, step_timeout(deadline_at, ref_limit * 2)
            )
            if ref_bad:
                return VerifyOutcome("failed", "algorithm", facts, environment, error="参考解运行失败（数据集契约问题）")
            actual_out, err, duration, bad = _run_solution(
                solver_cmd,
                stress_input,
                step_timeout(deadline_at, limit * settings.algorithm_stress_cases),
            )
            stress_total += 1
            ok = (not bad) and _tokens_equal(actual_out, expected_out)
            if ok:
                stress_passed += 1
            elif first_failure is None:
                first_failure = {
                    "seed": seed,
                    "error": tail(err, 300) if err else None,
                    "duration_s": round(duration, 3),
                    "input_head": tail(stress_input, 500),
                }
    facts["stress"] = {
        "batches": stress_total,
        "passed_batches": stress_passed,
        "first_failure": first_failure,
    }
    facts["verdict"] = {
        "samples_ok": facts["samples"]["passed"] == facts["samples"]["total"] and bool(samples),
        "stress_ok": stress_total > 0 and stress_passed == stress_total,
    }
    facts["wall_time_s"] = round(time.perf_counter() - started, 3)
    return VerifyOutcome("completed", "algorithm", facts, environment, tail("\n".join(logs)))
