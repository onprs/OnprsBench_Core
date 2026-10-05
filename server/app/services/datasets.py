"""数据集安装与加载：只依赖 Dataset Protocol v1（manifest.yaml + task bundle）。

协议要点（详见 protocol/docs/dataset-protocol-v1.md）：
- manifest.yaml 是发布产物，task 内容以 path 引用的文件 bundle 承载
- 文件 hash、bundle_sha256、manifest hash 三级 SHA-256 校验
- rubric 为 weight + anchors 模型（维度权重之和为 1.0）
- meta.yaml 由框架读取用于筛选统计，不进入 solver / judge 提示词

安装时把数据集目录复制到数据目录的托管副本（datasets_dir/<manifest_hash 前 16 位>），
与原始目录解耦，保证历史 Run 可追溯、verify 契约可重复执行。
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path

import sqlalchemy as sa
import yaml
from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from ..config import settings
from ..models import DatasetInstallation, TaskCache

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS = {"1"}


class DatasetValidationError(Exception):
    """数据集不符合 Dataset Protocol。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bundle_sha256(file_hashes: dict[str, str]) -> str:
    """协议定义的 bundle hash：路径字典序拼接 "<path>  <sha256>" 后再取 SHA-256。"""
    lines = "".join(f"{rel}  {digest}\n" for rel, digest in sorted(file_hashes.items()))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def manifest_hash(manifest_path: Path) -> str:
    """manifest.yaml 文件字节的 SHA-256（与数据集发布产物一致）。"""
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _normalize_dates(value: object) -> object:
    """YAML 日期对象统一转 ISO 字符串（保证 schema 校验与落库稳定）。"""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_dates(v) for v in value]
    return value


def _load_yaml(path: Path) -> object:
    return _normalize_dates(yaml.safe_load(path.read_text(encoding="utf-8")))


def _validate_schema(manifest: dict) -> list[str]:
    schema = json.loads(settings.protocol_schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        location = "/".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"schema: {location}: {err.message}")
    return errors


def _validate_task_hashes(dataset_dir: Path, task: dict) -> list[str]:
    """校验 task bundle：文件 hash、bundle hash、可见性引用、目录外泄。"""
    errors: list[str] = []
    task_id = task["id"]
    task_dir = dataset_dir / task["path"]
    if not task_dir.is_dir():
        return [f"task {task_id}: bundle 目录不存在: {task['path']}"]

    declared = task["hashes"]["files"]
    for rel in declared:
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            errors.append(f"task {task_id}: 非法相对路径: {rel}")
    if errors:
        return errors

    actual = {
        p.relative_to(task_dir).as_posix(): sha256_file(p)
        for p in sorted(task_dir.rglob("*"))
        if p.is_file()
    }
    for rel, digest in declared.items():
        if rel not in actual:
            errors.append(f"task {task_id}: 声明的文件不存在: {rel}")
        elif actual[rel] != digest:
            errors.append(f"task {task_id}: 文件 hash 不匹配: {rel}")
    for rel in actual:
        if rel not in declared:
            errors.append(f"task {task_id}: 存在未登记 hash 的文件: {rel}")
    if not errors and bundle_sha256(actual) != task["hashes"]["bundle_sha256"]:
        errors.append(f"task {task_id}: bundle_sha256 不匹配")

    solver_visible = task["solver_visible"]
    judge_visible = task["judge_visible"]
    overlap = set(solver_visible) & set(judge_visible)
    if overlap:
        errors.append(f"task {task_id}: solver/judge 可见性重叠: {sorted(overlap)}")
    for rel in solver_visible + judge_visible:
        if rel not in declared:
            errors.append(f"task {task_id}: 可见性引用了未登记文件: {rel}")
    if "problem.md" not in solver_visible:
        errors.append(f"task {task_id}: solver_visible 缺少 problem.md")
    if "rubric.yaml" not in judge_visible:
        errors.append(f"task {task_id}: judge_visible 缺少 rubric.yaml")
    return errors


