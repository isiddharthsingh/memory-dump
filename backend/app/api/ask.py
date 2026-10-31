import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import ask as ask_module
from app.ai.retrieval import Scope
from app.api.schemas import AskRequest, AskResponse, CitationOut
from app.auth.rate_limit import rate_limit
from app.db.models import User
from app.db.session import get_session
from app.deps import get_current_user
from app.errors import user_message

log = logging.getLogger(__name__)

_ask_rate_limit = rate_limit("ask", "rate_limit_ask_per_hour")

router = APIRouter(prefix="/ask", tags=["ask"], dependencies=[Depends(_ask_rate_limit)])


def _history(body: AskRequest) -> list[ask_module.Turn]:
    return [ask_module.Turn(question=t.question, answer=t.answer) for t in body.history]


async def _scope(session: AsyncSession, user: User, body: AskRequest) -> Scope | None:
    """The item or page the question is about. One the user doesn't have is
    a 404, answered before the stream starts with a 200."""
    try:
        return await ask_module.resolve_scope(session, user.id, item=body.item, topic=body.topic)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("", response_model=AskResponse)
async def ask_endpoint(
    body: AskRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> AskResponse:
    scope = await _scope(session, current_user, body)
    result = await ask_module.ask(
        session,
        body.question,
        current_user.id,
        history=_history(body),
        scope=scope,
        tz=ask_module.zone(body.zone),
    )
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
    scope = await _scope(session, current_user, body)
    history = _history(body)
    tz = ask_module.zone(body.zone)

    async def gen() -> AsyncIterator[bytes]:
        try:
            async for evt in ask_module.ask_stream(
                session, body.question, user_id, history=history, scope=scope, tz=tz
            ):
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
            # The answer has started streaming, so the status is already 200:
            # the failure goes in the stream, as a sentence the app can show.
            log.warning("ask stream failed", exc_info=e)
            yield _sse("error", {"message": user_message(e)})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
