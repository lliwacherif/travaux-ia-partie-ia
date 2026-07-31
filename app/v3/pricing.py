"""Versioned catalog-price resolution and cent-exact monetary arithmetic."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class PriceRecord:
    price_id: str
    version: int
    unit_price_cents: int
    effective_from: date | None = None
    effective_to: date | None = None
    active: bool = True


@dataclass(frozen=True, slots=True)
class PriceResolution:
    price_id: str
    price_version: int
    unit_price_cents: int


@dataclass(frozen=True, slots=True)
class PricedAmount:
    quantity: float
    unit_price_cents: int
    total_ht_cents: int


@dataclass(frozen=True, slots=True)
class MoneyTotals:
    ht_cents: int
    vat_cents: int
    ttc_cents: int


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
    raise TypeError(f"Expected mapping-like price data, got {type(value).__name__}")


def _record(value: Any) -> PriceRecord:
    if isinstance(value, PriceRecord):
        return value
    raw = _mapping(value)
    return PriceRecord(
        price_id=str(raw.get("price_id") or ""),
        version=int(raw.get("version") or raw.get("price_version") or 0),
        unit_price_cents=int(raw.get("unit_price_cents") or 0),
        effective_from=raw.get("effective_from"),
        effective_to=raw.get("effective_to"),
        active=bool(raw.get("active", True)),
    )


def resolve_price(
    line: Any,
    price_records: Iterable[Any] = (),
    *,
    pricing_date: date | None = None,
) -> PriceResolution:
    """Resolve an official price ID/version; never synthesize a price."""

    raw_line = _mapping(line)
    price_id = str(raw_line.get("price_id") or "")
    if not price_id:
        raise ValueError("A catalog line must have a price_id")

    records = []
    for value in price_records:
        record = _record(value)
        if record.price_id != price_id or not record.active:
            continue
        if pricing_date is not None:
            if record.effective_from and pricing_date < record.effective_from:
                continue
            if record.effective_to and pricing_date > record.effective_to:
                continue
        records.append(record)

    if records:
        selected = sorted(
            records,
            key=lambda record: (
                -record.version,
                -(record.effective_from or date.min).toordinal(),
                record.price_id,
            ),
        )[0]
        if selected.version < 1 or selected.unit_price_cents < 0:
            raise ValueError(f"Invalid official price record {selected.price_id}")
        return PriceResolution(
            price_id=selected.price_id,
            price_version=selected.version,
            unit_price_cents=selected.unit_price_cents,
        )

    embedded_version = int(
        raw_line.get("price_version") or raw_line.get("version") or 0
    )
    embedded_cents = raw_line.get("unit_price_cents")
    if embedded_version < 1 or embedded_cents is None or int(embedded_cents) < 0:
        raise ValueError(f"No valid versioned price exists for {price_id}")
    return PriceResolution(
        price_id=price_id,
        price_version=embedded_version,
        unit_price_cents=int(embedded_cents),
    )


def money_round(value: Decimal) -> int:
    """Round one monetary value to a cent using deterministic half-up."""

    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_line_amount(
    quantity: int | float | Decimal,
    unit_price_cents: int,
) -> PricedAmount:
    quantity_decimal = Decimal(str(quantity))
    if quantity_decimal <= 0:
        raise ValueError("quantity must be strictly positive")
    if unit_price_cents < 0:
        raise ValueError("unit_price_cents cannot be negative")
    return PricedAmount(
        quantity=float(quantity_decimal),
        unit_price_cents=int(unit_price_cents),
        total_ht_cents=money_round(
            quantity_decimal * Decimal(unit_price_cents)
        ),
    )


def calculate_vat_cents(total_ht_cents: int, vat_rate: Any) -> int:
    if total_ht_cents < 0:
        raise ValueError("total_ht_cents cannot be negative")
    rate = Decimal(str(vat_rate))
    if rate < 0:
        raise ValueError("vat_rate cannot be negative")
    return money_round(Decimal(total_ht_cents) * rate / Decimal("100"))


def calculate_totals(lines: Iterable[Any]) -> MoneyTotals:
    """Recompute HT, VAT, and TTC from final lines, all in integer cents."""

    ht_cents = 0
    vat_cents = 0
    for line in lines:
        raw = _mapping(line)
        line_ht = int(raw.get("total_ht_cents") or 0)
        if line_ht < 0:
            raise ValueError("Line HT cannot be negative")
        ht_cents += line_ht
        vat_cents += calculate_vat_cents(line_ht, raw.get("vat_rate") or 0)
    return MoneyTotals(
        ht_cents=ht_cents,
        vat_cents=vat_cents,
        ttc_cents=ht_cents + vat_cents,
    )


__all__ = [
    "MoneyTotals",
    "PriceRecord",
    "PriceResolution",
    "PricedAmount",
    "calculate_line_amount",
    "calculate_totals",
    "calculate_vat_cents",
    "money_round",
    "resolve_price",
]
