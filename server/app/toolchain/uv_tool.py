"""uv 自举：优先复用本框架自带副本，其次系统 PATH，最后自动下载。

uv 是单文件静态二进制，负责后续全部 Python 解释器供给与依赖安装。
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import stat
import subprocess
from pathlib import Path

from ..config import settings
from .download import download_file, extract_tar_gz, extract_zip
from .errors import ToolchainUnavailable

logger = logging.getLogger(__name__)

# uv GitHub releases 的各平台资产名（latest/download 永远指向最新稳定版）
_UV_ASSETS = {
    ("Windows", "AMD64"): ("uv-x86_64-pc-windows-msvc.zip", "zip"),
    ("Linux", "x86_64"): ("uv-x86_64-unknown-linux-gnu.tar.gz", "tar.gz"),
    ("Darwin", "x86_64"): ("uv-x86_64-apple-darwin.tar.gz", "tar.gz"),
    ("Darwin", "arm64"): ("uv-aarch64-apple-darwin.tar.gz", "tar.gz"),
}
_UV_RELEASE_BASE = "https://github.com/astral-sh/uv/releases/latest/download"


def _uv_binary_name() -> str:
    return "uv.exe" if os.name == "nt" else "uv"


def _managed_uv_dir() -> Path:
    return settings.toolchain_dir / "uv"


def _run_uv_check(binary: Path) -> bool:
    try:
        subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            timeout=30,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_uv() -> Path:
    """返回可用的 uv 可执行文件路径。无法供给时抛 ToolchainUnavailable。"""
    managed = _managed_uv_dir() / _uv_binary_name()
    if managed.is_file() and _run_uv_check(managed):
        return managed

    on_path = shutil.which("uv")
    if on_path and _run_uv_check(Path(on_path)):
        logger.info("复用系统 uv: %s", on_path)
        return Path(on_path)

    key = (platform.system(), platform.machine())
    asset = _UV_ASSETS.get(key)
    if asset is None:
        raise ToolchainUnavailable(f"当前平台无预置 uv 资产: {key[0]}/{key[1]}")
    filename, kind = asset
    url = f"{_UV_RELEASE_BASE}/{filename}"
    archive = download_file(url, _managed_uv_dir() / filename)
    extracted = extract_zip(archive, _managed_uv_dir() / "extracted") if kind == "zip" else extract_tar_gz(archive, _managed_uv_dir() / "extracted")

    # 解压后 uv 可能在顶层或同名子目录
    candidates = list(extracted.rglob(_uv_binary_name()))
    if not candidates:
        raise ToolchainUnavailable(f"uv 归档中未找到可执行文件: {archive}")
    shutil.copy2(candidates[0], managed)
    if os.name != "nt":
        managed.chmod(managed.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not _run_uv_check(managed):
        raise ToolchainUnavailable(f"下载的 uv 无法执行: {managed}")
    logger.info("已供给自带 uv: %s", managed)
    return managed
