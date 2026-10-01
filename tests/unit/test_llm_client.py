"""LLMClient: retries, fallback, budget, streaming and one record per attempt (spec §3.3)."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest
import structlog
import structlog.testing

from samvidhan.core.errors import LLMBusyError, LLMUnavailableError
from samvidhan.llm.budget import BudgetGuard
from samvidhan.llm.client import LLMClient, is_configured
from samvidhan.llm.fake import FakeProvider, fake_llm
from samvidhan.llm.recorder import MemoryCallRecorder
from samvidhan.llm.types import Completion, LLMRequest, LLMResult, ProviderError, ProviderRequest

PRIMARY, FALLBACK = "groq/llama-3.3-70b-versatile", "gemini/gemini-flash"


def request(models: tuple[str, ...] = (PRIMARY, FALLBACK), timeout_s: float = 5) -> LLMRequest:
    return LLMRequest(
        purpose="answer",
        models=models,
        messages=[{"role": "user", "content": "What does Article 21 say?"}],
        max_tokens=100,
        temperature=0.1,
        timeout_s=timeout_s,
        prompt_version="answer.v1",
    )


def client(provider: FakeProvider, **kwargs: object) -> tuple[LLMClient, MemoryCallRecorder]:
    recorder = MemoryCallRecorder()
    return fake_llm(provider, recorder, **kwargs), recorder  # type: ignore[arg-type]


async def test_primary_ok_records_one_row() -> None:
    llm, recorder = client(FakeProvider({"answer": "Life and liberty [Art. 21]."}))
    result = await llm.complete(request())
    assert result.text == "Life and liberty [Art. 21]." and result.model == PRIMARY
    assert result.status == "ok" and result.provider == "groq" and result.attempts == 1
    [row] = recorder.records
    assert (row.purpose, row.model, row.status, row.prompt_version) == (
        "answer", PRIMARY, "ok", "answer.v1"
    )  # fmt: skip
    assert row.input_tokens and row.output_tokens and row.latency_ms is not None


async def test_bad_key_falls_back_without_retry() -> None:
    provider = FakeProvider({"answer": "fallback answer"}, fail_models={PRIMARY: "auth"})
    llm, recorder = client(provider)
    with structlog.testing.capture_logs() as logs:
        result = await llm.complete(request())
    assert result.model == FALLBACK and result.status == "fallback" and result.attempts == 2
    assert [(r.model, r.status, r.error_code) for r in recorder.records] == [
        (PRIMARY, "error", "auth"),
        (FALLBACK, "fallback", None),
    ]
    fallback = next(e for e in logs if e["event"] == "llm_fallback")
    assert fallback["from_model"] == PRIMARY and fallback["to_model"] == FALLBACK
    assert fallback["error_code"] == "auth"


async def test_retryable_error_is_retried_once_then_falls_back() -> None:
    provider = FakeProvider({"answer": "ok"}, fail_models={PRIMARY: "rate_limited"})
    llm, recorder = client(provider)
    result = await llm.complete(request())
    assert result.model == FALLBACK
    assert [r.status for r in recorder.records] == ["error", "error", "fallback"]
    assert [c.model for c in provider.calls] == [PRIMARY, PRIMARY, FALLBACK]


async def test_every_model_failing_raises_unavailable_and_logs_each_attempt() -> None:
    provider = FakeProvider({"answer": "x"}, fail_models={PRIMARY: "auth", FALLBACK: "timeout"})
    llm, recorder = client(provider)
    with pytest.raises(LLMUnavailableError):
        await llm.complete(request())
    assert [(r.model, r.error_code) for r in recorder.records] == [
        (PRIMARY, "auth"),
        (FALLBACK, "timeout"),
        (FALLBACK, "timeout"),
    ]


async def test_placeholder_fallback_is_skipped() -> None:
    provider = FakeProvider({"answer": "x"}, fail_models={PRIMARY: "auth"})
    llm, recorder = client(provider)
    with structlog.testing.capture_logs() as logs, pytest.raises(LLMUnavailableError):
        await llm.complete(request((PRIMARY, "gemini/<current-flash-id>", "")))
    assert [c.model for c in provider.calls] == [PRIMARY]
    assert any(e["event"] == "llm_model_unconfigured" for e in logs)
    assert not is_configured("gemini/<current-flash-id>") and is_configured(PRIMARY)


async def test_hung_provider_times_out() -> None:
    class Hanging(FakeProvider):
        async def complete(self, request: ProviderRequest) -> Completion:
            if request.model == PRIMARY:
                await asyncio.sleep(10)
            return await super().complete(request)

    llm, recorder = client(Hanging({"answer": "late but fine"}), max_retries=0)
    result = await llm.complete(request(timeout_s=0.01))
    assert result.model == FALLBACK
    assert recorder.records[0].error_code == "timeout"


async def test_request_and_session_ids_come_from_log_context() -> None:
    session_id = uuid.uuid4()
    structlog.contextvars.bind_contextvars(request_id="req-1", session_id=str(session_id))
    llm, recorder = client(FakeProvider({"answer": "x"}))
    await llm.complete(request())
    assert recorder.records[0].request_id == "req-1"
    assert recorder.records[0].session_id == session_id


async def _count(model: str) -> int:
    return {PRIMARY: 950, FALLBACK: 10}.get(model, 0)


async def test_budget_near_cap_routes_to_fallback() -> None:
    budget = BudgetGuard(_count, {PRIMARY: 1000, FALLBACK: 1000}, warn_ratio=0.8)
    provider = FakeProvider({"answer": "x"})
    llm, recorder = client(provider, budget=budget)
    with structlog.testing.capture_logs() as logs:
        result = await llm.complete(request())
    assert result.model == FALLBACK and result.status == "fallback"
    assert [c.model for c in provider.calls] == [FALLBACK]
    near = next(e for e in logs if e["event"] == "budget_near_cap")
    assert near["model"] == PRIMARY and near["used_today"] == 950 and near["cap"] == 1000


async def test_every_model_over_budget_is_busy_without_calls() -> None:
    budget = BudgetGuard(_count, {PRIMARY: 100, FALLBACK: 10}, warn_ratio=0.8)
    provider = FakeProvider({"answer": "x"})
    llm, recorder = client(provider, budget=budget)
    with pytest.raises(LLMBusyError):
        await llm.complete(request())
    assert provider.calls == [] and recorder.records == []


async def _collect(stream: AsyncIterator[str | LLMResult]) -> tuple[list[str], LLMResult]:
    tokens: list[str] = []
    async for item in stream:
        if isinstance(item, LLMResult):
            return tokens, item
        tokens.append(item)
    raise AssertionError("stream ended without a result")


async def test_stream_yields_tokens_then_result_with_ttft() -> None:
    llm, recorder = client(FakeProvider({"answer": "No person shall be deprived [Art. 21]."}))
    tokens, result = await _collect(llm.stream(request()))
    assert "".join(tokens) == "No person shall be deprived [Art. 21]."
    assert result.text == "".join(tokens) and result.ttft_ms is not None
    assert recorder.records[0].ttft_ms == result.ttft_ms and recorder.records[0].status == "ok"


async def test_stream_falls_back_before_the_first_token() -> None:
    provider = FakeProvider({"answer": "from gemini"}, fail_models={PRIMARY: "provider_5xx"})
    llm, recorder = client(provider)
    tokens, result = await _collect(llm.stream(request()))
    assert "".join(tokens) == "from gemini" and result.status == "fallback"
    assert [r.status for r in recorder.records] == ["error", "error", "fallback"]


async def test_stream_failure_after_tokens_is_unavailable() -> None:
    provider = FakeProvider({"answer": "one two three four"}, fail_after_tokens=2)
    llm, recorder = client(provider)
    tokens: list[str] = []
    with pytest.raises(LLMUnavailableError):
        async for item in llm.stream(request()):
            assert isinstance(item, str)
            tokens.append(item)
    assert tokens == ["one ", "two "]
    assert [c.model for c in provider.calls] == [PRIMARY]  # no replay on another provider
    assert recorder.records[0].error_code == "connection"


async def test_stream_that_hangs_times_out_and_falls_back() -> None:
    class HangingStream(FakeProvider):
        async def stream(self, request: ProviderRequest) -> AsyncIterator[str | Completion]:
            if request.model == PRIMARY:
                await asyncio.sleep(10)
            async for item in super().stream(request):
                yield item

    llm, recorder = client(HangingStream({"answer": "from fallback"}), max_retries=0)
    tokens, result = await _collect(llm.stream(request(timeout_s=0.05)))
    assert "".join(tokens) == "from fallback" and result.model == FALLBACK
    assert recorder.records[0].error_code == "timeout"


async def test_slow_consumer_does_not_count_against_the_timeout() -> None:
    llm, _ = client(FakeProvider({"answer": "a b c"}))
    tokens: list[str] = []
    async for item in llm.stream(request(timeout_s=0.05)):
        if isinstance(item, str):
            tokens.append(item)
            await asyncio.sleep(0.03)  # 3 × 30 ms > 50 ms deadline, but it's our time, not theirs
    assert tokens == ["a ", "b ", "c"]


async def test_stream_truncation_is_reported() -> None:
    llm, _ = client(FakeProvider({"answer": "cut off"}, finish_reason="length"))
    _, result = await _collect(llm.stream(request()))
    assert result.truncated


def test_provider_error_retryable_codes() -> None:
    assert ProviderError("timeout").retryable and ProviderError("rate_limited").retryable
    assert not ProviderError("auth").retryable and not ProviderError("bad_request").retryable
