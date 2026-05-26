"""Heuristic post-processor that runs after the AI generates a devis.

Three jobs (mirrored from the legacy ``revolutionaryAIQuoteService.ts``):

1. **Required-complement injection.** If the devis mentions a heading
   service but forgot a logical complement, we silently add the missing
   line. Two rules from the spec:

   * ``toiture`` (roofing) without ``évacuation`` → inject
     "Évacuation des déchets de toiture" at 150 € forfait.
   * ``carrelage`` (tiling) without ``ragréage`` → inject
     "Ragréage du sol" at 12 € / m².

2. **Zero-price auto-fill.** When the model emits a line with ``pu = 0``,
   we look the description up in ``trade_services`` (case-insensitive,
   prefix-then-substring matching) and overwrite ``pu`` + ``unit`` with
   the catalog values when ``estimated_price > 0``.

3. **Total recomputation.** Any change above invalidates the headline
   ``montant_ttc``, so we always recompute it via
   :func:`devis_repair.recompute_montant_ttc` at the end.

The module exposes a single async entry point :func:`apply_upsell_rules`
that orchestrates the three steps. Each individual rule is also exposed
so they can be tested independently.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade_service import TradeService
from app.services.devis_repair import recompute_montant_ttc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Required-complement rules
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _ComplementRule:
    """One "if X is present and Y is absent, inject Z" rule."""

    trigger_keywords: tuple[str, ...]
    missing_keywords: tuple[str, ...]
    designation: str
    qte: float
    unit: str
    pu: float


_COMPLEMENT_RULES: tuple[_ComplementRule, ...] = (
    _ComplementRule(
        trigger_keywords=("toiture", "couverture"),
        missing_keywords=("évacuation", "evacuation", "déchets", "dechets", "benne"),
        designation="Évacuation des déchets de toiture",
        qte=1.0,
        unit="forfait",
        pu=150.0,
    ),
    _ComplementRule(
        trigger_keywords=("carrelage", "faïence", "faience"),
        missing_keywords=("ragréage", "ragreage", "ragré", "ragre"),
        designation="Ragréage du sol",
        qte=1.0,
        unit="m²",
        pu=12.0,
    ),
)


def inject_missing_complements(devis: dict[str, Any]) -> int:
    """Append missing complement lines in place. Returns the count injected."""
    blocs = devis.get("blocs") or []
    if not blocs:
        return 0

    injected = 0
    for rule in _COMPLEMENT_RULES:
        target_lot, target_bloc = _locate_trigger_lot(blocs, rule.trigger_keywords)
        if target_lot is None:
            continue

        if _devis_contains_any(blocs, rule.missing_keywords):
            continue

        new_line = _build_complement_line(target_lot, rule)
        target_lot.setdefault("lignes", []).append(new_line)
        injected += 1
        logger.info(
            "Upsell: injected '%s' into bloc=%r / lot=%r.",
            rule.designation,
            (target_bloc or {}).get("title"),
            target_lot.get("title"),
        )
    return injected


# ---------------------------------------------------------------------------
# Zero-price auto-fill
# ---------------------------------------------------------------------------
async def fill_zero_prices_from_catalog(
    devis: dict[str, Any],
    db: AsyncSession,
) -> int:
    """Replace ``pu = 0`` lines with catalog values. Returns count fixed."""
    fixed = 0
    for bloc in devis.get("blocs") or []:
        for lot in bloc.get("lots") or []:
            for ligne in lot.get("lignes") or []:
                if not _is_zero_price(ligne):
                    continue

                designation = (ligne.get("description") or "").strip()
                if len(designation) < 6:
                    continue

                match = await _find_catalog_match(db, designation)
                if match is None or match.estimated_price <= 0:
                    continue

                old_pu = ligne.get("pu")
                ligne["pu"] = float(match.estimated_price)
                ligne["unit"] = match.unit or ligne.get("unit") or "u"
                _refresh_line_totals(ligne)
                fixed += 1
                logger.info(
                    "Upsell auto-fill: '%s' pu %s -> %s (catalog match).",
                    designation[:60],
                    old_pu,
                    ligne["pu"],
                )
    return fixed


# ---------------------------------------------------------------------------
# Public orchestrator
# ---------------------------------------------------------------------------
async def apply_upsell_rules(
    devis: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    """Run every upsell heuristic on ``devis`` and recompute the total."""
    injected = inject_missing_complements(devis)
    fixed_prices = await fill_zero_prices_from_catalog(devis, db)
    if injected or fixed_prices:
        recompute_montant_ttc(devis)
    return devis


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _locate_trigger_lot(
    blocs: list[dict[str, Any]],
    trigger_keywords: tuple[str, ...],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Find the first lot whose title or any line description matches."""
    for bloc in blocs:
        for lot in bloc.get("lots") or []:
            haystack = " ".join(
                [
                    str(lot.get("title") or ""),
                    *(
                        str(ligne.get("description") or "")
                        for ligne in (lot.get("lignes") or [])
                    ),
                ]
            ).lower()
            if any(kw in haystack for kw in trigger_keywords):
                return lot, bloc
    return None, None


