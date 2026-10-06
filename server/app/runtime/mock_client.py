"""Mock 模型客户端：确定性、零网络，用于测试与离线演示。

- Solver 角色：根据题面 hash 生成确定性回答。
- Judge 角色：识别 judge prompt 标记，解析其中的 rubric 维度行
  （`- <id>（权重 <w>）：...`），输出 0~1 维度分的结构化 JSON，
  分数由候选回答内容 hash 决定，保证可重复且不同回答得分不同。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

from .base import CallTimer, ModelRequest, ModelResult, ToolCall, ToolStep, UsageInfo

# judge prompt 模板中的固定标记，mock client 据此切换行为
JUDGE_PROMPT_MARKER = "Candidate Response #A"


def _hash_int(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class MockClient:
    """确定性 mock。api_model_name 可携带行为提示，如 'mock-strong' / 'mock-weak'。

    agent 模式（complete_with_tools）：通过 agent_script 注入动作序列
    （[{"tool": "write_file", "arguments": {...}}, ..., {"text": "最终回答"}]），
    供测试驱动工具循环；无脚本时退化为直接文本回答。
    """

    def __init__(
        self,
        api_model_name: str = "mock-model",
        latency_s: float = 0.05,
        agent_script: list[dict[str, Any]] | None = None,
    ) -> None:
        self._name = api_model_name
        self._latency_s = latency_s
        self._agent_script = list(agent_script or [])
        self._agent_cursor = 0

    async def complete_with_tools(self, request: ModelRequest, tools: list[dict[str, Any]]) -> ToolStep:
        await asyncio.sleep(self._latency_s)
        action = (
            self._agent_script[self._agent_cursor]
            if self._agent_cursor < len(self._agent_script)
            else None
        )
        self._agent_cursor += 1
        prompt_text = "\n".join(str(m.get("content", "")) for m in request.messages)
        usage = UsageInfo(
            input_tokens=_estimate_tokens(prompt_text),
            cached_input_tokens=0,
            output_tokens=8,
            reasoning_tokens=0,
        )
        if action is not None and "tool" in action:
            call = ToolCall(
                id=f"mock-call-{self._agent_cursor}",
                name=str(action["tool"]),
                arguments=dict(action.get("arguments") or {}),
            )
            return ToolStep(
                content="",
                tool_calls=[call],
                assistant_message={
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False)},
                        }
                    ],
                },
                usage=usage,
                raw_response={"mock": True, "agent_action": action["tool"]},
            )
        text = str((action or {}).get("text") or "mock agent 已完成工作区修改")
        return ToolStep(
            content=text,
            tool_calls=[],
            assistant_message={"role": "assistant", "content": text},
            usage=usage,
            raw_response={"mock": True},
        )

    async def complete(self, request: ModelRequest) -> ModelResult:
        timer = CallTimer()
        await asyncio.sleep(self._latency_s)

        prompt_text = "\n".join(m.get("content", "") for m in request.messages)
        is_judge = JUDGE_PROMPT_MARKER in prompt_text

        if is_judge:
            text = self._judge_response(prompt_text)
        else:
            text = self._solver_response(prompt_text)

        finished_at, latency = timer.finish()
        usage = UsageInfo(
            input_tokens=_estimate_tokens(prompt_text),
            cached_input_tokens=0,
            output_tokens=_estimate_tokens(text),
            reasoning_tokens=0,
        )
        raw: dict[str, Any] = {
            "id": f"mock-{_hash_int(prompt_text) & 0xFFFFFFFF:08x}",
            "model": self._name,
            "mock": True,
        }
        return ModelResult(
            text=text,
            raw_response=raw,
            usage=usage,
            started_at=timer.started_at,
            finished_at=finished_at,
            total_latency_s=latency,
            ttft_s=None,
            generation_time_s=None,
        )

    def _solver_response(self, prompt_text: str) -> str:
        # 名字含 weak 的 mock 模型给出错误回答，便于演示评分差异
        if "weak" in self._name:
            return "答案是 41。（mock-weak 故意给出的错误回答，未展示计算过程）"
        seed = _hash_int(prompt_text + self._name) % 3
        variants = [
            "答案是 42。计算过程：17 + 25 = 17 + 20 + 5 = 37 + 5 = 42。",
            "结论：42。个位 7+5=12 进 1，十位 1+2+1=4，因此 17 + 25 = 42。",
            "17 + 25 = 42。分步：先加整十 20 得 37，再加 5 得 42。",
        ]
        return variants[seed]

    def _judge_response(self, prompt_text: str) -> str:
        # 从 prompt 中提取 rubric 维度与候选回答段落，分数由内容决定，保证确定性
        dim_ids = re.findall(r"^-\s+([a-z0-9_]+)\uff08\u6743\u91cd", prompt_text, re.MULTILINE)
        if not dim_ids:
            dim_ids = ["correctness"]
        candidate = prompt_text.split(JUDGE_PROMPT_MARKER, 1)[-1]
        h = _hash_int(candidate + self._name)
        wrong = "41" in candidate and "42" not in candidate
        dimensions = {}
        for index, dim_id in enumerate(dim_ids):
            if wrong:
                score = ((h >> (index * 3)) % 20) / 100.0  # 0.00 ~ 0.19
            else:
                score = 0.75 + ((h >> (index * 5)) % 24) / 100.0  # 0.75 ~ 0.98
            dimensions[dim_id] = round(score, 2)
        payload = {
            "dimensions": dimensions,
            "fatal_error": wrong,
            "summary": "mock judge 确定性评分",
            "key_errors": ["最终答案错误"] if wrong else [],
        }
        return json.dumps(payload, ensure_ascii=False)
