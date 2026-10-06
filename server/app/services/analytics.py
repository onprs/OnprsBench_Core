"""派生指标：Run 结果聚合、Judge 分歧、时间序列、Run 可比性。

全部为 derived data：从 immutable raw facts 实时计算，不写回覆盖原始记录。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..models import (
    ConfigSnapshot,
    JudgeExecution,
    Run,
    SolverExecution,
    UsageRecord,
    VerifierExecution,
)

# Judge 总分标准差超过该阈值即标记 High Judge Disagreement
HIGH_DISAGREEMENT_STDDEV = 15.0


@dataclass
class JudgeSummary:
    mean: float | None
    median: float | None
    stddev: float | None
    min: float | None
    max: float | None
    count: int
    high_disagreement: bool


def summarize_judge_totals(totals: list[float]) -> JudgeSummary:
    """对同一 solver execution 的多个 Judge 总分做聚合统计。"""
    if not totals:
        return JudgeSummary(None, None, None, None, None, 0, False)
    return JudgeSummary(
        mean=statistics.fmean(totals),
        median=statistics.median(totals),
        stddev=statistics.stdev(totals) if len(totals) >= 2 else 0.0,
        min=min(totals),
        max=max(totals),
        count=len(totals),
        high_disagreement=len(totals) >= 2 and statistics.stdev(totals) > HIGH_DISAGREEMENT_STDDEV,
    )


def run_results(session: Session, run_id: str) -> dict:
    """单个 Run 的完整结果视图：按 Evaluation Target 聚合 + 逐 task 明细。"""
    run = session.get(Run, run_id)
    solver_executions = session.scalars(
        sa.select(SolverExecution).where(SolverExecution.run_id == run_id)
    ).all()
    judge_executions = session.scalars(
        sa.select(JudgeExecution).where(JudgeExecution.run_id == run_id)
    ).all()
    usage_records = session.scalars(sa.select(UsageRecord).where(UsageRecord.run_id == run_id)).all()

    judges_by_solver_execution: dict[str, list[JudgeExecution]] = {}
    for je in judge_executions:
        judges_by_solver_execution.setdefault(je.solver_execution_id, []).append(je)

    # 每个 solver execution 最近一次程序判定（可能不存在：任务无 verify 契约时）
    verifier_rows = session.scalars(
        sa.select(VerifierExecution).where(VerifierExecution.run_id == run_id)
    ).all()
    # completed 判定优先（与 Judge 取用的判定事实一致），其次取最近一次
    verifier_by_solver_execution: dict[str, VerifierExecution] = {}
    for ve in sorted(
        verifier_rows,
        key=lambda r: (r.status == "completed", r.started_at or run.created_at),
    ):
        verifier_by_solver_execution[ve.solver_execution_id] = ve

    cost_by_owner: dict[str, float | None] = {}
    for record in usage_records:
        # 任一记录价格未知 → 该 owner 成本未知（None），不静默记 0
        prev = cost_by_owner.get(record.owner_id, 0.0)
        cost_by_owner[record.owner_id] = None if prev is None or record.cost is None else prev + record.cost

    # 按 Evaluation Target（deployment × profile）分组
    targets: dict[str, dict] = {}
    for se in solver_executions:
        key = f"{se.deployment_id}:{se.reasoning_profile_id or ''}"
        target = targets.setdefault(
            key,
            {
                "deployment_id": se.deployment_id,
                "reasoning_profile_id": se.reasoning_profile_id,
                "label": se.deployment_label + (f" · {se.profile_name}" if se.profile_name else ""),
                "entries": [],
            },
        )
        judges = judges_by_solver_execution.get(se.id, [])
        totals = [j.weighted_total for j in judges if j.weighted_total is not None]
        summary = summarize_judge_totals(totals)
        verifier = verifier_by_solver_execution.get(se.id)
        raw = se.raw_response_json or {}
        target["entries"].append(
            {
                "solver_execution_id": se.id,
                "task_id": se.task_id,
                "task_revision": se.task_revision,
                "status": se.status,
                "response_text": se.response_text,
                "error": se.error,
                "total_latency_s": se.total_latency_s,
                "cost": cost_by_owner.get(se.id, 0.0),
                # 花费轮次与截断事实（Solver 侧）
                "turns": se.turns,
                "finish_reason": se.finish_reason,
                "truncated": bool(se.truncated),
                "solver_mode": raw.get("solver_mode"),
                "verifier": (
                    {
                        "verifier_execution_id": verifier.id,
                        "kind": verifier.verifier_kind,
                        "status": verifier.status,
                        "facts": verifier.facts_json,
                        "wall_time_s": verifier.wall_time_s,
                        "error": verifier.error,
                    }
                    if verifier
                    else None
                ),
                "judge_score_summary": summary.__dict__,
                "judges": [
                    {
                        "judge_execution_id": j.id,
                        "judge_label": j.deployment_label + (f" · {j.profile_name}" if j.profile_name else ""),
                        "status": j.status,
                        "parse_ok": j.parse_ok,
                        "fatal_error": j.fatal_error,
                        "dimension_scores": j.dimension_scores,
                        "weighted_total": j.weighted_total,
                        "rubric_version": j.rubric_version,
                        "aggregation_version": j.aggregation_version,
                        "summary": j.judge_summary,
                        "key_errors": j.key_errors,
                        "total_latency_s": j.total_latency_s,
                        "cost": cost_by_owner.get(j.id, 0.0),
                        "error": j.error,
                    }
                    for j in judges
                ],
            }
        )

    target_summaries = []
    for target in targets.values():
        entries = target["entries"]
        means = [e["judge_score_summary"]["mean"] for e in entries if e["judge_score_summary"]["mean"] is not None]
        latencies = [e["total_latency_s"] for e in entries if e["total_latency_s"] is not None]
        turns = [e["turns"] for e in entries if e["turns"] is not None]
        target_summaries.append(
            {
                "deployment_id": target["deployment_id"],
                "reasoning_profile_id": target["reasoning_profile_id"],
                "label": target["label"],
                "task_count": len(entries),
                "completed_count": sum(1 for e in entries if e["status"] == "completed"),
                "score_mean": statistics.fmean(means) if means else None,
                "total_cost": (
                    None
                    if any(e["cost"] is None for e in entries)
                    else sum(e["cost"] for e in entries)
                ),
                "latency_mean_s": statistics.fmean(latencies) if latencies else None,
                # 花费轮次对比字段：平均轮次 / 总轮次 / 被截断的任务数
                "turns_mean": statistics.fmean(turns) if turns else None,
                "turns_total": sum(turns) if turns else None,
                "truncated_count": sum(1 for e in entries if e["truncated"]),
            }
        )

    return {
        "run_id": run_id,
        "status": run.status,
        "targets": target_summaries,
        "entries_by_target": {
            f"{t['deployment_id']}:{t['reasoning_profile_id'] or ''}": targets[
                f"{t['deployment_id']}:{t['reasoning_profile_id'] or ''}"
            ]["entries"]
            for t in target_summaries
        },
        "wall_time": {
            "solver_s": run.solver_wall_time_s,
            "verifier_s": run.verifier_wall_time_s,
            "judge_s": run.judge_wall_time_s,
            "total_s": run.total_wall_time_s,
        },
        "cost": {"solver": run.solver_cost, "judge": run.judge_cost, "total": run.total_cost},
    }


def timeseries(session: Session, dataset_id: str | None = None) -> list[dict]:
    """Score / Cost / Latency / Turns over time：按 Run × Solver Target 出点。"""
    query = sa.select(Run).where(Run.status == "completed").order_by(Run.created_at)
    if dataset_id:
        query = query.where(Run.dataset_id == dataset_id)
    runs = session.scalars(query).all()

    points: list[dict] = []
    for run in runs:
        results = run_results(session, run.id)
        for target in results["targets"]:
            points.append(
                {
                    "run_id": run.id,
                    "run_name": run.name,
                    "run_at": run.created_at,
                    "dataset_id": run.dataset_id,
                    "suite_id": run.suite_id,
                    "deployment_id": target["deployment_id"],
                    "reasoning_profile_id": target["reasoning_profile_id"],
                    "label": target["label"],
                    "score_mean": target["score_mean"],
                    "total_cost": target["total_cost"],
                    "latency_mean_s": target["latency_mean_s"],
                    "turns_mean": target["turns_mean"],
                    "truncated_count": target["truncated_count"],
                }
            )
    return points


@dataclass
class ComparabilityVerdict:
    comparable: bool
    reasons: list[str] = field(default_factory=list)


def compare_runs(session: Session, run_ids: list[str]) -> dict:
    """判断多个历史 Run 是否可直接比较，并给出原因。"""
    runs = [session.get(Run, rid) for rid in run_ids]
    if any(r is None for r in runs):
        missing = [rid for rid, r in zip(run_ids, runs) if r is None]
        return {"comparable": False, "reasons": [f"run 不存在: {rid}" for rid in missing]}

    reasons: list[str] = []

    def check(field: str, label: str) -> None:
        values = {getattr(r, field) for r in runs}
        if len(values) > 1:
            reasons.append(f"{label} 不一致: {sorted(values)}")

    check("dataset_id", "dataset id")
    check("dataset_version", "dataset version")
    check("dataset_revision", "dataset revision")
    check("manifest_hash", "dataset manifest hash")
    check("suite_id", "suite")
    check("framework_version", "framework version")

    # 冻结的评分口径一致性：judge prompt 或聚合算法变化时，分数不可直接比较；
    # Solver / Judge 目标不同属于正常对比场景（跨 target 比较），不作为不可比原因
    def snapshot_of(run: Run) -> dict:
        snapshot = session.get(ConfigSnapshot, run.config_snapshot_id)
        return snapshot.payload if snapshot else {}

    def check_snapshot(field_name: str, label: str) -> None:
        values = {
            json.dumps(snapshot_of(r).get(field_name), sort_keys=True, ensure_ascii=False)
            for r in runs
        }
        if len(values) > 1:
            reasons.append(f"{label} 不一致")

    check_snapshot("judge_prompt_version", "judge prompt 版本")
    check_snapshot("aggregation_version", "聚合算法版本")

    # task revision 集合一致性
    revision_sets = []
    for r in runs:
        rows = session.execute(
            sa.select(SolverExecution.task_id, SolverExecution.task_revision)
            .where(SolverExecution.run_id == r.id)
            .distinct()
        ).all()
        revision_sets.append({(row.task_id, row.task_revision) for row in rows})
    if len({frozenset(s) for s in revision_sets}) > 1:
        reasons.append("task revision 集合不一致")

    return {
        "comparable": not reasons,
        "verdict": "Comparable" if not reasons else "Not Directly Comparable",
        "reasons": reasons,
    }
