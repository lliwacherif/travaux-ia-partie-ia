"""Layer 3bis demand normalization with item-scoped quantities and dimensions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from .context import normalize_text, normalize_unit
from .contracts import DemandMatrix


_STATUS_ALIASES = {
    "REQUIRED": "REQUIRED",
    "OBLIGATOIRE": "REQUIRED",
    "DEMANDE": "REQUIRED",
    "EXCLUDED": "EXCLUDED",
    "EXCLU": "EXCLUDED",
    "EXCLUE": "EXCLUDED",
    "CONDITIONAL": "CONDITIONAL",
    "CONDITIONNEL": "CONDITIONAL",
    "CONDITIONNELLE": "CONDITIONAL",
    "OPTIONAL": "OPTIONAL",
    "OPTIONNEL": "OPTIONAL",
    "OPTIONNELLE": "OPTIONAL",
}
_KIND_ALIASES = {
    "LENGTH": "LENGTH",
    "LONGUEUR": "LENGTH",
    "WIDTH": "WIDTH",
    "LARGEUR": "WIDTH",
    "HEIGHT": "HEIGHT",
    "HAUTEUR": "HEIGHT",
    "AXIS_LENGTH": "AXIS_LENGTH",
    "AXE": "AXIS_LENGTH",
    "LONGUEUR_AXE": "AXIS_LENGTH",
    "SEGMENT_LENGTH": "SEGMENT_LENGTH",
    "SEGMENT": "SEGMENT_LENGTH",
    "PERIMETER": "PERIMETER",
    "PERIMETRE": "PERIMETER",
    "DEVELOPED_LENGTH": "DEVELOPED_LENGTH",
    "DEVELOPPE": "DEVELOPED_LENGTH",
    "SURFACE": "SURFACE",
    "AREA": "SURFACE",
    "COUNT": "COUNT",
    "NOMBRE": "COUNT",
    "QUANTITE": "COUNT",
    "COVERAGE_WIDTH": "COVERAGE_WIDTH",
    "LARGEUR_UTILE": "COVERAGE_WIDTH",
}
_LINEAR_MODES = {
    "EXPLICIT",
    "AXIAL",
    "LONGITUDINAL",
    "PERIMETRIC",
    "DEVELOPED",
    "CUMULATED",
    "SURFACE_TO_LINEAR",
    "COUNT_TIMES_LENGTH",
}
_QUANTITY_UNITS = {"M2", "ML", "M3", "UNIT", "HOUR", "DAY", "FORFAIT"}
_DIMENSION_UNITS = {"MM", "CM", "M", "M2", "UNIT"}
_ACTION_ALIASES = {
    "PROVIDE": "fournir et poser",
    "SUPPLY": "fournir",
    "INSTALL": "poser",
    "CONDUCT": "concevoir",
    "DESIGN": "concevoir",
    "FABRICATE": "fabriquer",
    "TRANSPORT": "transporter",
    "LIFT": "lever",
    "BOLT": "assembler",
    "ASSEMBLE": "assembler",
    "ADJUST": "régler",
    "CONSTRUCT": "construire",
    "BUILD": "construire",
    "TREAT": "traiter",
    "FIX": "fixer",
    "PREPARE": "préparer",
    "EXCLUDE": "exclure",
}


@dataclass(frozen=True, slots=True)
class DemandNormalizationAssumption:
    code: str
    request_item_id: str | None
    field: str
    source_value: Any
    normalized_value: Any


@dataclass(frozen=True, slots=True)
class DemandNormalizationResult:
    matrix: DemandMatrix
    assumptions: tuple[DemandNormalizationAssumption, ...]


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="python"))
    raise TypeError(f"Expected mapping-like demand data, got {type(value).__name__}")


def _canonical_symbol(value: Any) -> str:
    return normalize_text(str(value or "")).matching.upper().replace(" ", "_")


def _normalize_action(value: Any, object_value: Any) -> str:
    symbol = _canonical_symbol(value)
    object_symbol = _canonical_symbol(object_value)
    if symbol in {"PREVOIR", "PLANIFIER"} and (
        "ETUDE" in object_symbol or "PLAN" in object_symbol
    ):
        return "concevoir"
    if symbol in {"CONCEVOIR", "DESIGN"} and not (
        "ETUDE" in object_symbol or "PLAN" in object_symbol
    ):
        return "construire"
    if symbol == "APPLY":
        if "PROTECTION" in object_symbol or "ANTICORROSION" in object_symbol:
            return "protéger"
        if "TRAITEMENT" in object_symbol:
            return "traiter"
    return _ACTION_ALIASES.get(
        symbol,
        normalize_text(str(value or "")).canonical,
    )


def _normalize_material(value: Any) -> str | None:
    if value in (None, ""):
        return None
    symbol = _canonical_symbol(value)
    if symbol in {
        "ACIER",
        "METAL",
        "METALLIQUE",
        "STRUCTURE_METALLIQUE",
    }:
        return "acier"
    if symbol in {"BOIS", "BOIS_TRAITE", "TRAITE"}:
        return "bois"
    if symbol in {
        "ANTICORROSION",
        "PROTECTION_ANTICORROSION",
        "TRAITEMENT_ANTICORROSION",
    }:
        return None
    return normalize_text(str(value)).canonical


def _positive_number(value: Any, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    number = Decimal(str(value))
    if number <= 0:
        raise ValueError(f"{field_name} must be strictly positive")
    return float(number)


def _normalize_quantity_and_unit(
    quantity: Any,
    unit: Any,
    item_id: str,
    assumptions: list[DemandNormalizationAssumption],
) -> tuple[float | None, str | None]:
    number = _positive_number(quantity, f"{item_id}.quantity")
    normalized_unit = normalize_unit(None if unit is None else str(unit))
    if normalized_unit is None:
        return number, None
    if normalized_unit == "M":
        normalized_unit = "ML"
    elif normalized_unit in {"MM", "CM"} and number is not None:
        divisor = Decimal("1000") if normalized_unit == "MM" else Decimal("100")
        converted = Decimal(str(number)) / divisor
        assumptions.append(
            DemandNormalizationAssumption(
                code="EXPLICIT_LENGTH_CONVERTED_TO_ML",
                request_item_id=item_id,
                field="unit",
                source_value={"quantity": quantity, "unit": unit},
                normalized_value={"quantity": float(converted), "unit": "ML"},
            )
        )
        number = float(converted)
        normalized_unit = "ML"
    if normalized_unit not in _QUANTITY_UNITS:
        assumptions.append(
            DemandNormalizationAssumption(
                code="UNSUPPORTED_QUANTITY_UNIT_CLEARED",
                request_item_id=item_id,
                field="unit",
                source_value=unit,
                normalized_value=None,
            )
        )
        normalized_unit = None
    return number, normalized_unit


def normalize_demand_matrix_with_metadata(
    raw_matrix: Any,
    context: Any | None = None,
) -> DemandNormalizationResult:
    """Normalize a raw extractor result without propagating values across ITEMs."""

    raw = _mapping(raw_matrix)
    raw_items = list(raw.get("items") or ())
    if not raw_items:
        raise ValueError("A DemandMatrix must contain at least one atomic item")

    assumptions: list[DemandNormalizationAssumption] = []
    normalized_items: list[dict[str, Any]] = []
    next_dimension_number = 1

    for item_number, raw_item_value in enumerate(raw_items, start=1):
        raw_item = _mapping(raw_item_value)
        item_id = f"ITEM-{item_number:03d}"
        quantity, unit = _normalize_quantity_and_unit(
            raw_item.get("quantity"),
            raw_item.get("unit"),
            item_id,
            assumptions,
        )

        status_source = raw_item.get("status")
        status = _STATUS_ALIASES.get(_canonical_symbol(status_source))
        if status is None:
            status = "REQUIRED"
            assumptions.append(
                DemandNormalizationAssumption(
                    code="DEMAND_STATUS_DEFAULT_REQUIRED",
                    request_item_id=item_id,
                    field="status",
                    source_value=status_source,
                    normalized_value=status,
                )
            )

        dimensions: list[dict[str, Any]] = []
        for raw_dimension_value in raw_item.get("dimensions") or ():
            raw_dimension = _mapping(raw_dimension_value)
            dimension_id = f"DIM-{next_dimension_number:03d}"
            next_dimension_number += 1
            kind = _KIND_ALIASES.get(_canonical_symbol(raw_dimension.get("kind")))
            if kind is None:
                raise ValueError(
                    f"{item_id}.{dimension_id} has an unsupported dimension kind"
                )
            dimension_unit = normalize_unit(str(raw_dimension.get("unit") or ""))
            if dimension_unit == "ML":
                dimension_unit = "M"
            if dimension_unit not in _DIMENSION_UNITS:
                raise ValueError(
                    f"{item_id}.{dimension_id} has unsupported unit "
                    f"{raw_dimension.get('unit')!r}"
                )
            dimensions.append(
                {
                    "dimension_id": dimension_id,
                    "kind": kind,
                    "value": _positive_number(
                        raw_dimension.get("value"),
                        f"{item_id}.{dimension_id}.value",
                    ),
                    "unit": dimension_unit,
                    "source_excerpt": str(
                        raw_dimension.get("source_excerpt")
                        or raw_item.get("source_excerpt")
                        or ""
                    ).strip(),
                }
            )

        mode_value = raw_item.get("linear_measurement_hint")
        mode = _canonical_symbol(mode_value) if mode_value else None
        if mode not in _LINEAR_MODES:
            if mode is not None:
                assumptions.append(
                    DemandNormalizationAssumption(
                        code="UNSUPPORTED_LINEAR_HINT_CLEARED",
                        request_item_id=item_id,
                        field="linear_measurement_hint",
                        source_value=mode_value,
                        normalized_value=None,
                    )
                )
            mode = None

        normalized_items.append(
            {
                "request_item_id": item_id,
                "action": _normalize_action(
                    raw_item.get("action"),
                    raw_item.get("object"),
                ),
                "object": normalize_text(str(raw_item.get("object") or "")).canonical,
                "material": _normalize_material(raw_item.get("material")),
                "quantity": quantity,
                "unit": unit,
                "dimensions": dimensions,
                "linear_measurement_hint": mode,
                "location": (
                    normalize_text(str(raw_item["location"])).canonical
                    if raw_item.get("location") not in (None, "")
                    else None
                ),
                "status": status,
                "condition": (
                    str(raw_item["condition"]).strip()
                    if raw_item.get("condition") not in (None, "")
                    else None
                ),
                "source_excerpt": str(raw_item.get("source_excerpt") or "").strip(),
            }
        )

    global_context = _mapping(raw.get("global_context") or {})
    notes = [str(note) for note in global_context.get("notes") or ()]
    notes.extend(
        f"ASSUMPTION:{entry.code}:{entry.request_item_id or 'GLOBAL'}"
        for entry in assumptions
    )
    normalized_global = {
        # Global values remain global metadata.  They are never copied into an
        # ITEM here; a line must opt into GLOBAL_AREA/GLOBAL_LENGTH later.
        "global_area_m2": _positive_number(
            global_context.get("global_area_m2"),
            "global_context.global_area_m2",
        ),
        "global_length_ml": _positive_number(
            global_context.get("global_length_ml"),
            "global_context.global_length_ml",
        ),
        "notes": notes,
    }
    matrix = DemandMatrix.model_validate(
        {"items": normalized_items, "global_context": normalized_global}
    )
    return DemandNormalizationResult(matrix=matrix, assumptions=tuple(assumptions))


def normalize_demand_matrix(raw_matrix: Any, context: Any | None = None) -> DemandMatrix:
    """Return only the normalized V3 ``DemandMatrix`` contract."""

    return normalize_demand_matrix_with_metadata(raw_matrix, context).matrix


__all__ = [
    "DemandNormalizationAssumption",
    "DemandNormalizationResult",
    "normalize_demand_matrix",
    "normalize_demand_matrix_with_metadata",
]
