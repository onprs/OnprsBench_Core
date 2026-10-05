"""模型调用运行时抽象。

业务层只依赖本模块定义的协议，不直接绑定 LiteLLM 内部数据结构。
保留该抽象层以便未来接入其他 eval runtime（如 Inspect AI adapter）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol


@dataclass
class ModelRequest:
    """一次模型调用的标准化输入。"""

    messages: list[dict[str, str]]
    # 推理与采样参数（None 表示不传递）
    reasoning_effort: str | None = None
    reasoning_budget: int | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    provider_params: dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 600.0


@dataclass
class UsageInfo:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass
class ModelResult:
    """一次模型调用的原始事实。"""

    text: str
    raw_response: dict[str, Any] | None
    usage: UsageInfo
    started_at: datetime
    finished_at: datetime
    total_latency_s: float
    # 非流式调用通常无法获得 TTFT；不支持时保持 None
    ttft_s: float | None = None
    generation_time_s: float | None = None


class ModelClient(Protocol):
    """模型客户端协议。任何 driver 实现该协议即可被 runner 使用。"""

    async def complete(self, request: ModelRequest) -> ModelResult: ...


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class CallTimer:
    """调用计时辅助。"""

    def __init__(self) -> None:
        self.started_at = now_utc()
        self._start = time.perf_counter()

    def finish(self) -> tuple[datetime, float]:
        finished = now_utc()
        return finished, time.perf_counter() - self._start
