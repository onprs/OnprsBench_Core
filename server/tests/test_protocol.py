"""Dataset Protocol 校验测试（framework 侧 loader + 独立校验工具共用语义）。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.services.datasets import DatasetValidationError, load_manifest, manifest_hash, task_hash

from .conftest import MOCK_DATASET_DIR


def test_mock_sample_passes() -> None:
    manifest, mhash = load_manifest(MOCK_DATASET_DIR)
    assert manifest["protocol_version"] == "1"
    assert len(mhash) == 64


def test_missing_manifest_fails(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="manifest.json"):
        load_manifest(tmp_path)


def _copy_sample(tmp_path: Path) -> Path:
    dest = tmp_path / "ds"
    shutil.copytree(MOCK_DATASET_DIR, dest)
    return dest


def test_schema_violation_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    del manifest["tasks"][0]["judge_visible"]["rubric"]
    (ds / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DatasetValidationError) as exc_info:
        load_manifest(ds)
    assert any("rubric" in e for e in exc_info.value.errors)


def test_unsupported_protocol_version_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    manifest["protocol_version"] = "99"
    (ds / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="protocol_version"):
        load_manifest(ds)


def test_dangling_suite_reference_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    manifest["suites"][0]["task_ids"].append("no-such-task")
    (ds / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="不存在的 task"):
        load_manifest(ds)


def test_asset_hash_mismatch_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    asset_path = ds / "assets" / "hint.txt"
    asset_path.parent.mkdir(exist_ok=True)
    asset_path.write_text("被篡改的内容", encoding="utf-8")

    manifest = json.loads((ds / "manifest.json").read_text(encoding="utf-8"))
    manifest["tasks"][0]["solver_visible"]["assets"] = [
        {"path": "assets/hint.txt", "sha256": "0" * 64, "media_type": "text/plain"}
    ]
    (ds / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="hash 不匹配"):
        load_manifest(ds)


def test_hashes_are_stable_and_sensitive() -> None:
    manifest, mhash = load_manifest(MOCK_DATASET_DIR)
    task = manifest["tasks"][0]
    assert task_hash(task) == task_hash(json.loads(json.dumps(task)))
    changed = {**task, "revision": task["revision"] + 1}
    assert task_hash(changed) != task_hash(task)
    assert manifest_hash(manifest) == mhash
