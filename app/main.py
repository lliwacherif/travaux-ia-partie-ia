"""FastAPI application entry point.

Run locally with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import (
    chat_router,
    devis_router,
    landing_chat_router,
    mobile_chat_router,
    trade_line_router,
    voice_router,
)
from app.core.config import settings
from app.services.ai_service import ai_service

# Importing the models package registers every ORM model on ``Base.metadata``
# which is essential for Alembic autogeneration and test fixtures.
from app import models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup / shutdown hooks.

    On shutdown we release the shared OpenAI client so in-flight httpx
    connections are closed cleanly.
    """
    try:
        yield
    finally:
        await ai_service.aclose()


app: FastAPI = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    debug=settings.DEBUG,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
)

# ---------------------------------------------------------------------------
# CORS - fully permissive (dev / ngrok-friendly).
#
# We deliberately accept *any* origin, *any* method and *any* header. The
# regex form ``.*`` is preferred over ``allow_origins=["*"]`` because it
# still works when ``allow_credentials=True`` (browsers reject the literal
# wildcard in that combination, but echo the actual Origin back when a
# regex is used). This also makes OPTIONS preflights succeed automatically,
# which is what was failing with the bare ``405`` you were seeing.
#
# To lock CORS down later (e.g. in production), replace the block below
# with the env-driven ``settings.BACKEND_CORS_ORIGINS`` list.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.include_router(devis_router, prefix=settings.API_V1_PREFIX)
app.include_router(trade_line_router, prefix=settings.API_V1_PREFIX)
app.include_router(chat_router, prefix=settings.API_V1_PREFIX)
app.include_router(landing_chat_router, prefix=settings.API_V1_PREFIX)
app.include_router(mobile_chat_router, prefix=settings.API_V1_PREFIX)
app.include_router(voice_router, prefix=settings.API_V1_PREFIX)


@app.get("/health", tags=["system"], summary="Liveness probe")
async def health() -> dict[str, Any]:
    """Basic liveness endpoint used by orchestrators / uptime monitors."""
    return {
        "status": "ok",
        "service": settings.PROJECT_NAME,
        "version": app.version,
        "environment": settings.ENVIRONMENT,
    }
