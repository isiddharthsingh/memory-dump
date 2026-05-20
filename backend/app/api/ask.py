from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import ask as ask_module
from app.api.schemas import AskRequest, AskResponse, CitationOut
from app.db.session import get_session
from app.deps import require_token

router = APIRouter(prefix="/ask", tags=["ask"], dependencies=[Depends(require_token)])


@router.post("", response_model=AskResponse)
async def ask_endpoint(
    body: AskRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AskResponse:
    result = await ask_module.ask(session, body.question)
    return AskResponse(
        answer=result.answer,
        citations=[
            CitationOut(
                kind=c.kind,
                id=c.id,
                title=c.title,
                url=c.url,
                slug=c.slug,
            )
            for c in result.citations
        ],
    )
