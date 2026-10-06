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


def test_visibility_overrides_are_respected(tmp_path: Path) -> None:
    """visibility_overrides 能覆盖默认可见性，且只能引用已登记文件。"""
    from hashlib import sha256

    dest = tmp_path / "override-dataset"
    shutil.copytree(SAMPLE, dest)
    manifest_path = dest / "manifest.yaml"

    def refresh_task_hashes() -> None:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        task = next(t for t in manifest["suites"][0]["tasks"] if t["id"] == "mock-sum-001")
        task_dir = dest / task["path"]
        files = {
            p.relative_to(task_dir).as_posix(): sha256(p.read_bytes()).hexdigest()
            for p in sorted(task_dir.rglob("*"))
            if p.is_file()
        }
        lines = "".join(f"{rel}  {digest}\n" for rel, digest in sorted(files.items()))
        task["hashes"] = {
            "bundle_sha256": sha256(lines.encode("utf-8")).hexdigest(),
            "files": files,
        }
        manifest_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    task_dir = dest / "tasks" / "mock-core" / "mock-sum-001"
    meta_path = task_dir / "meta.yaml"
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))

    # 合法：把判分参考改为 meta 可见（既不进 solver 也不进 judge）
    meta["visibility_overrides"] = {"reference/canonical.md": "meta"}
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8")
    refresh_task_hashes()
    result = run_validator(dest)
    assert result.returncode == 0, result.stderr

    # 非法：引用未登记文件
    meta["visibility_overrides"] = {"not-registered.md": "solver"}
    meta_path.write_text(yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8")
    refresh_task_hashes()
    result = run_validator(dest)
    assert result.returncode == 1
    assert "visibility_overrides" in result.stderr


def test_tampered_file_rejected(tmp_path: Path) -> None:
    dest = tmp_path / "tampered-dataset"
    shutil.copytree(SAMPLE, dest)
    problem = dest / "tasks" / "mock-core" / "mock-sum-001" / "problem.md"
    problem.write_text("被篡改的题面", encoding="utf-8")

    result = run_validator(dest)
    assert result.returncode == 1
    assert "hash" in result.stderr
