"""The single LLM entry point (spec: llm-router-generation §3.3, observability §1.5).

Every provider attempt — ok, failed or fallback — becomes one `llm_calls` row and one
`llm_call_completed` event. No code path may call a model without going through `LLMClient`.

    for each model (primary, then fallbacks; placeholders and over-budget models skipped):
        try up to 1 + max_retries times (retry only timeouts / 429 / 5xx / connection errors)
    every model skipped by budget → LLMBusyError; otherwise → LLMUnavailableError
"""

import asyncio
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal

import structlog

from samvidhan.core.errors import LLMBusyError, LLMUnavailableError
from samvidhan.core.logging import get_logger
from samvidhan.llm.budget import BudgetGuard
from samvidhan.llm.provider import Provider
from samvidhan.llm.recorder import CallRecorder
from samvidhan.llm.types import (
    CallRecord,
    CallStatus,
    Completion,
    LLMRequest,
    LLMResult,
    ProviderError,
    ProviderRequest,
    provider_of,
)

log = get_logger(__name__)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def is_configured(model: str) -> bool:
    """False for empty ids and `.env.example` placeholders like `gemini/<current-flash-id>`."""
    return bool(model.strip()) and "<" not in model


@dataclass(slots=True)
class _Outcome:
    """Bookkeeping across the models of one request."""

    attempts: int = 0
    tried: int = 0  # models actually called
    skipped_by_budget: int = 0
    last_failure: tuple[str, str] | None = None  # (model, error_code)


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        recorder: CallRecorder,
        budget: BudgetGuard | None = None,
        *,
        max_retries: int = 1,
        backoff_s: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._provider = provider
        self._recorder = recorder
        self._budget = budget
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._sleep = sleep
        self._warned_unconfigured: set[str] = set()

    @property
    def recorder(self) -> CallRecorder:
        return self._recorder

    async def complete(self, request: LLMRequest) -> LLMResult:
        """Full reply from the first model that answers."""
        outcome = _Outcome()
        for position, model in enumerate(self._models(request)):
            if not await self._allowed(model):
                outcome.skipped_by_budget += 1
                continue
            self._note_fallback(request, outcome, model)
            outcome.tried += 1
            for attempt in range(1 + self._max_retries):
                outcome.attempts += 1
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(request.timeout_s):
                        completion = await self._provider.complete(
                            self._provider_request(request, model)
                        )
                except (ProviderError, TimeoutError) as exc:
                    error = _as_provider_error(exc)
                    self._record(request, model, "error", started, error_code=error.code)
                    outcome.last_failure = (model, error.code)
                    if error.retryable and attempt < self._max_retries:
                        await self._backoff(attempt)
                        continue
                    break
                return self._success(request, model, position, completion, started, None, outcome)
        raise self._exhausted(outcome)

    async def stream(self, request: LLMRequest) -> AsyncIterator[str | LLMResult]:
        """Text deltas, then one `LLMResult`. Falls back only before the first token: a partial
        answer can't be replayed on another provider, so a later failure → LLMUnavailableError."""
        outcome = _Outcome()
        for position, model in enumerate(self._models(request)):
            if not await self._allowed(model):
                outcome.skipped_by_budget += 1
                continue
            self._note_fallback(request, outcome, model)
            outcome.tried += 1
            for attempt in range(1 + self._max_retries):
                outcome.attempts += 1
                started = time.perf_counter()
                ttft_ms: int | None = None
                parts: list[str] = []
                final: Completion | None = None
                # The timeout budgets only time spent waiting on the provider, never the
                # consumer's time between tokens (a timeout across `yield` would cancel the
                # consumer's own code).
                waited = 0.0
                items = aiter(self._provider.stream(self._provider_request(request, model)))
                try:
                    while True:
                        wait_started = time.perf_counter()
                        try:
                            item = await asyncio.wait_for(
                                anext(items), max(request.timeout_s - waited, 0)
                            )
                        except StopAsyncIteration:
                            break
                        finally:
                            waited += time.perf_counter() - wait_started
                        if isinstance(item, Completion):
                            final = item
                        elif item:
                            if ttft_ms is None:
                                ttft_ms = _ms(started)
                            parts.append(item)
                            yield item
                except (ProviderError, TimeoutError) as exc:
                    error = _as_provider_error(exc)
                    self._record(
                        request, model, "error", started, error_code=error.code, ttft_ms=ttft_ms
                    )
                    outcome.last_failure = (model, error.code)
                    if parts:
                        raise LLMUnavailableError(
                            "The answer was interrupted. Please try again."
                        ) from exc
                    if error.retryable and attempt < self._max_retries:
                        await self._backoff(attempt)
                        continue
                    break
                finally:
                    await _close(items)  # release the provider's HTTP stream promptly
                completion = final or Completion(text="".join(parts))
                if final is not None and not final.text:
                    completion = Completion(
                        text="".join(parts),
                        input_tokens=final.input_tokens,
                        output_tokens=final.output_tokens,
                        finish_reason=final.finish_reason,
                        cost_usd=final.cost_usd,
                    )
                yield self._success(request, model, position, completion, started, ttft_ms, outcome)
                return
        raise self._exhausted(outcome)

    # ---- helpers ----

    def _models(self, request: LLMRequest) -> Iterator[str]:
        seen: set[str] = set()
        for model in request.models:
            if model in seen:
                continue
            seen.add(model)
            if not is_configured(model):
                if model not in self._warned_unconfigured:
                    self._warned_unconfigured.add(model)
                    log.warning("llm_model_unconfigured", model=model, purpose=request.purpose)
                continue
            yield model

    async def _allowed(self, model: str) -> bool:
        return self._budget is None or await self._budget.allows(model)

    @staticmethod
    def _note_fallback(request: LLMRequest, outcome: _Outcome, model: str) -> None:
        if outcome.last_failure is not None:
            from_model, code = outcome.last_failure
            log.warning(
                "llm_fallback",
                purpose=request.purpose,
                from_model=from_model,
                to_model=model,
                error_code=code,
            )

    @staticmethod
    def _provider_request(request: LLMRequest, model: str) -> ProviderRequest:
        return ProviderRequest(
            purpose=request.purpose,
            model=model,
            messages=request.messages,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            timeout_s=request.timeout_s,
            json_mode=request.json_mode,
        )

    async def _backoff(self, attempt: int) -> None:
        base = self._backoff_s * (2**attempt)
        await self._sleep(base + random.uniform(0, base))  # noqa: S311 — jitter, not crypto

    def _success(
        self,
        request: LLMRequest,
        model: str,
        position: int,
        completion: Completion,
        started: float,
        ttft_ms: int | None,
        outcome: _Outcome,
    ) -> LLMResult:
        status: CallStatus = "ok" if position == 0 else "fallback"
        latency_ms = self._record(
            request, model, status, started, completion=completion, ttft_ms=ttft_ms
        )
        return LLMResult(
            text=completion.text,
            model=model,
            provider=provider_of(model),
            status=status,
            finish_reason=completion.finish_reason,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            attempts=outcome.attempts,
        )

    def _record(
        self,
        request: LLMRequest,
        model: str,
        status: CallStatus,
        started: float,
        *,
        completion: Completion | None = None,
        error_code: str | None = None,
        ttft_ms: int | None = None,
    ) -> int:
        latency_ms = _ms(started)
        request_id, session_id = _context_ids()
        record = CallRecord(
            purpose=request.purpose,
            provider=provider_of(model),
            model=model,
            status=status,
            prompt_version=request.prompt_version,
            request_id=request_id,
            session_id=session_id,
            input_tokens=completion.input_tokens if completion else None,
            output_tokens=completion.output_tokens if completion else None,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            cost_usd=completion.cost_usd if completion else Decimal(0),
            error_code=error_code,
        )
        self._recorder.record(record)
        if self._budget is not None:
            self._budget.note_call(model)
        log.info(
            "llm_call_completed",
            purpose=record.purpose,
            provider=record.provider,
            model=model,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            status=status,
            error_code=error_code,
            prompt_version=request.prompt_version,
        )
        return latency_ms

    @staticmethod
    def _exhausted(outcome: _Outcome) -> Exception:
        if outcome.tried == 0 and outcome.skipped_by_budget:
            return LLMBusyError()
        return LLMUnavailableError()


async def _close(items: AsyncIterator[object]) -> None:
    aclose = getattr(items, "aclose", None)
    if aclose is not None:
        await aclose()


def _as_provider_error(exc: BaseException) -> ProviderError:
    return exc if isinstance(exc, ProviderError) else ProviderError("timeout")


def _context_ids() -> tuple[str | None, uuid.UUID | None]:
    """`request_id` / `session_id` bound by the request middleware or a CLI."""
    context = structlog.contextvars.get_contextvars()
    request_id = context.get("request_id")
    session_id = context.get("session_id")
    if session_id is not None and not isinstance(session_id, uuid.UUID):
        try:
            session_id = uuid.UUID(str(session_id))
        except ValueError:
            session_id = None
    return (str(request_id) if request_id else None), session_id
