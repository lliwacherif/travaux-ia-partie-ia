"""Correctifs ciblés à intégrer dans la V3.2 §1 — découpage en interventions.

Each atomic demand item receives an intervention_id. Multi-work descriptions
are split into coherent interventions; search/selection run per intervention.
Each request_item_id belongs to exactly one intervention.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from app.v3.context import normalize_text
from app.v3.contracts import DemandItem, DemandMatrix
from app.v3.referential import (
    resolve_action_code,
    resolve_location_code,
    resolve_material_family_code,
    resolve_object_family_code,
    resolve_system_code,
    resolve_work_type_code,
)


@dataclass(frozen=True, slots=True)
class InterventionBlock:
    intervention_id: str
    trade_hint: str | None
    item_ids: tuple[str, ...]


def _intervention_key(item: DemandItem) -> str:
    """Group items that share object/location/system into one intervention."""

    object_code = item.object_family_code or normalize_text(item.object).matching
    location_code = item.location_code or (
        normalize_text(item.location or "").matching if item.location else ""
    )
    system_code = item.system_code or ""
    work = item.work_type_code or ""
    return "|".join((object_code, location_code, system_code, work))


def assign_intervention_ids(matrix: DemandMatrix) -> DemandMatrix:
    """Correctifs ciblés à intégrer dans la V3.2 — stamp intervention_id on items."""

    buckets: dict[str, list[DemandItem]] = defaultdict(list)
    order: list[str] = []
    for item in matrix.items:
        key = _intervention_key(item)
        if key not in buckets:
            order.append(key)
        buckets[key].append(item)

    updated: list[DemandItem] = []
    for index, key in enumerate(order, start=1):
        intervention_id = f"INTERVENTION-{index:03d}"
        for item in buckets[key]:
            updated.append(
                item.model_copy(update={"intervention_id": intervention_id})
            )
    return matrix.model_copy(update={"items": updated})


def enrich_normalized_codes(matrix: DemandMatrix) -> DemandMatrix:
    """Map raw GPT terms to referential codes; never invent codes."""

    enriched: list[DemandItem] = []
    for item in matrix.items:
        enriched.append(
            item.model_copy(
                update={
                    "action_code": resolve_action_code(item.action),
                    "work_type_code": resolve_work_type_code(
                        " ".join(
                            part
                            for part in (item.action, item.object, item.source_excerpt)
                            if part
                        )
                    ),
                    "object_family_code": resolve_object_family_code(item.object),
                    "location_code": resolve_location_code(item.location),
                    "system_code": resolve_system_code(
                        " ".join(
                            part
                            for part in (item.object, item.source_excerpt)
                            if part
                        )
                    ),
                    "material_family_code": resolve_material_family_code(
                        item.material
                    ),
                    "variant_attributes": list(item.variant_attributes or []),
                    "explicit_fact_codes": list(item.explicit_fact_codes or []),
                }
            )
        )
    return matrix.model_copy(update={"items": enriched})


def interventions_from_matrix(matrix: DemandMatrix) -> tuple[InterventionBlock, ...]:
    grouped: dict[str, list[str]] = defaultdict(list)
    order: list[str] = []
    trade_hints: dict[str, str | None] = {}
    for item in matrix.items:
        iid = item.intervention_id or "INTERVENTION-001"
        if iid not in grouped:
            order.append(iid)
            trade_hints[iid] = item.object_family_code
        grouped[iid].append(item.request_item_id)
    return tuple(
        InterventionBlock(
            intervention_id=iid,
            trade_hint=trade_hints.get(iid),
            item_ids=tuple(grouped[iid]),
        )
        for iid in order
    )


def matrix_for_intervention(
    matrix: DemandMatrix,
    intervention_id: str,
) -> DemandMatrix:
    items = [
        item
        for item in matrix.items
        if (item.intervention_id or "INTERVENTION-001") == intervention_id
    ]
    if not items:
        raise ValueError(f"NO_ITEMS_FOR_INTERVENTION:{intervention_id}")
    return matrix.model_copy(update={"items": items})


def assert_single_intervention_ownership(matrix: DemandMatrix) -> None:
    ownership: dict[str, str] = {}
    for item in matrix.items:
        iid = item.intervention_id or ""
        if not iid:
            raise ValueError(f"MISSING_INTERVENTION_ID:{item.request_item_id}")
        previous = ownership.get(item.request_item_id)
        if previous and previous != iid:
            raise ValueError(
                f"ITEM_MULTI_INTERVENTION:{item.request_item_id}:{previous}/{iid}"
            )
        ownership[item.request_item_id] = iid


__all__ = [
    "InterventionBlock",
    "assign_intervention_ids",
    "assert_single_intervention_ownership",
    "enrich_normalized_codes",
    "interventions_from_matrix",
    "matrix_for_intervention",
]
