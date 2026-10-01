"""Cross-encoder rerank with bge-reranker-v2-m3 (spec: retrieval §3.7, ADR-0008).

Scores are sigmoid probabilities in [0, 1], so they compare directly with LOW_CONFIDENCE_THRESHOLD.
"""

import re
from collections.abc import Sequence
from typing import Protocol

from samvidhan.core.errors import InvalidSourceError
from samvidhan.ingestion.embed import MODEL_LOCK, resolve_device


class Reranker(Protocol):
    model_id: str

    def score(self, query: str, texts: Sequence[str]) -> list[float]: ...


class BgeReranker:
    """Local cross-encoder; loaded once per process and injected."""

    def __init__(self, model_id: str, *, device: str, max_length: int) -> None:
        import torch
        from sentence_transformers import CrossEncoder

        try:
            self._model = CrossEncoder(
                model_id,
                device=resolve_device(device),
                max_length=max_length,
                local_files_only=True,
                activation_fn=torch.nn.Sigmoid(),
            )
        except OSError as exc:
            raise InvalidSourceError(
                f"Model {model_id} is not downloaded; run `make models RERANK=1`"
            ) from exc
        self.model_id = model_id

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        with MODEL_LOCK:
            scores = self._model.predict([(query, text) for text in texts], show_progress_bar=False)
        return [float(value) for value in scores]


_WORD = re.compile(r"\w+")


class FakeReranker:
    """Share of query words found in the text; deterministic, no model (tests)."""

    model_id = "fake-reranker"

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        words = {w.lower() for w in _WORD.findall(query)}
        if not words:
            return [0.0 for _ in texts]
        return [
            len(words & {w.lower() for w in _WORD.findall(text)}) / len(words) for text in texts
        ]
