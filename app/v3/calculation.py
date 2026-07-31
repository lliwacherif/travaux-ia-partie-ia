"""Layer 6 deterministic quantity, price and VAT application."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from typing import Any, Mapping

from app.v3.contracts import (
    DemandMatrix,
    QuoteLine,
    ResolutionSource,
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

        final_lines.append(
            QuoteLine(
                line_id=line_id,
                pack_id=selection.pack_id,
                phase=Phase(str(line.get("phase") or "")),
                slot_index=int(line.get("slot_index") or 0),
                designation=str(line.get("designation") or ""),
                quantity=quantity.value,
                unit=quantity.unit,
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
