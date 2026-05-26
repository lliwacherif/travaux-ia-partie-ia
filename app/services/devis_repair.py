"""Domain-aware repair pass for devis payloads coming back from the LLM.

``clean_and_parse_json`` rebuilds *structurally* valid JSON when the model
truncates its output (it closes braces/brackets and any open string). What
remains can still fail :class:`~app.schemas.devis.DevisResponse` validation
because the **last** ``Ligne`` is often missing required scalar fields.

This module patches that:

* Computes ``ht`` and ``ttc`` from ``qte * pu`` and ``ht * (1 + tva/100)``
  whenever they are missing - those two numbers are *not* AI-creative, they
  are arithmetic, so we can rebuild them safely.
* Drops any ``Ligne`` still missing one of the six required base fields
  after the recompute (``num``, ``description``, ``qte``, ``unit``, ``pu``,
  ``tva``).
* Drops empty ``Lot`` / ``Bloc`` left behind by the previous step.
* Recomputes the top-level ``montant_ttc`` as the sum of every remaining
  ``Ligne.ttc`` so the headline figure always matches the lines.
* Raises :class:`UnrepairableDevisError` if **everything** had to be
  dropped - emitting a 502 with a clear message is better than returning
  an empty devis to the frontend.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# The six fields that *must* come from the LLM. ``ht`` and ``ttc`` are
# deliberately excluded because we can rebuild them from the others.
_REQUIRED_LIGNE_FIELDS: frozenset[str] = frozenset(
    {"num", "description", "qte", "unit", "pu", "tva"}
)


class UnrepairableDevisError(ValueError):
    """Raised when the AI payload was so truncated nothing useful remains."""


def repair_devis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Best-effort repair of a parsed devis dict so it passes ``DevisResponse``.

    The function mutates and returns the same ``payload`` instance for
    convenience (the caller usually wants the patched version).
    """
    if not isinstance(payload, dict):
        raise UnrepairableDevisError(
            f"Expected a dict at the root, got {type(payload).__name__}."
        )

    blocs = payload.get("blocs") or []
    if not isinstance(blocs, list):
        raise UnrepairableDevisError("`blocs` is missing or not a list.")

    repaired_blocs: list[dict[str, Any]] = []
    dropped_lignes = 0
    rebuilt_lignes = 0

    for bloc in blocs:
        if not isinstance(bloc, dict):
            continue

        repaired_lots: list[dict[str, Any]] = []
        for lot in bloc.get("lots") or []:
            if not isinstance(lot, dict):
                continue

            repaired_lignes: list[dict[str, Any]] = []
            for ligne in lot.get("lignes") or []:
                if not isinstance(ligne, dict):
                    continue

                fixed = _try_repair_ligne(ligne)
                if fixed is None:
                    dropped_lignes += 1
                    continue
                if fixed is not ligne:
                    rebuilt_lignes += 1
                repaired_lignes.append(fixed)

            if repaired_lignes:
                lot["lignes"] = repaired_lignes
                repaired_lots.append(lot)

        if repaired_lots:
            bloc["lots"] = repaired_lots
            repaired_blocs.append(bloc)

    if not repaired_blocs:
        raise UnrepairableDevisError(
            "Every bloc was dropped during repair - the AI output was too "
            "truncated to recover any usable line."
        )

    payload["blocs"] = repaired_blocs

    # Always recompute the top-level total from the (now-consistent) lines.
    old_total = payload.get("montant_ttc")
    new_total = recompute_montant_ttc(payload)

    if dropped_lignes or rebuilt_lignes:
        logger.warning(
            "Devis repaired: rebuilt %d lignes (ht/ttc), dropped %d lignes; "
            "montant_ttc %s -> %s.",
            rebuilt_lignes,
            dropped_lignes,
            old_total,
            new_total,
        )

    return payload


def recompute_montant_ttc(devis: dict[str, Any]) -> float:
    """Sum every ``ligne.ttc`` and write the result to ``devis['montant_ttc']``.

    Public so the upsell engine can call it after injecting / mutating lines.
    Lines without a numeric ``ttc`` are silently skipped.
    """
    total = 0.0
    for bloc in devis.get("blocs") or []:
        for lot in bloc.get("lots") or []:
            for ligne in lot.get("lignes") or []:
                try:
                    total += float(ligne.get("ttc", 0) or 0)
                except (TypeError, ValueError):
                    continue
    rounded = round(total, 2)
    devis["montant_ttc"] = rounded
    return rounded


def _try_repair_ligne(ligne: dict[str, Any]) -> dict[str, Any] | None:
    """Return a repaired ligne dict, or ``None`` if it is unsalvageable."""
    if not _REQUIRED_LIGNE_FIELDS.issubset(ligne.keys()):
        return None

    try:
        qte = float(ligne["qte"])
        pu = float(ligne["pu"])
        tva = float(ligne["tva"])
    except (TypeError, ValueError):
        return None

    ht_raw = ligne.get("ht")
    if ht_raw is None:
        ht = round(qte * pu, 2)
        ligne["ht"] = ht
    else:
        try:
            ht = float(ht_raw)
        except (TypeError, ValueError):
            ht = round(qte * pu, 2)
            ligne["ht"] = ht

    ttc_raw = ligne.get("ttc")
    if ttc_raw is None:
        ligne["ttc"] = round(ht * (1.0 + tva / 100.0), 2)
    else:
        try:
            float(ttc_raw)
        except (TypeError, ValueError):
            ligne["ttc"] = round(ht * (1.0 + tva / 100.0), 2)

    return ligne


__all__ = [
    "UnrepairableDevisError",
    "recompute_montant_ttc",
    "repair_devis_payload",
]
