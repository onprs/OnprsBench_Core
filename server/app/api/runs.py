"""Run API：创建/启动、查询、结果、重新评分、可比性判定。"""

from __future__ import annotations

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db, session_scope
from ..models import (
    AggregationVersion,
    ConfigSnapshot,
    DatasetInstallation,
    Deployment,
    JudgeExecution,
    PricingSnapshot,
    ReasoningProfile,
    Run,
    SolverExecution,
    UsageRecord,
)
from ..schemas import RejudgeRequest, RunCreate, TargetSpecIn
from ..services import aggregation, analytics, datasets as dataset_service, prompts
from ..services.runner import run_manager
from ..version import __version__, get_git_commit

router = APIRouter(prefix="/api/runs", tags=["runs"])


def run_dict(run: Run, db: Session) -> dict:
    solver_count = db.scalar(
        sa.select(sa.func.count()).select_from(SolverExecution).where(SolverExecution.run_id == run.id)
    )
    judge_count = db.scalar(
        sa.select(sa.func.count()).select_from(JudgeExecution).where(JudgeExecution.run_id == run.id)
    )
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "framework_version": run.framework_version,
        "framework_commit": run.framework_commit,
        "dataset_id": run.dataset_id,
        "dataset_version": run.dataset_version,
        "dataset_revision": run.dataset_revision,
        "manifest_hash": run.manifest_hash,
        "suite_id": run.suite_id,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "solver_wall_time_s": run.solver_wall_time_s,
        "judge_wall_time_s": run.judge_wall_time_s,
        "total_wall_time_s": run.total_wall_time_s,
        "solver_cost": run.solver_cost,
        "judge_cost": run.judge_cost,
        "total_cost": run.total_cost,
        "error": run.error,
        "solver_execution_count": solver_count,
        "judge_execution_count": judge_count,
    }


def _validate_targets(db: Session, targets: list[TargetSpecIn], role: str) -> None:
    for t in targets:
        deployment = db.get(Deployment, t.deployment_id)
        if deployment is None:
            raise HTTPException(404, f"{role} deployment 不存在: {t.deployment_id}")
        if t.reasoning_profile_id:
            profile = db.get(ReasoningProfile, t.reasoning_profile_id)
            if profile is None or profile.deployment_id != t.deployment_id:
                raise HTTPException(404, f"{role} reasoning profile 无效: {t.reasoning_profile_id}")


@router.post("", status_code=201)
def create_run(body: RunCreate, db: Session = Depends(get_db)) -> dict:
    installation = db.get(DatasetInstallation, body.installation_id)
    if installation is None:
        raise HTTPException(404, "数据集安装不存在")
    suite = next((s for s in installation.suites if s["id"] == body.suite_id), None)
    if suite is None:
        raise HTTPException(404, f"suite 不存在: {body.suite_id}")
    _validate_targets(db, body.solvers, "solver")
    _validate_targets(db, body.judges, "judge")

    try:
        tasks = dataset_service.get_suite_tasks(db, installation, body.suite_id)
    except dataset_service.DatasetValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    # 冻结 config snapshot：Run 可复现性的核心
    snapshot_payload = {
        "suite_id": body.suite_id,
        "judge_prompt_version": prompts.JUDGE_PROMPT_VERSION,
        "aggregation_version": aggregation.AGGREGATION_VERSION,
        "solvers": [t.model_dump() for t in body.solvers],
        "judges": [t.model_dump() for t in body.judges],
        "task_revisions": {t.task_id: t.revision for t in tasks},
    }

    with session_scope() as session:
        agg_version = session.scalar(
            sa.select(AggregationVersion).where(
                AggregationVersion.name == "weighted-mean",
                AggregationVersion.version == aggregation.AGGREGATION_VERSION.split("/")[1],
            )
        )
        if agg_version is None:
            session.add(
                AggregationVersion(
                    name="weighted-mean",
                    version=aggregation.AGGREGATION_VERSION.split("/")[1],
                    definition={"description": "维度分按 rubric 满分归一化到 0~100；fatal_error 记 0"},
                )
            )
        snapshot = ConfigSnapshot(payload=snapshot_payload)
        session.add(snapshot)
        session.flush()

        run = Run(
            name=body.name,
            status="pending",
            framework_version=__version__,
            framework_commit=get_git_commit(),
            dataset_id=installation.dataset_id,
            dataset_version=installation.dataset_version,
            dataset_revision=installation.dataset_revision,
            manifest_hash=installation.manifest_hash,
            suite_id=body.suite_id,
            installation_id=installation.id,
            config_snapshot_id=snapshot.id,
        )
        session.add(run)
        session.flush()
        run_id = run.id

    run_manager.start_run(run_id)
    with session_scope() as session:
        return run_dict(session.get(Run, run_id), session)


