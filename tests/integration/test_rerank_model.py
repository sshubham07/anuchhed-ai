"""Smoke test for the local bge-reranker-v2-m3 weights (spec: retrieval §3.7).

Needs `make models RERANK=1` first.
"""

import pytest
from huggingface_hub import try_to_load_from_cache

from samvidhan.core.config import Settings

pytestmark = pytest.mark.slow


def test_reranker_scores_relevant_text_higher(settings: Settings) -> None:
    if not isinstance(try_to_load_from_cache(settings.rerank_model, "config.json"), str):
        pytest.skip(f"{settings.rerank_model} not downloaded; run `make models RERANK=1`")
    from samvidhan.retrieval.rerank import BgeReranker

    reranker = BgeReranker(
        settings.rerank_model, device="cpu", max_length=settings.rerank_max_length
    )
    relevant, other = reranker.score(
        "Who appoints the Chief Election Commissioner?",
        [
            "Article 324: The Chief Election Commissioner shall be appointed by the President.",
            "Article 48A: The State shall endeavour to protect and improve the environment.",
        ],
    )
    assert 0.0 <= other < relevant <= 1.0
