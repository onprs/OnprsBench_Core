"""框架版本信息。每次 Run 会冻结 version 与 git commit。"""

from __future__ import annotations

import subprocess
from pathlib import Path

__version__ = "0.1.0"


def get_git_commit() -> str:
    """尽力获取当前 git commit；非 git 环境返回 'unknown'。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"
