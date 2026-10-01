"""RetrievalService with stubbed legs and fakes (spec: retrieval §3.1, §3.7–3.8)."""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest
import structlog.testing

from samvidhan.core.config import Settings
from samvidhan.core.errors import ServiceUnavailableError
from samvidhan.ingestion.embed import FakeEmbedder
from samvidhan.retrieval import service as service_module
from samvidhan.retrieval.rerank import FakeReranker
from samvidhan.retrieval.service import RetrievalService
from samvidhan.retrieval.types import ScoredChunk
from tests.unit.retrieval_helpers import chunk

KNOWN = frozenset({"14", "21", "21A", "22", "SCH-7"})
ART21 = chunk("art-21#0", article_no="21", text="life and personal liberty", seq=21)
ART22 = chunk("art-22#0", article_no="22", text="arrest and detention grounds", seq=22)
ART14 = chunk("art-14#0", article_no="14", text="equality before law", seq=14)
ART21A = chunk("art-21A#0", article_no="21A", text="right to education", seq=23, pinned=True)


class RaisingReranker:
    model_id = "boom"

    def score(self, query: str, texts: Sequence[str]) -> list[float]:
        raise RuntimeError("model crashed")


@asynccontextmanager
async def _fake_session() -> AsyncIterator[None]:
    yield None


def _service(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    *,
    dense: list[ScoredChunk],
    lexical: list[ScoredChunk],
    pinned: list[ScoredChunk] = (),  # type: ignore[assignment]
    known: frozenset[str] = KNOWN,
    reranker: Any = None,
) -> RetrievalService:
    def ranked(stage: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        return [c.with_stage(stage, r, 1.0 / r) for r, c in enumerate(chunks, start=1)]

    async def fake_dense(session: Any, vec: Any, k: int) -> list[ScoredChunk]:
        return ranked("dense", dense)[:k]

    async def fake_lexical(session: Any, query: str, k: int, **_: Any) -> list[ScoredChunk]:
        return ranked("lexical", lexical)[:k]

    async def fake_pinned(session: Any, refs: Sequence[str]) -> list[ScoredChunk]:
        return [c for c in pinned if c.ref in refs]

    async def fake_known(session: Any) -> frozenset[str]:
        return known

    monkeypatch.setattr(service_module, "dense_search", fake_dense)
    monkeypatch.setattr(service_module, "lexical_search", fake_lexical)
    monkeypatch.setattr(service_module, "fetch_pinned", fake_pinned)
    monkeypatch.setattr(service_module, "load_known_refs", fake_known)
    return RetrievalService(
        _fake_session,  # type: ignore[arg-type]
        FakeEmbedder(8),
        reranker or FakeReranker(),
        settings,
    )


async def test_hybrid_rerank_orders_by_reranker(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    svc = _service(monkeypatch, settings, dense=[ART21, ART14], lexical=[ART14, ART22])
    result = await svc.retrieve("arrest detention grounds")
    assert result.chunks[0].id == "art-22#0"
    assert result.top_score == pytest.approx(1.0)
    assert not result.low_confidence
    assert {c.id for c in result.candidates} == {"art-21#0", "art-14#0", "art-22#0"}
    assert result.trace["final"][0]["id"] == "art-22#0"
    assert "rerank" in result.latency_ms and "retrieval" in result.latency_ms


async def test_pinned_first_deduped_and_refs_validated(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    pinned21 = chunk("art-21#0", article_no="21", seq=21, pinned=True)
    svc = _service(
        monkeypatch, settings, dense=[ART21, ART14], lexical=[], pinned=[ART21A, pinned21]
    )
    with structlog.testing.capture_logs() as logs:
        result = await svc.retrieve("education", refs=["Art. 21-A", "21", "999", "nonsense"])
    assert result.refs == ["21A", "21"]
    assert [c.id for c in result.chunks][:2] == ["art-21A#0", "art-21#0"]
    assert [c.id for c in result.chunks].count("art-21#0") == 1
    dropped = {e["ref"] for e in logs if e["event"] == "unknown_ref_dropped"}
    assert dropped == {"999", "nonsense"}


async def test_low_confidence_only_without_pins(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    svc = _service(monkeypatch, settings, dense=[ART14], lexical=[], pinned=[ART21A])
    with structlog.testing.capture_logs() as logs:
        weak = await svc.retrieve("zebra crossing")
        pinned = await svc.retrieve("zebra crossing", refs=["21A"])
    assert weak.top_score == 0.0 and weak.low_confidence
    assert not pinned.low_confidence
    assert [e["event"] for e in logs].count("low_confidence_retrieval") == 1


async def test_rerank_failure_keeps_rrf_order(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    svc = _service(
        monkeypatch, settings, dense=[ART21, ART14], lexical=[ART21], reranker=RaisingReranker()
    )
    with structlog.testing.capture_logs() as logs:
        result = await svc.retrieve("anything")
    assert [c.id for c in result.chunks] == ["art-21#0", "art-14#0"]
    assert result.top_score is None and not result.low_confidence
    assert any(e["event"] == "rerank_skipped" for e in logs)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("dense", ["art-21#0"]), ("lexical", ["art-22#0"]), ("hybrid", ["art-21#0", "art-22#0"])],
)
async def test_modes_limit_legs_and_skip_rerank(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, mode: Any, expected: list[str]
) -> None:
    svc = _service(monkeypatch, settings, dense=[ART21], lexical=[ART22])
    result = await svc.retrieve("q", mode=mode)
    assert [c.id for c in result.chunks] == expected
    assert result.top_score is None and "rerank" not in result.latency_ms


async def test_context_and_ref_caps_log_limit_applied(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    capped = settings.model_copy(update={"max_context_chunks": 2, "max_article_refs": 1})
    svc = _service(monkeypatch, capped, dense=[ART21, ART14, ART22], lexical=[], pinned=[ART21A])
    with structlog.testing.capture_logs() as logs:
        result = await svc.retrieve("q", refs=["21A", "14"])
    assert result.refs == ["21A"]
    assert len(result.chunks) == 2 and result.chunks[0].id == "art-21A#0"
    limits = {e["limit"] for e in logs if e["event"] == "limit_applied"}
    assert limits == {"article_refs", "context_chunks"}


async def test_no_active_corpus_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    svc = _service(monkeypatch, settings, dense=[], lexical=[], known=frozenset())
    with pytest.raises(ServiceUnavailableError):
        await svc.retrieve("q")
