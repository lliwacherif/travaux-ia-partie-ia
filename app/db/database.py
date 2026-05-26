"""Async SQLAlchemy engine, session factory and declarative ``Base``.

We expose:

* ``engine``               - the ``AsyncEngine`` shared by the whole process.
* ``async_session_factory`` - the ``async_sessionmaker`` used to build sessions.
* ``Base``                 - the declarative base every ORM model inherits from.
* ``get_db``               - a FastAPI dependency that yields an ``AsyncSession``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base class shared by every ORM model."""


engine: AsyncEngine = create_async_engine(
    str(settings.DATABASE_URL),
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
    future=True,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a SQLAlchemy ``AsyncSession`` for the duration of a request.

    Usage in a router::

        from fastapi import Depends
        from app.db import get_db

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
