"""verifier 分发入口：按任务的 verify 契约形状选择判定器。

判定整体受 settings.verify_timeout_s 约束（安装、编译、测试全部计入），
超时返回 failed 判定事实，不阻塞 Run。solver_meta（轮次/截断）会附加到
判定事实，供 judge 区分“模型答错”与“输出被截断”。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from .base import VerifyOutcome, VerifyTimeout, deadline_at

logger = logging.getLogger(__name__)


def verifier_kind(task_payload: dict) -> str:
    """按契约形状返回判定器类型（swe_issue / algorithm）。"""
    verify = task_payload.get("verify") or {}
    evaluation = verify.get("evaluation") or {}
    if verify.get("base_commit") and evaluation.get("fail_to_pass") is not None:
        return "swe_issue"
    return "algorithm"


def has_verify_contract(task_payload: dict) -> bool:
    """任务是否声明了程序判定契约。"""
    verify = task_payload.get("verify")
    if not isinstance(verify, dict):
        return False
    evaluation = verify.get("evaluation") or {}
    return bool(
        evaluation.get("fail_to_pass") is not None and verify.get("base_commit")
        or evaluation.get("harness")
        or evaluation.get("reference_solution")
    )


def verify_task(
    *,
    task_payload: dict,
    task_dir: Path,
    response_text: str,
    work_dir: Path,
    solver_meta: dict | None = None,
) -> VerifyOutcome | None:
    """执行任务程序判定。任务无 verify 契约时返回 None。

    工具链无法供给时抛 ToolchainUnavailable，由调用方降级处理。
    """
    if not settings.verifier_enabled:
        return None
    verify = task_payload.get("verify")
    if not isinstance(verify, dict):
        return None
    evaluation = verify.get("evaluation") or {}
    kind = verifier_kind(task_payload)
    deadline = deadline_at(settings.verify_timeout_s)

    try:
        if verify.get("base_commit") and evaluation.get("fail_to_pass") is not None:
            from .swe import verify_swe

            outcome = verify_swe(task_dir, verify, response_text, work_dir, deadline_at=deadline)
        elif evaluation.get("harness") or evaluation.get("reference_solution"):
            from .algorithm import verify_algorithm

            outcome = verify_algorithm(task_dir, verify, response_text, work_dir, deadline_at=deadline)
        else:
            logger.info("task %s 的 verify 契约无法识别，跳过程序判定", task_payload.get("id"))
            return None
    except VerifyTimeout as exc:
        logger.warning("程序判定整体超时 task=%s: %s", task_payload.get("id"), exc)
        return VerifyOutcome("failed", kind, {}, {}, error=str(exc)).with_solver_meta(solver_meta)

    return outcome.with_solver_meta(solver_meta)
