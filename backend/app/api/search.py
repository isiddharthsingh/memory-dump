from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.retrieval import hybrid_search
from app.api.schemas import SearchHit, SearchRequest, SearchResponse
from app.db.session import get_session
from app.deps import require_token

router = APIRouter(prefix="/search", tags=["search"], dependencies=[Depends(require_token)])


def _snippet(text: str, max_chars: int = 280) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"


@router.post("", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SearchResponse:
    chunks = await hybrid_search(session, body.q, final_limit=body.limit)
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
