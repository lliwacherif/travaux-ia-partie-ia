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
from collections.abc import Iterable

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
    pattern = f"%{job_corp.strip()}%"
    effective_limit = limit if limit is not None and limit > 0 else _TRADE_LINE_LIMIT
    stmt = (
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
        .limit(effective_limit)
    )
    rows: list[tuple[TradeService, str]] = (await db.execute(stmt)).all()

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


__all__ = ["build_rag_context", "build_trade_line_context", "load_trade_names"]
