import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import ask as ask_module
from app.api.schemas import AskRequest, AskResponse, CitationOut
from app.db.models import User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/ask", tags=["ask"])


@router.post("", response_model=AskResponse)
async def ask_endpoint(
    body: AskRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AskResponse:
    result = await ask_module.ask(session, body.question, current_user.id)
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


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


@router.post("/stream")
async def ask_stream_endpoint(
    body: AskRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    user_id = current_user.id

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for evt in ask_module.ask_stream(session, body.question, user_id):
                if isinstance(evt, ask_module.StreamToken):
                    yield _sse("token", {"text": evt.text})
                else:
                    yield _sse(
                        "done",
                        {
                            "citations": [
                                {
                                    "kind": c.kind,
                                    "id": str(c.id),
                                    "title": c.title,
                                    "url": c.url,
                                    "slug": c.slug,
                                }
                                for c in evt.citations
                            ]
                        },
                    )
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
