"""Dataset Protocol 校验测试（bundle 形态：manifest.yaml + task 目录 + 三级 hash）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from app.services.datasets import DatasetValidationError, bundle_sha256, load_manifest, manifest_hash

from .conftest import MOCK_DATASET_DIR


def _load_manifest_file(ds: Path) -> dict:
    return yaml.safe_load((ds / "manifest.yaml").read_text(encoding="utf-8"))


def _save_manifest_file(ds: Path, manifest: dict) -> None:
    (ds / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def test_mock_sample_passes() -> None:
    manifest, mhash = load_manifest(MOCK_DATASET_DIR)
    assert manifest["protocol_version"] == "1"
    assert manifest["dataset"]["id"] == "onprs-mock-protocol-sample"
    assert len(mhash) == 64
    # manifest hash 是文件字节 hash，与内容规范化无关
    assert mhash == manifest_hash(MOCK_DATASET_DIR / "manifest.yaml")


def test_missing_manifest_fails(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="manifest.yaml"):
        load_manifest(tmp_path)


def _copy_sample(tmp_path: Path) -> Path:
    dest = tmp_path / "ds"
    shutil.copytree(MOCK_DATASET_DIR, dest)
    return dest


def test_schema_violation_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    manifest = _load_manifest_file(ds)
    del manifest["suites"][0]["tasks"][0]["judge_visible"]
    _save_manifest_file(ds, manifest)
    with pytest.raises(DatasetValidationError) as exc_info:
        load_manifest(ds)
    assert any("judge_visible" in e for e in exc_info.value.errors)


def test_unsupported_protocol_version_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    manifest = _load_manifest_file(ds)
    manifest["protocol_version"] = "99"
    _save_manifest_file(ds, manifest)
    with pytest.raises(DatasetValidationError, match="protocol_version"):
        load_manifest(ds)


def test_task_file_hash_mismatch_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    problem = ds / "tasks" / "mock-core" / "mock-sum-001" / "problem.md"
    problem.write_text("被篡改的题面", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="hash 不匹配"):
        load_manifest(ds)


def test_unregistered_file_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    extra = ds / "tasks" / "mock-core" / "mock-sum-001" / "answer.md"
    extra.write_text("未登记的文件", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="未登记"):
        load_manifest(ds)


def test_duplicate_task_id_fails(tmp_path: Path) -> None:
    ds = _copy_sample(tmp_path)
    manifest = _load_manifest_file(ds)
    duplicated = dict(manifest["suites"][0]["tasks"][0])
    manifest["suites"][0]["tasks"].append(duplicated)
    _save_manifest_file(ds, manifest)
    with pytest.raises(DatasetValidationError, match="重复"):
        load_manifest(ds)


def test_bundle_hash_rule_matches_protocol() -> None:
    """bundle hash 规则：路径字典序拼接 "<path>  <sha256>" 后再取 SHA-256。"""
    file_hashes = {"b.txt": "1" * 64, "a.txt": "0" * 64}
    expected = bundle_sha256({"a.txt": "0" * 64, "b.txt": "1" * 64})
    assert bundle_sha256(file_hashes) == expected  # 顺序无关
