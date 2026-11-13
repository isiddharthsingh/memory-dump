import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cached import cached_json
from app.api.schemas import CorrectionIn, TopicPageDetail, TopicPageSummary
from app.auth.rate_limit import rate_limit
from app.db.models import TopicPage, User
from app.db.session import get_session
from app.deps import get_current_user
from app.queue import correct_job_id, enqueue

log = logging.getLogger(__name__)

router = APIRouter(prefix="/wiki", tags=["wiki"])

# A correction has Claude rewrite the page, so it counts against the same
# hourly cap as saving.
_ingest_rate_limit = rate_limit("ingest", "rate_limit_ingest_per_hour")

# Every patch of the page reads them all, so a page keeps this many at most.
MAX_CORRECTIONS = 20


@router.get("", response_model=list[TopicPageSummary])
async def list_pages(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[TopicPage]:
    stmt = (
        select(TopicPage)
        .where(TopicPage.user_id == current_user.id)
        .order_by(TopicPage.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _owned_page(
    session: AsyncSession, slug: str, user: User, *, for_update: bool = False
) -> TopicPage:
    stmt = select(TopicPage).where(TopicPage.user_id == user.id).where(TopicPage.slug == slug)
    if for_update:
        stmt = stmt.with_for_update()
    page = (await session.execute(stmt)).scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic page not found")
    return page


@router.get("/{slug}", response_model=TopicPageDetail)
async def get_page(
    slug: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    page = await _owned_page(session, slug, current_user)
    return cached_json(request, TopicPageDetail.model_validate(page))


@router.post(
    "/{slug}/corrections",
    response_model=TopicPageDetail,
    dependencies=[Depends(_ingest_rate_limit)],
)
async def add_correction(
    slug: str,
    body: CorrectionIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TopicPage:
    """Keeps what the user says the page gets wrong and queues its rewrite
    (`correct_page`). The page keeps the correction, so whatever is saved
    later is worked into it without undoing it."""
    # The row lock keeps two corrections sent at once from losing one.
    page = await _owned_page(session, slug, current_user, for_update=True)
    kept = list(page.corrections or [])
    if len(kept) >= MAX_CORRECTIONS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A page keeps {MAX_CORRECTIONS} corrections at most. Remove one first.",
        )
    correction = {"id": str(uuid.uuid4()), "text": body.text, "at": datetime.now(UTC).isoformat()}
    page.corrections = [*kept, correction]
    await session.commit()
    await session.refresh(page)
    try:
        await enqueue(
            "correct_page",
            str(current_user.id),
            str(page.id),
            _job_id=correct_job_id(correction["id"]),
        )
    except Exception:
        # The correction is kept either way, and binds the page's next patch.
        # An error here would have the user send it a second time.
        log.warning("couldn't queue the rewrite of %s", slug, exc_info=True)
    return page


@router.delete("/{slug}/corrections/{correction_id}", response_model=TopicPageDetail)
async def remove_correction(
    slug: str,
    correction_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TopicPage:
    """Stops holding the page to a correction. What it already changed on the
    page stays until a later save changes it."""
    page = await _owned_page(session, slug, current_user, for_update=True)
    kept = [c for c in page.corrections or [] if c["id"] != str(correction_id)]
    if len(kept) == len(page.corrections or []):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Correction not found")
    page.corrections = kept or None
    await session.commit()
    await session.refresh(page)
    return page
