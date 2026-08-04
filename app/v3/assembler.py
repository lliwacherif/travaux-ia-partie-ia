"""Layer 7 assembly from official lines using only SSOT geometries.

V3.2 — assembleWithOneSharedProfile: SETUP/FINISH come from one shared
profile for the whole quote; CORE lines come from each definitive pack.
Never concatenates SETUP/FINISH per métier.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

from .contracts import (
    ExecutionTrace,
    GenerationMode,
    QuoteLine,
    QuoteResult,
    QuoteTotals,
    SourceEntityType,
    TradeBlock,
)
from .pricing import calculate_totals
from .ssot import Flow, Phase, expected_geometry


_MODE_SEVERITY = {
    GenerationMode.EXACT_PACK.value: 0,
    GenerationMode.REPAIRED_PACK.value: 1,
    GenerationMode.OFFICIAL_FALLBACK.value: 2,
}


@dataclass(frozen=True, slots=True)
class AssembledQuoteParts:
    quote_id: str
    flow: Flow
    generation_mode: GenerationMode
    review_required: bool
    # V3.2 — single shared profile framing the quote.
    shared_profile_id: str
    shared_profile_code: str
    shared_profile_version: int
    setup_lines: tuple[QuoteLine, ...]
    trade_blocks: tuple[TradeBlock, ...]
    finish_lines: tuple[QuoteLine, ...]
    totals: QuoteTotals


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="python"))
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"Expected mapping-like assembly data, got {type(value).__name__}")


def _line_phase(line: Any) -> str:
    phase = _mapping(line).get("phase")
    return str(getattr(phase, "value", phase) or "").upper()


def _line_id(line: Any) -> str:
    return str(_mapping(line).get("line_id") or "")


def _sorted_lines(lines: Iterable[Any], phase: Phase) -> tuple[QuoteLine, ...]:
    selected = [line for line in lines if _line_phase(line) == phase.value]
    selected.sort(
        key=lambda line: (
            int(_mapping(line).get("slot_index") or 0),
            _line_id(line),
        )
    )
    return tuple(
        line if isinstance(line, QuoteLine) else QuoteLine.model_validate(_mapping(line))
        for line in selected
    )


def _selection_lines(
    selection: Any,
    quote_lines_by_pack: Mapping[str, Iterable[Any]] | None,
) -> tuple[Any, ...]:
    selection_data = _mapping(selection)
    pack = selection_data.get("pack")
    pack_data = _mapping(pack) if pack is not None else {}
    pack_id = str(
        selection_data.get("pack_id")
        or pack_data.get("pack_id")
        or ""
    )
    if quote_lines_by_pack is not None and pack_id in quote_lines_by_pack:
        return tuple(quote_lines_by_pack[pack_id])
    if selection_data.get("lines") is not None:
        return tuple(selection_data["lines"])
    if pack_data.get("lines") is not None:
        return tuple(pack_data["lines"])
    result: list[Any] = []
    for name in ("setup", "core", "finish", "setup_lines", "core_lines", "finish_lines"):
        result.extend(pack_data.get(name) or ())
    return tuple(result)


def _selection_value(selection: Any, name: str, default: Any = None) -> Any:
    selection_data = _mapping(selection)
    if selection_data.get(name) is not None:
        return selection_data[name]
    pack = selection_data.get("pack")
    if pack is not None:
        return _mapping(pack).get(name, default)
    return default


def _mode(selections: Sequence[Any]) -> GenerationMode:
    values = [
        str(
            getattr(
                _selection_value(selection, "generation_mode", "EXACT_PACK"),
                "value",
                _selection_value(selection, "generation_mode", "EXACT_PACK"),
            )
        )
        for selection in selections
    ]
    selected = max(values, key=lambda value: _MODE_SEVERITY.get(value, 99))
    if selected not in _MODE_SEVERITY:
        raise ValueError(f"Unsupported generation mode {selected!r}")
    return GenerationMode(selected)


def _shared_profile_from_lines(
    lines: Sequence[QuoteLine],
    selection: Any,
) -> tuple[str, str, int]:
    """V3.2 — extract the single shared profile identity from SETUP/FINISH lines."""

    for line in lines:
        if line.source_entity_type is SourceEntityType.SHARED_PROFILE:
            if (
                line.shared_profile_id
                and line.shared_profile_code
                and line.shared_profile_version
            ):
                return (
                    line.shared_profile_id,
                    line.shared_profile_code,
                    line.shared_profile_version,
                )
    pack_id = str(_selection_value(selection, "pack_id") or "unknown")
    pack_code = str(_selection_value(selection, "pack_code") or pack_id)
    pack_version = int(_selection_value(selection, "pack_version") or 1)
    return (
        f"legacy-profile-{pack_id}",
        f"LEGACY-{pack_code}",
        pack_version,
    )


def assemble_parts_by_ssot_geometry(
    *,
    quote_id: str,
    flow: Flow | str,
    selections: Sequence[Any],
    quote_lines_by_pack: Mapping[str, Iterable[Any]] | None = None,
    review_required: bool = False,
    totals: QuoteTotals | Mapping[str, Any] | None = None,
) -> AssembledQuoteParts:
    """V3.2 assembleWithOneSharedProfile — exact geometry, no padding."""

    resolved_flow = Flow(flow)
    geometry = expected_geometry(resolved_flow)
    if not quote_id:
        raise ValueError("quote_id is required and must be stable upstream")
    if not selections:
        raise ValueError("At least one selected pack is required")
    if resolved_flow is Flow.DEPANNAGE and len(selections) != 1:
        raise ValueError("DEPANNAGE accepts exactly one intervention")

    intervention_ids: set[str] = set()
    pack_ids: set[str] = set()
    blocks: list[TradeBlock] = []
    all_lines_by_selection: list[tuple[Any, ...]] = []
    for selection in selections:
        intervention_id = str(
            _selection_value(selection, "intervention_id") or ""
        )
        pack_id = str(_selection_value(selection, "pack_id") or "")
        pack_code = str(_selection_value(selection, "pack_code") or "")
        trade_code = str(_selection_value(selection, "trade_code") or "")
        pack = _selection_value(selection, "pack")
        if pack is not None:
            pack_data = _mapping(pack)
            trade_code = trade_code or str(pack_data.get("trade_code") or "")
            pack_id = pack_id or str(pack_data.get("pack_id") or "")
            pack_code = pack_code or str(pack_data.get("pack_code") or "")
        if not intervention_id or intervention_id in intervention_ids:
            raise ValueError("Each selection requires one unique intervention_id")
        if not pack_id or pack_id in pack_ids:
            raise ValueError("Exactly one distinct pack is required per intervention")
        if not trade_code:
            raise ValueError("Every selected pack requires a trade_code")
        intervention_ids.add(intervention_id)
        pack_ids.add(pack_id)

        lines = _selection_lines(selection, quote_lines_by_pack)
        core_lines = _sorted_lines(lines, Phase.CORE)
        if len(core_lines) != geometry.core_per_trade:
            raise ValueError(
                f"PACK_GEOMETRY_INVALID:CORE:{pack_id}:"
                f"{len(core_lines)}!={geometry.core_per_trade}"
            )
        if any(
            line.pack_id not in (pack_id, None) and line.source_entity_type is SourceEntityType.PACK
            for line in core_lines
        ):
            raise ValueError("CORE line pack_id must equal the definitive pack_id")
        pack_version = int(
            _selection_value(selection, "pack_version")
            or (
                _mapping(pack).get("version")
                if pack is not None
                else 1
            )
            or 1
        )
        if not pack_code:
            pack_code = pack_id
        blocks.append(
            TradeBlock(
                intervention_id=intervention_id,
                trade_code=trade_code,
                pack_id=pack_id,
                pack_code=pack_code,
                pack_version=pack_version,
                lines=list(core_lines),
            )
        )
        all_lines_by_selection.append(lines)

    # V3.2 — SETUP and FINISH come once from the first selection's shared profile.
    setup_lines = _sorted_lines(all_lines_by_selection[0], Phase.SETUP)
    finish_lines = _sorted_lines(all_lines_by_selection[0], Phase.FINISH)
    if len(setup_lines) != geometry.setup:
        raise ValueError(
            f"PACK_GEOMETRY_INVALID:SETUP:{len(setup_lines)}!={geometry.setup}"
        )
    if len(finish_lines) != geometry.finish:
        raise ValueError(
            f"PACK_GEOMETRY_INVALID:FINISH:{len(finish_lines)}!={geometry.finish}"
        )

    shared_profile_id, shared_profile_code, shared_profile_version = (
        _shared_profile_from_lines(
            (*setup_lines, *finish_lines),
            selections[0],
        )
    )

    final_lines = [
        *setup_lines,
        *(line for block in blocks for line in block.lines),
        *finish_lines,
    ]
    recomputed = calculate_totals(final_lines)
    recomputed_totals = QuoteTotals(
        ht_cents=recomputed.ht_cents,
        vat_cents=recomputed.vat_cents,
        ttc_cents=recomputed.ttc_cents,
    )
    if totals is not None:
        supplied = (
            totals
            if isinstance(totals, QuoteTotals)
            else QuoteTotals.model_validate(_mapping(totals))
        )
        if supplied != recomputed_totals:
            raise ValueError("Supplied totals differ from cent-exact line totals")

    generation_mode = _mode(selections)
    assumption_used = any(
        line.linear_measurement
        and line.linear_measurement.assumption_code is not None
        for line in final_lines
    )
    return AssembledQuoteParts(
        quote_id=quote_id,
        flow=resolved_flow,
        generation_mode=generation_mode,
        review_required=(
            review_required
            or generation_mode is not GenerationMode.EXACT_PACK
            or assumption_used
        ),
        shared_profile_id=shared_profile_id,
        shared_profile_code=shared_profile_code,
        shared_profile_version=shared_profile_version,
        setup_lines=setup_lines,
        trade_blocks=tuple(blocks),
        finish_lines=finish_lines,
        totals=recomputed_totals,
    )


def finalize_quote(
    parts: AssembledQuoteParts,
    trace: ExecutionTrace | Mapping[str, Any],
) -> QuoteResult:
    resolved_trace = (
        trace
        if isinstance(trace, ExecutionTrace)
        else ExecutionTrace.model_validate(_mapping(trace))
    )
    return QuoteResult(
        quote_id=parts.quote_id,
        flow=parts.flow,
        generation_mode=parts.generation_mode,
        review_required=parts.review_required or bool(resolved_trace.assumption_codes),
        shared_profile_id=parts.shared_profile_id,
        shared_profile_code=parts.shared_profile_code,
        shared_profile_version=parts.shared_profile_version,
        setup_lines=list(parts.setup_lines),
        trade_blocks=list(parts.trade_blocks),
        finish_lines=list(parts.finish_lines),
        totals=parts.totals,
        trace=resolved_trace,
    )


def assemble_by_ssot_geometry(
    *,
    quote_id: str,
    flow: Flow | str,
    selections: Sequence[Any],
    trace: ExecutionTrace | Mapping[str, Any],
    quote_lines_by_pack: Mapping[str, Iterable[Any]] | None = None,
    review_required: bool = False,
    totals: QuoteTotals | Mapping[str, Any] | None = None,
) -> QuoteResult:
    parts = assemble_parts_by_ssot_geometry(
        quote_id=quote_id,
        flow=flow,
        selections=selections,
        quote_lines_by_pack=quote_lines_by_pack,
        review_required=review_required,
        totals=totals,
    )
    return finalize_quote(parts, trace)


# V3.2 public alias matching the specification name.
assemble_with_one_shared_profile = assemble_by_ssot_geometry
assemble_quote = assemble_by_ssot_geometry


__all__ = [
    "AssembledQuoteParts",
    "assemble_by_ssot_geometry",
    "assemble_parts_by_ssot_geometry",
    "assemble_quote",
    "assemble_with_one_shared_profile",
    "finalize_quote",
]
