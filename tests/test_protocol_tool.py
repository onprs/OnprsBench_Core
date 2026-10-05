"""跨层测试：独立 Dataset Protocol 校验工具（黑盒，经子进程运行）。

运行方式（仓库根目录）：
    server/.venv/Scripts/python -m pytest tests/
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

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
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    # 破坏 solver/judge 隔离所必需的结构：删除 judge_visible
    del manifest["tasks"][0]["judge_visible"]
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    result = run_validator(dest)
    assert result.returncode == 1
    assert "judge_visible" in result.stderr


def test_missing_manifest_rejected(tmp_path: Path) -> None:
    result = run_validator(tmp_path)
    assert result.returncode == 1
    assert "manifest.json" in result.stderr
