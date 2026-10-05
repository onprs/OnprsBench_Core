"""数据集安装与加载：只依赖 Dataset Protocol（manifest.json + JSON Schema）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DatasetInstallation, TaskCache

SUPPORTED_PROTOCOL_VERSIONS = {"1"}


class DatasetValidationError(Exception):
    """数据集不符合 Dataset Protocol。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def canonical_json(data: object) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def task_hash(task: dict) -> str:
    return hashlib.sha256(canonical_json(task)).hexdigest()


def manifest_hash(manifest: dict) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def validate_manifest(manifest: dict, dataset_dir: Path) -> list[str]:
    """校验 manifest：JSON Schema + 协议版本 + 引用完整性 + asset hash。"""
    errors: list[str] = []

    schema = json.loads(settings.protocol_schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {location}: {err.message}")
    if errors:
        return errors

    if manifest["protocol_version"] not in SUPPORTED_PROTOCOL_VERSIONS:
        errors.append(f"不支持的 protocol_version: {manifest['protocol_version']}")

    tasks = manifest["tasks"]
    task_ids = {t["id"] for t in tasks}
    if len(task_ids) != len(tasks):
        errors.append("tasks 中存在重复 task id")
    for suite in manifest["suites"]:
        for tid in suite["task_ids"]:
            if tid not in task_ids:
                errors.append(f"suite {suite['id']} 引用了不存在的 task: {tid}")

    for task in tasks:
        for section in ("solver_visible", "judge_visible"):
            for asset in task.get(section, {}).get("assets", []) or []:
                asset_path = dataset_dir / asset["path"]
                if not asset_path.is_file():
                    errors.append(f"task {task['id']}: asset 不存在: {asset['path']}")
                    continue
                digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()
                if digest != asset["sha256"]:
                    errors.append(f"task {task['id']}: asset hash 不匹配: {asset['path']}")
    return errors


def load_manifest(dataset_dir: Path) -> tuple[dict, str]:
    """读取并校验数据集目录，返回 (manifest, manifest_hash)。"""
    manifest_path = dataset_dir / "manifest.json"
    if not manifest_path.is_file():
        raise DatasetValidationError([f"缺少 manifest.json: {manifest_path}"])
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetValidationError([f"manifest.json 不是合法 JSON: {exc}"]) from exc

    errors = validate_manifest(manifest, dataset_dir)
    if errors:
        raise DatasetValidationError(errors)
    return manifest, manifest_hash(manifest)


def install_dataset(session: Session, dataset_dir: Path) -> tuple[DatasetInstallation, bool]:
    """安装数据集。相同 manifest_hash 重复安装时复用已有记录。

    返回 (installation, created)。
    """
    dataset_dir = dataset_dir.resolve()
    manifest, mhash = load_manifest(dataset_dir)

    existing = session.scalar(
        sa.select(DatasetInstallation).where(DatasetInstallation.manifest_hash == mhash)
    )
    if existing is not None:
        return existing, False

    ds = manifest["dataset"]
    installation = DatasetInstallation(
        dataset_id=ds["id"],
        dataset_name=ds["name"],
        dataset_version=ds["version"],
        dataset_revision=ds["revision"],
        protocol_version=manifest["protocol_version"],
        manifest_hash=mhash,
        source_path=str(dataset_dir),
        suites=[{"id": s["id"], "name": s["name"], "task_ids": s["task_ids"]} for s in manifest["suites"]],
        capabilities=ds.get("capabilities", []),
    )
    session.add(installation)
    session.flush()

    for task in manifest["tasks"]:
        session.add(
            TaskCache(
                installation_id=installation.id,
                task_id=task["id"],
                revision=task["revision"],
                task_hash=task_hash(task),
                type=task["type"],
                tags=task["tags"],
                domains=task["metadata"]["domains"],
                contamination=task["metadata"]["contamination"],
                freshness=task["metadata"]["freshness"],
                payload=task,
            )
        )
    session.flush()
    return installation, True


def get_suite_tasks(session: Session, installation: DatasetInstallation, suite_id: str) -> list[TaskCache]:
    """按 suite 取 task 缓存记录，保持 suite 中声明的顺序。"""
    suite = next((s for s in installation.suites if s["id"] == suite_id), None)
    if suite is None:
        raise DatasetValidationError([f"suite 不存在: {suite_id}"])
    rows = session.scalars(
        sa.select(TaskCache).where(TaskCache.installation_id == installation.id)
    ).all()
    by_task_id = {row.task_id: row for row in rows}
    return [by_task_id[tid] for tid in suite["task_ids"]]
