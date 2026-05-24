from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import TopicPageDetail, TopicPageSummary
from app.db.models import TopicPage, User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/wiki", tags=["wiki"])


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


@router.get("/{slug}", response_model=TopicPageDetail)
async def get_page(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TopicPage:
    page = (
        await session.execute(
            select(TopicPage)
            .where(TopicPage.user_id == current_user.id)
            .where(TopicPage.slug == slug)
        )
    ).scalar_one_or_none()
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic page not found")
    return page
