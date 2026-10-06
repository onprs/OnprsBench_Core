"""工作区改动比对：把 agent 工作副本与纯净快照的差异生成为 unified diff。

不依赖 git（工作副本没有 .git）：按文件树比对，文本文件用 difflib 产出
git 风格补丁（a/ b/ 前缀，供 patch-ng 应用）。
"""

from __future__ import annotations

import difflib
from pathlib import Path

_BINARY_SAMPLE = 8192


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:_BINARY_SAMPLE]


# 判定无关的产物目录/文件：agent 运行测试会生成缓存，不得进入补丁
_EXCLUDE_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules", ".tox", ".eggs"}
_EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def _excluded(rel: str) -> bool:
    parts = rel.split("/")
    return (
        any(part in _EXCLUDE_DIRS for part in parts)
        or any(rel.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES)
        or any(part.endswith(".egg-info") for part in parts)
    )


def _rel_files(root: Path) -> dict[str, Path]:
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and not _excluded(p.relative_to(root).as_posix())
    }


def _read_lines(path: Path | None) -> list[str]:
    """读取文本行并归一化为 LF 行尾（保证补丁与平台/编辑器行尾无关）。"""
    if path is None:
        return []
    text = path.read_bytes().decode("utf-8", errors="replace")
    if not text:
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # 末尾换行产生的空串
    return [line + "\n" for line in lines]


def diff_workspace(pristine: Path, workspace: Path) -> str:
    """返回 workspace 相对 pristine 的 unified diff（无改动返回空串）。"""
    base_files = _rel_files(pristine)
    work_files = _rel_files(workspace)

    patches: list[str] = []
    for rel in sorted(set(base_files) | set(work_files)):
        base_path = base_files.get(rel)
        work_path = work_files.get(rel)
        # 两侧都存在且字节相同才算无变化（注意空文件删除时两侧内容同为 b""）
        if base_path is not None and work_path is not None:
            if base_path.read_bytes() == work_path.read_bytes():
                continue
        base_bytes = base_path.read_bytes() if base_path else b""
        work_bytes = work_path.read_bytes() if work_path else b""
        if _is_binary(base_bytes) or _is_binary(work_bytes):
            # 二进制文件无法进 unified diff；记录为标记行，判定时按未生效处理
            patches.append(f"diff --git a/{rel} b/{rel}\n-- 二进制文件改动未纳入补丁 --\n")
            continue
        if not base_bytes or not work_bytes:
            # unified diff 无法表达空文件的新增/删除（无内容行可构成 hunk），标记之
            if not base_bytes and not work_bytes:
                patches.append(f"-- 空文件变动未纳入补丁: {rel} --\n")
                continue
            if base_path is not None and work_path is not None:
                # 一侧变空的修改可以用 hunk 表达，继续走 difflib
                pass
            elif (work_path is not None and not work_bytes) or (base_path is not None and not base_bytes):
                patches.append(f"-- 空文件{'新增' if work_path is not None else '删除'}未纳入补丁: {rel} --\n")
                continue

        fromfile = f"a/{rel}" if base_path else "/dev/null"
        tofile = f"b/{rel}" if work_path else "/dev/null"
        body = difflib.unified_diff(
            _read_lines(base_path), _read_lines(work_path),
            fromfile=fromfile, tofile=tofile, n=3, lineterm="\n",
        )
        patches.append(f"diff --git a/{rel} b/{rel}\n" + "".join(body))
    return "".join(patches)
