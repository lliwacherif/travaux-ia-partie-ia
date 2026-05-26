"""Application settings.

All environment configuration is centralized here and validated at import time
by :class:`Settings` (pydantic-settings). Anywhere in the codebase you can
simply do::

    from app.core.config import settings
    settings.DATABASE_URL
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings loaded from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---------------------------- Application ------------------------------
    PROJECT_NAME: str = "Devis Generation API"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = True

    # ---------------------------- Database ---------------------------------
    # Example: postgresql+asyncpg://devis:devis@localhost:5432/devis
    DATABASE_URL: PostgresDsn = Field(
        default="postgresql+asyncpg://devis:devis@localhost:5432/devis",
        description="Async SQLAlchemy DSN (must use the `asyncpg` driver).",
    )

    # Sync DSN used by Alembic / tools that don't support async drivers.
    # Automatically derived from DATABASE_URL when not provided.
    SYNC_DATABASE_URL: PostgresDsn | None = None

    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # ---------------------------- Scaleway AI ------------------------------
    # The ``openai`` Python SDK is pointed at Scaleway's OpenAI-compatible
    # Generative API. The key and base URL come from the Scaleway console:
    # https://console.scaleway.com/generative-api
    SCALEWAY_API_KEY: str = Field(
        default="",
        description="Scaleway Generative API key used for gpt-oss-120b calls.",
    )
    SCALEWAY_BASE_URL: HttpUrl = Field(
        default="https://api.scaleway.ai/v1",  # type: ignore[assignment]
        description="OpenAI-compatible base URL of the Scaleway AI endpoint.",
    )
    SCALEWAY_MODEL: str = Field(
        default="gpt-oss-120b",
        description="Default Scaleway model id used by the AI service.",
    )

    # ---------------------------- OpenAI (legacy) --------------------------
    # Kept for backward-compatibility. The active provider is Scaleway above.
    OPENAI_API_KEY: str = Field(default="")
    OPENAI_MODEL: str = "gpt-4o-mini"

    # ---------------------------- CORS -------------------------------------
    BACKEND_CORS_ORIGINS: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("SYNC_DATABASE_URL", mode="before")
    @classmethod
    def _default_sync_dsn(cls, value: str | None, info) -> str | None:
        """Derive a sync DSN from ``DATABASE_URL`` when not explicitly set.

        Alembic and ``psycopg2`` can't use the ``asyncpg`` driver, so we swap
        it for ``psycopg2`` automatically.
        """
        if value:
            return value
        async_dsn = info.data.get("DATABASE_URL")
        if async_dsn is None:
            return None
        return str(async_dsn).replace("+asyncpg", "+psycopg2")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance.

    Using ``lru_cache`` guarantees the ``.env`` file is only parsed once per
    process which matters in tests and autoreload scenarios.
    """
    return Settings()


settings: Settings = get_settings()
