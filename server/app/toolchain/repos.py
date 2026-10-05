"""源码仓库快照供给：按 commit 下载 GitHub 归档，本地缓存，不依赖用户安装 git。"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path

from ..config import settings
from .download import download_file, extract_tar_gz
from .errors import ToolchainUnavailable

logger = logging.getLogger(__name__)

_GITHUB_RE = re.compile(r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$")

# 同一仓库快照的并发准备互斥（进程内锁；框架为单进程 sidecar）
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _parse_github(repo_url: str) -> tuple[str, str]:
    match = _GITHUB_RE.search(repo_url.strip())
    if match is None:
        raise ToolchainUnavailable(f"仅支持 GitHub 仓库归档下载，无法解析: {repo_url}")
    return match.group("owner"), match.group("repo")


def fetch_repo_snapshot(repo_url: str, commit: str) -> Path:
    """获取仓库在指定 commit 的纯净快照目录（缓存复用）。

    返回目录内容已剥离归档顶层目录，可直接作为工作副本复制。
    """
    owner, repo = _parse_github(repo_url)
    key = f"{owner}-{repo}@{commit}"
    archive = settings.cache_dir / "repo_archives" / f"{owner}-{repo}-{commit}.tar.gz"
    snapshot_dir = settings.cache_dir / "repo_snapshots" / key

    with _lock_for(key):
        if snapshot_dir.is_dir() and any(snapshot_dir.iterdir()):
            return snapshot_dir
        url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{commit}"
        download_file(url, archive)
        snapshot_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp = snapshot_dir.with_name(snapshot_dir.name + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        extract_tar_gz(archive, tmp, strip_components=1)
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        tmp.rename(snapshot_dir)
        logger.info("仓库快照就绪: %s", snapshot_dir)
    return snapshot_dir


def materialize_workspace(snapshot: Path, dest: Path) -> Path:
    """把纯净快照复制为一次判定的工作目录（dest 必须不存在或为空）。"""
    shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(snapshot, dest)
    return dest
