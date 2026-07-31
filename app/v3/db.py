"""V3-only SQLAlchemy base, engine, and async session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

V3_NAMING_CONVENTION = {
    "ix": "ix_v3_%(table_name)s_%(column_0_name)s",
    "uq": "uq_v3_%(table_name)s_%(column_0_name)s",
    "ck": "ck_v3_%(constraint_name)s",
    "fk": "fk_v3_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_v3_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base used exclusively by V3 models."""

    metadata = MetaData(naming_convention=V3_NAMING_CONVENTION)


V3Base = Base

v3_engine: AsyncEngine = create_async_engine(
    str(settings.V3_DATABASE_URL),
    echo=settings.DB_ECHO,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_pre_ping=True,
)

v3_async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=v3_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_v3_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an isolated V3 session and roll it back on failure."""
    async with v3_async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


__all__ = [
    "Base",
    "V3Base",
    "get_v3_db",
    "v3_async_session_factory",
    "v3_engine",
]
