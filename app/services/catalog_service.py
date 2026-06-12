"""Read-side helpers that turn DB rows into the prompt-ready strings the AI
pipeline needs.

Two pieces of context are produced here:

* :func:`load_trade_names` - used by Stage 1 (trade detection) to tell the
  model which trades exist in the catalog (``{trades_list}``).
* :func:`build_rag_context` - used by Stage 2 (devis generation) to ground
  the model on actual priced services, formatted as the bullet-list the
  product spec mandates::

      BIBLIOTHÈQUE DISPONIBLE (N prestations spécialisées):
      • {designation} ({unit}) - {description} - Catégorie: {trade name}

  When a list of trade names (from Stage 1's ``detectedTrades``) is supplied
  we scope the SQL query to only those trades using case-insensitive
  substring matches, so minor paraphrasing from the LLM ("Plomberie" vs
  "Plomberie / Sanitaires") is still resolved correctly.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trade import Trade
from app.models.trade_service import TradeService

logger = logging.getLogger(__name__)

_DEFAULT_DESCRIPTION = "Description standard"
_EMPTY_CONTEXT = (
    "BIBLIOTHÈQUE DISPONIBLE (0 prestations spécialisées):\n"
    "(Aucune prestation trouvée pour les corps de métier détectés.)"
)


async def load_trade_names(db: AsyncSession) -> list[str]:
    """Return every distinct trade name, alphabetically.

    Used to populate the ``{trades_list}`` placeholder in
    :data:`~app.core.prompts.TRADE_DETECTION_PROMPT`.
    """
    result = await db.execute(select(Trade.name).order_by(Trade.name))
    return [row[0] for row in result.all()]


async def build_rag_context(
    db: AsyncSession,
    *,
    trade_names: Iterable[str] | None = None,
) -> str:
    """Build the bulleted ``BIBLIOTHÈQUE DISPONIBLE`` string for Stage 2.

    Parameters
    ----------
    db:
        A live async session.
    trade_names:
        Optional iterable of trade names (typically Stage 1's
        ``detectedTrades``) to scope the retrieval to. If omitted or empty
        we fall back to the full catalog.
    """
    stmt = (
        select(TradeService, Trade.name)
        .join(Trade, Trade.id == TradeService.trade_id)
        .order_by(Trade.name, TradeService.designation)
    )

    filtered = False
    if trade_names:
        patterns = [name.strip() for name in trade_names if name and name.strip()]
        if patterns:
            stmt = stmt.where(
                or_(*(Trade.name.ilike(f"%{pattern}%") for pattern in patterns))
            )
            filtered = True

    rows: list[tuple[TradeService, str]] = (await db.execute(stmt)).all()

    # If filtering produced nothing, retry without the filter so the AI
    # still gets a usable (if broader) library instead of an empty context.
    if filtered and not rows:
        logger.warning(
            "RAG filter on %s returned 0 rows - falling back to full catalog.",
            list(trade_names) if trade_names else [],
        )
        fallback_stmt = (
            select(TradeService, Trade.name)
            .join(Trade, Trade.id == TradeService.trade_id)
            .order_by(Trade.name, TradeService.designation)
        )
        rows = (await db.execute(fallback_stmt)).all()

    if not rows:
        return _EMPTY_CONTEXT

    lines: list[str] = [
        f"BIBLIOTHÈQUE DISPONIBLE ({len(rows)} prestations spécialisées):"
    ]
    for service, trade_name in rows:
        description = (service.description or _DEFAULT_DESCRIPTION).strip()
        lines.append(
            f"• {service.designation} ({service.unit}) "
            f"- {description} - Catégorie: {trade_name}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Single-line / job_corp scoped lookup (used by /trade-line/generate)
# ---------------------------------------------------------------------------
_TRADE_LINE_LIMIT = 40
_EMPTY_LINE_CONTEXT = (
    "BIBLIOTHÈQUE DISPONIBLE (0 prestations spécialisées):\n"
    "(Aucune prestation trouvée pour ce corps de métier — utilise ton expertise.)"
)
_ENERGY_TVA_KEYWORDS = (
    "isolation",
    "isolant",
    "thermique",
    "energetique",
    "énergétique",
    "laine",
    "combles",
)
_REFERENCE_PRICES: tuple[tuple[tuple[str, ...], float], ...] = (
    (("nettoyage", "fin de chantier"), 3),
    (("peinture", "acrylique", "badigeon"), 20),
    (("enduit", "chaux", "argile"), 30),
    (("faux plafond", "plafond decoratif"), 70),
    (("placo", "ba13", "cloison"), 22),
    (("isolation", "isolant", "laine"), 20),
    (("carrelage", "faience"), 70),
    (("tableau electrique",), 550),
    (("plomberie", "point d'eau", "sanitaire"), 350),
    (("electricite", "prise", "point lumineux"), 115),
    (("demolition", "curage", "depose"), 55),
    (("charpente",), 115),
    (("couverture", "tuile", "ardoise"), 100),
    (("maconnerie", "parpaing", "mur porteur"), 85),
)
_UNIT_REFERENCE_PRICES = {
    "m2": 45,
    "m²": 45,
    "ml": 60,
    "u": 180,
    "unite": 180,
    "unité": 180,
    "forfait": 450,
}


def _normalise_text(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    return without_accents.casefold()


def _trade_line_tva(*values: str | None) -> float:
    """Infer the default VAT rate for a catalog-backed trade-line item."""
    haystack = _normalise_text(" ".join(value or "" for value in values))
    if any(keyword in haystack for keyword in _ENERGY_TVA_KEYWORDS):
        return 5.5
    return 10


def _trade_line_description(service: TradeService) -> str:
    """Prefer the catalog designation; append useful short context if needed."""
    designation = service.designation.strip()
    description = (service.description or "").strip()
    if (
        not description
        or description == _DEFAULT_DESCRIPTION
        or description.lower() in designation.lower()
    ):
        return designation

    combined = f"{designation} - {description}"
    return combined if len(combined) <= 160 else designation


def _trade_line_pu(service: TradeService, trade_name: str) -> float:
    """Use catalog price when present, otherwise a deterministic BTP reference."""
    if service.estimated_price and service.estimated_price > 0:
        return float(service.estimated_price)

    haystack = _normalise_text(
        " ".join(
            value or ""
            for value in (
                service.designation,
                service.description,
                service.category,
                trade_name,
            )
        )
    )
    for keywords, price in _REFERENCE_PRICES:
        if any(keyword in haystack for keyword in keywords):
            return float(price)

    unit = _normalise_text(service.unit or "forfait")
    return float(_UNIT_REFERENCE_PRICES.get(unit, 120))


def _trade_line_item(
    service: TradeService,
    trade_name: str,
    job_corp: str,
) -> dict[str, Any]:
    return {
        "job_corp": job_corp,
        "description": _trade_line_description(service),
        "unit": service.unit or "forfait",
        "pu": _trade_line_pu(service, trade_name),
        "tva": _trade_line_tva(
            service.designation,
            service.description,
            service.category,
            trade_name,
        ),
    }


def _trade_line_stmt(job_corp: str, limit: int):
    pattern = f"%{job_corp.strip()}%"
    return (
        select(TradeService, Trade.name)
        .join(Trade, Trade.id == TradeService.trade_id)
        .where(
            or_(
                Trade.name.ilike(pattern),
                Trade.description.ilike(pattern),
                Trade.category.ilike(pattern),
                Trade.subcategory.ilike(pattern),
                TradeService.designation.ilike(pattern),
                TradeService.description.ilike(pattern),
                TradeService.category.ilike(pattern),
            )
        )
        .order_by(Trade.name, TradeService.designation)
        .limit(limit)
    )


async def build_trade_line_items(
    db: AsyncSession,
    *,
    job_corp: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return response-ready trade-line items directly from the catalog.

    This is the fast path for ``/trade-line/generate``. The endpoint powers a
    picker UI, so catalog rows are already structured enough to return without
    waiting for a remote LLM to rewrite them.
    """
    effective_limit = limit if limit is not None and limit > 0 else _TRADE_LINE_LIMIT
    rows: list[tuple[TradeService, str]] = (
        await db.execute(_trade_line_stmt(job_corp, effective_limit))
    ).all()

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for service, trade_name in rows:
        description_key = service.designation.strip().casefold()
        if not description_key or description_key in seen:
            continue
        seen.add(description_key)
        items.append(_trade_line_item(service, trade_name, job_corp))
        if len(items) >= effective_limit:
            break

    return items


