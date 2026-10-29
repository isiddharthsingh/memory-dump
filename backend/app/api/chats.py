"""Ask history. The app saves the whole chat after each question and answer;
History lists summaries and opens one chat at a time."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChatIn, ChatOut, ChatSummary
from app.db.models import Chat, User
from app.db.session import get_session
from app.deps import get_current_user

router = APIRouter(prefix="/chats", tags=["chats"])


@router.get("", response_model=list[ChatSummary])
async def list_chats(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChatSummary]:
    # Only the questions, so History doesn't download every answer.
    questions = func.jsonb_path_query_array(
        Chat.turns, literal_column("'$[*].question'"), type_=JSONB
    )
    rows = (
        await session.execute(
            select(Chat.id, questions, Chat.created_at, Chat.updated_at)
            .where(Chat.user_id == current_user.id)
            .order_by(Chat.updated_at.desc())
        )
    ).all()
    return [
        ChatSummary(id=id_, questions=qs, created_at=created, updated_at=updated)
        for id_, qs, created, updated in rows
    ]


@router.get("/{chat_id}", response_model=ChatOut)
async def get_chat(
    chat_id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatOut:
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    return ChatOut.model_validate(chat, from_attributes=True)


@router.put("/{chat_id}", response_model=ChatSummary)
async def save_chat(
    chat_id: uuid.UUID,
    body: ChatIn,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChatSummary:
    values = insert(Chat).values(
        id=chat_id,
        user_id=current_user.id,
        turns=[t.model_dump(mode="json") for t in body.turns],
    )
    stmt = values.on_conflict_do_update(
        index_elements=[Chat.id],
        set_={"turns": values.excluded.turns, "updated_at": func.now()},
        # Never overwrite another user's chat that happens to have this id.
        where=Chat.user_id == current_user.id,
    ).returning(Chat.created_at, Chat.updated_at)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat not found")
    await session.commit()
    return ChatSummary(
        id=chat_id,
        questions=[t.question for t in body.turns],
        created_at=row.created_at,
        updated_at=row.updated_at,
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
