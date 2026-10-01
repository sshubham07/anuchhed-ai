"""FakeLLM for tests and key-less demo runs (standards §8).

`FakeProvider` replaces only the provider: `fake_llm()` returns a real `LLMClient`, so retries,
fallback, budget and `llm_calls` logging run exactly as in production.
"""

import json
import re
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import dataclass, field

from samvidhan.llm.budget import BudgetGuard
from samvidhan.llm.client import LLMClient
from samvidhan.llm.recorder import CallRecorder, MemoryCallRecorder
from samvidhan.llm.types import Completion, ProviderError, ProviderRequest, Purpose

Reply = str | Callable[[ProviderRequest], str]


async def _no_sleep(_: float) -> None:
    return None


@dataclass
class FakeProvider:
    """Scripted replies per purpose.

    `responses[purpose]` is a string, a callable of the request, or a list consumed in order.
    `fail_models[model]` is an error code (e.g. `"auth"` for a bad key) raised for that model;
    `fail_after_tokens` makes `stream` fail after yielding that many deltas.
    """

    responses: Mapping[Purpose, Reply | list[Reply]] = field(default_factory=dict)
    fail_models: Mapping[str, str] = field(default_factory=dict)
    finish_reason: str = "stop"
    fail_after_tokens: int | None = None
    calls: list[ProviderRequest] = field(default_factory=list)
    _queues: dict[Purpose, list[Reply]] = field(default_factory=dict, init=False)

    def _reply(self, request: ProviderRequest) -> str:
        self.calls.append(request)
        if code := self.fail_models.get(request.model):
            raise ProviderError(code)
        script = self.responses.get(request.purpose)
        if script is None:
            raise AssertionError(f"FakeProvider has no reply for purpose {request.purpose!r}")
        if isinstance(script, list):
            queue = self._queues.setdefault(request.purpose, list(script))
            if not queue:
                raise AssertionError(f"FakeProvider ran out of {request.purpose!r} replies")
            script = queue.pop(0)
        return script(request) if callable(script) else script

    async def complete(self, request: ProviderRequest) -> Completion:
        text = self._reply(request)
        return Completion(
            text=text,
            input_tokens=_words(m["content"] for m in request.messages),
            output_tokens=_words([text]),
            finish_reason=self.finish_reason,
        )

    async def stream(self, request: ProviderRequest) -> AsyncIterator[str | Completion]:
        text = self._reply(request)
        tokens = re.findall(r"\S+\s*", text) or [text]
        for n, token in enumerate(tokens):
            if self.fail_after_tokens is not None and n == self.fail_after_tokens:
                raise ProviderError("connection")
            yield token
        yield Completion(
            text=text,
            input_tokens=_words(m["content"] for m in request.messages),
            output_tokens=len(tokens),
            finish_reason=self.finish_reason,
        )


def _words(texts: Iterable[str]) -> int:
    return sum(len(t.split()) for t in texts)


def fake_llm(
    provider: FakeProvider,
    recorder: CallRecorder | None = None,
    budget: BudgetGuard | None = None,
    *,
    max_retries: int = 1,
) -> LLMClient:
    return LLMClient(
        provider,
        recorder or MemoryCallRecorder(),
        budget,
        max_retries=max_retries,
        sleep=_no_sleep,
    )


# ---- Key-less demo replies (`graph.cli --fake-llm`) ----

_REF = re.compile(r"\b(?:article|art\.?)\s*(\d{1,3}[a-z]{0,3})\b", re.IGNORECASE)
_EXCERPT = re.compile(
    r'<excerpt id="[^"]+" cite="([^"]+)" title="([^"]*)">\s*(.*?)</excerpt>', re.S
)
_QUESTION = re.compile(r"<message>\s*(.*?)\s*</message>", re.S)


def demo_router(request: ProviderRequest) -> str:
    """Heuristic stand-in for the router: lookup when an Article is named, else simple."""
    message = _QUESTION.search(request.messages[-1]["content"])
    text = message.group(1) if message else request.messages[-1]["content"]
    refs = _REF.findall(text)
    lowered = text.lower().strip(" !.?")
    if lowered in {"hi", "hello", "hey", "thanks", "thank you"}:
        kind = "chitchat"
    else:
        kind = "article_lookup" if refs else "simple"
    return json.dumps(
        {"type": kind, "standalone_query": text, "article_refs": refs, "reason": "fake-llm"}
    )


def demo_answer(request: ProviderRequest) -> str:
    """Quotes the opening of the first two excerpts with their citations."""
    excerpts = _EXCERPT.findall(request.messages[-1]["content"])
    if not excerpts:
        return "The provided excerpts do not cover this question."
    lines = ["(fake LLM — no model was called) The most relevant provisions retrieved are:"]
    for cite, title, body in excerpts[:2]:
        snippet = " ".join(body.split())[:220]
        lines.append(f"- {title or cite}: “{snippet}…” [{cite}]")
    return "\n".join(lines)


def demo_provider() -> FakeProvider:
    return FakeProvider(
        responses={
            "router": demo_router,
            "answer": demo_answer,
            "hyde": lambda request: request.messages[-1]["content"],
        }
    )
