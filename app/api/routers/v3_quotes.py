"""Parallel V3 quote API; existing V2 routes remain unchanged."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.v3.contracts import (
    CompanyContext,
    PipelineInput,
    ProjectContext,
    QuoteResult,
)
from app.v3.db import get_v3_db
from app.v3.models import QuoteExecution, QuotePack
from app.v3.orchestrator import V3QuoteEngine
from app.v3.presentation import present_quote
from app.v3.validator import DisplayGateError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devis", tags=["devis-v3"])


class GenerateV3Request(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    request_id: str | None = Field(default=None, max_length=150)
    company: CompanyContext | None = None
    project: ProjectContext | None = None


class V3QuoteEnvelope(BaseModel):
    quote: QuoteResult
    devis: dict[str, Any]


def _pipeline_input(request: GenerateV3Request) -> PipelineInput:
    company = request.company or CompanyContext(
        primary_trade_code="",
        enabled_service_codes=[],
    )
    project = request.project or ProjectContext(
        country="FR",
        # V3.2 — default metropolitan territory when the client omits project.
        territory_code="FR-MET",
        customer_type=None,
        building_use=None,
        building_age_years=None,
        location=None,
    )
    return PipelineInput(
        request_id=request.request_id or str(uuid4()),
        description=request.text,
        company=company,
        project=project,
    )


@router.post(
    "/generate",
    response_model=V3QuoteEnvelope,
    status_code=status.HTTP_200_OK,
    summary="Generate a fully traced V3 quote",
)
async def generate_v3_quote(
    request: GenerateV3Request,
    session: AsyncSession = Depends(get_v3_db),
) -> V3QuoteEnvelope:
    try:
        quote = await V3QuoteEngine(session).generate(_pipeline_input(request))
    except DisplayGateError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "V3 display gate rejected the quote.",
                "errors": exc.errors,
                "allowed_repairs": exc.allowed_repairs,
            },
        ) from exc
    except Exception as exc:
        await session.rollback()
        logger.exception("V3 quote generation failed.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"V3 quote engine unavailable: {type(exc).__name__}: {exc}",
        ) from exc
    return V3QuoteEnvelope(quote=quote, devis=present_quote(quote))


@router.get(
    "/{quote_id:uuid}",
    response_model=V3QuoteEnvelope,
    summary="Read a persisted V3 quote",
)
async def get_v3_quote(
    quote_id: UUID,
    session: AsyncSession = Depends(get_v3_db),
) -> V3QuoteEnvelope:
    record = (
        await session.execute(
            select(QuoteExecution).where(QuoteExecution.quote_id == quote_id)
        )
    ).scalar_one_or_none()
    if record is None or record.result_payload is None:
        raise HTTPException(status_code=404, detail="V3 quote not found")
    quote = QuoteResult.model_validate(record.result_payload)
    return V3QuoteEnvelope(quote=quote, devis=present_quote(quote))


@router.get(
    "/{quote_id:uuid}/trace",
    summary="Read V3 execution evidence",
)
async def get_v3_trace(
    quote_id: UUID,
    session: AsyncSession = Depends(get_v3_db),
) -> dict[str, Any]:
    record = (
        await session.execute(
            select(QuoteExecution).where(QuoteExecution.quote_id == quote_id)
        )
    ).scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="V3 quote not found")
    return {
        "quote_id": str(quote_id),
        "status": record.status,
        "generation_mode": record.generation_mode,
        "stage_completion_rate": float(record.stage_completion_rate),
        "display_gate_passed": record.display_gate_passed,
        "confidence": record.confidence,
        "review_required": record.review_required,
        "assumption_codes": record.assumption_codes,
        "validation_report": record.validation_report,
        "metrics": record.metrics,
    }


@router.get(
    "/system/readiness",
    summary="Check V3 database and official-library readiness",
)
async def v3_readiness(
    session: AsyncSession = Depends(get_v3_db),
) -> dict[str, Any]:
    await session.execute(text("SELECT 1"))
    vector_available = bool(
        (
            await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension "
                    "WHERE extname = 'vector')"
                )
            )
        ).scalar_one()
    )
    published_packs = int(
        (
            await session.execute(
                select(func.count()).select_from(QuotePack).where(
                    QuotePack.status == "PUBLISHED"
                )
            )
        ).scalar_one()
    )
    # V3.2 — service is operational only with a published+validated snapshot.
    from app.v3.models import LibrarySnapshot

    validated_snapshots = int(
        (
            await session.execute(
                select(func.count()).select_from(LibrarySnapshot).where(
                    LibrarySnapshot.status == "PUBLISHED",
                    LibrarySnapshot.validated_at.is_not(None),
                )
            )
        ).scalar_one()
    )
    ready = vector_available and published_packs > 0 and validated_snapshots > 0
    return {
        "status": "ready" if ready else "not_ready",
        "pipeline_version": "V3.2",
        "pgvector": vector_available,
        "published_packs": published_packs,
        "validated_library_snapshots": validated_snapshots,
    }


__all__ = ["GenerateV3Request", "V3QuoteEnvelope", "router"]

