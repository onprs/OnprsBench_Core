"""分析 API：时间序列与元信息。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..services import analytics
from ..version import __version__, get_git_commit

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/meta")
def get_meta() -> dict:
    return {
        "framework_version": __version__,
        "framework_commit": get_git_commit(),
        "protocol_versions": ["1"],
    }


@router.get("/analytics/timeseries")
def get_timeseries(dataset_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    """Score / Cost / Latency over time 数据点（按 Run × Evaluation Target）。"""
    points = analytics.timeseries(db, dataset_id=dataset_id)
    for p in points:
        p["run_at"] = p["run_at"].isoformat() if p["run_at"] else None
    return points
