"""Correctifs ciblés à intégrer dans la V3.2 §4 — éligibilité bloquante à 3 états.

COMPATIBLE / UNKNOWN / INCOMPATIBLE before scoring.
Explicit contradictions eliminate; missing information does not.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Iterable, Mapping

from app.v3.context import normalize_text
from app.v3.ssot import EligibilityStatus


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
    raise TypeError(f"Expected mapping-like eligibility data, got {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class EligibilityDecision:
    status: EligibilityStatus
    hard_exclusion_reasons: tuple[str, ...]
    soft_mismatch_reasons: tuple[str, ...]


def _codes(value: Any) -> set[str]:
    if value in (None, "", ()):
        return set()
    if isinstance(value, str):
        return {normalize_text(value).matching}
    return {
        normalize_text(str(item)).matching
        for item in value
        if item not in (None, "")
    }


def evaluate_pack_eligibility(
    pack: Any,
    matrix: Any,
    *,
    trade_code: str,
    flow: str,
) -> EligibilityDecision:
    """Hard eligibility gate applied after flow/trade/snapshot lock."""

    pack_data = _mapping(pack)
    hard: list[str] = []
    soft: list[str] = []

    pack_trade = str(pack_data.get("trade_code") or "")
    pack_flow = str(pack_data.get("flow") or "").upper()
    if pack_trade and pack_trade != trade_code:
        hard.append(f"TRADE_MISMATCH:{pack_trade}")
    if pack_flow and pack_flow != flow.upper():
        hard.append(f"FLOW_MISMATCH:{pack_flow}")
    if pack_data.get("active") is False or str(pack_data.get("status") or "").upper() not in (
        "",
        "PUBLISHED",
    ):
        # CatalogPack may not carry status; only hard-fail explicit inactive.
        if pack_data.get("active") is False:
            hard.append("PACK_INACTIVE")

    pack_exclusions = _codes(pack_data.get("exclusion_tags"))
    for line in pack_data.get("lines") or ():
        pack_exclusions |= _codes(_mapping(line).get("exclusion_tags"))

    known_compat = False
    for item in _mapping(matrix).get("items") or ():
        item_data = _mapping(item)
        status = str(item_data.get("status") or "").upper()
        material = normalize_text(str(item_data.get("material") or "")).matching
        material_code = str(item_data.get("material_family_code") or "")
        object_code = str(item_data.get("object_family_code") or "")
        action_code = str(item_data.get("action_code") or "")

        # Explicit excluded demand billed against pack capability → incompatible.
        if status == "EXCLUDED":
            continue

        item_tokens = {
            token
            for token in (
                material,
                normalize_text(str(item_data.get("object") or "")).matching,
                normalize_text(str(item_data.get("action") or "")).matching,
                material_code.lower(),
                object_code.lower(),
                action_code.lower(),
            )
            if token
        }
        contradiction = pack_exclusions.intersection(item_tokens)
        if contradiction:
            hard.append(
                "EXPLICIT_EXCLUSION:"
                + ",".join(sorted(contradiction))
                + f":{item_data.get('request_item_id')}"
            )

        # Soft: material declared on both sides and diverge without exclusion.
        pack_materials: set[str] = set()
        for line in pack_data.get("lines") or ():
            line_material = normalize_text(
                str(_mapping(line).get("material_family") or "")
            ).matching
            if line_material:
                pack_materials.add(line_material)
        if material and pack_materials and material not in pack_materials:
            soft.append(
                f"MATERIAL_SOFT_MISMATCH:{item_data.get('request_item_id')}:{material}"
            )
        elif material and material in pack_materials:
            known_compat = True
        elif object_code or action_code:
            known_compat = True

    if hard:
        return EligibilityDecision(
            status=EligibilityStatus.INCOMPATIBLE,
            hard_exclusion_reasons=tuple(hard),
            soft_mismatch_reasons=tuple(soft),
        )
    if known_compat and not soft:
        return EligibilityDecision(
            status=EligibilityStatus.COMPATIBLE,
            hard_exclusion_reasons=(),
            soft_mismatch_reasons=(),
        )
    # Correctifs ciblés à intégrer dans la V3.2 — info manquante ≠ élimination.
    return EligibilityDecision(
        status=EligibilityStatus.UNKNOWN,
        hard_exclusion_reasons=(),
        soft_mismatch_reasons=tuple(soft),
    )


def filter_eligible_candidates(
    candidates: Iterable[Any],
    packs: Mapping[str, Any],
    matrix: Any,
    *,
    trade_code: str,
    flow: str,
) -> tuple[Any, ...]:
    """Drop only INCOMPATIBLE packs; UNKNOWN and COMPATIBLE remain candidates."""

    kept: list[Any] = []
    for candidate in candidates:
        data = _mapping(candidate)
        pack = data.get("pack") or packs.get(str(data.get("pack_id") or ""))
        if pack is None:
            kept.append(candidate)
            continue
        decision = evaluate_pack_eligibility(
            pack, matrix, trade_code=trade_code, flow=flow
        )
        update = {
            "eligibility_status": decision.status,
            "hard_exclusion_reasons": list(decision.hard_exclusion_reasons),
            "soft_mismatch_reasons": list(decision.soft_mismatch_reasons),
        }
        model_copy = getattr(candidate, "model_copy", None)
        updated = (
            model_copy(update=update)
            if callable(model_copy)
            else {**data, **update}
        )
        if decision.status is EligibilityStatus.INCOMPATIBLE:
            continue
        kept.append(updated)
    return tuple(kept)


__all__ = [
    "EligibilityDecision",
    "evaluate_pack_eligibility",
    "filter_eligible_candidates",
]
