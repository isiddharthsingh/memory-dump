"""Ask history. The app saves the whole chat after each question and answer;
History lists summaries and opens one chat at a time."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, func, literal_column, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cached import cached_json
from app.api.schemas import ChatIn, ChatOut, ChatSummary
from app.db.models import Chat, User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/chats", tags=["chats"])


@router.get("", response_model=list[ChatSummary])
async def list_chats(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    limit: int = Query(500, ge=1, le=1000),
    before: Annotated[
        uuid.UUID | None, Query(description="Id of the last chat of the previous page")
    ] = None,
) -> list[ChatSummary]:
    # Only the questions, so History doesn't download every answer.
    questions = func.jsonb_path_query_array(
        Chat.turns, literal_column("'$[*].question'"), type_=JSONB
    )
    stmt = select(Chat.id, questions, Chat.created_at, Chat.updated_at, Chat.version).where(
        Chat.user_id == current_user.id
    )
    if before is not None:
        anchor = await session.scalar(
            select(Chat.updated_at).where(Chat.id == before, Chat.user_id == current_user.id)
        )
        if anchor is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown cursor")
        stmt = stmt.where(tuple_(Chat.updated_at, Chat.id) < tuple_(anchor, before))
    rows = (
        await session.execute(stmt.order_by(Chat.updated_at.desc(), Chat.id.desc()).limit(limit))
    ).all()
    return [
        ChatSummary(id=id_, questions=qs, created_at=created, updated_at=updated, version=version)
        for id_, qs, created, updated, version in rows
    ]


@router.get("/{chat_id}", response_model=ChatOut)
async def get_chat(
    chat_id: uuid.UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return cached_json(request, ChatOut.model_validate(chat, from_attributes=True))


@router.put("/{chat_id}", response_model=ChatSummary)
async def save_chat(
    chat_id: uuid.UUID,
    body: ChatIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSummary:
    """Saves the whole chat. With `version`, only over that version: another
    device's save in between gets 409, and the app merges and saves again."""
    values = insert(Chat).values(
        id=chat_id,
        user_id=current_user.id,
        turns=[t.model_dump(mode="json") for t in body.turns],
        version=1,
    )
    # Never overwrite another user's chat that happens to have this id.
    only = Chat.user_id == current_user.id
    if body.version is not None:
        only = only & (Chat.version == body.version)
    stmt = values.on_conflict_do_update(
        index_elements=[Chat.id],
        set_={
            "turns": values.excluded.turns,
            "updated_at": func.now(),
            "version": Chat.version + 1,
        },
        where=only,
    ).returning(Chat.created_at, Chat.updated_at, Chat.version)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        owner = await session.scalar(select(Chat.user_id).where(Chat.id == chat_id))
        if owner != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Chat changed on another device"
        )
    await session.commit()
    return ChatSummary(
        id=chat_id,
        questions=[t.question for t in body.turns],
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(
    chat_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> None:
    deleted = (
        await session.execute(
            delete(Chat)
            .where(Chat.id == chat_id, Chat.user_id == current_user.id)
            .returning(Chat.id)
        )
    ).scalar_one_or_none()
    if deleted is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    await session.commit()
