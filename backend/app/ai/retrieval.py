"""Hybrid retrieval: BM25 (tsvector) + dense (pgvector) → RRF → Voyage rerank.

Returns ranked Item + (optionally) topic-page chunks for the Ask pipeline.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embed import embed_texts, rerank

CandidateKind = Literal["item", "topic_page"]


@dataclass
class Candidate:
    kind: CandidateKind
    chunk_id: uuid.UUID
    parent_id: uuid.UUID  # item.id or topic_page.id
    ord: int
    text: str
    title: str | None
    url: str | None       # for items
    slug: str | None      # for topic pages
    score: float = 0.0


def _rrf_merge(rankings: list[list[tuple[str, str]]], k: int = 60) -> dict[tuple[str, str], float]:
    """Reciprocal Rank Fusion over multiple ranked lists.

    Each ranking is a list of (kind, chunk_id_str). Returns
    {(kind, chunk_id_str): rrf_score}.
    """
    out: dict[tuple[str, str], float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            out[key] = out.get(key, 0.0) + 1.0 / (k + rank + 1)
    return out


async def _dense_search(
    session: AsyncSession, query_vec: list[float], limit: int
) -> list[Candidate]:
    sql = text(
        """
        WITH ic AS (
            SELECT 'item'::text AS kind,
                   ic.id AS chunk_id,
                   ic.item_id AS parent_id,
                   ic.ord, ic.text,
                   i.title AS title,
                   i.url AS url,
                   NULL::text AS slug,
                   (ic.embedding <=> CAST(:qv AS vector)) AS distance
            FROM item_chunks ic
            JOIN items i ON i.id = ic.item_id
            WHERE i.status = 'ready' AND ic.embedding IS NOT NULL
            ORDER BY ic.embedding <=> CAST(:qv AS vector)
            LIMIT :lim
        ),
        tc AS (
            SELECT 'topic_page'::text AS kind,
                   tc.id AS chunk_id,
                   tc.topic_page_id AS parent_id,
                   tc.ord, tc.text,
                   tp.title AS title,
                   NULL::text AS url,
                   tp.slug AS slug,
                   (tc.embedding <=> CAST(:qv AS vector)) AS distance
            FROM topic_page_chunks tc
            JOIN topic_pages tp ON tp.id = tc.topic_page_id
            WHERE tc.embedding IS NOT NULL
            ORDER BY tc.embedding <=> CAST(:qv AS vector)
            LIMIT :lim
        )
        SELECT * FROM ic
        UNION ALL
        SELECT * FROM tc
        ORDER BY distance
        LIMIT :lim
        """
    )
    qv_str = "[" + ",".join(f"{x:.7f}" for x in query_vec) + "]"
    result = await session.execute(sql, {"qv": qv_str, "lim": limit})
    return [
        Candidate(
            kind=row.kind,
            chunk_id=row.chunk_id,
            parent_id=row.parent_id,
            ord=row.ord,
            text=row.text,
            title=row.title,
            url=row.url,
            slug=row.slug,
        )
        for row in result
    ]


async def _lexical_search(
    session: AsyncSession, query: str, limit: int
) -> list[Candidate]:
    # Use the precomputed `fts_doc` generated columns (created in the
    # initial migration) — same content as item/topic-page fields, indexed
    # for fast GIN lookup. For per-chunk ranking we still need a runtime
    # tsvector over the chunk text, but that's a per-row computation, not
    # an index expression, so STABLE is fine.
    sql = text(
        """
        WITH q AS (SELECT websearch_to_tsquery('english'::regconfig, :q) AS tsq),
        ic AS (
            SELECT 'item'::text AS kind,
                   ic.id AS chunk_id,
                   ic.item_id AS parent_id,
                   ic.ord, ic.text,
                   i.title, i.url,
                   NULL::text AS slug,
                   ts_rank_cd(
                     i.fts_doc ||
                     to_tsvector('english'::regconfig, coalesce(ic.text,'')),
                     q.tsq
                   ) AS score
            FROM item_chunks ic
            JOIN items i ON i.id = ic.item_id, q
            WHERE i.status = 'ready'
              AND (
                i.fts_doc @@ q.tsq
                OR to_tsvector('english'::regconfig, coalesce(ic.text,'')) @@ q.tsq
              )
            ORDER BY score DESC
            LIMIT :lim
        ),
        tc AS (
            SELECT 'topic_page'::text AS kind,
                   tc.id AS chunk_id,
                   tc.topic_page_id AS parent_id,
                   tc.ord, tc.text,
                   tp.title,
                   NULL::text AS url,
                   tp.slug,
                   ts_rank_cd(
                     tp.fts_doc ||
                     to_tsvector('english'::regconfig, coalesce(tc.text,'')),
                     q.tsq
                   ) AS score
            FROM topic_page_chunks tc
            JOIN topic_pages tp ON tp.id = tc.topic_page_id, q
            WHERE tp.fts_doc @@ q.tsq
               OR to_tsvector('english'::regconfig, coalesce(tc.text,'')) @@ q.tsq
            ORDER BY score DESC
            LIMIT :lim
        )
        SELECT * FROM ic
        UNION ALL
        SELECT * FROM tc
        ORDER BY score DESC
        LIMIT :lim
        """
    )
    result = await session.execute(sql, {"q": query, "lim": limit})
    return [
        Candidate(
            kind=row.kind,
            chunk_id=row.chunk_id,
            parent_id=row.parent_id,
            ord=row.ord,
            text=row.text,
            title=row.title,
            url=row.url,
            slug=row.slug,
        )
        for row in result
    ]


async def hybrid_search(
    session: AsyncSession,
    query: str,
    *,
    candidate_limit: int = 50,
    final_limit: int = 6,
    use_reranker: bool = True,
) -> list[Candidate]:
    """Run dense + lexical, fuse with RRF, optionally rerank, return top-N."""
    embeddings = await embed_texts([query], input_type="query")
    if not embeddings:
        return []
    qv = embeddings[0]

    dense = await _dense_search(session, qv, candidate_limit)
    lexical = await _lexical_search(session, query, candidate_limit)

    by_key: dict[tuple[str, str], Candidate] = {}
    for c in dense + lexical:
        by_key[(c.kind, str(c.chunk_id))] = c

    rrf = _rrf_merge(
        [
            [(c.kind, str(c.chunk_id)) for c in dense],
            [(c.kind, str(c.chunk_id)) for c in lexical],
        ]
    )

    fused = sorted(
        ((by_key[k], v) for k, v in rrf.items() if k in by_key),
        key=lambda kv: kv[1],
        reverse=True,
    )
    fused_candidates = [c for c, _ in fused[: max(final_limit * 4, 12)]]
    for cand, score in fused[: len(fused_candidates)]:
        cand.score = score

    if not use_reranker or len(fused_candidates) <= final_limit:
        return fused_candidates[:final_limit]

    try:
        rerank_input = [c.text for c in fused_candidates]
        rerank_result = await rerank(query, rerank_input, top_k=final_limit)
        reranked = []
        for idx, score in rerank_result:
            cand = fused_candidates[idx]
            cand.score = float(score)
            reranked.append(cand)
        return reranked
    except Exception:
        # Reranker is optional — fall back to RRF order.
        return fused_candidates[:final_limit]


# Keep bindparam imported for SQLAlchemy versions that want it explicit.
_ = bindparam
