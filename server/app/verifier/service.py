"""verifier 分发入口：按任务的 verify 契约形状选择判定器。"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import settings
from .base import VerifyOutcome

logger = logging.getLogger(__name__)


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

    if verify.get("base_commit") and evaluation.get("fail_to_pass") is not None:
        from .swe import verify_swe

        return verify_swe(task_dir, verify, response_text, work_dir)
    if evaluation.get("harness") or evaluation.get("reference_solution"):
        from .algorithm import verify_algorithm

        return verify_algorithm(task_dir, verify, response_text, work_dir)

    logger.info("task %s 的 verify 契约无法识别，跳过程序判定", task_payload.get("id"))
    return None
