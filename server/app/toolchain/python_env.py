"""Python 解释器与虚拟环境供给（经 uv，无需用户预装 Python）。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from ..config import settings
from .errors import ToolchainUnavailable
from .uv_tool import ensure_uv

logger = logging.getLogger(__name__)


def _run(args: list[str], *, cwd: Path | None = None, timeout_s: float = 600.0) -> subprocess.CompletedProcess:
    logger.debug("执行: %s (cwd=%s)", " ".join(str(a) for a in args), cwd)
    try:
        return subprocess.run(
            [str(a) for a in args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolchainUnavailable(f"命令超时（{timeout_s}s）: {args[0]}") from exc
    except OSError as exc:
        raise ToolchainUnavailable(f"命令无法执行: {args[0]}: {exc}") from exc


def ensure_python(version_spec: str) -> Path:
    """供给指定版本的 Python 解释器，返回可执行文件路径。

    优先经 uv 下载独立构建（uv python install）；失败时回退到匹配版本的系统解释器。
    """
    uv = ensure_uv()
    # uv python install 幂等：已安装时直接返回
    result = _run([uv, "python", "install", version_spec], timeout_s=settings.toolchain_download_timeout_s)
    if result.returncode == 0:
        found = _run([uv, "python", "find", version_spec])
        if found.returncode == 0 and found.stdout.strip():
            path = Path(found.stdout.strip())
            if path.is_file():
                return path
    logger.warning("uv 无法供给 Python %s（%s），尝试系统解释器", version_spec, result.stderr.strip()[:200])

    for candidate in (f"python{version_spec}", "python3", "python", sys.executable):
        try:
            check = subprocess.run(
                [candidate, "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if check.returncode == 0 and check.stdout.strip() == version_spec.strip():
            which = subprocess.run(
                [candidate, "-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if which.returncode == 0 and which.stdout.strip():
                logger.info("回退到系统 Python %s: %s", version_spec, which.stdout.strip())
                return Path(which.stdout.strip())
    raise ToolchainUnavailable(f"无法供给 Python {version_spec}")


def venv_python(venv_dir: Path) -> Path:
    """跨平台返回 venv 内 python 路径。"""
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def create_venv(python_path: Path, venv_dir: Path) -> Path:
    """在 venv_dir 创建虚拟环境，返回 venv 内 python 路径。"""
    uv = ensure_uv()
    result = _run([uv, "venv", "--python", str(python_path), str(venv_dir)])
    py = venv_python(venv_dir)
    if result.returncode != 0 or not py.is_file():
        raise ToolchainUnavailable(f"创建 venv 失败: {result.stderr.strip()[:300]}")
    return py


def pip_install(venv_py: Path, args: list[str], *, cwd: Path | None = None) -> None:
    """向 venv 安装依赖（经 uv，利用其全局缓存加速重复安装）。"""
    uv = ensure_uv()
    env = os.environ.copy()
    # uv pip 需要知道目标环境
    env["VIRTUAL_ENV"] = str(venv_py.parent.parent)
    result = _run(
        [uv, "pip", "install", "--python", str(venv_py), *args],
        cwd=cwd,
        timeout_s=settings.verify_step_timeout_s,
    )
    if result.returncode != 0:
        raise ToolchainUnavailable(f"依赖安装失败（{' '.join(args)}）: {result.stderr.strip()[:300]}")
