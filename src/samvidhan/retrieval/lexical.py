"""Lexical leg: Postgres FTS with OR semantics, ranked by ts_rank_cd (spec: retrieval §3.4)."""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.retrieval import sql
from samvidhan.retrieval.types import ScoredChunk, chunk_from_row

# websearch syntax `-word` becomes `!word`; OR-ed, `!word` matches almost every chunk.
_NEGATION = re.compile(r"(?:^|(?<=\s))-\S+")


def strip_negations(query: str) -> str:
    """Drop `-word` exclusions: under OR semantics they would match nearly everything."""
    return _NEGATION.sub(" ", query).strip()


async def lexical_search(
    session: AsyncSession, query: str, k: int, *, match_all: bool = False
) -> list[ScoredChunk]:
    """`match_all=True` uses plain `websearch_to_tsquery` (AND); only the ablation sets it."""
    statement = sql.LEXICAL_AND if match_all else sql.LEXICAL
    result = await session.execute(statement, {"query": strip_negations(query), "k": k})
    return [
        chunk_from_row(row).with_stage("lexical", rank, float(row.score))
        for rank, row in enumerate(result, start=1)
    ]
