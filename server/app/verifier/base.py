"""verifier 基础类型与子进程执行工具。"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

LOG_TAIL_LIMIT = 4000


@dataclass
class VerifyOutcome:
    """一次程序判定的结果（原始事实）。"""

    status: str  # completed / failed / unavailable
    verifier_kind: str  # swe_issue / algorithm
    facts: dict = field(default_factory=dict)  # 注入 judge prompt 的判定事实
    environment: dict = field(default_factory=dict)  # 工具链版本等可复现信息
    log_tail: str = ""
    error: str | None = None


@dataclass
class CommandResult:
    returncode: int | None  # None = 超时被杀
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_s: float = 600.0,
    env: dict[str, str] | None = None,
    stdin_text: str | None = None,
) -> CommandResult:
    """同步执行命令，捕获输出与耗时；超时杀进程并标记。"""
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(a) for a in args],
            cwd=cwd,
            env=env,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration_s=time.perf_counter() - start,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=None,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            duration_s=time.perf_counter() - start,
            timed_out=True,
        )
    except OSError as exc:
        return CommandResult(
            returncode=None,
            stdout="",
            stderr=f"无法执行: {exc}",
            duration_s=time.perf_counter() - start,
        )


def tail(text: str, limit: int = LOG_TAIL_LIMIT) -> str:
    """日志截断（保留尾部，前部信息量低）。"""
    if len(text) <= limit:
        return text
    return f"...（前部省略 {len(text) - limit} 字符）\n" + text[-limit:]
