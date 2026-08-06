"""Versioned French VAT rule resolution.

Correctifs ciblés à intégrer dans la V3.2 §7 —
copie exacte de vat_rule_id + version + taux du pack.
Aucune substitution automatique FR_STANDARD_20.
Contexte fiscal incomplet → conserve la TVA ligne + review_required.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping

from app.v3.ssot import COPY_PACK_VAT_EXACTLY, FORBID_VAT_CONTEXT_SUBSTITUTION


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
    review_required: bool = False


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


def copy_pack_vat(line: Any, *, project: Any | None = None) -> VatResolution:
    """Correctifs ciblés à intégrer dans la V3.2 §7 — copie exacte pack → ligne."""

    if COPY_PACK_VAT_EXACTLY is not True or FORBID_VAT_CONTEXT_SUBSTITUTION is not True:
        raise RuntimeError("VAT correctifs flags must remain True")

    line_data = _mapping(line)
    vat_rule_id = str(line_data.get("vat_rule_id") or "").strip()
    vat_rule_version = int(line_data.get("vat_rule_version") or 0)
    raw_rate = line_data.get("vat_rate")
    review_required = False
    assumption: str | None = None

    project_data = _mapping(project)
    fiscally_incomplete = any(
        project_data.get(field) in (None, "")
        for field in ("customer_type", "building_use", "building_age_years")
    )
    if fiscally_incomplete:
        # Correctifs: keep pack VAT; flag review — never substitute FR_STANDARD_20.
        review_required = True
        assumption = "VAT_CONTEXT_INCOMPLETE:REVIEW_REQUIRED"

    if not vat_rule_id or vat_rule_version < 1 or raw_rate is None:
        review_required = True
        assumption = (
            f"{assumption}|VAT_PACK_INCOMPLETE:REVIEW_REQUIRED"
            if assumption
            else "VAT_PACK_INCOMPLETE:REVIEW_REQUIRED"
        )
        return VatResolution(
            vat_rule_id=vat_rule_id or DEFAULT_VAT_RULE_ID,
            vat_rule_version=max(vat_rule_version, 1),
            vat_rate=float(raw_rate if raw_rate is not None else 20),
            assumption_code=assumption,
            review_required=True,
        )

    return VatResolution(
        vat_rule_id=vat_rule_id,
        vat_rule_version=vat_rule_version,
        vat_rate=float(raw_rate),
        assumption_code=assumption,
        review_required=review_required,
    )


def resolve_vat(
    project: Any,
    vat_rules: Iterable[Any] = (),
    *,
    work_nature: str | None = None,
    preferred_rule_id: str | None = None,
    pricing_date: date | None = None,
    pack_line: Any | None = None,
) -> VatResolution:
    """Prefer pack-line VAT copy; never auto-substitute FR_STANDARD_20."""

    del work_nature, preferred_rule_id, pricing_date, vat_rules
    if pack_line is not None:
        return copy_pack_vat(pack_line, project=project)
    # Legacy call sites without pack_line: still forbid silent 20% substitution
    # when fiscal context is incomplete — require review instead.
    project_data = _mapping(project)
    fiscally_incomplete = any(
        project_data.get(field) in (None, "")
        for field in ("customer_type", "building_use", "building_age_years")
    )
    return VatResolution(
        vat_rule_id=DEFAULT_VAT_RULE_ID,
        vat_rule_version=DEFAULT_VAT_RULE_VERSION,
        vat_rate=20.0,
        assumption_code=(
            "VAT_CONTEXT_INCOMPLETE:REVIEW_REQUIRED"
            if fiscally_incomplete
            else "VAT_NO_PACK_LINE:REVIEW_REQUIRED"
        ),
        review_required=True,
    )


__all__ = [
    "DEFAULT_VAT_RULE_ID",
    "DEFAULT_VAT_RULE_VERSION",
    "VatResolution",
    "VatRule",
    "copy_pack_vat",
    "resolve_vat",
]
