"""Agent 多轮工具循环（与框架无关的通用实现）。

Solver 在 workspace 副本中读取/搜索/编辑文件并运行命令，
每轮模型输出经 LiteLLM function calling 协议往返，直到模型给出最终文本
或达到轮次上限。消息轨迹与聚合 usage 作为原始事实返回。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime.base import ModelRequest, ToolsCapableClient
from .tools import TOOL_SCHEMAS, WorkspaceTools

AGENT_SYSTEM_PROMPT = """你是一名严谨的软件工程师，正在修复代码仓库中的问题。

【工作环境】
- 你的当前目录就是仓库根目录（已 checkout 到问题修复前的状态），所有工具路径均为仓库内相对路径。
- 可用工具：list_files（列目录）、read_file（读文件）、write_file（写文件）、
  search_text（搜索文本）、run_command（在仓库根目录执行命令，如 python -m pytest tests/ -q）。
- 不要使用绝对路径，不要访问当前目录以外的任何位置，不要探查系统环境。

【工作纪律】
1. 先读题面理解问题，用 search_text / read_file 定位相关源码。
2. 用 write_file 实施最小修复（只改源码，不改测试文件）。
3. 用 run_command 运行相关测试验证。
4. 测试通过后立即停止：用一段纯文本总结你的修复（不再调用任何工具）。
   反复无进展时也应尽快总结当前状态并停止。你的工作区文件改动会自动作为修复补丁提交，无需手动导出。"""


@dataclass
class AgentLoopResult:
    final_text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    wall_time_s: float = 0.0
    stop_reason: str = "completed"  # completed / max_turns / error
    error: str | None = None


async def run_agent_loop(
    client: ToolsCapableClient,
    *,
    problem: str,
    workspace: Path,
    max_turns: int = 40,
    command_timeout_s: int = 120,
    timeout_s: float = 600.0,
) -> AgentLoopResult:
    """运行 agent 循环。模型异常时返回带 error 的结果（不抛出）。"""
    tools = WorkspaceTools(workspace, command_timeout_s=command_timeout_s)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": problem},
    ]
    result = AgentLoopResult(final_text="", started_at=datetime.now(timezone.utc))
    t0 = time.perf_counter()

    def accumulate(usage) -> None:
        for attr in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
            value = getattr(usage, attr, None)
            if value is not None:
                setattr(result, attr, (getattr(result, attr) or 0) + value)

    try:
        for _turn in range(max_turns):
            step = await client.complete_with_tools(
                ModelRequest(messages=messages, timeout_s=timeout_s),
                TOOL_SCHEMAS,
            )
            accumulate(step.usage)
            result.turns += 1
            messages.append(step.assistant_message)

            if not step.tool_calls:
                result.final_text = step.content
                result.stop_reason = "completed"
                break

            for call in step.tool_calls:
                output = await tools.dispatch(call.name, call.arguments)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": output}
                )

            # 轮次将尽时引导收尾，避免模型无限迭代到硬性截断
            remaining = max_turns - result.turns
            if remaining == 3:
                messages.append(
                    {
                        "role": "user",
                        "content": "轮次即将用尽（剩余 3 轮）。请立即停止进一步操作，用一段纯文本总结你已完成的工作（不再调用任何工具）。",
                    }
                )
        else:
            result.stop_reason = "max_turns"
            result.final_text = messages[-1].get("content", "") if messages else ""
    except Exception as exc:
        result.stop_reason = "error"
        result.error = str(exc)

    result.finished_at = datetime.now(timezone.utc)
    result.wall_time_s = time.perf_counter() - t0
    result.messages = messages
    return result
