"""Layer 6 deterministic quantity, price and VAT application.

V3.2 — QuoteLine carries source_entity_type and shared-profile provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Any, Mapping

from app.v3.contracts import (
    DemandMatrix,
    QuantityUnit,
    QuoteLine,
    ResolutionSource,
    SourceEntityType,
)
from app.v3.ssot import Phase
from app.v3.measurements import resolve_quantity
from app.v3.pricing import calculate_line_amount, resolve_price
from app.v3.selector import SelectionResult
from app.v3.vat import VatRule as ResolvableVatRule
from app.v3.vat import resolve_vat


@dataclass(frozen=True, slots=True)
class CalculatedSelection:
    selection: SelectionResult
    lines: tuple[QuoteLine, ...]
    assumption_codes: tuple[str, ...]
    linear_formula_ids: tuple[str, ...]


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
    raise TypeError(f"Expected mapping-like line, got {type(value).__name__}")


def _pack_lines(pack: Any) -> list[Any]:
    raw = _mapping(pack)
    if raw.get("lines") is not None:
        return list(raw["lines"])
    result: list[Any] = []
    for field in ("setup", "core", "finish", "setup_lines", "core_lines", "finish_lines"):
        result.extend(raw.get(field) or ())
    return result


def _covered_items(
    matrix: DemandMatrix,
    request_item_ids: tuple[str, ...],
) -> list[Any]:
    accepted = set(request_item_ids)
    return [
        item for item in matrix.items if item.request_item_id in accepted
    ]


def _entity_provenance(
    line: Mapping[str, Any],
    pack: Mapping[str, Any],
) -> tuple[SourceEntityType, str | None, str | None, int | None, str | None, str | None, int | None]:
    """V3.2 — resolve PACK vs SHARED_PROFILE provenance for a catalog line."""

    phase = str(line.get("phase") or "").upper()
    raw_type = str(line.get("source_entity_type") or "").upper()
    if raw_type == SourceEntityType.SHARED_PROFILE.value or phase in {"SETUP", "FINISH"}:
        profile_id = (
            str(line.get("shared_profile_id") or "")
            or str(pack.get("shared_profile_id") or "")
            or f"legacy-profile-{pack.get('pack_id')}"
        )
        profile_code = (
            str(line.get("shared_profile_code") or "")
            or str(pack.get("shared_profile_code") or "")
            or f"LEGACY-{pack.get('pack_code') or pack.get('pack_id')}"
        )
        profile_version = int(
            line.get("shared_profile_version")
            or pack.get("shared_profile_version")
            or pack.get("version")
            or 1
        )
        return (
            SourceEntityType.SHARED_PROFILE,
            None,
            None,
            None,
            profile_id,
            profile_code,
            profile_version,
        )

    pack_id = str(line.get("pack_id") or pack.get("pack_id") or "")
    pack_code = str(line.get("pack_code") or pack.get("pack_code") or "")
    pack_version = int(line.get("pack_version") or pack.get("version") or 1)
    return (
        SourceEntityType.PACK,
        pack_id,
        pack_code,
        pack_version,
        None,
        None,
        None,
    )


def calculate_selection(
    selection: SelectionResult,
    matrix: DemandMatrix,
    project: Mapping[str, Any],
) -> CalculatedSelection:
    """Price an already selected official pack without any creative fallback."""

    assumptions: list[str] = []
    formulas: list[str] = []
    final_lines: list[QuoteLine] = []
    consumed_dimension_ids: set[str] = set()
    project_with_demand = {
        **dict(project),
        "global_context": matrix.global_context.model_dump(mode="python"),
    }
    pack_data = _mapping(selection.pack)

    for source_line in _pack_lines(selection.pack):
        line = _mapping(source_line)
        line_id = str(line.get("line_id") or "")
        covered_ids = tuple(
            selection.coverage.line_to_request_item_ids.get(line_id, ())
        )
        dependency_ids = tuple(
            selection.coverage.line_technical_dependency_ids.get(line_id, ())
            or line.get("technical_dependency_ids")
            or ()
        )
        if not covered_ids and not dependency_ids:
            raise ValueError(f"UNJUSTIFIED_OFFICIAL_LINE:{line_id}")

        quantity = resolve_quantity(
            line,
            _covered_items(matrix, covered_ids),
            project_with_demand,
            consumed_dimension_ids=consumed_dimension_ids,
        )
        if quantity.assumption_code:
            assumptions.append(quantity.assumption_code)
        if quantity.formula_id:
            formulas.append(quantity.formula_id)

        price = resolve_price(line)
        amount = calculate_line_amount(quantity.value, price.unit_price_cents)

        embedded_rate = float(line.get("vat_rate", 20))
        preferred = ResolvableVatRule(
            vat_rule_id=str(line.get("vat_rule_id") or "FR_STANDARD_20"),
            version=int(line.get("vat_rule_version") or 1),
            vat_rate=Decimal(str(embedded_rate)),
            territories=tuple(
                filter(
                    None,
                    (
                        str(project.get("territory_code") or "").upper(),
                    ),
                )
            ),
        )
        standard = ResolvableVatRule(
            vat_rule_id="FR_STANDARD_20",
            version=1,
            vat_rate=Decimal("20"),
            priority=-1000,
        )
        vat = resolve_vat(
            project,
            (preferred, standard),
            preferred_rule_id=preferred.vat_rule_id,
        )
        if vat.assumption_code:
            assumptions.append(vat.assumption_code)

        (
            source_entity_type,
            pack_id,
            pack_code,
            pack_version,
            shared_profile_id,
            shared_profile_code,
            shared_profile_version,
        ) = _entity_provenance(line, pack_data)

        unit_raw = str(quantity.unit or line.get("unit") or "FORFAIT")
        final_lines.append(
            QuoteLine(
                line_id=line_id,
                source_entity_type=source_entity_type,
                pack_id=pack_id,
                pack_code=pack_code,
                pack_version=pack_version,
                shared_profile_id=shared_profile_id,
                shared_profile_code=shared_profile_code,
                shared_profile_version=shared_profile_version,
                phase=Phase(str(line.get("phase") or "")),
                slot_index=int(line.get("slot_index") or 0),
                designation=str(line.get("designation") or ""),
                quantity=quantity.value,
                unit=QuantityUnit(unit_raw),
                price_id=price.price_id,
                price_version=price.price_version,
                unit_price_cents=price.unit_price_cents,
                vat_rule_id=vat.vat_rule_id,
                vat_rule_version=vat.vat_rule_version,
                vat_rate=vat.vat_rate,
                total_ht_cents=amount.total_ht_cents,
                covered_request_item_ids=list(covered_ids),
                technical_dependency_ids=list(dependency_ids),
                quantity_source=ResolutionSource(quantity.source),
                linear_measurement=quantity.linear_measurement,
            )
        )
    return CalculatedSelection(
        selection=selection,
        lines=tuple(final_lines),
        assumption_codes=tuple(sorted(set(assumptions))),
        linear_formula_ids=tuple(sorted(set(formulas))),
    )


__all__ = ["CalculatedSelection", "calculate_selection"]
