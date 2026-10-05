"""Benchmark 编排：Solver 并发执行 → 多 Judge 并行评分。

不变量：
- 原始事实（prompt/response/raw usage/pricing snapshot/timestamps）只写一次，绝不覆盖。
- Judge 并发执行的总耗时按 wall time 记录，不把各 Judge latency 相加。
- 重新 Judge 只新增 JudgeExecution，不触碰 Solver 原始记录。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa

from ..config import settings
from ..db import SessionLocal, session_scope
from ..models import (
    Deployment,
    JudgeExecution,
    PricingSnapshot,
    Provider,
    ReasoningProfile,
    Run,
    SolverExecution,
    TaskCache,
    UsageRecord,
)
from ..runtime.base import ModelRequest
from ..runtime.factory import build_client
from . import aggregation, datasets, prompts
from .pricing import ResolvedPricing, compute_cost, resolve_pricing

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class TargetSpec:
    """一个 Solver 或 Judge 目标（Deployment × ReasoningProfile）。"""

    deployment: Deployment
    provider: Provider
    profile: ReasoningProfile | None

    @property
    def label(self) -> str:
        # Deployment 名称是用户赋予的 Evaluation Target 身份，如 “Kimi K3 · Official”
        return self.deployment.name

    def build_request(self, messages: list[dict[str, str]]) -> ModelRequest:
        p = self.profile
        return ModelRequest(
            messages=messages,
            reasoning_effort=p.reasoning_effort if p else None,
            reasoning_budget=p.reasoning_budget if p else None,
            max_output_tokens=p.max_output_tokens if p else None,
            temperature=p.temperature if p else None,
            top_p=p.top_p if p else None,
            seed=p.seed if p else None,
            provider_params=p.provider_params if p and p.provider_params else {},
            timeout_s=settings.llm_timeout_s,
        )


class PricingRegistry:
    """Run 级价格快照登记：每个 Deployment 每次 Run 固化一次。"""

    def __init__(self) -> None:
        self._by_deployment: dict[str, tuple[str, ResolvedPricing]] = {}

    def ensure(self, spec: TargetSpec) -> None:
        dep_id = spec.deployment.id
        if dep_id in self._by_deployment:
            return
        resolved = resolve_pricing(spec.deployment, spec.provider)
        with session_scope() as session:
            snapshot = PricingSnapshot(
                deployment_id=dep_id,
                source=resolved.source,
                price_input_per_mtok=resolved.price_input_per_mtok,
                price_output_per_mtok=resolved.price_output_per_mtok,
                price_cached_input_per_mtok=resolved.price_cached_input_per_mtok,
                currency=resolved.currency,
                raw_json=resolved.raw,
            )
            session.add(snapshot)
            session.flush()
            self._by_deployment[dep_id] = (snapshot.id, resolved)

    def get(self, deployment_id: str) -> tuple[str, ResolvedPricing] | None:
        return self._by_deployment.get(deployment_id)


def _record_usage(
    *,
    run_id: str,
    owner_type: str,
    owner_id: str,
    usage,
    pricing: tuple[str, ResolvedPricing] | None,
) -> float | None:
    """写入 UsageRecord（含 pricing snapshot 引用与成本），返回 cost。"""
    snapshot_id, resolved = (pricing if pricing else (None, None))
    cost = None
    if resolved is not None:
        cost = compute_cost(
            resolved,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
        )
    with session_scope() as session:
        session.add(
            UsageRecord(
                run_id=run_id,
                owner_type=owner_type,
                owner_id=owner_id,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                pricing_snapshot_id=snapshot_id,
                cost=cost,
            )
        )
    return cost


async def _run_solver_call(
    run_id: str,
    spec: TargetSpec,
    task: TaskCache,
    pricing: PricingRegistry,
    semaphore: asyncio.Semaphore,
) -> str:
    """执行一次 Solver 调用并落库，返回 solver_execution_id。"""
    async with semaphore:
        messages = prompts.build_solver_messages(task.payload)
        with session_scope() as session:
            execution = SolverExecution(
                run_id=run_id,
                task_cache_id=task.id,
                task_id=task.task_id,
                task_revision=task.revision,
                task_hash=task.task_hash,
                deployment_id=spec.deployment.id,
                reasoning_profile_id=spec.profile.id if spec.profile else None,
                deployment_label=spec.label,
                profile_name=spec.profile.name if spec.profile else None,
                status="running",
                started_at=utcnow(),
                prompt_json=messages,
            )
            session.add(execution)
            session.flush()
            execution_id = execution.id

        client = build_client(spec.deployment, spec.provider)
        try:
            result = await client.complete(spec.build_request(messages))
        except Exception as exc:
            logger.warning("solver 调用失败 run=%s task=%s: %s", run_id, task.task_id, exc)
            with session_scope() as session:
                row = session.get(SolverExecution, execution_id)
                row.status = "failed"
                row.finished_at = utcnow()
                row.error = str(exc)
            return execution_id

        with session_scope() as session:
            row = session.get(SolverExecution, execution_id)
            row.status = "completed"
            row.finished_at = result.finished_at
            row.started_at = result.started_at
            row.ttft_s = result.ttft_s
            row.generation_time_s = result.generation_time_s
            row.total_latency_s = result.total_latency_s
            row.response_text = result.text
            row.raw_response_json = result.raw_response

        _record_usage(
            run_id=run_id,
            owner_type="solver",
            owner_id=execution_id,
            usage=result.usage,
            pricing=pricing.get(spec.deployment.id),
        )
        return execution_id


async def _run_judge_call(
    run_id: str,
    spec: TargetSpec,
    solver_execution_id: str,
    pricing: PricingRegistry,
    semaphore: asyncio.Semaphore,
) -> str:
    """执行一次 Judge 调用并落库，返回 judge_execution_id。"""
    async with semaphore:
        with session_scope() as session:
            solver_execution = session.get(SolverExecution, solver_execution_id)
            task = session.get(TaskCache, solver_execution.task_cache_id)
            task_payload = task.payload
            candidate = solver_execution.response_text or ""
            rubric = task_payload["judge_visible"]["rubric"]
            messages = prompts.build_judge_messages(task_payload, candidate)

            execution = JudgeExecution(
                run_id=run_id,
                solver_execution_id=solver_execution_id,
                deployment_id=spec.deployment.id,
                reasoning_profile_id=spec.profile.id if spec.profile else None,
                deployment_label=spec.label,
                profile_name=spec.profile.name if spec.profile else None,
                status="running",
                started_at=utcnow(),
                rubric_version=rubric["version"],
                prompt_json=messages,
            )
            session.add(execution)
            session.flush()
            execution_id = execution.id

        client = build_client(spec.deployment, spec.provider)
        try:
            result = await client.complete(spec.build_request(messages))
        except Exception as exc:
            logger.warning("judge 调用失败 run=%s solver_execution=%s: %s", run_id, solver_execution_id, exc)
            with session_scope() as session:
                row = session.get(JudgeExecution, execution_id)
                row.status = "failed"
                row.finished_at = utcnow()
                row.error = str(exc)
            return execution_id

        parsed = aggregation.parse_judge_output(result.text, rubric)
        total = aggregation.weighted_total(parsed, rubric)

        with session_scope() as session:
            row = session.get(JudgeExecution, execution_id)
            row.status = "completed"
            row.finished_at = result.finished_at
            row.started_at = result.started_at
            row.total_latency_s = result.total_latency_s
            row.raw_output_text = result.text
            row.raw_response_json = result.raw_response
            row.parse_ok = parsed.parse_ok
            row.fatal_error = parsed.fatal_error
            row.dimension_scores = parsed.dimensions if parsed.parse_ok else None
            row.judge_summary = parsed.summary
            row.key_errors = parsed.key_errors if parsed.parse_ok else None
            row.aggregation_version = aggregation.AGGREGATION_VERSION if parsed.parse_ok else None
            row.weighted_total = total
            if not parsed.parse_ok:
                row.error = parsed.error

        _record_usage(
            run_id=run_id,
            owner_type="judge",
            owner_id=execution_id,
            usage=result.usage,
            pricing=pricing.get(spec.deployment.id),
        )
        return execution_id


def _load_target_specs(session, snapshot_payload: dict, key: str) -> list[TargetSpec]:
    """按 config snapshot 加载 Solver/Judge 目标（eager load，供跨会话使用）。"""
    specs: list[TargetSpec] = []
    for entry in snapshot_payload[key]:
        deployment = session.scalar(
            sa.select(Deployment)
            .options(sa.orm.joinedload(Deployment.model), sa.orm.joinedload(Deployment.provider))
            .where(Deployment.id == entry["deployment_id"])
        )
        if deployment is None:
            raise RuntimeError(f"Deployment 不存在: {entry['deployment_id']}")
        provider = deployment.provider
        profile = (
            session.get(ReasoningProfile, entry["reasoning_profile_id"])
            if entry.get("reasoning_profile_id")
            else None
        )
        specs.append(TargetSpec(deployment=deployment, provider=provider, profile=profile))
    return specs


def _recompute_run_costs(session, run_id: str) -> None:
    """从 usage_records 重算 run 的成本派生值。任一记录价格未知则该组成记 None。"""
    records = session.scalars(sa.select(UsageRecord).where(UsageRecord.run_id == run_id)).all()

    def component(owner_type: str) -> float | None:
        rows = [r for r in records if r.owner_type == owner_type]
        if any(r.cost is None for r in rows):
            return None
        return sum(r.cost or 0.0 for r in rows)

    solver_cost = component("solver")
    judge_cost = component("judge")
    run = session.get(Run, run_id)
    run.solver_cost = solver_cost
    run.judge_cost = judge_cost
    run.total_cost = None if solver_cost is None or judge_cost is None else solver_cost + judge_cost


async def _execute_run(run_id: str) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        run.status = "running"
        run.started_at = utcnow()

    try:
        run_t0 = time.perf_counter()

        with session_scope() as session:
            run = session.get(Run, run_id)
            snapshot_payload = run_snapshot_payload(session, run)
            installation = session.get(datasets.DatasetInstallation, run.installation_id)
            tasks = datasets.get_suite_tasks(session, installation, run.suite_id)
            solvers = _load_target_specs(session, snapshot_payload, "solvers")
            judges = _load_target_specs(session, snapshot_payload, "judges")

        pricing = PricingRegistry()
        for spec in solvers + judges:
            pricing.ensure(spec)

        # Solver 阶段
        solver_t0 = time.perf_counter()
        solver_sem = asyncio.Semaphore(settings.solver_concurrency)
        solver_execution_ids = await asyncio.gather(
            *(_run_solver_call(run_id, spec, task, pricing, solver_sem) for spec in solvers for task in tasks)
        )
        solver_wall = time.perf_counter() - solver_t0

        # Judge 阶段：同一 solver execution 的全部 judge 并行，全局信号量限流
        judge_t0 = time.perf_counter()
        judge_sem = asyncio.Semaphore(settings.solver_concurrency * 2)
        with session_scope() as session:
            completed_ids = [
                row.id
                for row in session.scalars(
                    sa.select(SolverExecution).where(
                        SolverExecution.run_id == run_id,
                        SolverExecution.status == "completed",
                    )
                )
            ]
        await asyncio.gather(
            *(
                _run_judge_call(run_id, judge_spec, se_id, pricing, judge_sem)
                for se_id in completed_ids
                for judge_spec in judges
            )
        )
        judge_wall = time.perf_counter() - judge_t0

        with session_scope() as session:
            run = session.get(Run, run_id)
            run.status = "completed"
            run.finished_at = utcnow()
            run.solver_wall_time_s = solver_wall
            run.judge_wall_time_s = judge_wall
            run.total_wall_time_s = time.perf_counter() - run_t0
            _recompute_run_costs(session, run_id)

        logger.info(
            "run 完成 id=%s solver=%d judge=%d wall=%.2fs",
            run_id,
            len(solver_execution_ids),
            len(completed_ids) * len(judges),
            time.perf_counter() - run_t0,
        )
    except asyncio.CancelledError:
        logger.info("run 被取消 id=%s", run_id)
        with session_scope() as session:
            run = session.get(Run, run_id)
            run.status = "cancelled"
            run.finished_at = utcnow()
            _cancel_running_executions(session, run_id)
    except Exception as exc:
        logger.exception("run 失败 id=%s", run_id)
        with session_scope() as session:
            run = session.get(Run, run_id)
            run.status = "failed"
            run.finished_at = utcnow()
            run.error = str(exc)


def _cancel_running_executions(session, run_id: str) -> None:
    """把该 Run 下仍处于 running/pending 的执行记录置为 cancelled。"""
    for model_cls in (SolverExecution, JudgeExecution):
        rows = session.scalars(
            sa.select(model_cls).where(
                model_cls.run_id == run_id,
                model_cls.status.in_(["pending", "running"]),
            )
        ).all()
        for row in rows:
            row.status = "cancelled"
            row.finished_at = utcnow()


async def _execute_rejudge(run_id: str, solver_execution_ids: list[str], judge_entries: list[dict]) -> None:
    """对历史 Solver 回答重新评分：只新增 JudgeExecution，不动 Solver 原始记录。"""
    try:
        with session_scope() as session:
            run = session.get(Run, run_id)
            snapshot_payload = {"judges": judge_entries}
            judges = _load_target_specs(session, snapshot_payload, "judges")

        pricing = PricingRegistry()
        for spec in judges:
            pricing.ensure(spec)

        judge_sem = asyncio.Semaphore(settings.solver_concurrency * 2)
        await asyncio.gather(
            *(
                _run_judge_call(run_id, judge_spec, se_id, pricing, judge_sem)
                for se_id in solver_execution_ids
                for judge_spec in judges
            )
        )
        with session_scope() as session:
            _recompute_run_costs(session, run_id)
    except asyncio.CancelledError:
        logger.info("rejudge 被取消 run=%s", run_id)
        with session_scope() as session:
            _cancel_running_executions(session, run_id)


def run_snapshot_payload(session, run: Run) -> dict:
    from ..models import ConfigSnapshot

    snapshot = session.get(ConfigSnapshot, run.config_snapshot_id)
    return snapshot.payload


class RunManager:
    """Run 生命周期管理。任务统一提交到应用主事件循环（lifespan 绑定时捕获）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Future | concurrent.futures.Future] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def is_active(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    def cancel(self, run_id: str) -> bool:
        """请求取消正在执行的任务。返回是否成功发出取消。"""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        return task.cancel()

    def _submit(self, run_id: str, coro) -> None:
        if self.is_active(run_id):
            raise RuntimeError(f"run 已在执行中: {run_id}")
        if self._loop is None or not self._loop.is_running():
            raise RuntimeError("事件循环未绑定，无法启动后台任务")
        # 路由 handler 运行在 threadpool，需线程安全地提交到主事件循环
        self._tasks[run_id] = asyncio.run_coroutine_threadsafe(coro, self._loop)

    def start_run(self, run_id: str) -> None:
        self._submit(run_id, _execute_run(run_id))

    def start_rejudge(self, run_id: str, solver_execution_ids: list[str], judge_entries: list[dict]) -> None:
        self._submit(run_id, _execute_rejudge(run_id, solver_execution_ids, judge_entries))


run_manager = RunManager()
