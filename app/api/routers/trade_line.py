"""HTTP router for the multi-item trade-line catalog endpoint.

``POST /api/v1/trade-line/generate`` accepts a corps de métier (e.g.
``"Peinture"``) and returns a DYNAMIC LIST of representative billable
prestations the frontend can show as a "choisir une prestation" picker:

    {
      "job_corp": "Peinture",
      "count": 3,
      "items": [
        { "job_corp": "Peinture", "description": "...", "unit": "m2", "pu": 22, "tva": 10 },
        { "job_corp": "Peinture", "description": "...", "unit": "m2", "pu": 18, "tva": 10 },
        ...
      ]
    }

Open endpoint — no authentication, on purpose (front-end estimator widget).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.schemas.trade_line import (
    TRADE_LINE_DEFAULT_LIMIT,
    TradeLineRequest,
    TradeLineResponse,
)
from app.services.ai_service import (
    InvalidBuildingRequestError,
    ai_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trade-line", tags=["trade-line"])


@router.post(
    "/generate",
    response_model=TradeLineResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a list of representative billable prestations for a corps de métier",
    responses={
        400: {"description": "`job_corp` is not a recognised building trade."},
        502: {"description": "Generated response failed schema validation."},
    },
)
async def generate_trade_line(
    request: TradeLineRequest,
    db: AsyncSession = Depends(get_db),
) -> TradeLineResponse:
    """Return a :class:`TradeLineResponse` for the picker UI.

    Steps:

    1. Fuzzy-load catalog rows that match ``job_corp`` and return them
       directly when possible.
    2. Fall back to deterministic BTP reference items when the catalog has no
       match.
    3. Validate the final ``{job_corp, count, items}`` payload against
       ``TradeLineResponse``.
    """
    limit = request.limit if request.limit is not None else TRADE_LINE_DEFAULT_LIMIT

    try:
        raw = await ai_service.generate_trade_line(
            request.job_corp, db, limit=limit
        )
    except InvalidBuildingRequestError as exc:
        logger.info("Rejected non-building job_corp=%r: %s", request.job_corp, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Not a valid building trade: {exc}",
        ) from exc

    try:
        return TradeLineResponse.model_validate(raw)
    except ValidationError as exc:
        logger.warning(
            "AI JSON failed TradeLineResponse validation: %s", exc.errors()
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Generated trade-line payload does not match TradeLineResponse.",
                "errors": exc.errors(),
            },
        ) from exc


__all__ = ["router"]
