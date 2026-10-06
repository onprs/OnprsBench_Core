"""Agent 命令沙箱：命令白名单、参数校验、环境清理与 Python 网络限制。

背景：`run_command` 若是 shell 直通，工作区隔离就只剩提示词约束。本模块在
进程内提供可执行的加固层（不依赖用户安装额外工具）：

1. 命令白名单：只允许解释器/编译器/只读查看类命令；下载工具（curl/wget/git/
   pip/uv/ssh…）与 shell（sh/bash/cmd/powershell）不在名单内；
2. 参数校验：拒绝绝对路径、父目录穿越、URL，以及 `python -c` 直执行代码、
   `-S/-E/-I` 逃逸解释器环境等；
3. 环境清理：子进程不继承代理与疑似凭据变量，TEMP 指向工作区；
4. Python 网络限制：注入 sitecustomize，把 socket 解析与连接限制在回环地址
   （pytest 的本地服务可用）；Linux 且权限允许时额外使用 `unshare -n`。

如实说明的能力边界：工作区内新编译出的二进制文件无法在进程内限制其网络与
文件系统行为；真正的强隔离需要平台级沙箱（见 docs/architecture.md Roadmap）。
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 允许直接执行的命令（按可执行文件 basename，去掉 .exe 后小写比较）
_ALLOWED_EXECUTABLES = {
    # Python 与测试
    "python", "python3", "py", "pytest",
    # C/C++ 工具链
    "g++", "gcc", "clang++", "clang", "cc", "c++", "make", "cmake", "mingw32-make",
    "ld", "ar", "as", "nm", "objdump", "strip",
    # 只读查看 / 搜索 / 文本处理
    "ls", "dir", "cat", "type", "head", "tail", "find", "grep", "rg", "sed", "awk",
    "echo", "wc", "diff", "sort", "uniq", "printf", "pwd", "where", "which", "tree",
    "stat", "file", "xxd", "od", "md5sum", "sha256sum",
    # 归档（无网络能力）
    "tar", "unzip",
    "true", "false",
}
# 下载/远端工具（curl/wget/git/pip/uv/ssh/certutil…）与 shell 不在名单内

# 子进程不继承的代理变量
_PROXY_VARS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
# 不直接执行的脚本后缀（直接执行会绕过解释器级网络限制）
_NON_EXECUTABLE_SUFFIXES = {".sh", ".bat", ".cmd", ".ps1", ".pl", ".rb", ".py", ".js", ".vbs"}

# Python 解释器的逃逸参数（跳过 sitecustomize / 忽略环境变量 / 隔离模式）
_PYTHON_ESCAPE_FLAGS = {"-S", "-E", "-I", "-c"}

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_WINDOWS_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")

_network_isolation_probed = False
_network_isolation_prefix: list[str] = []


@dataclass
class CommandDecision:
    """一条命令的沙箱判定结果。"""

    allowed: bool
    argv: list[str] | None = None
    reason: str | None = None
    reasons: list[str] = field(default_factory=list)  # 审计用：命中的规则


def _executable_key(token: str) -> str:
    name = Path(token).name.lower()
    if name.endswith(".exe"):
        name = name[: -len(".exe")]
    return name


def build_argv(command: str) -> list[str]:
    """按平台解析命令为参数列表（不经过 shell）。"""
    text = (command or "").strip()
    if not text:
        return []
    if os.name == "nt":
        return [token.strip('"') for token in shlex.split(text, posix=False)]
    return shlex.split(text, posix=True)


def _path_like(token: str) -> str | None:
    """返回路径类 token 的违规原因；None 表示通过。"""
    candidate = token
    # `--flag=value` 形态：检查等号右侧
    if "=" in token and not token.startswith("="):
        candidate = token.split("=", 1)[1]
    if not candidate:
        return None
    if _URL_RE.match(candidate):
        return f"URL: {candidate}"
    if _WINDOWS_ABSOLUTE_RE.match(candidate) or candidate.startswith("/") or candidate.startswith("~"):
        return f"绝对路径: {candidate}"
    parts = Path(candidate).parts
    if ".." in parts:
        return f"父目录穿越: {candidate}"
    return None


def _workspace_executable(token: str, workspace: Path) -> bool:
    """工作区内的二进制产物可以执行（编译出的程序等），脚本后缀除外。"""
    if "/" not in token and "\\" not in token:
        return False
    candidate = (workspace / token).resolve()
    try:
        candidate.relative_to(workspace.resolve())
    except ValueError:
        return False
    if not candidate.is_file():
        return False
    return candidate.suffix.lower() not in _NON_EXECUTABLE_SUFFIXES


def decide_command(command: str, workspace: Path) -> CommandDecision:
    """判定命令是否允许执行；被拒绝时给出原因，命中规则全部进入审计。"""
    argv = build_argv(command)
    if not argv:
        return CommandDecision(False, None, "空命令", ["空命令"])

    executable = argv[0]
    key = _executable_key(executable)
    allowed_executable = key in _ALLOWED_EXECUTABLES or _workspace_executable(executable, workspace)
    if not allowed_executable:
        return CommandDecision(
            False,
            None,
            f"可执行文件不在允许名单内: {Path(executable).name}",
            [f"可执行文件不在允许名单内: {Path(executable).name}"],
        )

    reasons: list[str] = []
    if key in {"python", "python3", "py", "pytest"}:
        for token in argv[1:]:
            if token in _PYTHON_ESCAPE_FLAGS:
                reasons.append(f"Python 逃逸参数: {token}")
    for token in argv[1:]:
        violation = _path_like(token)
        if violation:
            reasons.append(violation)

    if reasons:
        return CommandDecision(False, None, "；".join(reasons[:3]), reasons)
    return CommandDecision(True, argv, None, [])


# ---------------------------------------------------------------------------
# 子进程环境与 Python 网络限制
# ---------------------------------------------------------------------------

_PYTHON_SANDBOX_SITECUSTOMIZE = '''\
"""由 OnprsBench 沙箱注入：把 Python 子进程的网络访问限制在回环地址。"""

import socket as _socket

_LOCAL_NAMES = {"localhost", "127.0.0.1", "::1", "", None}


def _is_local(host) -> bool:
    if host in _LOCAL_NAMES:
        return True
    if isinstance(host, str):
        name = host.strip("[]").lower()
        if name in _LOCAL_NAMES:
            return True
        if name.startswith("127.") or name in {"::1", "0.0.0.0"}:
            return True
    return False


def _guard(original):
    def wrapper(self, address):
        if isinstance(address, tuple) and address and not _is_local(address[0]):
            raise PermissionError(
                "sandbox: network access to non-loopback address blocked: %r" % (address,)
            )
        return original(self, address)

    return wrapper


_socket.socket.connect = _guard(_socket.socket.connect)
_socket.socket.connect_ex = _guard(_socket.socket.connect_ex)

_original_getaddrinfo = _socket.getaddrinfo


def _guarded_getaddrinfo(host, *args, **kwargs):
    if not _is_local(host):
        raise PermissionError("sandbox: DNS resolution blocked: %r" % (host,))
    return _original_getaddrinfo(host, *args, **kwargs)


_socket.getaddrinfo = _guarded_getaddrinfo
'''


def python_sandbox_dir(workspace: Path) -> Path:
    """写入 sitecustomize.py 并返回可注入 PYTHONPATH 的目录。"""
    sandbox_dir = workspace / ".sandbox"
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    sitecustomize = sandbox_dir / "sitecustomize.py"
    content = _PYTHON_SANDBOX_SITECUSTOMIZE
    if not sitecustomize.is_file() or sitecustomize.read_text(encoding="utf-8") != content:
        sitecustomize.write_text(content, encoding="utf-8", newline="\n")
    return sandbox_dir


def build_child_env(workspace: Path, *, inherit: dict[str, str] | None = None) -> dict[str, str]:
    """构造子进程环境：不继承代理/凭据变量，TEMP 指向工作区，注入网络限制。"""
    source = inherit if inherit is not None else dict(os.environ)
    proxy_names = {name.upper() for name in _PROXY_VARS}
    env: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        if any(marker in upper for marker in _SECRET_MARKERS):
            continue
        if upper in proxy_names:
            continue
        env[name] = value

    tmp_dir = workspace / ".sandbox" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["TEMP"] = str(tmp_dir)
    env["TMP"] = str(tmp_dir)
    env["TMPDIR"] = str(tmp_dir)

    sandbox_dir = python_sandbox_dir(workspace)
    existing = source.get("PYTHONPATH")
    env["PYTHONPATH"] = str(sandbox_dir) + (os.pathsep + existing if existing else "")
    # 阻止用户级 site-packages 干扰工作区代码
    env["PYTHONNOUSERSITE"] = "1"
    return env


def network_isolation_prefix() -> list[str]:
    """Linux 上可用时返回 `unshare -n --` 前缀（网络命名空间隔离）。"""
    global _network_isolation_probed, _network_isolation_prefix
    if _network_isolation_probed:
        return _network_isolation_prefix
    _network_isolation_probed = True
    if os.name == "nt" or shutil.which("unshare") is None:
        return []
    probe = subprocess.run(["unshare", "-n", "true"], capture_output=True)
    if probe.returncode == 0:
        _network_isolation_prefix = ["unshare", "-n", "--"]
    return _network_isolation_prefix
