"""Hybrid retrieval: BM25 (tsvector) + dense (pgvector) → RRF → Voyage rerank.

Returns ranked Item + (optionally) topic-page chunks for the Ask pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Literal

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embed import embed_query, rerank

log = logging.getLogger(__name__)

CandidateKind = Literal["item", "topic_page"]

# The values of `items.source`.
SOURCES = ("youtube", "twitter", "instagram", "reddit", "web", "pdf", "image")


@dataclass
class Candidate:
    kind: CandidateKind
    chunk_id: uuid.UUID
    parent_id: uuid.UUID  # item.id or topic_page.id
    ord: int
    text: str
    title: str | None
    url: str | None  # for items
    slug: str | None  # for topic pages
    score: float = 0.0
    source: str | None = None  # for items
    # When the item was saved, or the page last updated.
    at: datetime | None = None


@dataclass(frozen=True)
class Scope:
    """Searches only these items' and pages' chunks (an Ask about one item or
    one wiki page and its sources)."""

    item_ids: tuple[uuid.UUID, ...] = ()
    page_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class Filters:
    """Which saved items a question is about: their source and the days they
    were saved (days in `tz`, the user's time zone, both ends included). Wiki
    pages aren't saved items, so a search with any filter leaves them out."""

    source: str | None = None
    since: date | None = None
    until: date | None = None
    tz: tzinfo = UTC

    def __bool__(self) -> bool:
        return bool(self.source or self.since or self.until)


def _restrictions(scope: Scope | None, filters: Filters | None) -> tuple[str, str, dict[str, Any]]:
    """SQL conditions on item chunks (`ic`, joined to `i`) and page chunks
    (`tc`), and their parameters. Built per search, not as `:x IS NULL OR …`,
    so Postgres plans each combination with what it actually filters on."""
    items: list[str] = []
    pages: list[str] = []
    params: dict[str, Any] = {}
    if scope is not None:
        items.append("ic.item_id = ANY(CAST(:item_ids AS uuid[]))")
        pages.append("tc.topic_page_id = ANY(CAST(:page_ids AS uuid[]))")
        params["item_ids"] = list(scope.item_ids)
        params["page_ids"] = list(scope.page_ids)
    if filters:
        pages.append("false")
        if filters.source:
            items.append("i.source = :source")
            params["source"] = filters.source
        if filters.since:
            items.append("i.created_at >= :since")
            params["since"] = datetime.combine(filters.since, time(), filters.tz)
        if filters.until:
            items.append("i.created_at < :until")
            params["until"] = datetime.combine(
                filters.until + timedelta(days=1), time(), filters.tz
            )
    return (
        "".join(f" AND {c}" for c in items),
        "".join(f" AND {c}" for c in pages),
        params,
    )


def _candidate(row: Any) -> Candidate:
    return Candidate(
        kind=row.kind,
        chunk_id=row.chunk_id,
        parent_id=row.parent_id,
        ord=row.ord,
        text=row.text,
        title=row.title,
        url=row.url,
        slug=row.slug,
        source=row.source,
        at=row.at,
    )


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
    session: AsyncSession,
    query_vec: list[float],
    limit: int,
    user_id: uuid.UUID,
    scope: Scope | None = None,
    filters: Filters | None = None,
) -> list[Candidate]:
    # The HNSW indexes hold every user's chunks and the user filter applies
    # after them. With a fixed scan (ef_search, 40 by default) a user owning a
    # small share of the chunks would get few or none of theirs back, so the
    # scan carries on until it has `limit` of this user's (pgvector 0.8
    # iterative scans). relaxed_order may return them a little out of order;
    # the query sorts them again.
    await session.execute(
        text(
            "SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true),"
            " set_config('hnsw.ef_search', '100', true)"
        )
    )
    item_where, page_where, params = _restrictions(scope, filters)
    # A scope holds a handful of chunks, which an index scan would only reach
    # after scanning up to hnsw.max_scan_tuples of everyone's. Ordering by an
    # expression the index can't serve makes Postgres measure the scope's
    # chunks exactly instead.
    exact = " + 0" if scope is not None else ""
    sql = text(
        f"""
        WITH ic AS (
            SELECT 'item'::text AS kind,
                   ic.id AS chunk_id,
                   ic.item_id AS parent_id,
                   ic.ord, ic.text,
                   i.title AS title,
                   i.url AS url,
                   NULL::text AS slug,
                   i.source AS source,
                   i.created_at AS at,
                   (ic.embedding <=> CAST(:qv AS vector)) AS distance
            FROM item_chunks ic
            JOIN items i ON i.id = ic.item_id
            WHERE ic.user_id = :uid
              AND i.status = 'ready' AND ic.embedding IS NOT NULL{item_where}
            ORDER BY (ic.embedding <=> CAST(:qv AS vector)){exact}
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
                   NULL::text AS source,
                   tp.updated_at AS at,
                   (tc.embedding <=> CAST(:qv AS vector)) AS distance
            FROM topic_page_chunks tc
            JOIN topic_pages tp ON tp.id = tc.topic_page_id
            WHERE tc.user_id = :uid AND tc.embedding IS NOT NULL{page_where}
            ORDER BY (tc.embedding <=> CAST(:qv AS vector)){exact}
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
    result = await session.execute(sql, {"qv": qv_str, "lim": limit, "uid": str(user_id), **params})
    return [_candidate(row) for row in result]


async def _lexical_search(
    session: AsyncSession,
    query: str,
    limit: int,
    user_id: uuid.UUID,
    scope: Scope | None = None,
    filters: Filters | None = None,
) -> list[Candidate]:
    # A chunk matches when its own text does (the chunk's `fts` column) or when
    # its item or page as a whole does (`fts_doc`: title, tags, the lexical
    # anchors). Both are stored tsvectors with GIN indexes, so each branch of
    # the UNION is an index lookup instead of parsing every chunk per query.
    item_where, page_where, params = _restrictions(scope, filters)
    sql = text(
        f"""
        WITH q AS (SELECT websearch_to_tsquery('english'::regconfig, :q) AS tsq),
        ic_hits AS (
            SELECT ic.id FROM item_chunks ic, q
            WHERE ic.user_id = :uid AND ic.fts @@ q.tsq
            UNION
            SELECT ic.id FROM items i JOIN item_chunks ic ON ic.item_id = i.id, q
            WHERE i.user_id = :uid AND i.fts_doc @@ q.tsq
        ),
        ic AS (
            SELECT 'item'::text AS kind,
                   ic.id AS chunk_id,
                   ic.item_id AS parent_id,
                   ic.ord, ic.text,
                   i.title, i.url,
                   NULL::text AS slug,
                   i.source AS source,
                   i.created_at AS at,
                   ts_rank_cd(i.fts_doc || ic.fts, q.tsq) AS score
            FROM ic_hits
            JOIN item_chunks ic ON ic.id = ic_hits.id
            JOIN items i ON i.id = ic.item_id, q
            WHERE i.status = 'ready'{item_where}
            ORDER BY score DESC
            LIMIT :lim
        ),
        tc_hits AS (
            SELECT tc.id FROM topic_page_chunks tc, q
            WHERE tc.user_id = :uid AND tc.fts @@ q.tsq
            UNION
            SELECT tc.id FROM topic_pages tp JOIN topic_page_chunks tc ON tc.topic_page_id = tp.id, q
            WHERE tp.user_id = :uid AND tp.fts_doc @@ q.tsq
        ),
        tc AS (
            SELECT 'topic_page'::text AS kind,
                   tc.id AS chunk_id,
                   tc.topic_page_id AS parent_id,
                   tc.ord, tc.text,
                   tp.title,
                   NULL::text AS url,
                   tp.slug,
                   NULL::text AS source,
                   tp.updated_at AS at,
                   ts_rank_cd(tp.fts_doc || tc.fts, q.tsq) AS score
            FROM tc_hits
            JOIN topic_page_chunks tc ON tc.id = tc_hits.id
            JOIN topic_pages tp ON tp.id = tc.topic_page_id, q
            WHERE true{page_where}
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
    result = await session.execute(sql, {"q": query, "lim": limit, "uid": str(user_id), **params})
    return [_candidate(row) for row in result]


def _rerank_text(c: Candidate) -> str:
    """What the reranker reads: the chunk under its item's or page's title, so
    a passage deep in a long transcript still says what it belongs to. An
    item's first chunk already starts with it."""
    if not c.title or c.text.startswith("Title:"):
        return c.text
    return f"{c.title}\n\n{c.text}"


def _cap(chunks: list[Candidate], per_parent: int | None) -> list[Candidate]:
    """At most `per_parent` chunks of any one item or page, best first."""
    if per_parent is None:
        return chunks
    taken: dict[tuple[str, uuid.UUID], int] = {}
    out = []
    for c in chunks:
        key = (c.kind, c.parent_id)
        if taken.get(key, 0) < per_parent:
            taken[key] = taken.get(key, 0) + 1
            out.append(c)
    return out


async def hybrid_search(
    session: AsyncSession,
    query: str,
    user_id: uuid.UUID,
    *,
    candidate_limit: int = 50,
    final_limit: int = 6,
    use_reranker: bool = True,
    min_score: float | None = None,
    scope: Scope | None = None,
    filters: Filters | None = None,
    per_parent: int | None = None,
) -> list[Candidate]:
    """Run dense + lexical, fuse with RRF, optionally rerank, return top-N.

    All hits are scoped to `user_id` — items + topic-page chunks owned by
    other users are never considered. `scope` narrows them to some items and
    pages, `filters` to items by source and date, and `per_parent` keeps at
    most that many chunks of one item or page, so a long transcript can't
    fill every slot.

    With `min_score`, a chunk found only by the dense search is dropped
    unless its rerank score reaches it: nearest neighbours exist even for a
    query that matches nothing. Keyword matches are kept whatever their score.
    """
    # The keyword search doesn't need the embedding, so it runs while Voyage
    # embeds the query.
    embedded, lexical = await asyncio.gather(
        embed_query(query),
        _lexical_search(session, query, candidate_limit, user_id, scope, filters),
        return_exceptions=True,
    )
    if isinstance(lexical, BaseException):
        raise lexical
    qv: list[float] | None = None
    if isinstance(embedded, BaseException):
        # Without the query's embedding, keyword search still answers.
        log.warning("query embedding failed, searching by keyword only", exc_info=embedded)
    else:
        qv = embedded

    dense = await _dense_search(session, qv, candidate_limit, user_id, scope, filters) if qv else []
    # Done with the database: don't hold a pooled connection (or the SET
    # LOCALs above) while Voyage reranks and Claude answers.
    await session.commit()

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

    if not use_reranker or (min_score is None and len(fused_candidates) <= final_limit):
        return _cap(fused_candidates, per_parent)[:final_limit]

    keyword = {(c.kind, str(c.chunk_id)) for c in lexical}
    try:
        rerank_input = [_rerank_text(c) for c in fused_candidates]
        # With a cap, every candidate's score is wanted: the ones past
        # `final_limit` fill the places of chunks the cap drops.
        top_k = len(fused_candidates) if per_parent is not None else final_limit
        rerank_result = await rerank(query, rerank_input, top_k=top_k)
        reranked = []
        for idx, score in rerank_result:
            cand = fused_candidates[idx]
            cand.score = float(score)
            reranked.append(cand)
        if min_score is not None:
            reranked = [
                c for c in reranked if c.score >= min_score or (c.kind, str(c.chunk_id)) in keyword
            ]
        return _cap(reranked, per_parent)[:final_limit]
    except Exception:
        # Reranker is optional — fall back to RRF order. With no rerank scores
        # to judge dense-only chunks by, `min_score` keeps keyword matches only.
        if min_score is not None:
            fused_candidates = [c for c in fused_candidates if (c.kind, str(c.chunk_id)) in keyword]
        return _cap(fused_candidates, per_parent)[:final_limit]


# Keep bindparam imported for SQLAlchemy versions that want it explicit.
_ = bindparam