async def build_trade_line_context(
    db: AsyncSession,
    *,
    job_corp: str,
    limit: int | None = None,
) -> str:
    """Build a small bulleted catalog scoped to a single corps de métier.

    The match is intentionally fuzzy: we ilike on ``Trade.name``,
    ``Trade.description``, ``Trade.category``, ``Trade.subcategory`` AND
    ``TradeService.designation`` / ``TradeService.description`` /
    ``TradeService.category`` so that user-typed labels such as
    ``"Peinture"`` (which is not a literal trade name) still resolve to
    the relevant rows (here: ``Revêtements murs``).

    Returns the same ``• designation (unit) - description - Catégorie: trade``
    bullet format as :func:`build_rag_context` so the prompt formatting is
    consistent.
    """
    effective_limit = limit if limit is not None and limit > 0 else _TRADE_LINE_LIMIT
    rows: list[tuple[TradeService, str]] = (
        await db.execute(_trade_line_stmt(job_corp, effective_limit))
    ).all()

    if not rows:
        logger.info("No catalog rows matched job_corp=%r.", job_corp)
        return _EMPTY_LINE_CONTEXT

    lines: list[str] = [
        f"BIBLIOTHÈQUE DISPONIBLE ({len(rows)} prestations spécialisées):"
    ]
    for service, trade_name in rows:
        description = (service.description or _DEFAULT_DESCRIPTION).strip()
        lines.append(
            f"• {service.designation} ({service.unit}) "
            f"- {description} - Catégorie: {trade_name}"
        )
    return "\n".join(lines)


__all__ = [
    "build_rag_context",
    "build_trade_line_context",
    "build_trade_line_items",
    "load_trade_names",
]
