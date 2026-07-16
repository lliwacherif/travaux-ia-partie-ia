"""Application settings.

All environment configuration is centralized here and validated at import time
by :class:`Settings` (pydantic-settings). Anywhere in the codebase you can
simply do::

    from app.core.config import settings
    settings.DATABASE_URL
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    """Strongly-typed application settings loaded from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
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

    # ---------------------------- OpenAI (active provider) ------------------
    # The ``openai`` Python SDK is pointed at the official OpenAI API.
    OPENAI_API_KEY: str = Field(
        default="",
        description="OpenAI API key used for all LLM calls.",
    )
    OPENAI_MODEL: str = Field(
        default="gpt-5",
        description="Default OpenAI model id used by the AI service.",
    )
    OPENAI_REASONING_EFFORT: Literal["minimal", "low", "medium", "high"] = Field(
        default="minimal",
        description=(
            "Reasoning effort used for devis semantic mapping. The task is a "
            "constrained structured extraction, so minimal avoids unnecessary "
            "hidden reasoning tokens while preserving the same response schema."
        ),
    )
    OPENAI_SERVICE_TIER: Literal["auto", "default", "priority"] = Field(
        default="priority",
        description=(
            "OpenAI processing tier for user-facing devis generation. Priority "
            "reduces provider queue latency but is billed at a premium."
        ),
    )
    OPENAI_PROMPT_CACHE_KEY: str = Field(
        default="travaux-ia-devis-v2",
        description="Stable routing key for the shared devis prompt and catalog prefix.",
    )
    OPENAI_CHATBOT_MODEL: str = Field(
        default="gpt-4",
        description="OpenAI model used by the main web chatbot API.",
    )
    OPENAI_MOBILE_MODEL: str = Field(
        default="gpt-4o-mini",
        description="Faster OpenAI model used by the mobile chatbot for lower latency.",
    )
    OPENAI_LANDING_MODEL: str = Field(
        default="gpt-4o-mini",
        description="Faster OpenAI model used by the landing-page chatbot for lower latency.",
    )
    OPENAI_VOICE_TRANSCRIPTION_MODEL: str = Field(
        default="gpt-4o-transcribe",
        description="OpenAI model id used by the file-upload voice transcription endpoint.",
    )
    OPENAI_VOICE_TRANSCRIPTION_LANGUAGE: str = Field(
        default="fr",
        description="ISO-639-1 language hint used by the file-upload voice transcription endpoint.",
    )
    OPENAI_VOICE_TRANSCRIPTION_PROMPT: str = Field(
        default=(
            "Transcris uniquement les mots reellement prononces. "
            "N'ajoute aucun mot qui n'a pas ete dit. "
            "Si l'audio est silencieux, contient seulement du bruit, "
            "ou est inintelligible, retourne une transcription vide."
        ),
        description="Prompt used to keep uploaded-audio transcriptions literal and concise.",
    )

    # ---------------------------- Scaleway AI (legacy) ---------------------
    # Kept for backward-compatibility. The active provider is OpenAI above.
    SCALEWAY_API_KEY: str = Field(default="")
    SCALEWAY_BASE_URL: HttpUrl = Field(
        default="https://api.scaleway.ai/v1",  # type: ignore[assignment]
        description="OpenAI-compatible base URL of the Scaleway AI endpoint.",
    )
    SCALEWAY_MODEL: str = Field(
        default="gpt-oss-120b",
        description="Legacy Scaleway model id.",
    )

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
