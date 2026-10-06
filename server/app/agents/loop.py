"""Agent 多轮工具循环（与框架无关的通用实现）。

Solver 在 workspace 中读取/搜索/编辑文件并运行命令，每轮模型输出经
function calling 协议往返，直到模型给出最终文本、达到轮次上限或模型连续
因输出预算耗尽而无法继续。消息轨迹、逐轮 usage 与截断情况作为原始事实返回。

轮次语义（与 ReasoningProfile.agent_max_turns 对应）：
- None：不限制，循环直到模型给出最终文本或发生错误；
- 正整数：最多执行该数量的模型调用轮次。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime.base import ModelRequest, ToolsCapableClient
from .tools import TOOL_SCHEMAS, WorkspaceTools

_COMMON_ENVIRONMENT = """【工作环境】
- 你的当前目录就是工作区根目录，所有工具路径均为工作区内相对路径。
- 可用工具：list_files（列目录）、read_file（读文件）、write_file（写文件）、
  search_text（搜索文本）、run_command（在工作区根目录执行命令）。
- run_command 只支持有限命令（python / pytest / g++ 与查看搜索类），不支持管道与重定向；
  需要多步命令行时写成脚本文件再运行，不要依赖 shell 特性。
- 路径限制在工作区内；不要访问工作区外的位置，不要联网下载任何内容，不要探查系统环境。"""

_COMMON_DISCIPLINE = """【工作纪律】
- 先读题面理解问题，再动手修改或编写代码。
- 反复无进展时立即用一段纯文本总结当前状态并停止。
- 输出预算有限：不要长篇复述推理过程。接近输出上限时立即停止推演并执行下一步操作。"""

_SWE_PROMPT = f"""你是一名严谨的软件工程师，正在修复代码仓库中的问题。

{_COMMON_ENVIRONMENT}
- run_command 常用于运行测试，如 `python -m pytest tests/ -q`。

{_COMMON_DISCIPLINE}
- 用 write_file 实施最小修复（只改源码，不改测试文件）。
- 用 run_command 运行相关测试验证；测试通过后立即停止，用一段纯文本总结修复。
- 你的工作区文件改动会自动作为修复补丁提交，无需手动导出。
- 不要把临时脚本、下载内容、测试缓存留在工作区里。"""

_CODE_PROMPT = f"""你是一名竞赛选手，正在解决一道算法题。

{_COMMON_ENVIRONMENT}
- 题面在工作区根目录的 problem.md；如有输入数据等附件也在工作区根目录。

