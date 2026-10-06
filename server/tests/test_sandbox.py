"""Agent 命令沙箱测试：白名单、参数校验、环境清理与 Python 网络限制。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.agents import sandbox
from app.agents.tools import WorkspaceTools


def test_allowed_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    allowed = [
        f"{sys.executable} -m pytest tests/ -q",
        "g++ -O2 -std=c++17 -o solution solution.cpp",
        "ls -la",
        "find . -name '*.py'",
        "sed -n '1,10p' solution.py",
    ]
    for command in allowed:
        decision = sandbox.decide_command(command, workspace)
        assert decision.allowed, (command, decision.reason)


def test_workspace_binary_allowed_but_scripts_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "solution.exe").write_bytes(b"fake-binary")
    (workspace / "run.sh").write_text("echo hi\n", encoding="utf-8")

    assert sandbox.decide_command("./solution.exe", workspace).allowed
    assert not sandbox.decide_command("./run.sh", workspace).allowed


def test_blocked_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    blocked = [
        "curl https://example.com/x.tar.gz",
        "wget http://example.com/a",
        "git clone https://github.com/pallets/click",
        "pip install requests",
        "uv pip install requests",
        "sh -c 'echo hi'",
        "powershell -Command Get-Date",
        "python -c 'import urllib.request'",
        "python -S -m pytest",
        "cat /etc/passwd",
        "cat ../outside.txt",
        "cat C:\\Windows\\win.ini",
        f"{sys.executable} -c 'print(1)'",
    ]
    for command in blocked:
        decision = sandbox.decide_command(command, workspace)
        assert not decision.allowed, command
        assert decision.reasons, command


def test_build_child_env_cleans_secrets_and_proxies(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    env = sandbox.build_child_env(
        workspace,
        inherit={
            "PATH": "/usr/bin",
            "HTTP_PROXY": "http://127.0.0.1:7890",
            "MY_API_KEY": "secret",
            "OPENAI_TOKEN": "secret",
            "TEMP": "/tmp/existing",
            "LANG": "C.UTF-8",
        },
    )
    assert "HTTP_PROXY" not in env and "http_proxy" not in env
    assert "MY_API_KEY" not in env and "OPENAI_TOKEN" not in env
    assert env["LANG"] == "C.UTF-8"
    assert Path(env["TEMP"]).resolve().is_relative_to(workspace.resolve())
    assert env["TMP"] == env["TEMP"] == env["TMPDIR"]
    assert str(workspace.resolve() / ".sandbox") in env["PYTHONPATH"]
    assert env["PYTHONNOUSERSITE"] == "1"
    assert (workspace / ".sandbox" / "sitecustomize.py").is_file()


def test_python_network_restricted_to_loopback(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    script = workspace / "net_check.py"
    script.write_text(
        "import json, socket\n"
        "socket.setdefaulttimeout(2)\n"
        "results = {}\n"
        "try:\n"
        "    socket.socket().connect(('203.0.113.1', 80))\n"
        "    results['external'] = 'connected'\n"
        "except PermissionError:\n"
        "    results['external'] = 'blocked'\n"
        "except OSError as exc:\n"
        "    results['external'] = type(exc).__name__\n"
        "try:\n"
        "    socket.socket().connect(('127.0.0.1', 9))\n"
        "    results['loopback'] = 'connected'\n"
        "except PermissionError:\n"
        "    results['loopback'] = 'blocked'\n"
        "except OSError as exc:\n"
        "    results['loopback'] = type(exc).__name__\n"
        "print(json.dumps(results))\n",
        encoding="utf-8",
    )
    env = sandbox.build_child_env(workspace)
    proc = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    assert results["external"] == "blocked"
    assert results["loopback"] != "blocked"


@pytest.mark.asyncio
async def test_workspace_tools_enforce_blocks_and_audits(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tools = WorkspaceTools(workspace, command_timeout_s=30)

    result = await tools.dispatch("run_command", {"command": "curl https://example.com"})
    assert "沙箱拒绝执行" in result
    assert tools.command_audit and tools.command_audit[0]["blocked"] is True

    ok = await tools.dispatch("run_command", {"command": f"{sys.executable} -V"})
    assert ok.startswith("exit=0"), ok


@pytest.mark.asyncio
async def test_workspace_tools_audit_mode_records_without_blocking(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tools = WorkspaceTools(workspace, command_timeout_s=30, sandbox_mode="audit")

    result = await tools.dispatch("run_command", {"command": f"{sys.executable} -c \"print(1)\""})
    assert "1" in result
    assert tools.command_audit and tools.command_audit[0]["blocked"] is False
    assert any("Python 逃逸参数" in reason for reason in tools.command_audit[0]["reasons"])


@pytest.mark.asyncio
async def test_workspace_tools_child_env_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("MY_API_KEY", "secret")
    script = workspace / "env_check.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps({"
        "'proxy': os.environ.get('HTTP_PROXY'), "
        "'key': os.environ.get('MY_API_KEY'), "
        "'tmp': os.environ.get('TEMP')}))\n",
        encoding="utf-8",
    )
    tools = WorkspaceTools(workspace, command_timeout_s=30)
    result = await tools.dispatch("run_command", {"command": f"{sys.executable} env_check.py"})
    payload = json.loads(result.splitlines()[1])
    assert payload["proxy"] is None
    assert payload["key"] is None
    assert Path(payload["tmp"]).resolve().is_relative_to(workspace.resolve())
