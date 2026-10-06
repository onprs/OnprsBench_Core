"""工作区改动比对：把 agent 工作副本与纯净快照的差异生成为 unified diff。

不依赖 git（工作副本没有 .git）：按文件树比对，文本文件用 difflib 产出
git 风格补丁（a/ b/ 前缀，供 patch-ng 应用）。

产物过滤：agent 运行测试、下载依赖或写临时脚本时会在工作区留下与修复无关的
文件（归档、缓存、下载的上游源码、超大日志等）。这些内容进入补丁会污染判定，
因此本模块在生成补丁时统一剔除并记录原因，同时报告可疑产物供原始事实留痕。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

_BINARY_SAMPLE = 8192

# 单文件超过该大小时不进入补丁（真实源码改动不会这么大）
_DIFF_SIZE_LIMIT = 512 * 1024

# 遍历与提交都跳过的目录
_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env", ".env",
    ".sandbox",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    "node_modules", ".tox", ".eggs", ".uv", "site-packages",
    "_dl", ".downloads", "_downloads",
    "dist", "build",
}
# 提交时跳过的后缀（构建/归档/二进制产物）
_EXCLUDE_SUFFIXES = (
    ".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".o", ".obj",
    ".class", ".jar", ".exe", ".bin", ".dat", ".db", ".sqlite",
    ".tar", ".tgz", ".zip", ".whl", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".log", ".tmp", ".bak", ".orig", ".rej", ".swp", ".lock",
)
# 依赖锁文件：agent 下载上游后常见，通常不是修复目标
_EXCLUDE_NAMES = {
    "uv.lock", "poetry.lock", "package-lock.json", "pnpm-lock.yaml",
    "yarn.lock", "Cargo.lock", "Gemfile.lock", "composer.lock",
}
# 可疑产物特征（下载/临时/转储），只报告不阻断
_SUSPICIOUS_HINTS = ("_dl/", "download", "fetch", "_dump", "tarball", "wheel", "upstream", "_scratch")


@dataclass
class WorkspaceDiff:
    """工作区差异结果：补丁 + 被剔除文件与可疑产物（供原始事实留痕）。"""

    patch: str
    excluded: list[dict[str, str]] = field(default_factory=list)
    suspicious: list[str] = field(default_factory=list)


def _is_binary(data: bytes) -> bool:
    return b"\0" in data[:_BINARY_SAMPLE]


def _rel_files(root: Path) -> dict[str, Path]:
    return {
        p.relative_to(root).as_posix(): p
        for p in sorted(root.rglob("*"))
        if p.is_file() and not any(part in _EXCLUDE_DIRS for part in p.relative_to(root).parts)
    }


def _exclusion_reason(rel: str, path: Path | None) -> str | None:
    """返回该文件不进入补丁的原因；None 表示可以进入补丁。"""
    parts = rel.split("/")
    if any(part in _EXCLUDE_DIRS for part in parts):
        return "缓存/依赖目录"
    if any(part.endswith(".egg-info") for part in parts):
        return "打包元数据"
    name = rel.rsplit("/", 1)[-1]
    if name in _EXCLUDE_NAMES:
        return "依赖锁文件"
    lowered = rel.lower()
    if any(lowered.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES):
        return "构建/归档产物"
    if path is not None and path.stat().st_size > _DIFF_SIZE_LIMIT:
        return f"文件过大（{path.stat().st_size} 字节）"
    return None


def _is_suspicious(rel: str) -> bool:
    lowered = rel.lower()
    return any(hint in lowered for hint in _SUSPICIOUS_HINTS)


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


def diff_workspace_report(pristine: Path, workspace: Path) -> WorkspaceDiff:
    """返回 workspace 相对 pristine 的差异报告（patch / excluded / suspicious）。"""
    base_files = _rel_files(pristine)
    work_files = _rel_files(workspace)
    report = WorkspaceDiff(patch="")

    patches: list[str] = []
    for rel in sorted(set(base_files) | set(work_files)):
        base_path = base_files.get(rel)
        work_path = work_files.get(rel)

        excluded_reason = _exclusion_reason(rel, work_path or base_path)
        if excluded_reason is not None:
            report.excluded.append({"path": rel, "reason": excluded_reason})
            continue
        if _is_suspicious(rel):
            report.suspicious.append(rel)

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

    report.patch = "".join(patches)
    return report


def diff_workspace(pristine: Path, workspace: Path) -> str:
    """返回 workspace 相对 pristine 的 unified diff（无改动返回空串）。"""
    return diff_workspace_report(pristine, workspace).patch
