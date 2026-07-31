"""Layer 5 deterministic single-pack selection and controlled CORE repair."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, fields, is_dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from .context import normalize_text
from .coverage import Coverage, coverage_score, match_item_to_line


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


def _pack_lines(pack: Any) -> tuple[Any, ...]:
    raw = _mapping(pack)
    if raw.get("lines") is not None:
        return tuple(raw["lines"])
    result: list[Any] = []
    for name in ("setup", "core", "finish", "setup_lines", "core_lines", "finish_lines"):
        result.extend(raw.get(name) or ())
    return tuple(result)


def _phase(line: Any) -> str:
    return str(_mapping(line).get("phase") or "").upper()


def _line_id(line: Any) -> str:
    return str(_mapping(line).get("line_id") or "")


def stable_candidate_order(candidates: Iterable[Any]) -> tuple[Any, ...]:
    """Apply the exact stable tie-break order mandated by the V3 spec."""

    def key(candidate: Any) -> tuple[Any, ...]:
        raw = _mapping(candidate)
        pack_code = str(raw.get("pack_code") or raw.get("pack_id") or "")
        stable_code = normalize_text(pack_code).matching
        return (
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


def _replace_line(pack: Any, old_line_id: str, new_line: Any) -> Any:
    raw = _mapping(pack)

    def replaced(values: Sequence[Any]) -> list[Any]:
        return [
            deepcopy(new_line) if _line_id(line) == old_line_id else deepcopy(line)
            for line in values
        ]

    updates: dict[str, Any] = {}
    if raw.get("lines") is not None:
        updates["lines"] = replaced(raw["lines"])
    else:
        for name in (
            "setup",
            "core",
            "finish",
            "setup_lines",
            "core_lines",
            "finish_lines",
        ):
            if raw.get(name) is not None:
                updates[name] = replaced(raw[name])

    if isinstance(pack, Mapping):
        result = deepcopy(dict(pack))
        result.update(updates)
        return result
    model_copy = getattr(pack, "model_copy", None)
    if callable(model_copy):
        return model_copy(update=updates, deep=True)
    if is_dataclass(pack):
        valid_updates = {field.name for field in fields(pack)}
        return replace(
            pack,
            **{key: value for key, value in updates.items() if key in valid_updates},
        )
    result = deepcopy(pack)
    for key, value in updates.items():
        setattr(result, key, value)
    return result


def _matrix_item(matrix: Any, item_id: str) -> Any | None:
    for item in _mapping(matrix).get("items") or ():
        if str(_mapping(item).get("request_item_id") or "") == item_id:
            return item
    return None


def repair_core_pack(
    base_pack: Any,
    missing_items: Iterable[Any],
    catalog_lines: Iterable[Any],
    matrix: Any,
    *,
    technical_dependencies: Mapping[str, Any] | None = None,
) -> tuple[Any, tuple[str, ...], tuple[str, ...], Coverage]:
    """Replace only official, replaceable CORE slots in the same group/trade."""

    repaired = deepcopy(base_pack)
    base_data = _mapping(base_pack)
    trade_code = str(base_data.get("trade_code") or "")
    official_lines = tuple(catalog_lines)
    replaced_ids: list[str] = []
    replacement_ids: list[str] = []
    current_coverage = coverage_score(
        matrix,
        repaired,
        technical_dependencies=technical_dependencies,
    )

    for missing_item in missing_items:
        item_id = str(_mapping(missing_item).get("request_item_id") or "")
        if item_id not in current_coverage.required_missing:
            continue
        pairs: list[tuple[float, int, int, str, str, Any, Any]] = []
        current_lines = _pack_lines(repaired)
        for slot in current_lines:
            slot_data = _mapping(slot)
            if _phase(slot) != "CORE":
                continue
            replacement_group = str(slot_data.get("replacement_group") or "")
            replaceable = bool(
                slot_data.get("replaceable", bool(replacement_group))
            )
            if not replaceable or not replacement_group:
                continue
            slot_id = _line_id(slot)
            slot_utility = len(
                current_coverage.line_to_request_item_ids.get(slot_id, ())
            )
            slot_index = int(slot_data.get("slot_index") or 0)
            for candidate_line in official_lines:
                candidate_data = _mapping(candidate_line)
                if (
                    not bool(candidate_data.get("active", True))
                    or _phase(candidate_line) != "CORE"
                    or str(candidate_data.get("trade_code") or trade_code)
                    != trade_code
                    or str(candidate_data.get("replacement_group") or "")
                    != replacement_group
                    or _line_id(candidate_line)
                    in {_line_id(line) for line in current_lines}
                ):
                    continue
                match = match_item_to_line(missing_item, candidate_line)
                if not match.matched:
                    continue
                pairs.append(
                    (
                        -match.score,
                        slot_utility,
                        slot_index,
                        slot_id,
                        _line_id(candidate_line),
                        slot,
                        candidate_line,
                    )
                )
        pairs.sort(key=lambda value: value[:5])
        for _score, _utility, _slot_index, slot_id, new_id, _slot, new_line in pairs:
            candidate_pack = _replace_line(repaired, slot_id, new_line)
            candidate_coverage = coverage_score(
                matrix,
                candidate_pack,
                technical_dependencies=technical_dependencies,
            )
            if candidate_coverage.excluded_violated:
                continue
            if len(candidate_coverage.required_covered) <= len(
                current_coverage.required_covered
            ):
                continue
            repaired = candidate_pack
            current_coverage = candidate_coverage
            replaced_ids.append(slot_id)
            replacement_ids.append(new_id)
            break

    return (
        repaired,
        tuple(replaced_ids),
        tuple(replacement_ids),
        current_coverage,
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


def select_one_pack_per_intervention_and_repair(
    candidates: Iterable[Any],
    matrix: Any,
    packs: Mapping[str, Any] | Iterable[Any],
    catalog_lines: Iterable[Any],
    *,
    intervention_id: str = "INTERVENTION-001",
    trade_code: str,
    flow: str,
    official_fallback: Any,
    technical_dependencies: Mapping[str, Any] | None = None,
) -> SelectionResult:
    """Return exactly one exact, repaired, or official fallback pack."""

    if isinstance(packs, Mapping):
        pack_by_id = dict(packs)
    else:
        pack_by_id = {_pack_id(pack): pack for pack in packs}

    for candidate in stable_candidate_order(candidates):
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
        initial_coverage = coverage_score(
            matrix,
            candidate_pack,
            technical_dependencies=technical_dependencies,
        )
        if not initial_coverage.excluded_violated and not initial_coverage.required_missing:
            return SelectionResult(
                intervention_id=intervention_id,
                pack=candidate_pack,
                pack_id=_pack_id(candidate_pack),
                generation_mode="EXACT_PACK",
                coverage=initial_coverage,
            )

        missing_items = tuple(
            item
            for item_id in initial_coverage.required_missing
            if (item := _matrix_item(matrix, item_id)) is not None
        )
        if missing_items and not initial_coverage.excluded_violated:
            repaired, replaced_ids, replacement_ids, repaired_coverage = repair_core_pack(
                candidate_pack,
                missing_items,
                catalog_lines,
                matrix,
                technical_dependencies=technical_dependencies,
            )
            if (
                replaced_ids
                and not repaired_coverage.required_missing
                and not repaired_coverage.excluded_violated
            ):
                return SelectionResult(
                    intervention_id=intervention_id,
                    pack=repaired,
                    pack_id=_pack_id(candidate_pack),
                    generation_mode="REPAIRED_PACK",
                    coverage=repaired_coverage,
                    replaced_line_ids=replaced_ids,
                    replacement_line_ids=replacement_ids,
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
        fallback_reason="NO_EXACT_OR_REPAIRABLE_PACK",
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
