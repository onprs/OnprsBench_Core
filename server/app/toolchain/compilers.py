"""C/C++ 编译器供给：优先系统编译器；Windows 缺失时自动下载便携 MinGW。"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
from pathlib import Path

from ..config import settings
from .download import download_file, extract_zip
from .errors import ToolchainUnavailable

logger = logging.getLogger(__name__)

# Windows 便携 MinGW（winlibs，UCRT、POSIX 线程）。固定版本保证可复现；
# 升级时改这里即可。约 200MB，首次使用时下载。
_WINLIBS_VERSION = "14.2.0posix-19.1.1-12.0.0-ucrt-r2"
_WINLIBS_ASSET = "winlibs-x86_64-posix-seh-gcc-14.2.0-mingw-w64ucrt-12.0.0-r2.zip"
_WINLIBS_URL = f"https://github.com/brechtsanders/winlibs_mingw/releases/download/{_WINLIBS_VERSION}/{_WINLIBS_ASSET}"


def _check_gpp(binary: Path) -> bool:
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, timeout=30
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _system_compiler() -> Path | None:
    for name in ("g++", "clang++", "c++"):
        found = shutil.which(name)
        if found and _check_gpp(Path(found)):
            return Path(found)
    return None


def ensure_cpp_compiler() -> Path:
    """返回可用的 C++ 编译器路径。无法供给时抛 ToolchainUnavailable。"""
    system = _system_compiler()
    if system is not None:
        return system

    if platform.system() != "Windows":
        raise ToolchainUnavailable(
            "未找到系统 C++ 编译器（g++/clang++）；请安装系统编译器后重试"
        )

    archive = settings.toolchain_dir / "mingw" / _WINLIBS_ASSET
    download_file(_WINLIBS_URL, archive)
    extracted = extract_zip(archive, settings.toolchain_dir / "mingw" / "extracted")
    candidates = list(extracted.rglob("g++.exe"))
    if not candidates:
        raise ToolchainUnavailable(f"MinGW 归档中未找到 g++.exe: {archive}")
    compiler = candidates[0]
    if not _check_gpp(compiler):
        raise ToolchainUnavailable(f"下载的 MinGW 无法执行: {compiler}")
    logger.info("已供给便携 MinGW: %s", compiler)
    return compiler


def compiler_version(binary: Path) -> str:
    try:
        result = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=30
        )
        first_line = (result.stdout or result.stderr).splitlines()[0]
        return first_line.strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "unknown"
