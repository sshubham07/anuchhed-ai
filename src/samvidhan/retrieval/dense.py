"""Dense leg: bge-m3 query vector → pgvector HNSW cosine top-k (spec: retrieval §3.3)."""

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from samvidhan.retrieval import sql
from samvidhan.retrieval.types import ScoredChunk, chunk_from_row


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector text form, `[0.1,0.2,…]`, bound as a parameter and cast in SQL."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


async def dense_search(
    session: AsyncSession, query_vec: Sequence[float], k: int
) -> list[ScoredChunk]:
    await session.execute(sql.ITERATIVE_SCAN)  # transaction-scoped; see sql.py
    result = await session.execute(sql.DENSE, {"query_vec": vector_literal(query_vec), "k": k})
    return [
        chunk_from_row(row).with_stage("dense", rank, float(row.score))
        for rank, row in enumerate(result, start=1)
    ]
