"""Compatibility presentation adapter for existing devis consumers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.v3.contracts import QuoteLine, QuoteResult


def _money(value: int) -> float:
    return float((Decimal(value) / Decimal(100)).quantize(Decimal("0.01")))


def _line_payload(line: QuoteLine, number: int) -> dict[str, Any]:
    vat_cents = int(
        (
            Decimal(line.total_ht_cents)
            * Decimal(str(line.vat_rate))
            / Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    return {
        "num": number,
        "description": line.designation,
        "qte": line.quantity,
        "unit": line.unit,
        "pu": _money(line.unit_price_cents),
        "tva": line.vat_rate,
        "ht": _money(line.total_ht_cents),
        "ttc": _money(line.total_ht_cents + vat_cents),
        "line_id": line.line_id,
        "pack_id": line.pack_id,
        # V3.2 — provenance for shared-profile vs pack CORE lines.
        "source_entity_type": getattr(
            line.source_entity_type, "value", line.source_entity_type
        ),
        "shared_profile_id": line.shared_profile_id,
        "price_id": line.price_id,
        "price_version": line.price_version,
        "vat_rule_id": line.vat_rule_id,
        "vat_rule_version": line.vat_rule_version,
        "covered_request_item_ids": line.covered_request_item_ids,
        "technical_dependency_ids": line.technical_dependency_ids,
        "quantity_source": line.quantity_source.value,
        "linear_measurement": (
            line.linear_measurement.model_dump(mode="json")
            if line.linear_measurement is not None
            else None
        ),
    }


def present_quote(
    quote: QuoteResult,
    *,
    created_at: datetime | None = None,
    validity_days: int = 30,
    duration_days: int = 30,
) -> dict[str, Any]:
    """Return the current blocs/lots/lignes shape plus V3 evidence."""

    now = created_at or datetime.now(timezone.utc)
    setup = [_line_payload(line, index) for index, line in enumerate(quote.setup_lines, 1)]
    finish = [
        _line_payload(line, index) for index, line in enumerate(quote.finish_lines, 1)
    ]
    blocs: list[dict[str, Any]] = [
        {
            "title": "Mise en place du chantier",
            "lots": [{"title": "Préparation", "ligne_ids": [line["line_id"] for line in setup], "lignes": setup}],
        }
    ]
    for block in quote.trade_blocks:
        lines = [_line_payload(line, index) for index, line in enumerate(block.lines, 1)]
        blocs.append(
            {
                "title": block.trade_code.replace("_", " ").title(),
                "lots": [
                    {
                        "title": "Travaux principaux",
                        "ligne_ids": [line["line_id"] for line in lines],
                        "lignes": lines,
                    }
                ],
            }
        )
    blocs.append(
        {
            "title": "Finitions et nettoyage",
            "lots": [{"title": "Nettoyage", "ligne_ids": [line["line_id"] for line in finish], "lignes": finish}],
        }
    )
    return {
        "date": now.isoformat(),
        "montant_ttc": _money(quote.totals.ttc_cents),
        "validite": (now + timedelta(days=validity_days)).isoformat(),
        "duree": duration_days,
        "blocs": blocs,
        "quote_id": quote.quote_id,
        "flow": quote.flow.value,
        "generation_mode": quote.generation_mode.value,
        "review_required": quote.review_required,
        "totals": quote.totals.model_dump(mode="json"),
        "trace": quote.trace.model_dump(mode="json"),
    }


__all__ = ["present_quote"]
