"""Smoke test for the local bge-m3 weights (spec: ingestion §3.8). Needs `make models` first."""

import math

import pytest
from huggingface_hub import try_to_load_from_cache

from samvidhan.core.config import Settings
from samvidhan.db.models import EMBEDDING_DIM

pytestmark = pytest.mark.slow


def test_embed_model_is_cached_and_emits_unit_vectors(settings: Settings) -> None:
    if not isinstance(try_to_load_from_cache(settings.embed_model, "config.json"), str):
        pytest.skip(f"{settings.embed_model} not downloaded; run `make models`")
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(settings.embed_model)
    vectors = model.encode(
        ["Article 21: Protection of life and personal liberty"], normalize_embeddings=True
    )

    assert vectors.shape == (1, EMBEDDING_DIM)
    assert math.isclose(float((vectors[0] ** 2).sum()), 1.0, rel_tol=1e-4)
