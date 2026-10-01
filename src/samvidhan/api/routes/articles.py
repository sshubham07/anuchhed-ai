"""Corpus endpoints: full text for a citation chip, and corpus/model metadata (HLD §11)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan import __version__
from samvidhan.api.deps import get_app_settings, get_db
from samvidhan.api.schemas import ArticleOut, ErrorEnvelope, MetaOut
from samvidhan.core.config import Settings
from samvidhan.core.errors import NotFoundError
from samvidhan.db.repositories.corpus import CorpusRepository
from samvidhan.generation.citations import label
from samvidhan.retrieval.lookup import normalize_ref

router = APIRouter(tags=["corpus"])

Db = Annotated[AsyncSession, Depends(get_db)]


@router.get("/articles/{article_no}", responses={404: {"model": ErrorEnvelope}})
async def get_article(article_no: str, db: Db) -> ArticleOut:
    """`21A`, `21-A`, `Sch. 7`, `preamble`… → every chunk of that provision, in reading order."""
    ref = normalize_ref(article_no)
    chunks = await CorpusRepository(db).active_chunks_for_ref(ref) if ref else []
    if ref is None or not chunks:
        raise NotFoundError(f"{article_no!r} is not in the Constitution text")
    first = chunks[0]
    return ArticleOut(
        ref=ref,
        label=label(ref),
        title=first.article_title,
        part_no=first.part_no,
        part_title=first.part_title,
        is_omitted=all(c.is_omitted for c in chunks),
        text="\n\n".join(c.text for c in chunks),
        chunk_ids=[c.id for c in chunks],
    )


@router.get("/meta")
async def get_meta(db: Db, settings: Annotated[Settings, Depends(get_app_settings)]) -> MetaOut:
    """Edition, pipeline versions and model ids. No secrets."""
    return MetaOut(
        app_version=__version__,
        edition_date=await CorpusRepository(db).active_version_date(),
        chunker_version=settings.chunker_version,
        embed_model=settings.embed_model,
        rerank_model=settings.rerank_model,
        router_model=settings.router_model,
        answer_model=settings.answer_model,
        prompt_versions={
            "router": settings.router_prompt_version,
            "answer": settings.answer_prompt_version,
            "hyde": settings.hyde_prompt_version,
        },
    )
