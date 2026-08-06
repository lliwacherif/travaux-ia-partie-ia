"""Deterministic quantity and linear-measurement engines for V3 layer 6.

Correctifs ciblés à intégrer dans la V3.2 §5 —
faits mesurés + liaison officielle (quantity_rule_id / share groups).
Remplace l'usage exclusif de uniqueExplicitQuantity(items, unit).
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal, ROUND_HALF_UP
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, MutableSet

from .context import normalize_unit
from .contracts import LinearMeasurementResolution


LINEAR_FORMULA_VERSION = "2026-07-31.1-correctifs"
LINEAR_FORMULA_MODES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "EXPLICIT": frozenset({"ML_EXPLICIT"}),
        "AXIAL": frozenset({"ML_AXIS"}),
        "LONGITUDINAL": frozenset({"ML_LONGITUDINAL"}),
        "PERIMETRIC": frozenset(
            {
                "ML_PERIMETER_GIVEN",
                "ML_PERIMETER_RECT",
                "ML_PERIMETER_SEGMENTS",
            }
        ),
        "DEVELOPED": frozenset({"ML_DEVELOPED"}),
        "CUMULATED": frozenset({"ML_CUMULATED"}),
        "SURFACE_TO_LINEAR": frozenset({"ML_SURFACE_TO_LINEAR"}),
        "COUNT_TIMES_LENGTH": frozenset({"ML_COUNT_TIMES_LENGTH"}),
    }
)
WHITELISTED_LINEAR_FORMULA_IDS = frozenset(
    formula_id
    for formula_ids in LINEAR_FORMULA_MODES.values()
    for formula_id in formula_ids
)
WHITELISTED_QUANTITY_FORMULA_IDS = frozenset(
    {
        "QTY_SUM_EXPLICIT",
        "QTY_COUNT_ITEMS",
        "QTY_RECTANGLE_AREA",
        "QTY_RECTANGULAR_VOLUME",
    }
)


@dataclass(frozen=True, slots=True)
class QuantityResolution:
    value: float
    unit: str
    source: str
    assumption_code: str | None = None
    formula_id: str | None = None
    input_dimension_ids: tuple[str, ...] = ()
    linear_measurement: LinearMeasurementResolution | None = None
    # Correctifs ciblés à intégrer dans la V3.2 §5.
    quantity_rule_id: str | None = None
    binding_scope: str | None = None
    share_group_id: str | None = None
    bound_fact_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Dimension:
    dimension_id: str
    kind: str
    value: Decimal
    unit: str
    item_id: str

    def metres(self) -> Decimal:
        if self.unit == "M":
            return self.value
        if self.unit == "CM":
            return self.value / Decimal("100")
        if self.unit == "MM":
            return self.value / Decimal("1000")
        raise ValueError(f"{self.dimension_id} is not a length dimension")

    def square_metres(self) -> Decimal:
        if self.unit != "M2":
            raise ValueError(f"{self.dimension_id} is not a surface dimension")
        return self.value

    def count(self) -> Decimal:
        if self.unit != "UNIT":
            raise ValueError(f"{self.dimension_id} is not a count dimension")
        return self.value


@dataclass(frozen=True, slots=True)
class _FormulaValue:
    value: Decimal
    dimension_ids: tuple[str, ...]


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
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    raise TypeError(f"Expected mapping-like value, got {type(value).__name__}")


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _items(values: Iterable[Any]) -> list[dict[str, Any]]:
    return [_mapping(value) for value in values]


def _dimensions(values: Iterable[Any]) -> tuple[_Dimension, ...]:
    dimensions: list[_Dimension] = []
    seen: set[str] = set()
    for item in _items(values):
        item_id = str(item.get("request_item_id") or "")
        for raw_value in item.get("dimensions") or ():
            raw = _mapping(raw_value)
            dimension_id = str(raw.get("dimension_id") or "")
            if not dimension_id or dimension_id in seen:
                continue
            seen.add(dimension_id)
            dimensions.append(
                _Dimension(
                    dimension_id=dimension_id,
                    kind=str(raw.get("kind") or "").upper(),
                    value=Decimal(str(raw.get("value"))),
                    unit=str(normalize_unit(str(raw.get("unit") or "")) or ""),
                    item_id=item_id,
                )
            )
    return tuple(dimensions)


def _unique_dimensions(
    dimensions: Iterable[_Dimension],
    consumed_dimension_ids: set[str],
) -> tuple[_Dimension, ...]:
    seen: set[str] = set()
    result: list[_Dimension] = []
    for dimension in dimensions:
        if (
            dimension.dimension_id in seen
            or dimension.dimension_id in consumed_dimension_ids
        ):
            continue
        seen.add(dimension.dimension_id)
        result.append(dimension)
    return tuple(result)


def _sum_lengths(dimensions: Iterable[_Dimension]) -> _FormulaValue | None:
    values = tuple(dimensions)
    if not values:
        return None
    return _FormulaValue(
        value=sum((dimension.metres() for dimension in values), Decimal("0")),
        dimension_ids=tuple(dimension.dimension_id for dimension in values),
    )


def _perimeter_rect(dimensions: Iterable[_Dimension]) -> _FormulaValue | None:
    by_item: dict[str, dict[str, list[_Dimension]]] = {}
    for dimension in dimensions:
        by_item.setdefault(dimension.item_id, {}).setdefault(
            dimension.kind, []
        ).append(dimension)
    rectangles: list[_FormulaValue] = []
    for item_id in sorted(by_item):
        kinds = by_item[item_id]
        lengths = kinds.get("LENGTH", ())
        widths = kinds.get("WIDTH", ())
        if len(lengths) == 1 and len(widths) == 1:
            length = lengths[0]
            width = widths[0]
            rectangles.append(
                _FormulaValue(
                    value=Decimal("2") * (length.metres() + width.metres()),
                    dimension_ids=(length.dimension_id, width.dimension_id),
                )
            )
    return rectangles[0] if len(rectangles) == 1 else None


def _surface_to_linear(dimensions: Iterable[_Dimension]) -> _FormulaValue | None:
    by_item: dict[str, dict[str, list[_Dimension]]] = {}
    for dimension in dimensions:
        by_item.setdefault(dimension.item_id, {}).setdefault(
            dimension.kind, []
        ).append(dimension)
    resolutions: list[_FormulaValue] = []
    for item_id in sorted(by_item):
        kinds = by_item[item_id]
        surfaces = kinds.get("SURFACE", ())
        widths = kinds.get("COVERAGE_WIDTH", ())
        if len(surfaces) == 1 and len(widths) == 1:
            surface = surfaces[0]
            width = widths[0]
            width_m = width.metres()
            if width_m > 0:
                resolutions.append(
                    _FormulaValue(
                        value=surface.square_metres() / width_m,
                        dimension_ids=(surface.dimension_id, width.dimension_id),
                    )
                )
    return resolutions[0] if len(resolutions) == 1 else None


def _count_times_length(dimensions: Iterable[_Dimension]) -> _FormulaValue | None:
    by_item: dict[str, dict[str, list[_Dimension]]] = {}
    for dimension in dimensions:
        by_item.setdefault(dimension.item_id, {}).setdefault(
            dimension.kind, []
        ).append(dimension)
    resolutions: list[_FormulaValue] = []
    for item_id in sorted(by_item):
        kinds = by_item[item_id]
        counts = kinds.get("COUNT", ())
        lengths = tuple(kinds.get("LENGTH", ())) + tuple(
            kinds.get("SEGMENT_LENGTH", ())
        )
        if len(counts) == 1 and len(lengths) == 1:
            count = counts[0]
            length = lengths[0]
            resolutions.append(
                _FormulaValue(
                    value=count.count() * length.metres(),
                    dimension_ids=(count.dimension_id, length.dimension_id),
                )
            )
    return resolutions[0] if len(resolutions) == 1 else None


def _evaluate_linear_formula(
    formula_id: str,
    dimensions: tuple[_Dimension, ...],
) -> _FormulaValue | None:
    if formula_id == "ML_AXIS":
        return _sum_lengths(
            dimension for dimension in dimensions if dimension.kind == "AXIS_LENGTH"
        )
    if formula_id == "ML_LONGITUDINAL":
        return _sum_lengths(
            dimension
            for dimension in dimensions
            if dimension.kind == "SEGMENT_LENGTH"
        )
    if formula_id == "ML_PERIMETER_GIVEN":
        return _sum_lengths(
            dimension for dimension in dimensions if dimension.kind == "PERIMETER"
        )
    if formula_id == "ML_PERIMETER_RECT":
        # Deliberately never derives a perimeter from SURFACE.
        return _perimeter_rect(dimensions)
    if formula_id == "ML_PERIMETER_SEGMENTS":
        return _sum_lengths(
            dimension
            for dimension in dimensions
            if dimension.kind == "SEGMENT_LENGTH"
        )
    if formula_id == "ML_DEVELOPED":
        return _sum_lengths(
            dimension
            for dimension in dimensions
            if dimension.kind == "DEVELOPED_LENGTH"
        )
    if formula_id == "ML_CUMULATED":
        return _sum_lengths(
            dimension
            for dimension in dimensions
            if dimension.kind
            in {
                "LENGTH",
                "AXIS_LENGTH",
                "SEGMENT_LENGTH",
                "DEVELOPED_LENGTH",
                "PERIMETER",
            }
        )
    if formula_id == "ML_SURFACE_TO_LINEAR":
        return _surface_to_linear(dimensions)
    if formula_id == "ML_COUNT_TIMES_LENGTH":
        return _count_times_length(dimensions)
    return None


def _round_by_rule(
    value: Decimal,
    precision: int,
    rounding_step: Any | None,
) -> Decimal:
    if rounding_step not in (None, "", 0, 0.0):
        step = Decimal(str(rounding_step))
        if step <= 0:
            raise ValueError("rounding_step must be positive")
        value = (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
    quantum = Decimal("1").scaleb(-max(0, int(precision)))
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _collect_measurement_facts(
    covered_items: Iterable[Any],
    unit: str,
) -> tuple[tuple[str, Decimal], ...]:
    """Correctifs ciblés à intégrer dans la V3.2 §5 — faits mesurés unitaires."""

    facts: list[tuple[str, Decimal]] = []
    for item in _items(covered_items):
        item_id = str(item.get("request_item_id") or "")
        for index, raw_fact in enumerate(item.get("measurement_facts") or (), start=1):
            fact = _mapping(raw_fact)
            fact_unit = normalize_unit(fact.get("unit"))
            if fact_unit == "M":
                fact_unit = "ML"
            if fact_unit != unit or fact.get("value") is None:
                continue
            fact_id = str(fact.get("fact_id") or f"FACT-{index:03d}")
            facts.append((fact_id, Decimal(str(fact["value"]))))
        item_unit = normalize_unit(item.get("unit"))
        if item_unit == "M":
            item_unit = "ML"
        if item.get("quantity") is not None and item_unit == unit:
            facts.append((f"EXPLICIT:{item_id}", Decimal(str(item["quantity"]))))
    return tuple(facts)


def _explicit_quantity(
    covered_items: Iterable[Any],
    unit: str,
) -> tuple[Decimal, tuple[str, ...]] | None:
    """Bind a single explicit measurement fact; never silently sum multiples."""

    facts = _collect_measurement_facts(covered_items, unit)
    if len(facts) == 1 and facts[0][1] > 0:
        return facts[0][1], (facts[0][0],)
    return None


def _binding_meta(line: Mapping[str, Any]) -> tuple[str, str, str | None]:
    rule = str(line.get("quantity_rule") or line.get("quantity_rule_id") or "LINE_DEFAULT")
    scope = str(line.get("quantity_binding_scope") or "LINE").upper()
    share = line.get("share_group_id")
    return rule, scope, str(share) if share else None


def _project_value(
    line: Any,
    project: Any | None,
    *,
    linear: bool,
) -> Decimal | None:
    line_data = _mapping(line)
    params = _mapping(line_data.get("linear_params") or {})
    project_field = params.get("project_field")
    quantity_rule = str(line_data.get("quantity_rule") or "")
    if quantity_rule.startswith("PROJECT:"):
        project_field = quantity_rule.partition(":")[2]
    if not project_field:
        return None
    allowed = (
        {"global_length_ml", "project_length_ml"}
        if linear
        else {
            "global_area_m2",
            "global_length_ml",
            "project_area_m2",
            "project_length_ml",
            "room_count",
        }
    )
    if project_field not in allowed:
        return None
    project_data = _mapping(project)
    if "global_context" in project_data:
        project_data = {**project_data, **_mapping(project_data["global_context"])}
    value = project_data.get(project_field)
    if value is None:
        return None
    number = Decimal(str(value))
    return number if number > 0 else None


def _linear_contract(
    *,
    line_id: str,
    request_item_ids: tuple[str, ...],
    mode: str,
    formula_id: str | None,
    dimension_ids: tuple[str, ...],
    value: Decimal,
    source: str,
    assumption_code: str | None,
) -> LinearMeasurementResolution:
    return LinearMeasurementResolution.model_validate(
        {
            "line_id": line_id,
            "request_item_ids": list(request_item_ids),
            "mode": mode,
            "formula_id": formula_id,
            "input_dimension_ids": list(dimension_ids),
            "value_ml": float(value),
            "source": source,
            "assumption_code": assumption_code,
        }
    )


def resolve_linear_measurement(
    line: Any,
    covered_items: Iterable[Any],
    project: Any | None = None,
    *,
    consumed_dimension_ids: MutableSet[str] | None = None,
) -> LinearMeasurementResolution:
    """Resolve all eight official modes with explicit/formula/context/default order."""

    line_data = _mapping(line)
    item_values = tuple(covered_items)
    request_item_ids = tuple(
        str(_field(item, "request_item_id", ""))
        for item in item_values
        if str(_field(item, "request_item_id", ""))
    )
    line_id = str(line_data.get("line_id") or "")
    mode = str(line_data.get("linear_measurement_mode") or "").upper()
    formula_id = str(line_data.get("linear_formula_id") or "") or None
    if mode not in LINEAR_FORMULA_MODES:
        raise ValueError(f"Unsupported linear measurement mode {mode!r}")

    explicit = _explicit_quantity(item_values, "ML")
    if explicit is not None:
        value, _fact_ids = explicit
        rounded = _round_by_rule(
            value,
            int(line_data.get("quantity_precision", 3)),
            line_data.get("rounding_step"),
        )
        return _linear_contract(
            line_id=line_id,
            request_item_ids=request_item_ids,
            mode="EXPLICIT",
            formula_id=None,
            dimension_ids=(),
            value=rounded,
            source="EXPLICIT",
            assumption_code=None,
        )

    consumed = set(consumed_dimension_ids or ())
    all_dimensions = _unique_dimensions(_dimensions(item_values), consumed)
    formula_failure: str | None = None
    if formula_id not in WHITELISTED_LINEAR_FORMULA_IDS:
        formula_failure = f"ML_FORMULA_NOT_REGISTERED:{line_id}"
    elif formula_id not in LINEAR_FORMULA_MODES[mode]:
        formula_failure = f"ML_FORMULA_MODE_MISMATCH:{line_id}"
    else:
        formula_value = _evaluate_linear_formula(formula_id, all_dimensions)
        if formula_value is not None and formula_value.value > 0:
            rounded = _round_by_rule(
                formula_value.value,
                int(line_data.get("quantity_precision", 3)),
                line_data.get("rounding_step"),
            )
            if consumed_dimension_ids is not None:
                consumed_dimension_ids.update(formula_value.dimension_ids)
            return _linear_contract(
                line_id=line_id,
                request_item_ids=request_item_ids,
                mode=mode,
                formula_id=formula_id,
                dimension_ids=formula_value.dimension_ids,
                value=rounded,
                source="FORMULA",
                assumption_code=None,
            )
        formula_failure = f"ML_FORMULA_INPUT_MISSING:{line_id}"

    context_value = _project_value(line, project, linear=True)
    if context_value is not None:
        rounded = _round_by_rule(
            context_value,
            int(line_data.get("quantity_precision", 3)),
            line_data.get("rounding_step"),
        )
        assumption = f"ML_PROJECT_CONTEXT:{line_id}"
        return _linear_contract(
            line_id=line_id,
            request_item_ids=request_item_ids,
            mode=mode,
            formula_id=formula_id,
            dimension_ids=(),
            value=rounded,
            source="PROJECT_CONTEXT",
            assumption_code=assumption,
        )

    default_value = Decimal(str(line_data.get("default_quantity") or "0"))
    if default_value <= 0:
        raise ValueError(f"Official ML default is missing for line {line_id}")
    rounded = _round_by_rule(
        default_value,
        int(line_data.get("quantity_precision", 3)),
        line_data.get("rounding_step"),
    )
    assumption = f"ML_PACK_DEFAULT:{line_id}"
    if formula_failure:
        assumption = f"{assumption}|{formula_failure}"
    return _linear_contract(
        line_id=line_id,
        request_item_ids=request_item_ids,
        mode=mode,
        formula_id=formula_id,
        dimension_ids=(),
        value=rounded,
        source="PACK_DEFAULT",
        assumption_code=assumption,
    )


def _quantity_formula(
    formula_id: str,
    items: tuple[Any, ...],
    line_unit: str,
) -> _FormulaValue | None:
    item_data = _items(items)
    dimensions = _dimensions(items)
    if formula_id == "QTY_SUM_EXPLICIT":
        values = [
            Decimal(str(item["quantity"]))
            for item in item_data
            if item.get("quantity") is not None
            and normalize_unit(item.get("unit")) == line_unit
        ]
        if values:
            return _FormulaValue(sum(values, Decimal("0")), ())
    if formula_id == "QTY_COUNT_ITEMS":
        if item_data:
            return _FormulaValue(Decimal(len(item_data)), ())
    if formula_id == "QTY_RECTANGLE_AREA":
        rectangle = _perimeter_rect(dimensions)
        # Re-read paired L/W: perimeter helper proves they belong to one ITEM,
        # but area must multiply rather than reuse its result.
        if rectangle:
            selected = {
                dimension.dimension_id: dimension
                for dimension in dimensions
                if dimension.dimension_id in rectangle.dimension_ids
            }
            length, width = (
                selected[dimension_id] for dimension_id in rectangle.dimension_ids
            )
            return _FormulaValue(
                length.metres() * width.metres(), rectangle.dimension_ids
            )
    if formula_id == "QTY_RECTANGULAR_VOLUME":
        by_item: dict[str, dict[str, list[_Dimension]]] = {}
        for dimension in dimensions:
            by_item.setdefault(dimension.item_id, {}).setdefault(
                dimension.kind, []
            ).append(dimension)
        values: list[_FormulaValue] = []
        for item_id in sorted(by_item):
            kinds = by_item[item_id]
            if all(len(kinds.get(kind, ())) == 1 for kind in ("LENGTH", "WIDTH", "HEIGHT")):
                selected = tuple(kinds[kind][0] for kind in ("LENGTH", "WIDTH", "HEIGHT"))
                values.append(
                    _FormulaValue(
                        value=selected[0].metres()
                        * selected[1].metres()
                        * selected[2].metres(),
                        dimension_ids=tuple(value.dimension_id for value in selected),
                    )
                )
        return values[0] if len(values) == 1 else None
    return None


def resolve_quantity(
    line: Any,
    covered_items: Iterable[Any],
    project: Any | None = None,
    *,
    consumed_dimension_ids: MutableSet[str] | None = None,
) -> QuantityResolution:
    """Resolve explicit -> official formula -> project context -> pack default."""

    line_data = _mapping(line)
    item_values = tuple(covered_items)
    line_id = str(line_data.get("line_id") or "")
    unit = str(normalize_unit(str(line_data.get("unit") or "")) or "")
    if unit == "M":
        unit = "ML"
    if unit == "ML":
        linear = resolve_linear_measurement(
            line,
            item_values,
            project,
            consumed_dimension_ids=consumed_dimension_ids,
        )
        rule_id, binding_scope, share_group_id = _binding_meta(line_data)
        return QuantityResolution(
            value=float(_field(linear, "value_ml")),
            unit="ML",
            source=str(_field(linear, "source")),
            assumption_code=_field(linear, "assumption_code"),
            formula_id=_field(linear, "formula_id"),
            input_dimension_ids=tuple(_field(linear, "input_dimension_ids", ())),
            linear_measurement=linear,
            quantity_rule_id=rule_id,
            binding_scope=binding_scope,
            share_group_id=share_group_id,
            bound_fact_ids=tuple(_field(linear, "input_dimension_ids", ())),
        )

    rule_id, binding_scope, share_group_id = _binding_meta(line_data)

    explicit = _explicit_quantity(item_values, unit)
    if explicit is not None:
        value, fact_ids = explicit
        return QuantityResolution(
            value=float(value),
            unit=unit,
            source="EXPLICIT",
            quantity_rule_id=rule_id,
            binding_scope=binding_scope,
            share_group_id=share_group_id,
            bound_fact_ids=fact_ids,
        )

    quantity_rule = str(line_data.get("quantity_rule") or "")
    formula_id = (
        quantity_rule.partition(":")[2]
        if quantity_rule.startswith("FORMULA:")
        else quantity_rule
    )
    if formula_id in WHITELISTED_QUANTITY_FORMULA_IDS:
        calculated = _quantity_formula(formula_id, item_values, unit)
        if calculated is not None and calculated.value > 0:
            consumed = set(consumed_dimension_ids or ())
            if not consumed.intersection(calculated.dimension_ids):
                if consumed_dimension_ids is not None:
                    consumed_dimension_ids.update(calculated.dimension_ids)
                return QuantityResolution(
                    value=float(calculated.value),
                    unit=unit,
                    source="FORMULA",
                    formula_id=formula_id,
                    input_dimension_ids=calculated.dimension_ids,
                    quantity_rule_id=rule_id or formula_id,
                    binding_scope=binding_scope,
                    share_group_id=share_group_id,
                    bound_fact_ids=calculated.dimension_ids,
                )

    context_value = _project_value(line, project, linear=False)
    if context_value is not None:
        return QuantityResolution(
            value=float(context_value),
            unit=unit,
            source="PROJECT_CONTEXT",
            assumption_code=f"QTY_PROJECT_CONTEXT:{line_id}",
            quantity_rule_id=rule_id,
            binding_scope="PROJECT",
            share_group_id=share_group_id,
        )

    default_value = Decimal(str(line_data.get("default_quantity") or "0"))
    if default_value <= 0:
        raise ValueError(f"Official default quantity is missing for line {line_id}")
    return QuantityResolution(
        value=float(default_value),
        unit=unit,
        source="PACK_DEFAULT",
        assumption_code=f"PACK_DEFAULT:{line_id}",
        quantity_rule_id=rule_id,
        binding_scope=binding_scope,
        share_group_id=share_group_id,
    )


__all__ = [
    "LINEAR_FORMULA_MODES",
    "LINEAR_FORMULA_VERSION",
    "QuantityResolution",
    "WHITELISTED_LINEAR_FORMULA_IDS",
    "WHITELISTED_QUANTITY_FORMULA_IDS",
    "resolve_linear_measurement",
    "resolve_quantity",
]
