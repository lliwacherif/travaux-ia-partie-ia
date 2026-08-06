"""Layer 5 deterministic single-pack selection.

Correctifs ciblés à intégrer dans la V3.2 §6 — packs immuables.
Interdit toute réparation hybride (swap de lignes CORE).
Seul un pack publié complet peut être (re)sélectionné, ou le fallback officiel.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Iterable, Mapping, Sequence

from .context import normalize_text
from .coverage import Coverage, coverage_score
from .ssot import EligibilityStatus, FORBID_HYBRID_PACK_REPAIR


@dataclass(frozen=True, slots=True)
class SelectionResult:
    intervention_id: str
    pack: Any
    pack_id: str
    generation_mode: str
    coverage: Coverage
    replaced_line_ids: tuple[str, ...] = ()
    replacement_line_ids: tuple[str, ...] = ()
    fallback_reason: str | None = None
    repair_action: str | None = None


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="python"))
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"Expected mapping-like selector data, got {type(value).__name__}")


def _pack_id(pack: Any) -> str:
    raw = _mapping(pack)
    return str(raw.get("pack_id") or raw.get("id") or "")


def stable_candidate_order(candidates: Iterable[Any]) -> tuple[Any, ...]:
    """Apply the exact stable tie-break order mandated by the V3 spec."""

    def key(candidate: Any) -> tuple[Any, ...]:
        raw = _mapping(candidate)
        pack_code = str(raw.get("pack_code") or raw.get("pack_id") or "")
        stable_code = normalize_text(pack_code).matching
        eligibility = str(raw.get("eligibility_status") or EligibilityStatus.UNKNOWN)
        # Compatible first, then unknown; incompatible should already be filtered.
        eligibility_rank = {
            EligibilityStatus.COMPATIBLE.value: 0,
            EligibilityStatus.UNKNOWN.value: 1,
            EligibilityStatus.INCOMPATIBLE.value: 2,
        }.get(eligibility, 1)
        return (
            eligibility_rank,
            -float(raw.get("final_score") or 0),
            -float(raw.get("coverage_score") or 0),
            -float(raw.get("line_parent_score") or 0),
            float(raw.get("extra_scope_penalty") or 0),
            -int(raw.get("pack_version") or 0),
            int(raw.get("fallback_rank") or 9999),
            stable_code,
            pack_code,
        )

    return tuple(sorted(candidates, key=key))


def repair_core_pack(*_args: Any, **_kwargs: Any) -> None:
    """Correctifs ciblés à intégrer dans la V3.2 — hybride interdit.

    Kept as a hard failure so callers cannot silently reintroduce CORE swaps.
    """

    raise RuntimeError(
        "HYBRID_PACK_REPAIR_FORBIDDEN:"
        "Correctifs ciblés à intégrer dans la V3.2 — "
        "use RESELECT_COMPLETE_PACK / RESELECTED_PUBLISHED_PACK only"
    )


def _fallback_pack(
    fallback: Any,
    trade_code: str,
    flow: str,
) -> Any | None:
    if callable(fallback):
        return fallback(trade_code, flow)
    if isinstance(fallback, Mapping) and not (
        "pack_id" in fallback or "lines" in fallback
    ):
        return fallback.get((trade_code, flow)) or fallback.get(trade_code)
    return fallback


def _is_incompatible(candidate: Any) -> bool:
    status = _mapping(candidate).get("eligibility_status")
    value = getattr(status, "value", status)
    return str(value or "") == EligibilityStatus.INCOMPATIBLE.value


def _full_coverage(coverage: Coverage) -> bool:
    return not coverage.excluded_violated and not coverage.required_missing


def select_one_pack_per_intervention_and_repair(
    candidates: Iterable[Any],
    matrix: Any,
    packs: Mapping[str, Any] | Iterable[Any],
    catalog_lines: Iterable[Any] = (),
    *,
    intervention_id: str = "INTERVENTION-001",
    trade_code: str,
    flow: str,
    official_fallback: Any,
    technical_dependencies: Mapping[str, Any] | None = None,
) -> SelectionResult:
    """Return exactly one exact, reselected published, or official fallback pack.

    Correctifs ciblés à intégrer dans la V3.2 §6 —
    ``catalog_lines`` / ``replacement_group`` are ignored by the generator.
    """

    del catalog_lines  # Correctifs: unused — no hybrid line swap.
    if FORBID_HYBRID_PACK_REPAIR is not True:
        raise RuntimeError("FORBID_HYBRID_PACK_REPAIR must remain True")

    if isinstance(packs, Mapping):
        pack_by_id = dict(packs)
    else:
        pack_by_id = {_pack_id(pack): pack for pack in packs}

    ordered = stable_candidate_order(candidates)
    first_incomplete_seen = False

    for candidate in ordered:
        if _is_incompatible(candidate):
            continue
        candidate_data = _mapping(candidate)
        candidate_pack = candidate_data.get("pack") or pack_by_id.get(
            str(candidate_data.get("pack_id") or "")
        )
        if candidate_pack is None:
            continue
        pack_data = _mapping(candidate_pack)
        if (
            str(pack_data.get("trade_code") or "") != trade_code
            or str(pack_data.get("flow") or "").upper() != flow.upper()
            or not bool(pack_data.get("active", True))
        ):
            continue
        coverage = coverage_score(
            matrix,
            candidate_pack,
            technical_dependencies=technical_dependencies,
        )
        if _full_coverage(coverage):
            mode = (
                "RESELECTED_PUBLISHED_PACK"
                if first_incomplete_seen
                else "EXACT_PACK"
            )
            return SelectionResult(
                intervention_id=intervention_id,
                pack=candidate_pack,
                pack_id=_pack_id(candidate_pack),
                generation_mode=mode,
                coverage=coverage,
                repair_action=(
                    "RESELECT_COMPLETE_PACK"
                    if mode == "RESELECTED_PUBLISHED_PACK"
                    else None
                ),
                fallback_reason=(
                    "RESELECTED_COMPLETE_PUBLISHED_PACK"
                    if mode == "RESELECTED_PUBLISHED_PACK"
                    else None
                ),
            )
        first_incomplete_seen = True

    # Correctifs ciblés à intégrer dans la V3.2 — try remaining published packs
    # even if they were not in the scored TopK evidence list.
    tried = {_pack_id(_mapping(c).get("pack") or pack_by_id.get(str(_mapping(c).get("pack_id") or ""))) for c in ordered}
    for pack_id, candidate_pack in sorted(pack_by_id.items()):
        if pack_id in tried:
            continue
        pack_data = _mapping(candidate_pack)
        if (
            str(pack_data.get("trade_code") or "") != trade_code
            or str(pack_data.get("flow") or "").upper() != flow.upper()
            or not bool(pack_data.get("active", True))
        ):
            continue
        coverage = coverage_score(
            matrix,
            candidate_pack,
            technical_dependencies=technical_dependencies,
        )
        if _full_coverage(coverage):
            return SelectionResult(
                intervention_id=intervention_id,
                pack=candidate_pack,
                pack_id=pack_id,
                generation_mode="RESELECTED_PUBLISHED_PACK",
                coverage=coverage,
                repair_action="RESELECT_COMPLETE_PACK",
                fallback_reason="RESELECTED_COMPLETE_PUBLISHED_PACK",
            )

    fallback = _fallback_pack(official_fallback, trade_code, flow.upper())
    if fallback is None:
        raise ValueError(
            f"Official fallback pack is missing for {flow}:{trade_code}"
        )
    fallback_data = _mapping(fallback)
    if (
        str(fallback_data.get("trade_code") or "") != trade_code
        or str(fallback_data.get("flow") or "").upper() != flow.upper()
        or not bool(fallback_data.get("active", True))
    ):
        raise ValueError("Official fallback does not match the intervention scope")
    fallback_coverage = coverage_score(
        matrix,
        fallback,
        technical_dependencies=technical_dependencies,
    )
    return SelectionResult(
        intervention_id=intervention_id,
        pack=fallback,
        pack_id=_pack_id(fallback),
        generation_mode="OFFICIAL_FALLBACK",
        coverage=fallback_coverage,
        fallback_reason="NO_EXACT_OR_RESELECTABLE_PACK",
        repair_action="USE_OFFICIAL_FALLBACK",
    )


def select_packs_per_intervention(
    interventions: Iterable[Mapping[str, Any]],
) -> tuple[SelectionResult, ...]:
    """Run the single-pack invariant independently for each intervention."""

    results: list[SelectionResult] = []
    seen_interventions: set[str] = set()
    for arguments in interventions:
        result = select_one_pack_per_intervention_and_repair(**dict(arguments))
        if result.intervention_id in seen_interventions:
            raise ValueError(
                f"Duplicate intervention_id {result.intervention_id!r}"
            )
        seen_interventions.add(result.intervention_id)
        results.append(result)
    return tuple(results)


# Short public alias.
select_one_pack = select_one_pack_per_intervention_and_repair


__all__ = [
    "SelectionResult",
    "repair_core_pack",
    "select_one_pack",
    "select_one_pack_per_intervention_and_repair",
    "select_packs_per_intervention",
    "stable_candidate_order",
]
