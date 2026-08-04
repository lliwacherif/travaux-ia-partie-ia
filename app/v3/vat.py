"""Versioned French VAT rule resolution with an explicit 20% fallback."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping


DEFAULT_VAT_RULE_ID = "FR_STANDARD_20"
DEFAULT_VAT_RULE_VERSION = 1


@dataclass(frozen=True, slots=True)
class VatRule:
    vat_rule_id: str
    version: int
    vat_rate: Decimal
    countries: tuple[str, ...] = ("FR",)
    customer_types: tuple[str, ...] = ()
    building_uses: tuple[str, ...] = ()
    minimum_building_age_years: Decimal | None = None
    maximum_building_age_years: Decimal | None = None
    work_natures: tuple[str, ...] = ()
    territories: tuple[str, ...] = ()
    effective_from: date | None = None
    effective_to: date | None = None
    priority: int = 0
    active: bool = True


@dataclass(frozen=True, slots=True)
class VatResolution:
    vat_rule_id: str
    vat_rule_version: int
    vat_rate: float
    assumption_code: str | None = None


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
    raise TypeError(f"Expected mapping-like VAT data, got {type(value).__name__}")


def _codes(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        return (value.upper(),)
    return tuple(sorted({str(entry).upper() for entry in value}))


def _rule(value: Any) -> VatRule:
    if isinstance(value, VatRule):
        return value
    raw = _mapping(value)
    return VatRule(
        vat_rule_id=str(raw.get("vat_rule_id") or raw.get("rule_id") or ""),
        version=int(raw.get("version") or raw.get("vat_rule_version") or 0),
        vat_rate=Decimal(str(raw.get("vat_rate", raw.get("rate", 0)))),
        countries=_codes(raw.get("countries") or raw.get("country") or ("FR",)),
        customer_types=_codes(raw.get("customer_types") or raw.get("customer_type")),
        building_uses=_codes(raw.get("building_uses") or raw.get("building_use")),
        minimum_building_age_years=(
            Decimal(str(raw["minimum_building_age_years"]))
            if raw.get("minimum_building_age_years") is not None
            else (
                Decimal(str(raw["min_building_age_years"]))
                if raw.get("min_building_age_years") is not None
                else None
            )
        ),
        maximum_building_age_years=(
            Decimal(str(raw["maximum_building_age_years"]))
            if raw.get("maximum_building_age_years") is not None
            else (
                Decimal(str(raw["max_building_age_years"]))
                if raw.get("max_building_age_years") is not None
                else None
            )
        ),
        work_natures=_codes(raw.get("work_natures") or raw.get("work_nature")),
        territories=_codes(raw.get("territories") or raw.get("territory")),
        effective_from=raw.get("effective_from"),
        effective_to=raw.get("effective_to"),
        priority=int(raw.get("priority") or 0),
        active=bool(raw.get("active", True)),
    )


def _applies(
    rule: VatRule,
    project: Mapping[str, Any],
    *,
    work_nature: str | None,
    pricing_date: date | None,
) -> bool:
    if not rule.active or rule.version < 1:
        return False
    country = str(project.get("country") or "FR").upper()
    if rule.countries and country not in rule.countries:
        return False
    customer_type = str(project.get("customer_type") or "").upper()
    if rule.customer_types and customer_type not in rule.customer_types:
        return False
    building_use = str(project.get("building_use") or "").upper()
    if rule.building_uses and building_use not in rule.building_uses:
        return False
    age_value = project.get("building_age_years")
    if rule.minimum_building_age_years is not None:
        if age_value is None or Decimal(str(age_value)) < rule.minimum_building_age_years:
            return False
    if rule.maximum_building_age_years is not None:
        if age_value is None or Decimal(str(age_value)) > rule.maximum_building_age_years:
            return False
    if rule.work_natures and str(work_nature or "").upper() not in rule.work_natures:
        return False
    territory = str(
        project.get("territory_code") or project.get("location") or ""
    ).upper()
    if rule.territories and territory not in rule.territories:
        return False
    if pricing_date is not None:
        if rule.effective_from and pricing_date < rule.effective_from:
            return False
        if rule.effective_to and pricing_date > rule.effective_to:
            return False
    # V3.2 — rate may be any official published percentage in [0, 100].
    if rule.vat_rate < 0 or rule.vat_rate > 100:
        return False
    return True


def _specificity(rule: VatRule) -> int:
    return sum(
        (
            bool(rule.customer_types),
            bool(rule.building_uses),
            rule.minimum_building_age_years is not None,
            rule.maximum_building_age_years is not None,
            bool(rule.work_natures),
            bool(rule.territories),
        )
    )


def _fallback(
    rules: Iterable[VatRule],
    assumption_code: str,
) -> VatResolution:
    official = [
        rule
        for rule in rules
        if rule.active
        and rule.vat_rule_id == DEFAULT_VAT_RULE_ID
        and rule.vat_rate == Decimal("20")
        and rule.version >= 1
    ]
    selected = (
        sorted(official, key=lambda rule: (-rule.version, rule.vat_rule_id))[0]
        if official
        else VatRule(
            vat_rule_id=DEFAULT_VAT_RULE_ID,
            version=DEFAULT_VAT_RULE_VERSION,
            vat_rate=Decimal("20"),
        )
    )
    return VatResolution(
        vat_rule_id=selected.vat_rule_id,
        vat_rule_version=selected.version,
        vat_rate=float(selected.vat_rate),
        assumption_code=assumption_code,
    )


def resolve_vat(
    project: Any,
    vat_rules: Iterable[Any] = (),
    *,
    work_nature: str | None = None,
    preferred_rule_id: str | None = None,
    pricing_date: date | None = None,
) -> VatResolution:
    """Select a matching official rule or trace ``FR_STANDARD_20`` fallback."""

    project_data = _mapping(project)
    rules = tuple(_rule(value) for value in vat_rules)
    fiscally_incomplete = any(
        project_data.get(field) in (None, "")
        for field in ("customer_type", "building_use", "building_age_years")
    )
    if fiscally_incomplete:
        return _fallback(rules, "VAT_CONTEXT_INCOMPLETE:FR_STANDARD_20")

    applicable = [
        rule
        for rule in rules
        if _applies(
            rule,
            project_data,
            work_nature=work_nature,
            pricing_date=pricing_date,
        )
    ]
    if preferred_rule_id:
        preferred = [
            rule for rule in applicable if rule.vat_rule_id == preferred_rule_id
        ]
        if preferred:
            applicable = preferred
    if not applicable:
        return _fallback(rules, "VAT_NO_MATCH:FR_STANDARD_20")

    selected = sorted(
        applicable,
        key=lambda rule: (
            -rule.priority,
            -_specificity(rule),
            -rule.version,
            rule.vat_rule_id,
        ),
    )[0]
    return VatResolution(
        vat_rule_id=selected.vat_rule_id,
        vat_rule_version=selected.version,
        vat_rate=float(selected.vat_rate),
        assumption_code=None,
    )


__all__ = [
    "DEFAULT_VAT_RULE_ID",
    "DEFAULT_VAT_RULE_VERSION",
    "VatResolution",
    "VatRule",
    "resolve_vat",
]
