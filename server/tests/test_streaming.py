"""LiteLLM 流式聚合测试：chunk 合并、tool_calls 累积、usage 与 TTFT 采集、重试。"""

from __future__ import annotations

import asyncio

import pytest

from app.runtime.base import ModelRequest
from app.runtime.litellm_client import LiteLLMClient, _stream_completion, _stream_with_retry


class _FakeDelta:
    def __init__(self, content=None, reasoning_content=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning_content
        self.tool_calls = tool_calls


class _FakeToolCallDelta:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5
    prompt_tokens_details = None
    completion_tokens_details = None


class _FakeChunk:
    def __init__(self, delta=None, usage=None, finish_reason=None):
        self.choices = [type("C", (), {"delta": delta, "finish_reason": finish_reason})()] if delta is not None or finish_reason else []
        self.usage = usage


async def _fake_stream(chunks):
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_stream_aggregates_content_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        _FakeChunk(delta=_FakeDelta(content="答")),
        _FakeChunk(delta=_FakeDelta(content="案是 42")),
        _FakeChunk(usage=_FakeUsage(), finish_reason="stop"),
    ]
    import litellm

    async def fake_acompletion(**kwargs):
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}
        return _fake_stream(chunks)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    agg = await _stream_completion({"model": "x", "messages": []})
    assert agg.content == "答案是 42"
    assert agg.usage.input_tokens == 10
    assert agg.usage.output_tokens == 5
    assert agg.finish_reason == "stop"
    assert agg.ttft_s is not None and agg.ttft_s >= 0
    assert agg.generation_time_s is not None


@pytest.mark.asyncio
async def test_stream_aggregates_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        _FakeChunk(delta=_FakeDelta(tool_calls=[_FakeToolCallDelta(0, id="c1", name="write_file", arguments='{"path":"a.py",')])),
        _FakeChunk(delta=_FakeDelta(tool_calls=[_FakeToolCallDelta(0, arguments='"content":"x=1"}')])),
        _FakeChunk(usage=_FakeUsage(), finish_reason="tool_calls"),
    ]
    import litellm

    async def fake_acompletion(**kwargs):
        return _fake_stream(chunks)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    client = LiteLLMClient(litellm_model="openai/x")
    step = await client.complete_with_tools(ModelRequest(messages=[{"role": "user", "content": "go"}]), tools=[])
    assert len(step.tool_calls) == 1
    assert step.tool_calls[0].id == "c1"
    assert step.tool_calls[0].name == "write_file"
    assert step.tool_calls[0].arguments == {"path": "a.py", "content": "x=1"}
    assert step.assistant_message["tool_calls"][0]["function"]["name"] == "write_file"


class _FakeAPIError(Exception):
    """模拟 litellm 异常：status_code 决定重试行为。"""

    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.asyncio
async def test_stream_retry_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _FakeAPIError(554, "gateway")
        return _fake_stream([_FakeChunk(delta=_FakeDelta(content="ok")), _FakeChunk(usage=_FakeUsage())])

    monkeypatch.setattr(litellm, "acompletion", flaky)
    monkeypatch.setattr("asyncio.sleep", lambda s: _instant())  # 重试退避不等

    agg = await _stream_with_retry({"model": "x", "messages": []})
    assert agg.content == "ok"
    assert calls["n"] == 3


async def _instant() -> None:
    return None


@pytest.mark.asyncio
async def test_stream_call_timeout_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次调用墙钟上限：超时视为确定性失败，不重试。"""
    import litellm

    calls = {"n": 0}

    async def hanging(**kwargs):
        calls["n"] += 1
        await asyncio.sleep(10)
        return _fake_stream([])

    monkeypatch.setattr(litellm, "acompletion", hanging)
    with pytest.raises(TimeoutError):
        await _stream_with_retry({"model": "x", "messages": []}, call_timeout_s=0.05)
    assert calls["n"] == 1  # 墙钟超时不重试


@pytest.mark.asyncio
async def test_stream_no_retry_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    import litellm

    async def bad(**kwargs):
        raise _FakeAPIError(401, "bad key")

    monkeypatch.setattr(litellm, "acompletion", bad)
    with pytest.raises(_FakeAPIError):
        await _stream_completion({"model": "x", "messages": []})
