"""Async database engine and session management.

The application talks to Postgres through asyncpg. Alembic does not — see `alembic/env.py` for
why migrations run under psycopg instead.
"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    """One engine per process — it owns the connection pool, so don't make more than one."""
    return create_async_engine(
        get_settings().database_url.get_secret_value(),
        # Cheap liveness check on checkout. Managed Postgres drops idle connections, and
        # without this the first request after an idle period fails instead of reconnecting.
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: with asyncio, touching an expired attribute after commit would
    # trigger lazy IO outside an await, which raises rather than silently blocking.
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. One session per request, closed when the request ends."""
    async with get_sessionmaker()() as session:
        yield session
