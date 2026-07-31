"""Persistent V3 execution evidence and controlled feedback events."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.v3.contracts import (
    DemandMatrix,
    PipelineInput,
    QuoteResult,
    SemanticPlan,
    ValidationReport,
)
from app.v3.models import QuoteExecution, QuoteFeedbackEvent, QuoteStageExecution
from app.v3.trace import stable_hash


def _uuid_values(values: list[str]) -> list[uuid.UUID]:
    result: list[uuid.UUID] = []
    for value in values:
        try:
            result.append(uuid.UUID(value))
        except ValueError:
            continue
    return result


async def persist_execution(
    session: AsyncSession,
    *,
    input_: PipelineInput,
    plan: SemanticPlan | None = None,
    demand_matrix: DemandMatrix | None = None,
    quote: QuoteResult,
    validation: ValidationReport,
) -> uuid.UUID:
    trace = quote.trace
    quote_id = uuid.UUID(quote.quote_id)
    execution = QuoteExecution(
        quote_id=quote_id,
        request_id=input_.request_id,
        pipeline_version=trace.pipeline_version,
        ssot_version=trace.ssot_version,
        library_version=trace.library_version,
        semantic_model=trace.semantic_model,
        embedding_model=trace.embedding_model,
        reranker_model=trace.reranker_model,
        input_hash=stable_hash(input_.model_dump(mode="json")),
        prompt_hash=trace.prompt_hash,
        config_hash=trace.config_hash,
        result_hash=stable_hash(quote.model_dump(mode="json")),
        status=trace.pipeline_status.value,
        generation_mode=quote.generation_mode.value,
        cache_hit=trace.cache_hit,
        arbitrage_applied=trace.arbitrage_applied,
        stage_completion_rate=Decimal(str(trace.stage_completion_rate)),
        display_gate_passed=trace.display_gate_passed,
        selected_pack_ids=_uuid_values(trace.selected_pack_ids),
        replaced_line_ids=_uuid_values(trace.replaced_line_ids),
        assumption_codes=trace.assumption_codes,
        input_payload=input_.model_dump(mode="json"),
        semantic_plan=plan.model_dump(mode="json") if plan is not None else None,
        demand_matrix=(
            demand_matrix.model_dump(mode="json")
            if demand_matrix is not None
            else None
        ),
        result_payload=quote.model_dump(mode="json"),
        metrics=validation.metrics.model_dump(mode="json"),
        validation_report=validation.model_dump(mode="json"),
        confidence=trace.confidence.value,
        review_required=quote.review_required,
        document_emitted=trace.document_emitted,
        unjustified_line_rate=Decimal(
            str(validation.metrics.unjustified_line_rate)
        ),
        duration_ms=trace.duration_ms,
        completed_at=datetime.now(timezone.utc),
    )
    session.add(execution)
    await session.flush()
    for attempt, evidence in enumerate(trace.stage_executions, start=1):
        session.add(
            QuoteStageExecution(
                execution_id=execution.execution_id,
                stage=evidence.stage.value,
                attempt=attempt,
                status=evidence.status.value,
                duration_ms=evidence.duration_ms,
                fallback_reason=evidence.fallback_reason,
                input_count=evidence.input_count,
                output_count=evidence.output_count,
                input_hash=evidence.input_hash,
                output_hash=evidence.output_hash,
                evidence=evidence.evidence,
            )
        )
    await session.commit()
    return execution.execution_id


async def record_feedback(
    session: AsyncSession,
    *,
    quote_id: str,
    company_id: str,
    pipeline_version: str,
    library_version: str,
    structural_diff: dict[str, Any],
    correction_scope: str,
    reason_code: str,
    quote_outcome: str | None = None,
) -> uuid.UUID:
    """Persist structural differences only; caller must remove all PII."""

    canonical = json.dumps(
        structural_diff,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    record = QuoteFeedbackEvent(
        quote_id=uuid.UUID(quote_id),
        company_id=uuid.UUID(company_id),
        pipeline_version=pipeline_version,
        library_version=library_version,
        structural_diff=structural_diff,
        correction_scope=correction_scope,
        reason_code=reason_code,
        quote_outcome=quote_outcome,
        schema_version=1,
        status="RECORDED",
        content_hash=f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}",
    )
    session.add(record)
    await session.commit()
    return record.event_id


__all__ = ["persist_execution", "record_feedback"]
