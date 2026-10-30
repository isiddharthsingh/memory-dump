from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_s,
        connect_args={
            "timeout": 10,
            # A stuck query, or a transaction left open while something else
            # is awaited, ends instead of holding its connection and locks.
            "server_settings": {
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    settings.db_idle_in_transaction_timeout_ms
                ),
            },
        },
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
        autoflush=False,
        class_=AsyncSession,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
