from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import IngestRequest, IngestResponse, ItemSummary
from app.db.models import Item
from app.db.session import get_session
from app.deps import require_token
from app.extractors.base import detect_source
from app.queue import get_arq_pool

router = APIRouter(prefix="/ingest", tags=["ingest"], dependencies=[Depends(require_token)])


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

    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_item", str(item.id))

    return IngestResponse(item=ItemSummary.model_validate(item))
