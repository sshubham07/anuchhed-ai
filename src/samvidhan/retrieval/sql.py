"""Raw SQL for retrieval — the only place it lives (spec: retrieval §3.2–3.6).

Every statement uses bound parameters and is restricted to the active document. Kept as plain
literals (no string building) so each query reads top to bottom.
"""

from sqlalchemy import text

# HNSW filters after the index scan; once inactive documents share the index, a plain scan can
# return fewer than k active rows. Iterative scan (pgvector ≥ 0.8) keeps searching until k match.
ITERATIVE_SCAN = text("SET LOCAL hnsw.iterative_scan = strict_order")

DENSE = text(
    """
    SELECT c.id, c.chunk_type, c.seq, c.article_no, c.schedule_no, c.appendix_no,
           c.article_title, c.text, c.embed_text,
           1 - (c.embedding <=> CAST(:query_vec AS vector)) AS score
    FROM chunks c
    WHERE c.document_id = (SELECT id FROM documents WHERE is_active)
    ORDER BY c.embedding <=> CAST(:query_vec AS vector)
    LIMIT :k
    """
)

# websearch_to_tsquery ANDs every term, so a natural-language question rarely matches any one
# chunk; the lexical leg ORs the same parsed terms instead (spec §3.4).
LEXICAL = text(
    """
    SELECT c.id, c.chunk_type, c.seq, c.article_no, c.schedule_no, c.appendix_no,
           c.article_title, c.text, c.embed_text,
           ts_rank_cd(c.tsv, q.query) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.is_active,
         (SELECT replace(websearch_to_tsquery('english', :query)::text, ' & ', ' | ')::tsquery
                 AS query) q
    WHERE c.tsv @@ q.query
    ORDER BY score DESC, c.seq
    LIMIT :k
    """
)

# HLD's original AND semantics; used only by the ablation.
LEXICAL_AND = text(
    """
    SELECT c.id, c.chunk_type, c.seq, c.article_no, c.schedule_no, c.appendix_no,
           c.article_title, c.text, c.embed_text,
           ts_rank_cd(c.tsv, q.query) AS score
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.is_active,
         (SELECT websearch_to_tsquery('english', :query) AS query) q
    WHERE c.tsv @@ q.query
    ORDER BY score DESC, c.seq
    LIMIT :k
    """
)

LOOKUP = text(
    """
    SELECT c.id, c.chunk_type, c.seq, c.article_no, c.schedule_no, c.appendix_no,
           c.article_title, c.text, c.embed_text
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.is_active
    WHERE c.article_no = ANY(:articles)
       OR c.schedule_no = ANY(:schedules)
       OR c.appendix_no = ANY(:appendices)
       OR (:preamble AND c.chunk_type = 'preamble')
    ORDER BY c.seq
    """
)

KNOWN_REFS = text(
    """
    SELECT DISTINCT c.chunk_type, c.article_no, c.schedule_no, c.appendix_no
    FROM chunks c
    JOIN documents d ON d.id = c.document_id AND d.is_active
    """
)
