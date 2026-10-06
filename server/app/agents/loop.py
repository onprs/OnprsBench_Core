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

AGENT_SYSTEM_PROMPT = """你是一名严谨的软件工程师，正在一个代码仓库的工作副本中修复问题。

可用工具：list_files（列目录）、read_file（读文件）、write_file（写文件）、
search_text（搜索文本）、run_command（在工作区执行命令，可运行测试）。

工作流程建议：先读题面理解问题 → 用 list_files / search_text 定位相关源码 →
实施最小修复（write_file）→ 用 run_command 运行相关测试验证 → 确认后给出最终总结。
只修改源码，不要修改测试文件。工作区的文件改动会自动作为你的修复补丁提交。"""


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
