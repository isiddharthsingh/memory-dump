import re
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.retrieval import hybrid_search
from app.api.schemas import SearchHit, SearchRequest, SearchResponse
from app.db.models import User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/search", tags=["search"])

# Rerank score a semantic-only match needs to be listed. With rerank-2.5 on a
# real library, the closest chunks to nonsense or off-topic queries scored
# 0.30–0.48, and on-topic queries' top chunks 0.50–0.93.
_MIN_SCORE = 0.5


# Item chunks are embedded as labeled blocks ("Title: …", "Tags: …", see
# workers/ingest.py). The title is already shown next to the snippet and the
# anchor lists are for retrieval only, so drop those blocks and strip the
# remaining labels.
_LABEL_RE = re.compile(
    r"^(Title|Tags|People|Handles|Brands|URL|Channel|Summary|Body|Transcript|Description|User note):\s*"
)
_DROP_LABELS = {"Title", "Tags", "People", "Handles", "Brands", "URL", "Channel"}
# Wiki chunks are markdown: headings repeat the title, and the citation /
# wikilink tokens and emphasis markers are syntax, not text.
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s.*$")
_ITEM_MARKER_RE = re.compile(r"\s*\[item:[^\]]+\]")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_EMPHASIS_RE = re.compile(r"\*\*|__")


def _snippet(text: str, max_chars: int = 280) -> str:
    parts: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        m = _LABEL_RE.match(block)
        if m:
            if m.group(1) in _DROP_LABELS:
                continue
            block = block[m.end() :]
        block = _HEADING_RE.sub("", block)
        block = _ITEM_MARKER_RE.sub("", block)
        block = _WIKILINK_RE.sub(lambda w: w.group(1).replace("-", " "), block)
        block = _EMPHASIS_RE.sub("", block)
        block = " ".join(block.split())
        if block:
            parts.append(block)
    text = " ".join(parts)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SearchResponse:
    chunks = await hybrid_search(
        session, body.q, current_user.id, final_limit=body.limit, min_score=_MIN_SCORE
    )
    hits = [
        SearchHit(
            kind=c.kind,
            id=c.parent_id,
            title=c.title,
            url=c.url,
            slug=c.slug,
            snippet=_snippet(c.text),
            score=c.score,
        )
        for c in chunks
    ]
    return SearchResponse(hits=hits)
