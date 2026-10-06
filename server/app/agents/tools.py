"""Agent 工作区工具：OpenAI function-calling 格式的 schema + 本地执行器。

全部工具绑定到指定 workspace 目录：文件路径不得逃逸 workspace，
命令在 workspace 内执行并限时限量输出。与具体 agent 框架无关。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

_OUTPUT_LIMIT = 8000
_READ_LIMIT = 20000

# OpenAI tools 格式（LiteLLM 与各 provider 通用）
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录内容（递归深度 3，跳过 .git/.venv 等目录）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的路径，默认根目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区中的文本文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入（覆盖）工作区中的文本文件，父目录自动创建",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根目录的文件路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "在工作区文件中搜索子串，返回匹配行（文件:行号:内容）",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "要搜索的文本"},
                    "path": {"type": "string", "description": "起始目录（相对工作区），默认根目录"},
                    "max_results": {"type": "integer", "description": "最多返回的匹配行数，默认 30"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "在工作区根目录执行 shell 命令（有超时；输出截断）",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令，如 python -m pytest tests/ -q"},
                    "timeout_s": {"type": "integer", "description": "超时秒数"},
                },
                "required": ["command"],
            },
        },
    },
]


def _resolve(workspace: Path, rel: str) -> Path:
    """解析相对路径并拒绝目录逃逸。"""
    base = workspace.resolve()
    path = (base / rel).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"路径越界（必须位于工作区内）: {rel}")
    return path


def _tail(text: str, limit: int = _OUTPUT_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...（截断，共 {len(text)} 字符）"


class WorkspaceTools:
    """绑定到 workspace 的工具执行器。"""

    def __init__(self, workspace: Path, *, command_timeout_s: int = 120) -> None:
        self.workspace = workspace.resolve()
        self.command_timeout_s = command_timeout_s
        self._handlers: dict[str, Callable[..., Awaitable[str]]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "write_file": self._write_file,
            "search_text": self._search_text,
            "run_command": self._run_command,
        }

    async def dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        """执行一次工具调用，返回工具输出文本（异常转为错误文本，不抛出）。"""
        handler = self._handlers.get(name)
        if handler is None:
            return f"未知工具: {name}"
        try:
            return await handler(**arguments)
        except TypeError as exc:
            return f"工具参数错误: {exc}"
        except (OSError, ValueError) as exc:
            return f"工具执行失败: {exc}"

    async def _list_files(self, path: str = ".") -> str:
        root = _resolve(self.workspace, path)
        if not root.is_dir():
            return f"目录不存在: {path}"
        skip = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}
        lines: list[str] = []
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if len(rel.parts) > 3 or any(part in skip for part in rel.parts):
                continue
            lines.append(("d " if p.is_dir() else "f ") + rel.as_posix())
            if len(lines) >= 500:
                lines.append("...（列表截断）")
                break
        return "\n".join(lines) or "（空目录）"

    async def _read_file(self, path: str) -> str:
        target = _resolve(self.workspace, path)
        if not target.is_file():
            return f"文件不存在: {path}"
        text = target.read_text(encoding="utf-8", errors="replace")
        return _tail(text, _READ_LIMIT)

    async def _write_file(self, path: str, content: str) -> str:
        target = _resolve(self.workspace, path)
        target.parent.mkdir(parents=True, exist_ok=True)

        def _write() -> None:
            # 固定 LF 写入：diff 补丁的字节稳定性不依赖平台默认行尾
            with target.open("w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)

        await asyncio.to_thread(_write)
        return f"已写入 {path}（{len(content)} 字符）"

    async def _search_text(self, pattern: str, path: str = ".", max_results: int = 30) -> str:
        root = _resolve(self.workspace, path)
        hits: list[str] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file() or any(part in {".git", ".venv", "__pycache__"} for part in p.parts):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                if pattern in line:
                    hits.append(f"{p.relative_to(self.workspace).as_posix()}:{lineno}: {line.strip()[:160]}")
                    if len(hits) >= max_results:
                        return "\n".join(hits) + "\n...（结果截断）"
        return "\n".join(hits) or "（无匹配）"

    async def _run_command(self, command: str, timeout_s: int | None = None) -> str:
        effective_timeout = min(timeout_s or self.command_timeout_s, self.command_timeout_s)

        def _run() -> str:
            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.workspace,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=effective_timeout,
                )
            except subprocess.TimeoutExpired:
                return f"命令超时（>{effective_timeout}s）已被终止"
            out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
            return f"exit={proc.returncode}\n" + (_tail(out) if out.strip() else "（无输出）")

        return await asyncio.to_thread(_run)
