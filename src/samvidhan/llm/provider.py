"""Provider protocol: one attempt against one model (spec: llm-router-generation §3.3).

`LLMClient` owns retries, fallback, budget and logging; providers only talk to a model.
"""

from collections.abc import AsyncIterator
from typing import Protocol

from samvidhan.llm.types import Completion, ProviderRequest


class Provider(Protocol):
    async def complete(self, request: ProviderRequest) -> Completion:
        """The full reply. Raises `ProviderError` on failure."""
        ...

    def stream(self, request: ProviderRequest) -> AsyncIterator[str | Completion]:
        """Text deltas, then exactly one final `Completion` (usage, finish reason, full text).
        Raises `ProviderError` on failure."""
        ...