def _assemble_task_payload(dataset_dir: Path, suite: dict, task: dict) -> dict:
    """把 bundle 文件组装为框架内部 task 快照（TaskCache.payload）。"""
    task_dir = dataset_dir / task["path"]

    def read(rel: str) -> str:
        return (task_dir / rel).read_text(encoding="utf-8")

    rubric_raw = _load_yaml(task_dir / "rubric.yaml")
    rubric = {
        "version": str(rubric_raw.get("rubric_version", "1")),
        "dimensions": [
            {
                "id": d["id"],
                "weight": float(d["weight"]),
                "description": d.get("description", ""),
                "anchors": d.get("anchors") or [],
            }
            for d in rubric_raw.get("dimensions") or []
        ],
    }

    # reference/ 下全部文本按路径序拼接为参考解答包
    reference_parts = []
    for rel in task["judge_visible"]:
        if rel.startswith("reference/") and rel.endswith(".md"):
            reference_parts.append(f"### {rel}\n\n{read(rel).strip()}")
    reference = "\n\n".join(reference_parts)

    # anchors/score-XXX.md → 校准回答列表
    anchors = []
    for rel in task["judge_visible"]:
        match = re.fullmatch(r"anchors/score-(\d+)\.md", rel)
        if match:
            anchors.append({"score": int(match.group(1)), "text": read(rel).strip()})
    anchors.sort(key=lambda a: a["score"])

    solver_assets = [
        {"path": rel, "sha256": task["hashes"]["files"][rel]}
        for rel in task["solver_visible"]
        if rel != "problem.md"
    ]

    meta: dict = {}
    if "meta.yaml" in task["hashes"]["files"]:
        raw_meta = _load_yaml(task_dir / "meta.yaml")
        meta = raw_meta if isinstance(raw_meta, dict) else {}

    verify = None
    if "judge_assets/verify.yaml" in task["judge_visible"]:
        raw_verify = _load_yaml(task_dir / "judge_assets" / "verify.yaml")
        verify = raw_verify if isinstance(raw_verify, dict) else None

    return {
        "id": task["id"],
        "revision": task["revision"],
        "title": task["title"],
        "type": task["type"],
        "tags": task["tags"],
        "status": task["status"],
        "suite_id": suite["id"],
        "path": task["path"],
        "solver_visible": {
            "problem": read("problem.md"),
            "assets": solver_assets,
        },
        "judge_visible": {
            "reference": reference,
            "rubric": rubric,
            "anchors": anchors,
            "judge_assets": [rel for rel in task["judge_visible"] if rel.startswith("judge_assets/")],
        },
        "verify": verify,
        "metadata": {
            "difficulty": task["metadata"]["difficulty"],
            "freshness": task["metadata"]["freshness"],
            "contamination": task["metadata"]["contamination"],
            "flagship": task["metadata"]["flagship"],
            "meta": meta,
        },
    }


def load_manifest(dataset_dir: Path) -> tuple[dict, str]:
    """读取并校验数据集目录，返回 (manifest, manifest_hash)。task hash 一并校验。"""
    dataset_dir = dataset_dir.resolve()
    manifest_path = dataset_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise DatasetValidationError([f"缺少 manifest.yaml: {manifest_path}"])
    try:
        manifest = _load_yaml(manifest_path)
    except yaml.YAMLError as exc:
        raise DatasetValidationError([f"manifest.yaml 不是合法 YAML: {exc}"]) from exc
    if not isinstance(manifest, dict):
        raise DatasetValidationError(["manifest.yaml 顶层必须是对象"])

    errors = _validate_schema(manifest)
    if errors:
        raise DatasetValidationError(errors)

    if manifest["protocol_version"] not in SUPPORTED_PROTOCOL_VERSIONS:
        raise DatasetValidationError([f"不支持的 protocol_version: {manifest['protocol_version']}"])

    errors = []
    seen_ids: set[str] = set()
    for suite in manifest["suites"]:
        for task in suite["tasks"]:
            if task["id"] in seen_ids:
                errors.append(f"task id 重复: {task['id']}")
                continue
            seen_ids.add(task["id"])
            errors.extend(_validate_task_hashes(dataset_dir, task))
    if errors:
        raise DatasetValidationError(errors)
    return manifest, manifest_hash(manifest_path)


def _materialize_managed_copy(dataset_dir: Path, mhash: str) -> Path:
    """把数据集目录复制到数据目录的托管副本（幂等）。"""
    managed = settings.datasets_dir / mhash[:16]
    if managed.exists():
        return managed
    tmp = managed.with_name(managed.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(dataset_dir, tmp)
    tmp.rename(managed)
    logger.info("数据集托管副本: %s -> %s", dataset_dir, managed)
    return managed


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

    managed_dir = _materialize_managed_copy(dataset_dir, mhash)

    ds = manifest["dataset"]
    installation = DatasetInstallation(
        dataset_id=ds["id"],
        dataset_name=ds["name"],
        dataset_version=ds["version"],
        dataset_revision=ds["revision"],
        protocol_version=manifest["protocol_version"],
        manifest_hash=mhash,
        source_path=str(managed_dir),
        suites=[
            {
                "id": s["id"],
                "name": s["name"],
                "description": s.get("description", ""),
                "layer": s["layer"],
                "adapter": s.get("adapter"),
                "task_ids": [t["id"] for t in s["tasks"]],
            }
            for s in manifest["suites"]
        ],
        capabilities=[],
    )
    session.add(installation)
    session.flush()

    for suite in manifest["suites"]:
        for task in suite["tasks"]:
            payload = _assemble_task_payload(managed_dir, suite, task)
            session.add(
                TaskCache(
                    installation_id=installation.id,
                    task_id=task["id"],
                    revision=task["revision"],
                    task_hash=task["hashes"]["bundle_sha256"],
                    title=task["title"],
                    suite_id=suite["id"],
                    type=task["type"],
                    status=task["status"],
                    tags=task["tags"],
                    difficulty=task["metadata"]["difficulty"],
                    flagship=task["metadata"]["flagship"],
                    contamination=task["metadata"]["contamination"],
                    freshness=task["metadata"]["freshness"],
                    task_path=task["path"],
                    payload=payload,
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
    task_ids = suite.get("task_ids") or []
    missing = [tid for tid in task_ids if tid not in by_task_id]
    if missing:
        raise DatasetValidationError([f"suite {suite_id} 引用了未安装的 task: {missing}"])
    return [by_task_id[tid] for tid in task_ids]
