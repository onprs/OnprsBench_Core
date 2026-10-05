"""大文件下载与缓存：流式写入 + 临时文件原子改名。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import httpx

from ..config import settings
from .errors import ToolchainUnavailable

logger = logging.getLogger(__name__)


def download_file(url: str, dest: Path, *, timeout_s: float | None = None) -> Path:
    """下载 url 到 dest（已存在则直接复用）。失败抛 ToolchainUnavailable。"""
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    timeout = timeout_s or settings.toolchain_download_timeout_s
    logger.info("下载工具链资源 %s -> %s", url, dest)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
            if resp.status_code != 200:
                raise ToolchainUnavailable(f"下载失败（HTTP {resp.status_code}）: {url}")
            with tmp.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
    except (httpx.HTTPError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        raise ToolchainUnavailable(f"下载失败 {url}: {exc}") from exc
    tmp.replace(dest)
    return dest


def extract_tar_gz(archive: Path, dest_dir: Path, *, strip_components: int = 0) -> Path:
    """解压 .tar.gz 到 dest_dir（已存在则跳过）。strip_components 剥离顶层目录。"""
    import tarfile

    if dest_dir.exists() and any(dest_dir.iterdir()):
        return dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts[strip_components:]
            if not parts:
                continue
            member.name = str(Path(*parts))
            tar.extract(member, dest_dir, filter="data")
    return dest_dir


def extract_zip(archive: Path, dest_dir: Path) -> Path:
    """解压 .zip 到 dest_dir（已存在且非空则跳过）。"""
    import zipfile

    if dest_dir.exists() and any(dest_dir.iterdir()):
        return dest_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest_dir)
    return dest_dir


def clear_dir(path: Path) -> Path:
    """清空并重建目录。"""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path