def _devis_contains_any(
    blocs: list[dict[str, Any]],
    keywords: tuple[str, ...],
) -> bool:
    for bloc in blocs:
        for lot in bloc.get("lots") or []:
            haystack = " ".join(
                [
                    str(lot.get("title") or ""),
                    *(
                        str(ligne.get("description") or "")
                        for ligne in (lot.get("lignes") or [])
                    ),
                ]
            ).lower()
            if any(kw in haystack for kw in keywords):
                return True
    return False


def _build_complement_line(
    lot: dict[str, Any],
    rule: _ComplementRule,
) -> dict[str, Any]:
    """Construct a fully-formed Ligne dict for the given rule."""
    existing_lignes = lot.get("lignes") or []
    next_num = max((ligne.get("num", 0) for ligne in existing_lignes), default=0) + 1

    # Reuse the TVA rate of the lot's other lines if any, fallback to 10 %
    # (rénovation standard, the most common case for both rules).
    tva = _dominant_tva(existing_lignes, default=10.0)

    ht = round(rule.qte * rule.pu, 2)
    ttc = round(ht * (1.0 + tva / 100.0), 2)
    return {
        "num": next_num,
        "description": rule.designation,
        "qte": rule.qte,
        "unit": rule.unit,
        "pu": rule.pu,
        "tva": tva,
        "ht": ht,
        "ttc": ttc,
    }


def _dominant_tva(lignes: list[dict[str, Any]], *, default: float) -> float:
    """Return the most common TVA rate in ``lignes`` (or ``default``)."""
    counts: dict[float, int] = {}
    for ligne in lignes:
        try:
            rate = float(ligne.get("tva"))
        except (TypeError, ValueError):
            continue
        counts[rate] = counts.get(rate, 0) + 1
    if not counts:
        return default
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _is_zero_price(ligne: dict[str, Any]) -> bool:
    try:
        return float(ligne.get("pu", 0) or 0) == 0.0
    except (TypeError, ValueError):
        return False


def _refresh_line_totals(ligne: dict[str, Any]) -> None:
    try:
        qte = float(ligne["qte"])
        pu = float(ligne["pu"])
        tva = float(ligne["tva"])
    except (KeyError, TypeError, ValueError):
        return
    ht = round(qte * pu, 2)
    ligne["ht"] = ht
    ligne["ttc"] = round(ht * (1.0 + tva / 100.0), 2)


# ---------------------------------------------------------------------------
# Catalog lookup
# ---------------------------------------------------------------------------
_DESIGNATION_PREFIX_LEN: int = 50
_FALLBACK_KEYWORD_LEN: int = 25


async def _find_catalog_match(
    db: AsyncSession,
    designation: str,
) -> TradeService | None:
    """Best-effort substring lookup of a service designation."""
    # 1. Try the exact normalized prefix first.
    normalized = re.sub(r"\s+", " ", designation).strip()
    if not normalized:
        return None

    prefix = normalized[:_DESIGNATION_PREFIX_LEN]
    stmt = (
        select(TradeService)
        .where(TradeService.designation.ilike(f"%{prefix}%"))
        .limit(1)
    )
    match = (await db.execute(stmt)).scalar_one_or_none()
    if match is not None:
        return match

    # 2. Fall back to the most distinctive word + the next one.
    candidates = [w for w in re.findall(r"[a-zA-Zéèêàâîôûœïÿ]{4,}", normalized.lower())]
    if not candidates:
        return None
    salient = " ".join(candidates[:2])[:_FALLBACK_KEYWORD_LEN]
    stmt = (
        select(TradeService)
        .where(TradeService.designation.ilike(f"%{salient}%"))
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


# A small public utility so other modules can reuse the formula in case the
# AI gives surface-only inputs in the future. Not used today, but cheap to
# expose.
def f_p_quantity(
    *,
    surface_sol: float,
    component: str,
    is_wet_room: bool = False,
) -> float:
    """Apply the F+P heuristics and return the recommended quantity.

    ``component`` ∈ {"murs", "plafonds", "plinthes", "faience"}.
    """
    surface = max(0.0, float(surface_sol))
    if component == "murs":
        return round(surface * (3.0 if is_wet_room else 2.4), 2)
    if component == "plafonds":
        return round(surface, 2)
    if component == "plinthes":
        return round(4.0 * math.sqrt(surface), 2)
    if component == "faience":
        return round(3.0 * surface, 2)
    raise ValueError(f"Unknown F+P component {component!r}.")


__all__ = [
    "apply_upsell_rules",
    "f_p_quantity",
    "fill_zero_prices_from_catalog",
    "inject_missing_complements",
]
