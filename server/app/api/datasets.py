"""数据集安装与浏览 API。只通过 Dataset Protocol 交互。"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db, session_scope
from ..models import DatasetInstallation, Run, TaskCache
from ..schemas import DatasetInstall
from ..services import datasets as dataset_service
from ..services.datasets import DatasetValidationError

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


def installation_dict(inst: DatasetInstallation) -> dict:
    return {
        "id": inst.id,
        "dataset_id": inst.dataset_id,
        "dataset_name": inst.dataset_name,
        "dataset_version": inst.dataset_version,
        "dataset_revision": inst.dataset_revision,
        "protocol_version": inst.protocol_version,
        "manifest_hash": inst.manifest_hash,
        "source_path": inst.source_path,
        "capabilities": inst.capabilities,
        "installed_at": inst.installed_at,
        "suites": [
            {"id": s["id"], "name": s["name"], "task_count": len(s["task_ids"])} for s in inst.suites
        ],
    }


@router.post("/installations", status_code=201)
def install_dataset(body: DatasetInstall) -> dict:
    """安装本地目录数据集（需包含符合 Dataset Protocol 的 manifest.json）。"""
    path = Path(body.path).expanduser()
    if not path.is_dir():
        raise HTTPException(400, f"目录不存在: {path}")
    try:
        with session_scope() as session:
            installation, created = dataset_service.install_dataset(session, path)
            return {"installation": installation_dict(installation), "created": created}
    except DatasetValidationError as exc:
        raise HTTPException(422, detail={"message": "数据集不符合 Dataset Protocol", "errors": exc.errors}) from exc


@router.get("/installations")
def list_installations(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(sa.select(DatasetInstallation).order_by(DatasetInstallation.installed_at.desc()))
    return [installation_dict(i) for i in rows]


@router.delete("/installations/{installation_id}", status_code=204)
def delete_installation(installation_id: str, db: Session = Depends(get_db)) -> None:
    installation = db.get(DatasetInstallation, installation_id)
    if installation is None:
        raise HTTPException(404, "数据集安装不存在")
    used = db.scalar(
        sa.select(sa.func.count()).select_from(Run).where(Run.installation_id == installation_id)
    )
    if used:
        raise HTTPException(409, "该数据集已被历史 Run 使用，为保持可追溯性不能卸载")
    db.query(TaskCache).filter(TaskCache.installation_id == installation_id).delete()
    db.delete(installation)
    db.commit()


@router.get("/installations/{installation_id}")
def get_installation(installation_id: str, db: Session = Depends(get_db)) -> dict:
    installation = db.get(DatasetInstallation, installation_id)
    if installation is None:
        raise HTTPException(404, "数据集安装不存在")
    return installation_dict(installation)


@router.get("/installations/{installation_id}/tasks")
def list_tasks(installation_id: str, suite_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    installation = db.get(DatasetInstallation, installation_id)
    if installation is None:
        raise HTTPException(404, "数据集安装不存在")
    query = sa.select(TaskCache).where(TaskCache.installation_id == installation_id)
    rows = db.scalars(query).all()
    if suite_id:
        suite = next((s for s in installation.suites if s["id"] == suite_id), None)
        if suite is None:
            raise HTTPException(404, f"suite 不存在: {suite_id}")
        order = {tid: idx for idx, tid in enumerate(suite["task_ids"])}
        rows = sorted((r for r in rows if r.task_id in order), key=lambda r: order[r.task_id])
    return [
        {
            "id": t.id,
            "task_id": t.task_id,
            "revision": t.revision,
            "task_hash": t.task_hash,
            "type": t.type,
            "tags": t.tags,
            "domains": t.domains,
            "contamination": t.contamination,
            "freshness": t.freshness,
            "problem": t.payload["solver_visible"]["problem"],
            "rubric_dimensions": [
                {"id": d["id"], "max_score": d["max_score"]}
                for d in t.payload["judge_visible"]["rubric"]["dimensions"]
            ],
        }
        for t in rows
    ]
