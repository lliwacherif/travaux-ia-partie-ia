"""Independent V3.2 source-to-quote validator and repair instructions.

V3.2 — shared-profile geometry, versioned price/VAT, stage 0B evidence.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping

from .context import normalize_text, normalize_unit
from .contracts import (
    RepairAction,
    ValidationIssue,
    ValidationMetrics,
    ValidationReport,
)
from .coverage import match_item_to_line
from .measurements import (
    LINEAR_FORMULA_MODES,
    WHITELISTED_LINEAR_FORMULA_IDS,
)
from .pricing import calculate_line_amount, calculate_totals
from .ssot import FORBIDDEN_LABELS, REQUIRED_STAGES, Flow, expected_geometry


class DisplayGateError(RuntimeError):
    """Raised when no independently validated quote may be displayed."""

    def __init__(self, errors: list[str], allowed_repairs: list[str]) -> None:
        self.errors = errors
        self.allowed_repairs = allowed_repairs
        super().__init__("; ".join(errors) or "V3 display gate rejected the quote")


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
    raise TypeError(f"Expected mapping-like validation data, got {type(value).__name__}")


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _index(values: Mapping[str, Any] | Iterable[Any], field_name: str) -> dict[str, Any]:
    if isinstance(values, Mapping):
        return {str(key): value for key, value in values.items()}
    result: dict[str, Any] = {}
    for value in values:
        raw = _mapping(value)
        identifier = str(raw.get(field_name) or raw.get("id") or "")
        if identifier:
            result[identifier] = value
    return result


def _quote_lines(quote: Any) -> tuple[Any, ...]:
    raw = _mapping(quote)
    lines = list(raw.get("setup_lines") or ())
    for block in raw.get("trade_blocks") or ():
        lines.extend(_mapping(block).get("lines") or ())
    lines.extend(raw.get("finish_lines") or ())
    return tuple(lines)


def _request_items(matrix: Any) -> dict[str, Any]:
    return {
        str(_mapping(item).get("request_item_id") or ""): item
        for item in _mapping(matrix).get("items") or ()
    }


def _line_id(line: Any) -> str:
    return str(_mapping(line).get("line_id") or "")


def _phase(line: Any) -> str:
    return str(_value(_mapping(line).get("phase")) or "").upper()


def _unit(value: Any) -> str | None:
    normalized = normalize_unit(None if value is None else str(_value(value)))
    return "ML" if normalized == "M" else normalized


def _forbidden(designation: str) -> bool:
    normalized = normalize_text(designation).matching
    return any(
        normalize_text(label).matching in normalized for label in FORBIDDEN_LABELS
    )


def _issue(
    issues: list[ValidationIssue],
    seen: set[tuple[str, str | None, str | None]],
    code: str,
    action: RepairAction,
    *,
    item_id: str | None = None,
    line_id: str | None = None,
) -> None:
    key = (code, item_id, line_id)
    if key in seen:
        return
    seen.add(key)
    issues.append(
        ValidationIssue(
            code=code,
            request_item_id=item_id,
            line_id=line_id,
            repair_action=action,
        )
    )


def _dimension_ownership(items: Mapping[str, Any]) -> dict[str, tuple[str, Any]]:
    result: dict[str, tuple[str, Any]] = {}
    for item_id, item in items.items():
        for dimension in _mapping(item).get("dimensions") or ():
            dimension_id = str(_mapping(dimension).get("dimension_id") or "")
            if dimension_id:
                result[dimension_id] = (item_id, dimension)
    return result


def _registry_versions(
    values: Iterable[Any],
    id_field: str,
    version_field: str = "version",
) -> set[tuple[str, int]]:
    result: set[tuple[str, int]] = set()
    for value in values:
        raw = _mapping(value)
        identifier = str(raw.get(id_field) or "")
        version = int(raw.get(version_field) or 0)
        if identifier and version:
            result.add((identifier, version))
    return result


def validate_source_to_quote(
    quote: Any,
    matrix: Any,
    *,
    catalog_lines: Mapping[str, Any] | Iterable[Any],
    catalog_packs: Mapping[str, Any] | Iterable[Any],
    price_records: Iterable[Any] = (),
    vat_rules: Iterable[Any] = (),
    technical_dependencies: Mapping[str, Any] | Iterable[Any] = (),
    required_stages: Iterable[str] = REQUIRED_STAGES,
    enforce_stage_evidence: bool = True,
    enforce_display_gate: bool = True,
) -> ValidationReport:
    """Validate geometry, semantics, catalog trace, calculations, and stages."""

    quote_data = _mapping(quote)
    matrix_data = _mapping(matrix)
    lines = _quote_lines(quote)
    items = _request_items(matrix)
    dimensions = _dimension_ownership(items)
    line_catalog = _index(catalog_lines, "line_id")
    pack_catalog = _index(catalog_packs, "pack_id")
    dependency_catalog = _index(technical_dependencies, "dependency_id")
    price_versions = _registry_versions(price_records, "price_id")
    vat_versions = _registry_versions(vat_rules, "vat_rule_id")

    issues: list[ValidationIssue] = []
    seen_issues: set[tuple[str, str | None, str | None]] = set()
    warnings: set[str] = set()

    flow = Flow(str(_value(quote_data.get("flow"))))
    geometry = expected_geometry(flow)
    setup_lines = tuple(quote_data.get("setup_lines") or ())
    finish_lines = tuple(quote_data.get("finish_lines") or ())
    blocks = tuple(quote_data.get("trade_blocks") or ())
    if (
        len(setup_lines) != geometry.setup
        or len(finish_lines) != geometry.finish
        or any(
            len(_mapping(block).get("lines") or ()) != geometry.core_per_trade
            for block in blocks
        )
        or (flow is Flow.DEPANNAGE and len(blocks) != 1)
    ):
        _issue(
            issues,
            seen_issues,
            "QUOTE_GEOMETRY_INVALID",
            RepairAction.USE_OFFICIAL_FALLBACK,
        )
    if (
        any(_phase(line) != "SETUP" for line in setup_lines)
        or any(_phase(line) != "FINISH" for line in finish_lines)
        or any(
            _phase(line) != "CORE"
            for block in blocks
            for line in _mapping(block).get("lines") or ()
        )
    ):
        _issue(
            issues,
            seen_issues,
            "QUOTE_PHASE_GEOMETRY_INVALID",
            RepairAction.USE_OFFICIAL_FALLBACK,
        )

    intervention_ids: set[str] = set()
    definitive_pack_ids: set[str] = set()
    for block in blocks:
        raw_block = _mapping(block)
        intervention_id = str(raw_block.get("intervention_id") or "")
        pack_id = str(raw_block.get("pack_id") or "")
        if intervention_id in intervention_ids:
            _issue(
                issues,
                seen_issues,
                "MULTIPLE_PACKS_PER_INTERVENTION",
                RepairAction.RESELECT_PACK,
            )
        intervention_ids.add(intervention_id)
        definitive_pack_ids.add(pack_id)
        if pack_id not in pack_catalog:
            _issue(
                issues,
                seen_issues,
                "PACK_ID_NOT_IN_LIBRARY",
                RepairAction.USE_OFFICIAL_FALLBACK,
            )

    all_item_ids = set(items)
    required_ids = {
        item_id
        for item_id, item in items.items()
        if str(_value(_mapping(item).get("status"))).upper() == "REQUIRED"
    }
    excluded_ids = {
        item_id
        for item_id, item in items.items()
        if str(_value(_mapping(item).get("status"))).upper() == "EXCLUDED"
    }
    covered_required: set[str] = set()
    covered_ids: set[str] = set()
    unjustified_count = 0
    explicit_checks = 0
    explicit_correct = 0
    linear_checks = 0
    linear_correct = 0
    consumed_dimensions: dict[str, str] = {}

    for line in lines:
        raw_line = _mapping(line)
        line_id = _line_id(line)
        catalog_line = line_catalog.get(line_id)
        catalog_data = _mapping(catalog_line) if catalog_line is not None else {}
        covered = tuple(
            str(item_id)
            for item_id in raw_line.get("covered_request_item_ids") or ()
        )
        dependency_ids = tuple(
            str(dependency_id)
            for dependency_id in raw_line.get("technical_dependency_ids") or ()
        )
        covered_ids.update(covered)
        covered_required.update(set(covered) & required_ids)

        if catalog_line is None:
            _issue(
                issues,
                seen_issues,
                "LINE_ID_NOT_IN_LIBRARY",
                RepairAction.REPLACE_OFFICIAL_LINE,
                line_id=line_id,
            )
        elif str(catalog_data.get("designation") or "") != str(
            raw_line.get("designation") or ""
        ):
            _issue(
                issues,
                seen_issues,
                "CATALOG_DESIGNATION_MODIFIED",
                RepairAction.REPLACE_OFFICIAL_LINE,
                line_id=line_id,
            )
        if _forbidden(str(raw_line.get("designation") or "")):
            _issue(
                issues,
                seen_issues,
                "FORBIDDEN_OPAQUE_LABEL",
                RepairAction.REPLACE_OFFICIAL_LINE,
                line_id=line_id,
            )
        if not covered and not dependency_ids:
            unjustified_count += 1
            _issue(
                issues,
                seen_issues,
                "LINE_WITHOUT_JUSTIFICATION",
                RepairAction.REPLACE_OFFICIAL_LINE,
                line_id=line_id,
            )

        for item_id in covered:
            if item_id not in all_item_ids:
                _issue(
                    issues,
                    seen_issues,
                    "UNKNOWN_COVERED_REQUEST_ITEM",
                    RepairAction.RESELECT_PACK,
                    line_id=line_id,
                )
                continue
            if item_id in excluded_ids:
                _issue(
                    issues,
                    seen_issues,
                    "EXCLUDED_ITEM_BILLED",
                    RepairAction.RESELECT_PACK,
                    item_id=item_id,
                    line_id=line_id,
                )
            if catalog_line is not None and not match_item_to_line(
                items[item_id], catalog_line
            ).matched:
                _issue(
                    issues,
                    seen_issues,
                    "COVERAGE_SEMANTIC_MISMATCH",
                    RepairAction.RESELECT_PACK,
                    item_id=item_id,
                    line_id=line_id,
                )

        for excluded_id in excluded_ids:
            if catalog_line is not None and match_item_to_line(
                items[excluded_id], catalog_line
            ).matched:
                _issue(
                    issues,
                    seen_issues,
                    "EXCLUSION_CAPABILITY_VIOLATION",
                    RepairAction.RESELECT_PACK,
                    item_id=excluded_id,
                    line_id=line_id,
                )

        for dependency_id in dependency_ids:
            dependency = dependency_catalog.get(dependency_id)
            if dependency is None:
                _issue(
                    issues,
                    seen_issues,
                    "TECHNICAL_DEPENDENCY_NOT_REGISTERED",
                    RepairAction.REPLACE_OFFICIAL_LINE,
                    line_id=line_id,
                )
                continue
            dependency_data = _mapping(dependency)
            if not bool(dependency_data.get("active", True)):
                _issue(
                    issues,
                    seen_issues,
                    "TECHNICAL_DEPENDENCY_INACTIVE",
                    RepairAction.REPLACE_OFFICIAL_LINE,
                    line_id=line_id,
                )

        line_unit = _unit(raw_line.get("unit"))
        source = str(_value(raw_line.get("quantity_source")) or "")
        matching_explicit_items = [
            items[item_id]
            for item_id in covered
            if item_id in items
            and _mapping(items[item_id]).get("quantity") is not None
            and _unit(_mapping(items[item_id]).get("unit")) == line_unit
        ]
        if len(matching_explicit_items) == 1:
            explicit_checks += 1
            expected_quantity = Decimal(
                str(_mapping(matching_explicit_items[0])["quantity"])
            )
            actual_quantity = Decimal(str(raw_line.get("quantity")))
            if source == "EXPLICIT" and actual_quantity == expected_quantity:
                explicit_correct += 1
            else:
                item_id = str(
                    _mapping(matching_explicit_items[0]).get("request_item_id")
                )
                _issue(
                    issues,
                    seen_issues,
                    "EXPLICIT_QUANTITY_NOT_PRESERVED",
                    RepairAction.RECOMPUTE_QUANTITY,
                    item_id=item_id,
                    line_id=line_id,
                )

        try:
            expected_ht = calculate_line_amount(
                raw_line.get("quantity"),
                int(raw_line.get("unit_price_cents")),
            ).total_ht_cents
        except (TypeError, ValueError, ArithmeticError):
            expected_ht = -1
        if expected_ht != int(raw_line.get("total_ht_cents") or 0):
            _issue(
                issues,
                seen_issues,
                "LINE_HT_CENTS_INVALID",
                RepairAction.RECOMPUTE_QUANTITY,
                line_id=line_id,
            )

        price_key = (
            str(raw_line.get("price_id") or ""),
            int(raw_line.get("price_version") or 0),
        )
        if price_versions and price_key not in price_versions:
            _issue(
                issues,
                seen_issues,
                "PRICE_VERSION_NOT_IN_LIBRARY",
                RepairAction.USE_OFFICIAL_FALLBACK,
                line_id=line_id,
            )
        vat_key = (
            str(raw_line.get("vat_rule_id") or ""),
            int(raw_line.get("vat_rule_version") or 0),
        )
        if vat_versions and vat_key not in vat_versions:
            _issue(
                issues,
                seen_issues,
                "VAT_RULE_VERSION_NOT_IN_LIBRARY",
                RepairAction.RECOMPUTE_VAT,
                line_id=line_id,
            )

        if line_unit == "ML":
            linear_checks += 1
            measurement = raw_line.get("linear_measurement")
            if measurement is None:
                _issue(
                    issues,
                    seen_issues,
                    "ML_TRACE_MISSING",
                    RepairAction.RECOMPUTE_QUANTITY,
                    line_id=line_id,
                )
                continue
            resolution = _mapping(measurement)
            mode = str(_value(resolution.get("mode")) or "")
            formula_id = resolution.get("formula_id")
            resolution_source = str(_value(resolution.get("source")) or "")
            input_ids = tuple(
                str(dimension_id)
                for dimension_id in resolution.get("input_dimension_ids") or ()
            )
            ml_valid = True
            if resolution_source == "FORMULA":
                if (
                    formula_id not in WHITELISTED_LINEAR_FORMULA_IDS
                    or formula_id not in LINEAR_FORMULA_MODES.get(mode, ())
                ):
                    ml_valid = False
                if catalog_data and (
                    str(catalog_data.get("linear_formula_id") or "") != formula_id
                    or str(
                        _value(catalog_data.get("linear_measurement_mode")) or ""
                    )
                    != mode
                ):
                    ml_valid = False
            if len(input_ids) != len(set(input_ids)):
                ml_valid = False
            for dimension_id in input_ids:
                owner = dimensions.get(dimension_id)
                if owner is None or owner[0] not in covered:
                    ml_valid = False
                previous_line = consumed_dimensions.get(dimension_id)
                if previous_line is not None and previous_line != line_id:
                    ml_valid = False
                    _issue(
                        issues,
                        seen_issues,
                        "DIMENSION_CONSUMED_BY_MULTIPLE_LINES",
                        RepairAction.RECOMPUTE_QUANTITY,
                        line_id=line_id,
                    )
                consumed_dimensions[dimension_id] = line_id
            if formula_id == "ML_PERIMETER_RECT":
                kinds = {
                    str(_value(_mapping(dimensions[dimension_id][1]).get("kind")))
                    for dimension_id in input_ids
                    if dimension_id in dimensions
                }
                if not {"LENGTH", "WIDTH"}.issubset(kinds):
                    ml_valid = False
            if formula_id == "ML_SURFACE_TO_LINEAR":
                kinds = {
                    str(_value(_mapping(dimensions[dimension_id][1]).get("kind")))
                    for dimension_id in input_ids
                    if dimension_id in dimensions
                }
                if not {"SURFACE", "COVERAGE_WIDTH"}.issubset(kinds):
                    ml_valid = False
            if Decimal(str(resolution.get("value_ml") or 0)) != Decimal(
                str(raw_line.get("quantity") or 0)
            ):
                ml_valid = False
            if ml_valid:
                linear_correct += 1
            else:
                _issue(
                    issues,
                    seen_issues,
                    "ML_RESOLUTION_INVALID",
                    RepairAction.RECOMPUTE_QUANTITY,
                    line_id=line_id,
                )
        elif raw_line.get("linear_measurement") is not None:
            _issue(
                issues,
                seen_issues,
                "NON_ML_LINE_HAS_LINEAR_TRACE",
                RepairAction.RECOMPUTE_QUANTITY,
                line_id=line_id,
            )

    for item_id in sorted(required_ids - covered_required):
        _issue(
            issues,
            seen_issues,
            "REQUIRED_ITEM_NOT_COVERED",
            RepairAction.RESELECT_PACK,
            item_id=item_id,
        )

    recomputed_totals = calculate_totals(lines)
    totals = _mapping(quote_data.get("totals"))
    if (
        int(totals.get("ht_cents") or 0) != recomputed_totals.ht_cents
        or int(totals.get("vat_cents") or 0) != recomputed_totals.vat_cents
        or int(totals.get("ttc_cents") or 0) != recomputed_totals.ttc_cents
    ):
        _issue(
            issues,
            seen_issues,
            "QUOTE_TOTALS_CENTS_INVALID",
            RepairAction.RECOMPUTE_VAT,
        )

    trace = _mapping(quote_data.get("trace"))
    executions = tuple(trace.get("stage_executions") or ())
    if enforce_stage_evidence:
        completed_stages = {
            str(_value(_mapping(execution).get("stage")) or "")
            for execution in executions
        }
        required = tuple(str(_value(stage)) for stage in required_stages)
        stage_completion_rate = (
            len(set(required) & completed_stages) / len(set(required))
            if required
            else 1.0
        )
        if stage_completion_rate < 1:
            _issue(
                issues,
                seen_issues,
                "MANDATORY_STAGE_EVIDENCE_MISSING",
                RepairAction.USE_OFFICIAL_FALLBACK,
            )
        for execution in executions:
            raw_execution = _mapping(execution)
            if (
                str(_value(raw_execution.get("status")))
                == "DEGRADED_AUTHORIZED"
                and not raw_execution.get("fallback_reason")
            ):
                _issue(
                    issues,
                    seen_issues,
                    "SILENT_FALLBACK_FORBIDDEN",
                    RepairAction.USE_OFFICIAL_FALLBACK,
                )
    else:
        stage_completion_rate = 1.0
    if enforce_display_gate and not bool(trace.get("display_gate_passed")):
        _issue(
            issues,
            seen_issues,
            "DISPLAY_GATE_NOT_PASSED",
            RepairAction.USE_OFFICIAL_FALLBACK,
        )

    assumption_codes = {
        str(code) for code in trace.get("assumption_codes") or () if str(code)
    }
    for line in lines:
        measurement = _mapping(_mapping(line).get("linear_measurement"))
        code = measurement.get("assumption_code")
        if code:
            assumption_codes.add(str(code))
    if assumption_codes:
        warnings.update(f"ASSUMPTION:{code}" for code in assumption_codes)

    line_count = len(lines)
    metrics = ValidationMetrics(
        required_coverage=(
            len(required_ids & covered_ids) / len(required_ids)
            if required_ids
            else 1.0
        ),
        explicit_quantity_accuracy=(
            explicit_correct / explicit_checks if explicit_checks else 1.0
        ),
        linear_measurement_accuracy=(
            linear_correct / linear_checks if linear_checks else 1.0
        ),
        unjustified_line_rate=(
            unjustified_count / line_count if line_count else 0.0
        ),
        stage_completion_rate=stage_completion_rate,
        assumptions_count=len(assumption_codes),
    )
    return ValidationReport(
        valid=not issues,
        critical=issues,
        warnings=sorted(warnings),
        metrics=metrics,
    )


def repair_actions(report: ValidationReport) -> tuple[RepairAction, ...]:
    """Return stable, de-duplicated actions requested by a validation report."""

    return tuple(
        dict.fromkeys(issue.repair_action for issue in report.critical)
    )


# The fallback uses the same independent checks; it is not a relaxed validator.
validate_fallback_integrity = validate_source_to_quote


__all__ = [
    "DisplayGateError",
    "repair_actions",
    "validate_fallback_integrity",
    "validate_source_to_quote",
]
