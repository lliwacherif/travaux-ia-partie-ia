"""Proof-of-execution recorder for every mandatory V3.2 stage."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, TypeVar

from app.v3.contracts import (
    ConfidenceLevel,
    ExecutionTrace,
    PipelineStatus,
    PriceVersionRef,
    SelectedPackRef,
    SharedProfileRef,
    StageEvidence,
    StageStatus,
    VatRuleVersionRef,
)
from app.v3.ssot import REQUIRED_STAGES, SSOT_VERSION, PipelineStage

ResultT = TypeVar("ResultT")


def stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except TypeError:
        payload = repr(value)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


async def _resolve(value: ResultT | Awaitable[ResultT]) -> ResultT:
    if inspect.isawaitable(value):
        return await value
    return value


class ExecutionTracer:
    def __init__(
        self,
        *,
        library_version: str,
        prompt_hash: str,
        config_hash: str,
        # V3.2 — snapshot + territory threaded into every finished trace.
        library_snapshot_id: str = "",
        fallback_snapshot_used: bool = False,
        territory_code: str = "FR-MET",
    ) -> None:
        self.library_version = library_version
        self.prompt_hash = prompt_hash
        self.config_hash = config_hash
        self.library_snapshot_id = library_snapshot_id
        self.fallback_snapshot_used = fallback_snapshot_used
        self.territory_code = territory_code
        self.started = perf_counter()
        self.executions: list[StageEvidence] = []
        self.assumption_codes: list[str] = []
        self.replaced_line_ids: list[str] = []
        self.selected_pack_ids: list[str] = []
        self.selected_packs: list[SelectedPackRef] = []
        self.shared_profile: SharedProfileRef | None = None
        self.price_versions: list[PriceVersionRef] = []
        self.vat_rule_versions: list[VatRuleVersionRef] = []
        self.cache_hit = False
        self.arbitrage_applied = False
        self.line_search_hits_count = 0
        self.parent_pack_candidates_count = 0
        self.reranked_pack_count = 0
        self.linear_measurements_count = 0
        self.linear_formula_ids: list[str] = []
        self.confidence = ConfidenceLevel.HIGH

    def bind_library_snapshot(
        self,
        *,
        snapshot_id: str,
        library_version: str,
        fallback_snapshot_used: bool,
    ) -> None:
        """V3.2 — update tracer after stage 0B resolves the snapshot."""

        self.library_snapshot_id = snapshot_id
        self.library_version = library_version
        self.fallback_snapshot_used = fallback_snapshot_used

    async def required(
        self,
        stage: PipelineStage,
        operation: Callable[[], ResultT | Awaitable[ResultT]],
        *,
        input_value: Any = None,
        input_count: int = 0,
        output_count: Callable[[ResultT], int] | None = None,
        evidence: dict[str, object] | None = None,
    ) -> ResultT:
        started = perf_counter()
        result = await _resolve(operation())
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        count = output_count(result) if output_count else _safe_count(result)
        self.executions.append(
            StageEvidence(
                stage=stage,
                status=StageStatus.PRIMARY,
                duration_ms=duration_ms,
                fallback_reason=None,
                input_count=input_count,
                output_count=count,
                input_hash=stable_hash(input_value) if input_value is not None else None,
                output_hash=stable_hash(_hashable(result)),
                evidence=evidence or {},
            )
        )
        return result

    async def required_with_fallback(
        self,
        stage: PipelineStage,
        primary: Callable[[], ResultT | Awaitable[ResultT]],
        fallback: Callable[[], ResultT | Awaitable[ResultT]],
        *,
        input_value: Any = None,
        input_count: int = 0,
        output_count: Callable[[ResultT], int] | None = None,
        fallback_reason: str | None = None,
    ) -> ResultT:
        started = perf_counter()
        try:
            result = await _resolve(primary())
            status = StageStatus.PRIMARY
            reason = None
        except Exception as exc:
            result = await _resolve(fallback())
            status = StageStatus.DEGRADED_AUTHORIZED
            reason = fallback_reason or f"{type(exc).__name__}:{exc}"
            if self.confidence is ConfidenceLevel.HIGH:
                self.confidence = ConfidenceLevel.MEDIUM
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        count = output_count(result) if output_count else _safe_count(result)
        self.executions.append(
            StageEvidence(
                stage=stage,
                status=status,
                duration_ms=duration_ms,
                fallback_reason=reason,
                input_count=input_count,
                output_count=count,
                input_hash=stable_hash(input_value) if input_value is not None else None,
                output_hash=stable_hash(_hashable(result)),
                evidence={},
            )
        )
        return result

    async def required_outcome(
        self,
        stage: PipelineStage,
        operation: Callable[
            [], tuple[ResultT, bool, str | None] | Awaitable[tuple[ResultT, bool, str | None]]
        ],
        *,
        input_value: Any = None,
        input_count: int = 0,
        output_count: Callable[[ResultT], int] | None = None,
        evidence: dict[str, object] | None = None,
    ) -> ResultT:
        """Record an operation that reports whether an authorized fallback ran."""

        started = perf_counter()
        result, degraded, reason = await _resolve(operation())
        if degraded and not reason:
            raise RuntimeError(f"SILENT_FALLBACK_FORBIDDEN:{stage.value}")
        duration_ms = max(0, round((perf_counter() - started) * 1000))
        count = output_count(result) if output_count else _safe_count(result)
        self.executions.append(
            StageEvidence(
                stage=stage,
                status=(
                    StageStatus.DEGRADED_AUTHORIZED
                    if degraded
                    else StageStatus.PRIMARY
                ),
                duration_ms=duration_ms,
                fallback_reason=reason if degraded else None,
                input_count=input_count,
                output_count=count,
                input_hash=stable_hash(input_value) if input_value is not None else None,
                output_hash=stable_hash(_hashable(result)),
                evidence=evidence or {},
            )
        )
        if degraded and self.confidence is ConfidenceLevel.HIGH:
            self.confidence = ConfidenceLevel.MEDIUM
        return result

    def record_degraded(
        self,
        stage: PipelineStage,
        *,
        reason: str,
        duration_ms: int,
        input_count: int,
        output_count: int,
        evidence: dict[str, object] | None = None,
    ) -> None:
        self.executions.append(
            StageEvidence(
                stage=stage,
                status=StageStatus.DEGRADED_AUTHORIZED,
                duration_ms=max(0, duration_ms),
                fallback_reason=reason,
                input_count=max(0, input_count),
                output_count=max(0, output_count),
                input_hash=None,
                output_hash=None,
                evidence=evidence or {},
            )
        )
        if self.confidence is ConfidenceLevel.HIGH:
            self.confidence = ConfidenceLevel.MEDIUM

    def completed_stages(self) -> set[str]:
        return {execution.stage.value for execution in self.executions}

    def stage_completion_rate(self) -> float:
        return len(self.completed_stages() & set(REQUIRED_STAGES)) / len(REQUIRED_STAGES)

    def missing_stages(self) -> tuple[str, ...]:
        completed = self.completed_stages()
        return tuple(stage for stage in REQUIRED_STAGES if stage not in completed)

    def finish(self, *, document_emitted: bool = True) -> ExecutionTrace:
        missing = self.missing_stages()
        if missing:
            labels = ", ".join(missing)
            raise RuntimeError(f"DISPLAY_GATE_STAGE_EVIDENCE_MISSING:{labels}")
        if not self.library_snapshot_id:
            raise RuntimeError("DISPLAY_GATE_LIBRARY_SNAPSHOT_MISSING")
        degraded = any(
            execution.status is StageStatus.DEGRADED_AUTHORIZED
            for execution in self.executions
        )
        return ExecutionTrace(
            ssot_version=SSOT_VERSION,
            library_version=self.library_version,
            library_snapshot_id=self.library_snapshot_id,
            fallback_snapshot_used=self.fallback_snapshot_used,
            territory_code=self.territory_code,
            prompt_hash=self.prompt_hash,
            config_hash=self.config_hash,
            arbitrage_applied=self.arbitrage_applied,
            pipeline_status=(
                PipelineStatus.COMPLETE_DEGRADED_AUTHORIZED
                if degraded
                else PipelineStatus.COMPLETE_PRIMARY
            ),
            display_gate_passed=True,
            cache_hit=self.cache_hit,
            stage_completion_rate=self.stage_completion_rate(),
            stage_executions=self.executions,
            line_search_hits_count=self.line_search_hits_count,
            parent_pack_candidates_count=self.parent_pack_candidates_count,
            reranked_pack_count=self.reranked_pack_count,
            selected_packs=self.selected_packs,
            shared_profile=self.shared_profile,
            price_versions=self.price_versions,
            vat_rule_versions=self.vat_rule_versions,
            selected_pack_ids=self.selected_pack_ids
            or [pack.pack_id for pack in self.selected_packs],
            replaced_line_ids=self.replaced_line_ids,
            assumption_codes=sorted(set(self.assumption_codes)),
            linear_measurements_count=self.linear_measurements_count,
            linear_formula_ids=sorted(set(self.linear_formula_ids)),
            confidence=self.confidence,
            document_emitted=document_emitted,
            duration_ms=max(0, round((perf_counter() - self.started) * 1000)),
        )


def _safe_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 1


def _hashable(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return value


__all__ = ["ExecutionTracer", "stable_hash"]
