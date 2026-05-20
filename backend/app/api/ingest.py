from typing import Annotated
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestRequest, IngestResponse, ItemSummary
from app.db.models import Item
from app.db.session import get_session
from app.deps import require_token

router = APIRouter(prefix="/ingest", tags=["ingest"], dependencies=[Depends(require_token)])


def detect_source(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host in {"youtube.com", "youtu.be", "m.youtube.com"}:
        return "youtube"
    if host in {"x.com", "twitter.com", "mobile.twitter.com"}:
        return "twitter"
    if host in {"instagram.com"}:
        return "instagram"
    return "web"


@router.post("", response_model=IngestResponse, status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    body: IngestRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> IngestResponse:
    url = str(body.url)
    item = Item(
        url=url,
        source=detect_source(url),
        status="pending",
        share_text=body.share_text,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    # TODO (phase 2): enqueue ingest worker via arq
    return IngestResponse(item=ItemSummary.model_validate(item))
