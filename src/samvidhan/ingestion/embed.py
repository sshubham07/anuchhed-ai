"""Embed step: bge-m3 dense vectors and token counting (spec: ingestion §3.7, ADR-0008).

The model is loaded once per process and injected; nothing downloads here — run `make models`
first (a missing model fails with a pointer to it instead of a silent 2 GB download).
"""

import hashlib
import math
import threading
from collections.abc import Sequence
from typing import Protocol

from samvidhan.core.errors import InvalidSourceError

# PyTorch's MPS backend is not thread-safe: concurrent forward passes from `asyncio.to_thread`
# workers race on its shader cache and segfault. Every local model call takes this lock.
MODEL_LOCK = threading.Lock()


class Embedder(Protocol):
    model_id: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def resolve_device(setting: str) -> str:
    """`auto` → cuda, then mps, then cpu."""
    if setting != "auto":
        return setting
    import torch

    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


class BgeM3Embedder:
    """Normalised dense embeddings from a local sentence-transformers model."""

    def __init__(self, model_id: str, *, device: str, batch_size: int, max_length: int) -> None:
        from sentence_transformers import SentenceTransformer

        try:
            self._model = SentenceTransformer(
                model_id, device=resolve_device(device), local_files_only=True
            )
        except OSError as exc:
            raise InvalidSourceError(
                f"Model {model_id} is not downloaded; run `make models`"
            ) from exc
        self._model.max_seq_length = max_length
        self._batch_size = batch_size
        self.model_id = model_id
        self.dimension = int(self._model.get_embedding_dimension() or 0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        with MODEL_LOCK:
            vectors = self._model.encode(
                list(texts),
                batch_size=self._batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        return [vector.tolist() for vector in vectors]


class FakeEmbedder:
    """Deterministic unit vectors for tests (hash-seeded; no model)."""

    def __init__(self, dimension: int, model_id: str = "fake-embedder") -> None:
        self.model_id = model_id
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        raw = [(seed[i % len(seed)] - 127.5) + i % 7 for i in range(self.dimension)]
        norm = math.sqrt(sum(value * value for value in raw))
        return [value / norm for value in raw]


class TokenizerCounter:
    """Counts tokens with the embedding model's own tokenizer (special tokens included)."""

    def __init__(self, model_id: str) -> None:
        from transformers import AutoTokenizer

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
        except OSError as exc:
            raise InvalidSourceError(
                f"Tokenizer for {model_id} is not downloaded; run `make models`"
            ) from exc
        # Whole Schedules are counted before they are split; only counting, never embedding,
        # so lift the 8192-token limit that would otherwise log a warning.
        self._tokenizer.model_max_length = 10**9

    def __call__(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=True))