@router.get("")
def list_runs(db: Session = Depends(get_db)) -> list[dict]:
    runs = db.scalars(sa.select(Run).order_by(Run.created_at.desc())).all()
    return [run_dict(r, db) for r in runs]


@router.get("/compare")
def compare_runs(ids: str, db: Session = Depends(get_db)) -> dict:
    """判定多个 Run 是否可直接比较。用法: /api/runs/compare?ids=id1,id2"""
    run_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if len(run_ids) < 2:
        raise HTTPException(400, "至少需要 2 个 run id")
    return analytics.compare_runs(db, run_ids)


@router.get("/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    return run_dict(run, db)


@router.get("/{run_id}/results")
def get_run_results(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    return analytics.run_results(db, run_id)


@router.get("/{run_id}/config-snapshot")
def get_run_config_snapshot(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    snapshot = db.get(ConfigSnapshot, run.config_snapshot_id)
    return {"run_id": run_id, "payload": snapshot.payload, "created_at": snapshot.created_at}


@router.get("/{run_id}/usage")
def get_run_usage(run_id: str, db: Session = Depends(get_db)) -> list[dict]:
    if db.get(Run, run_id) is None:
        raise HTTPException(404, "run 不存在")
    records = db.scalars(sa.select(UsageRecord).where(UsageRecord.run_id == run_id)).all()
    snapshots = {
        s.id: s
        for s in db.scalars(
            sa.select(PricingSnapshot).where(
                PricingSnapshot.id.in_([r.pricing_snapshot_id for r in records if r.pricing_snapshot_id])
            )
        )
    }
    return [
        {
            "id": r.id,
            "owner_type": r.owner_type,
            "owner_id": r.owner_id,
            "created_at": r.created_at,
            "input_tokens": r.input_tokens,
            "cached_input_tokens": r.cached_input_tokens,
            "output_tokens": r.output_tokens,
            "reasoning_tokens": r.reasoning_tokens,
            "cost": r.cost,
            "pricing": (
                {
                    "source": snapshots[r.pricing_snapshot_id].source,
                    "price_input_per_mtok": snapshots[r.pricing_snapshot_id].price_input_per_mtok,
                    "price_output_per_mtok": snapshots[r.pricing_snapshot_id].price_output_per_mtok,
                    "captured_at": snapshots[r.pricing_snapshot_id].captured_at,
                }
                if r.pricing_snapshot_id in snapshots
                else None
            ),
        }
        for r in records
    ]


@router.post("/{run_id}/cancel", status_code=202)
def cancel_run(run_id: str, db: Session = Depends(get_db)) -> dict:
    """取消正在运行/等待中的 Run。已完成的执行记录保留，进行中的置为 cancelled。"""
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    if run.status not in ("pending", "running"):
        raise HTTPException(409, f"run 已处于终态: {run.status}")
    if not run_manager.cancel(run_id):
        # 后台任务不在（如服务重启后遗留的 running 状态）：直接标记取消
        run.status = "cancelled"
        from ..services.runner import utcnow

        run.finished_at = utcnow()
        db.commit()
    return {"run_id": run_id, "status": "cancelling"}


@router.post("/{run_id}/rejudge", status_code=202)
def rejudge(run_id: str, body: RejudgeRequest, db: Session = Depends(get_db)) -> dict:
    """对历史 Solver 回答重新评分。只新增 JudgeExecution，原始记录不变。"""
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "run 不存在")
    if run_manager.is_active(run_id):
        raise HTTPException(409, "该 run 当前有正在执行的任务")

    if body.solver_execution_ids:
        solver_execution_ids = body.solver_execution_ids
        count = db.scalar(
            sa.select(sa.func.count())
            .select_from(SolverExecution)
            .where(
                SolverExecution.id.in_(solver_execution_ids),
                SolverExecution.run_id == run_id,
                SolverExecution.status == "completed",
            )
        )
        if count != len(solver_execution_ids):
            raise HTTPException(400, "存在不属于该 run 或未完成的 solver_execution_id")
    else:
        solver_execution_ids = [
            row.id
            for row in db.scalars(
                sa.select(SolverExecution).where(
                    SolverExecution.run_id == run_id, SolverExecution.status == "completed"
                )
            )
        ]
    if not solver_execution_ids:
        raise HTTPException(400, "没有可重新评分的 solver execution")

    if body.judges:
        _validate_targets(db, body.judges, "judge")
        judge_entries = [t.model_dump() for t in body.judges]
    else:
        snapshot = db.get(ConfigSnapshot, run.config_snapshot_id)
        judge_entries = snapshot.payload["judges"]
        _validate_targets(db, [TargetSpecIn(**e) for e in judge_entries], "judge")

    run_manager.start_rejudge(run_id, solver_execution_ids, judge_entries)
    return {"run_id": run_id, "solver_executions": len(solver_execution_ids), "status": "rejudging"}
