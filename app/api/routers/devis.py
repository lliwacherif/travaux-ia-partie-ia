"""HTTP router for the devis generation endpoints (JSON + SSE)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils import JSONHealingError
from app.db import get_db
from app.schemas.devis import DevisResponse
from app.services.ai_service import (
    AIServiceError,
    InvalidBuildingRequestError,
    UnrepairableDevisError,
    ai_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devis", tags=["devis"])


class GenerateRequest(BaseModel):
    """Input body for ``POST /devis/generate``."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(
        ...,
        min_length=1,
        description="Raw free-form user request describing the work to quote.",
    )


@router.post(
    "/generate",
    response_model=DevisResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a structured devis from a free-form text request",
    responses={
        400: {"description": "Request is not a valid building-related query."},
        502: {"description": "Upstream AI provider returned an invalid response."},
        503: {"description": "Upstream AI provider is unreachable."},
    },
)
async def generate_devis(
    request: GenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> DevisResponse:
    """Run the two-stage AI pipeline and return a :class:`DevisResponse`.

    The flow is:

    1. Load the available trades from the DB and hand the raw text to
       :meth:`AIService.generate_quote`, which performs trade detection
       (Stage 1), builds a scoped RAG context from ``trade_services`` and
       runs devis generation (Stage 2), returning a plain ``dict``.
    2. Validate the dict against :class:`DevisResponse`.
    3. Return it. Any shape / validation error becomes a 502 because it
       originates upstream (the LLM), not from the caller.
    """
    try:
        raw_devis = await ai_service.generate_quote(request.text, db)
    except InvalidBuildingRequestError as exc:
        logger.info("Rejected non-building request: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a valid building request: {exc}",
        ) from exc
    except JSONHealingError as exc:
        logger.warning("AI produced unparseable JSON: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI returned an unparseable response: {exc}",
        ) from exc
    except UnrepairableDevisError as exc:
        logger.warning("AI devis was too truncated to repair: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI devis was too truncated to recover: {exc}",
        ) from exc
    except AIServiceError as exc:
        logger.error("Scaleway AI provider error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"AI provider unavailable: {exc}",
        ) from exc

    try:
        return DevisResponse.model_validate(raw_devis)
    except ValidationError as exc:
        logger.warning("AI JSON failed DevisResponse validation: %s", exc.errors())
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "AI returned a JSON that does not match DevisResponse.",
                "errors": exc.errors(),
            },
        ) from exc


# ---------------------------------------------------------------------------
# Streaming variant - same body, server-sent events progress + final result.
# ---------------------------------------------------------------------------
@router.post(
    "/generate/stream",
    summary="Generate a devis and stream UI progress events (SSE)",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": (
                "Stream of `text/event-stream` events. The body always returns "
                "HTTP 200 once the stream opens; application errors are delivered "
                "as in-band `event: error` SSE events."
            ),
            "content": {"text/event-stream": {}},
        },
    },
)
async def generate_devis_stream(
    request: GenerateRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream the devis generation pipeline as Server-Sent Events.

    Frame format::

        event: progress
        data: {"step": 1, "total": 4, "label": "Analyse"}

        event: progress
        data: {"step": 2, "total": 4, "label": "Generate"}

        event: progress
        data: {"step": 3, "total": 4, "label": "Calculate"}

        event: progress
        data: {"step": 4, "total": 4, "label": "Finalise"}

        event: result
        data: { ...DevisResponse JSON... }

        event: title
        data: {"title": "Travaux de ..."}

        event: done
        data: {}

    On failure, ``result`` is replaced by ``error``::

        event: error
        data: {"status": 400, "detail": "Not a valid building request: ..."}

        event: done
        data: {}

    The stream always terminates with a final ``event: done``.
    """
    text = request.text

    async def _event_stream() -> AsyncIterator[bytes]:
        # Tiny preamble keeps proxies / browsers from buffering before the
        # first real event arrives.
        yield b": stream open\n\n"
        try:
            async for event in ai_service.generate_quote_stream(text, db):
                event_type = event.get("type", "message")
                payload = {k: v for k, v in event.items() if k != "type"}
                data = json.dumps(payload, ensure_ascii=False, default=str)
                yield f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unexpected error while streaming devis.")
            data = json.dumps(
                {"status": 500, "detail": str(exc)}, ensure_ascii=False
            )
            yield f"event: error\ndata: {data}\n\n".encode("utf-8")
        finally:
            yield b"event: done\ndata: {}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Disable buffering on common reverse proxies (nginx, traefik).
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["GenerateRequest", "router"]
