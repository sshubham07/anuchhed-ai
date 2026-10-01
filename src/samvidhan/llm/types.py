"""LLM layer types (spec: llm-router-generation §3.3)."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, TypedDict

Purpose = Literal["router", "answer", "hyde", "summary", "eval_judge"]
CallStatus = Literal["ok", "error", "fallback"]


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class LLMRequest:
    purpose: Purpose
    models: Sequence[str]  # primary first, then fallbacks
    messages: Sequence[Message]
    max_tokens: int
    temperature: float
    timeout_s: float
    json_mode: bool = False
    prompt_version: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """One attempt against one model. `purpose` lets fakes script replies; providers ignore it."""

    purpose: Purpose
    model: str
    messages: Sequence[Message]
    max_tokens: int
    temperature: float
    timeout_s: float
    json_mode: bool


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    cost_usd: Decimal = Decimal(0)


class ProviderError(Exception):
    """A failed provider attempt with a stable `code`; `retryable` errors are retried once."""

    RETRYABLE = frozenset({"timeout", "rate_limited", "provider_5xx", "connection"})

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)

    @property
    def retryable(self) -> bool:
        return self.code in self.RETRYABLE


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    provider: str
    status: CallStatus  # 'fallback' when a later model answered
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    ttft_ms: int | None
    attempts: int  # provider calls made, including failed ones

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One `llm_calls` row (HLD §10)."""

    purpose: Purpose
    provider: str
    model: str
    status: CallStatus
    prompt_version: str | None = None
    request_id: str | None = None
    session_id: uuid.UUID | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    ttft_ms: int | None = None
    cost_usd: Decimal = field(default_factory=lambda: Decimal(0))
    error_code: str | None = None


def provider_of(model: str) -> str:
    """`groq/llama-3.3-70b-versatile` → `groq`."""
    return model.split("/", 1)[0] if "/" in model else "unknown"
