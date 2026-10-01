"""Local model calls never overlap across threads (spec: retrieval §3.7; MPS segfault fix)."""

import asyncio
import threading
import time
from typing import Any

import numpy as np

from samvidhan.ingestion.embed import BgeM3Embedder
from samvidhan.retrieval.rerank import BgeReranker


class OverlapProbe:
    """Stands in for both sentence-transformers models; records peak concurrent callers."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.active = 0
        self.peak = 0

    def _enter(self) -> None:
        with self._guard:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.02)
        with self._guard:
            self.active -= 1

    def encode(self, texts: list[str], **_: Any) -> np.ndarray:
        self._enter()
        return np.zeros((len(texts), 1))

    def predict(self, pairs: list[tuple[str, str]], **_: Any) -> list[float]:
        self._enter()
        return [0.0 for _ in pairs]


async def test_embed_and_rerank_calls_are_serialised() -> None:
    probe = OverlapProbe()
    embedder = object.__new__(BgeM3Embedder)
    embedder._model = probe  # type: ignore[assignment]
    embedder._batch_size = 1
    reranker = object.__new__(BgeReranker)
    reranker._model = probe

    await asyncio.gather(
        *(asyncio.to_thread(embedder.embed, ["q"]) for _ in range(4)),
        *(asyncio.to_thread(reranker.score, "q", ["t"]) for _ in range(4)),
    )

    assert probe.peak == 1
