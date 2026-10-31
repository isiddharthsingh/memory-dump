"""Retrieval eval: recall@6 of Ask's retrieval on questions about one user's
library (evals/questions.json), overall, per kind and per question.

    docker compose exec -T api python -m evals.retrieval <user id> [--raw]

`--raw` searches with the question as typed, the way Ask did before it planned
its queries. Voyage calls that fail for a while (rate limits) are retried
instead of letting the search fall back to keywords or RRF order, which would
measure the fallback; a question whose search fell back anyway is marked `!`.
Query embeddings are cached in Redis, so a rerun mostly costs the reranks.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

from app.ai import retrieval
from app.ai.retrieval import Candidate, hybrid_search
from app.db.session import get_sessionmaker
from app.errors import is_transient

K = 6
QUESTIONS = Path(__file__).with_name("questions.json")

# Calls that still failed after retrying, for the question being run.
_failed: list[str] = []
# The planner's query and filters, for the question being run.
_plans: list[Any] = []


def _retrying[**P, T](
    name: str, call: Callable[P, Awaitable[T]], tries: int = 6
) -> Callable[P, Awaitable[T]]:
    async def run(*args: P.args, **kwargs: P.kwargs) -> T:
        for attempt in range(tries):
            try:
                return await call(*args, **kwargs)
            except Exception as exc:
                if not is_transient(exc) or attempt == tries - 1:
                    _failed.append(name)
                    raise
                await asyncio.sleep(min(60, 5 * 2**attempt))
        raise AssertionError("unreachable")

    return run


async def _search(question: str, user_id: uuid.UUID, today: date, raw: bool) -> list[Candidate]:
    async with get_sessionmaker()() as session:
        if raw:
            return await hybrid_search(session, question, user_id, final_limit=K)
        from app.ai import ask

        plan_query = ask.plan_query

        async def recording(*args: Any, **kwargs: Any) -> Any:
            _plans.append(await plan_query(*args, **kwargs))
            return _plans[-1]

        ask.plan_query = recording  # type: ignore[assignment]
        try:
            return await ask.retrieve(session, question, user_id, today=today)
        finally:
            ask.plan_query = plan_query  # type: ignore[assignment]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("user_id", type=uuid.UUID)
    parser.add_argument("--raw", action="store_true", help="search the question as typed")
    parser.add_argument("--only", type=int, nargs="*", help="question numbers to run")
    args = parser.parse_args()

    data: dict[str, Any] = json.loads(QUESTIONS.read_text())
    # The day the questions were written for, so "yesterday" still means it.
    today = date.fromisoformat(data["today"])
    retrieval.embed_query = _retrying("embed", retrieval.embed_query)
    retrieval.rerank = _retrying("rerank", retrieval.rerank)

    by_kind: dict[str, list[float]] = defaultdict(list)
    for n, case in enumerate(data["questions"], 1):
        if args.only and n not in args.only:
            continue
        _failed.clear()
        _plans.clear()
        hits = await _search(case["q"], args.user_id, today, args.raw)
        expected = set(case["expect"])
        found = [h.url for h in hits if h.kind == "item"]
        recall = len(expected & set(found)) / len(expected)
        by_kind[case["kind"]].append(recall)
        flag = "!" if _failed else " "
        print(f"{n:2d} {recall:4.2f}{flag} [{case['kind']}] {case['q']}")
        for plan in _plans:
            f = plan.filters
            print(
                f"         query {plan.query!r} source={f.source} since={f.since} until={f.until}"
            )
        for h in hits:
            mark = "+" if h.url in expected else " "
            label = h.title if h.kind == "item" else f"wiki:{h.slug}"
            print(f"         {mark} {h.score:5.3f} {label}")
        missed = expected - set(found)
        for url in sorted(missed):
            print(f"         - missed {url}")

    scores = [r for rs in by_kind.values() for r in rs]
    print()
    for kind, rs in by_kind.items():
        print(f"{kind:12s} recall@{K} {sum(rs) / len(rs):.3f} ({len(rs)} questions)")
    print(f"{'overall':12s} recall@{K} {sum(scores) / len(scores):.3f} ({len(scores)} questions)")


if __name__ == "__main__":
    asyncio.run(main())