{_COMMON_DISCIPLINE}
- 把最终解答写成完整程序文件：`solution.cpp` 或 `solution.py`（二选一，标准输入读入、标准输出写出）。
- 可以用 run_command 编译和运行自己的程序，用题面给出的样例自测；Python 用 `python solution.py`，C++ 用 `g++ -O2 -std=c++17 -o solution solution.cpp`。
- 自测通过或时间有限时，用一段纯文本总结你的解法与复杂度，然后停止。"""

_SYSTEM_PROMPTS = {
    "issue_resolution": _SWE_PROMPT,
    "code_generation": _CODE_PROMPT,
    "implementation": _CODE_PROMPT,
}


def build_agent_system_prompt(task_type: str | None) -> str:
    """按任务类型返回 agent system prompt（未知类型按算法题处理）。"""
    return _SYSTEM_PROMPTS.get(task_type or "", _CODE_PROMPT)


# 输出预算耗尽时的续行提示：把模型从“继续推演”拉回到“执行动作”
_TRUNCATION_HINT = (
    "上一轮输出因长度上限被截断。请立即停止长篇推演，"
    "直接执行下一步操作（写文件或运行命令）；如果已有可用方案，请把完整代码写入工作区文件。"
)

# 轮次将尽时的收尾提示
_FINAL_TURN_HINT = (
    "轮次即将用尽（剩余 {remaining} 轮）。请立即停止进一步操作，"
    "用一段纯文本总结你已完成的工作（不再调用任何工具）。"
)

# 连续截断提示的上限，避免模型陷入“截断-提示”死循环
_MAX_TRUNCATION_HINTS = 2


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
    # completed / max_turns / truncated / error
    stop_reason: str = "completed"
    error: str | None = None
    # 逐轮原始事实（usage、耗时、结束原因、工具调用），供落库与对比
    turn_records: list[dict[str, Any]] = field(default_factory=list)
    # 发生过输出预算耗尽的轮次数
    truncated_turns: int = 0
    # 最后一次调用的结束原因
    finish_reason: str | None = None
    # run_command 审计（疑似越界/联网的命令），供原始事实留痕
    command_audit: list[dict[str, Any]] = field(default_factory=list)


def _last_assistant_text(messages: list[dict[str, Any]]) -> str:
    """取最后一条带文本的 assistant 消息（工具消息不得当作最终总结）。"""
    for message in reversed(messages):
        if message.get("role") == "assistant" and str(message.get("content") or "").strip():
            return str(message["content"])
    return ""


def _usage_field(usage: Any, name: str) -> int | None:
    value = getattr(usage, name, None)
    return int(value) if value is not None else None


async def run_agent_loop(
    client: ToolsCapableClient,
    *,
    problem: str,
    workspace: Path,
    system_prompt: str | None = None,
    request_template: ModelRequest | None = None,
    max_turns: int | None = 40,
    command_timeout_s: int = 120,
) -> AgentLoopResult:
    """运行 agent 循环。模型异常时返回带 error 的结果（不抛出）。

    request_template 携带 Reasoning Profile 的推理参数（reasoning_effort、
    max_output_tokens、temperature、provider_params、单次调用墙钟上限），
    每轮调用复用该模板，仅替换 messages。
    """
    tools = WorkspaceTools(workspace, command_timeout_s=command_timeout_s)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt or build_agent_system_prompt(None)},
        {"role": "user", "content": problem},
    ]
    template = request_template or ModelRequest(messages=[])
    result = AgentLoopResult(final_text="", started_at=datetime.now(timezone.utc))
    t0 = time.perf_counter()
    truncation_hints = 0
    closing_hint_sent = False
    should_stop = False

    def accumulate(usage: Any) -> None:
        for attr in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
            value = _usage_field(usage, attr)
            if value is not None:
                setattr(result, attr, (getattr(result, attr) or 0) + value)

    try:
        while not should_stop:
            if max_turns is not None and result.turns >= max_turns:
                result.stop_reason = "max_turns"
                break

            request = ModelRequest(
                messages=list(messages),
                reasoning_effort=template.reasoning_effort,
                reasoning_budget=template.reasoning_budget,
                max_output_tokens=template.max_output_tokens,
                temperature=template.temperature,
                top_p=template.top_p,
                seed=template.seed,
                provider_params=template.provider_params,
                timeout_s=template.timeout_s,
                call_timeout_s=template.call_timeout_s,
            )
            step = await client.complete_with_tools(request, TOOL_SCHEMAS)
            accumulate(step.usage)
            result.turns += 1
            result.finish_reason = step.finish_reason
            truncated = step.finish_reason == "length"
            if truncated:
                result.truncated_turns += 1
            result.turn_records.append(
                {
                    "turn": result.turns,
                    "finish_reason": step.finish_reason,
                    "truncated": truncated,
                    "tool_calls": [call.name for call in step.tool_calls],
                    "content_chars": len(step.content or ""),
                    "input_tokens": _usage_field(step.usage, "input_tokens"),
                    "cached_input_tokens": _usage_field(step.usage, "cached_input_tokens"),
                    "output_tokens": _usage_field(step.usage, "output_tokens"),
                    "reasoning_tokens": _usage_field(step.usage, "reasoning_tokens"),
                    "ttft_s": step.ttft_s,
                    "total_latency_s": step.total_latency_s,
                    "started_at": step.started_at.isoformat() if step.started_at else None,
                    "finished_at": step.finished_at.isoformat() if step.finished_at else None,
                }
            )
            messages.append(step.assistant_message)

            # 收尾提示已发出后模型仍在调用工具：不再执行，直接结束
            if closing_hint_sent and step.tool_calls:
                result.stop_reason = "max_turns"
                break

            if not step.tool_calls:
                if truncated:
                    # 输出预算耗尽：内容可能是残缺推理。提示模型改用动作表达，
                    # 连续提示超过上限后接受当前可见内容作为最终回答。
                    if truncation_hints < _MAX_TRUNCATION_HINTS:
                        truncation_hints += 1
                        messages.append({"role": "user", "content": _TRUNCATION_HINT})
                        continue
                    result.final_text = step.content
                    result.stop_reason = "truncated"
                    break
                result.final_text = step.content
                result.stop_reason = "completed"
                break

            for call in step.tool_calls:
                output = await tools.dispatch(call.name, call.arguments, turn=result.turns)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})

            # 轮次将尽时引导收尾（恰剩 3 轮时提示一次），避免模型无限迭代到硬性截断
            if max_turns is not None and not closing_hint_sent:
                remaining = max_turns - result.turns
                if remaining == 3:
                    closing_hint_sent = True
                    messages.append(
                        {"role": "user", "content": _FINAL_TURN_HINT.format(remaining=remaining)}
                    )
    except Exception as exc:
        result.stop_reason = "error"
        result.error = str(exc)

    if result.stop_reason != "error" and not result.final_text:
        result.final_text = _last_assistant_text(messages)

    result.finished_at = datetime.now(timezone.utc)
    result.wall_time_s = time.perf_counter() - t0
    result.messages = messages
    result.command_audit = list(tools.command_audit)
    return result
