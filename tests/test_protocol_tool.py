"""跨层测试：独立 Dataset Protocol 校验工具（黑盒，经子进程运行）。

运行方式（仓库根目录）：
    server/.venv/Scripts/python -m pytest tests/
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "protocol" / "tools" / "validate_dataset.py"
SAMPLE = REPO_ROOT / "protocol" / "examples" / "mock-protocol-sample"


def run_validator(dataset_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), str(dataset_dir)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_valid_sample_passes() -> None:
    result = run_validator(SAMPLE)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_invalid_dataset_rejected(tmp_path: Path) -> None:
    dest = tmp_path / "bad-dataset"
    shutil.copytree(SAMPLE, dest)
    manifest_path = dest / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    # 破坏 solver/judge 隔离所必需的结构：删除 judge_visible
    del manifest["suites"][0]["tasks"][0]["judge_visible"]
    manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")

    result = run_validator(dest)
    assert result.returncode == 1
    assert "judge_visible" in result.stderr


def test_missing_manifest_rejected(tmp_path: Path) -> None:
    result = run_validator(tmp_path)
    assert result.returncode == 1
    assert "manifest.yaml" in result.stderr


def test_tampered_file_rejected(tmp_path: Path) -> None:
    dest = tmp_path / "tampered-dataset"
    shutil.copytree(SAMPLE, dest)
    problem = dest / "tasks" / "mock-core" / "mock-sum-001" / "problem.md"
    problem.write_text("被篡改的题面", encoding="utf-8")

    result = run_validator(dest)
    assert result.returncode == 1
    assert "hash" in result.stderr
