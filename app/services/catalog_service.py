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
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bpu_item import BpuItem
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
_TRADE_LINE_STOPWORDS = frozenset(
    {
        "avec",
        "dans",
        "de",
        "des",
        "du",
        "en",
        "et",
        "faire",
        "la",
        "le",
        "les",
        "pour",
        "sur",
        "travail",
        "travaux",
        "un",
        "une",
    }
)
_TRADE_LINE_ALIASES: dict[str, tuple[str, ...]] = {
    "calorifugeage": ("isolation", "isolant", "calorifuge", "tuyauterie", "plomberie"),
    "calorifuge": ("isolation", "isolant", "calorifugeage", "tuyauterie", "plomberie"),
    "elec": (
        "electricite",
        "électricité",
        "electrique",
        "électrique",
        "tableau electrique",
        "tableau électrique",
        "prise",
    ),
    "electricien": (
        "electricite",
        "électricité",
        "electrique",
        "électrique",
        "tableau electrique",
        "tableau électrique",
        "prise",
    ),
    "electricite": (
        "electricite",
        "électricité",
        "electrique",
        "électrique",
        "tableau electrique",
        "tableau électrique",
        "prise",
    ),
    "macon": ("maconnerie", "maçonnerie", "parpaing"),
    "maconnerie": ("maconnerie", "maçonnerie", "parpaing"),
    "peintre": ("peinture", "revetements murs", "revêtements murs"),
    "peinture": ("peinture", "revetements murs", "revêtements murs", "enduit"),
    "plombier": ("plomberie", "sanitaires"),
    "plomberie": ("plomberie", "sanitaires"),
    "sanitaire": ("plomberie", "sanitaires"),
    "toit": ("couverture", "toiture", "tuile"),
    "toiture": ("couverture", "toiture", "tuile"),
}
_REFERENCE_TRADE_ITEMS: tuple[tuple[tuple[str, ...], tuple[dict[str, Any], ...]], ...] = (
    (
        ("peinture", "peintre", "revetement mural", "revetements murs"),
        (
            {"description": "Préparation des supports avant peinture", "unit": "m2", "pu": 12, "tva": 10},
            {"description": "Application de peinture acrylique mate en deux couches", "unit": "m2", "pu": 20, "tva": 10},
            {"description": "Application de peinture satinée sur murs intérieurs", "unit": "m2", "pu": 24, "tva": 10},
            {"description": "Pose d'enduit de lissage avant finition", "unit": "m2", "pu": 18, "tva": 10},
            {"description": "Protection et nettoyage de fin d'intervention peinture", "unit": "forfait", "pu": 120, "tva": 10},
        ),
    ),
    (
        ("plomberie", "plombier", "sanitaire", "salle de bain"),
        (
            {"description": "Création ou reprise d'un point d'eau sanitaire", "unit": "u", "pu": 350, "tva": 10},
            {"description": "Pose et raccordement d'un lavabo ou meuble vasque", "unit": "u", "pu": 280, "tva": 10},
            {"description": "Remplacement d'un robinet ou mitigeur standard", "unit": "u", "pu": 120, "tva": 10},
            {"description": "Recherche et réparation de fuite accessible", "unit": "forfait", "pu": 220, "tva": 10},
            {"description": "Pose d'une évacuation PVC pour équipement sanitaire", "unit": "ml", "pu": 55, "tva": 10},
        ),
    ),
    (
        ("electricite", "electricien", "electrique", "tableau electrique", "elec"),
        (
            {"description": "Création d'un point lumineux avec appareillage", "unit": "u", "pu": 115, "tva": 10},
            {"description": "Pose d'une prise électrique standard encastrée", "unit": "u", "pu": 95, "tva": 10},
            {"description": "Mise en sécurité d'un circuit électrique existant", "unit": "forfait", "pu": 350, "tva": 10},
            {"description": "Remplacement d'un tableau électrique divisionnaire", "unit": "forfait", "pu": 550, "tva": 10},
            {"description": "Tirage de ligne électrique sous gaine ICTA", "unit": "ml", "pu": 35, "tva": 10},
        ),
    ),
    (
        ("maconnerie", "macon", "parpaing"),
        (
            {"description": "Montage de mur en parpaings avec joints courants", "unit": "m2", "pu": 85, "tva": 10},
            {"description": "Réalisation d'une dalle béton armé standard", "unit": "m2", "pu": 95, "tva": 10},
            {"description": "Reprise ponctuelle de maçonnerie existante", "unit": "forfait", "pu": 420, "tva": 10},
            {"description": "Ouverture ou rebouchage de réservation maçonnée", "unit": "u", "pu": 260, "tva": 10},
            {"description": "Application d'un enduit ciment sur support maçonné", "unit": "m2", "pu": 35, "tva": 10},
        ),
    ),
    (
        ("couverture", "toiture", "toit", "tuile", "ardoise"),
        (
            {"description": "Remplacement ponctuel de tuiles ou ardoises cassées", "unit": "u", "pu": 45, "tva": 10},
            {"description": "Réfection de couverture en tuiles mécaniques", "unit": "m2", "pu": 100, "tva": 10},
            {"description": "Pose d'écran sous-toiture avec contre-lattage", "unit": "m2", "pu": 35, "tva": 10},
            {"description": "Traitement d'un point singulier d'étanchéité toiture", "unit": "forfait", "pu": 380, "tva": 10},
            {"description": "Nettoyage et contrôle général de couverture", "unit": "forfait", "pu": 280, "tva": 10},
        ),
    ),
    (
        ("isolation", "isolant", "thermique", "combles", "calorifugeage", "calorifuge"),
        (
            {"description": "Pose d'isolation en laine minérale sous rampant", "unit": "m2", "pu": 20, "tva": 5.5},
            {"description": "Isolation de combles perdus par soufflage", "unit": "m2", "pu": 28, "tva": 5.5},
            {"description": "Calorifugeage de tuyauteries et canalisations", "unit": "ml", "pu": 35, "tva": 5.5},
            {"description": "Pose de doublage isolant intérieur avec parement", "unit": "m2", "pu": 45, "tva": 5.5},
            {"description": "Traitement de l'étanchéité à l'air avant finition", "unit": "m2", "pu": 12, "tva": 5.5},
            {"description": "Dépose partielle d'ancien isolant non conforme", "unit": "m2", "pu": 10, "tva": 5.5},
        ),
    ),
    (
        ("carrelage", "faience"),
        (
            {"description": "Pose de carrelage au sol avec encollage standard", "unit": "m2", "pu": 70, "tva": 10},
            {"description": "Pose de faïence murale en pièces humides", "unit": "m2", "pu": 75, "tva": 10},
            {"description": "Ragréage de support avant pose de carrelage", "unit": "m2", "pu": 18, "tva": 10},
            {"description": "Réalisation de joints de carrelage hydrofuges", "unit": "m2", "pu": 12, "tva": 10},
            {"description": "Dépose d'ancien revêtement carrelé existant", "unit": "m2", "pu": 35, "tva": 10},
        ),
    ),
)


def _normalise_text(value: str) -> str:
    without_accents = "".join(
        char
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    return without_accents.casefold()


def _trade_line_search_terms(job_corp: str) -> list[str]:
    normalised = _normalise_text(job_corp)
    raw_tokens = re.findall(r"[\w]+", job_corp.casefold(), flags=re.UNICODE)
    normalised_tokens = re.findall(r"[a-z0-9]+", normalised)
    terms: list[str] = []
    for raw_token, token in zip(raw_tokens, normalised_tokens, strict=False):
        if len(token) < 3 or token in _TRADE_LINE_STOPWORDS:
            continue
        terms.append(raw_token)
        terms.append(token)
        terms.extend(_TRADE_LINE_ALIASES.get(token, ()))

    if not terms and normalised.strip():
        terms.append(normalised.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped[:8]


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
    """Legacy query on trades + trade_services (used by build_trade_line_context)."""
    patterns = [f"%{term}%" for term in _trade_line_search_terms(job_corp)]
    if not patterns:
        patterns = [f"%{job_corp.strip()}%"]
    filters = []
    for pattern in patterns:
        filters.extend(
            (
                Trade.name.ilike(pattern),
                Trade.description.ilike(pattern),
                Trade.category.ilike(pattern),
                Trade.subcategory.ilike(pattern),
                TradeService.designation.ilike(pattern),
                TradeService.description.ilike(pattern),
                TradeService.category.ilike(pattern),
            )
        )
    return (
        select(TradeService, Trade.name)
        .join(Trade, Trade.id == TradeService.trade_id)
        .where(or_(*filters))
        .order_by(Trade.name, TradeService.designation)
        .limit(limit)
    )


# ---------------------------------------------------------------------------
# BpuItem-backed trade-line lookup (bibliothèque — 3 000+ items, 30+ trades)
# ---------------------------------------------------------------------------
_VALID_TVA_RATES: frozenset[float] = frozenset({5.5, 10.0, 20.0})


def _bpu_tva(taux: float, *fallback_texts: str | None) -> float:
    """Use the BPU's own TVA rate when valid, else infer from keywords."""
    if taux in _VALID_TVA_RATES:
        return taux
    # Fall back to keyword-based inference.
    return _trade_line_tva(*fallback_texts)


def _bpu_trade_line_stmt(job_corp: str, limit: int):
    """Build a fuzzy SELECT on ``bpu_items`` for the trade-line picker."""
    patterns = [f"%{term}%" for term in _trade_line_search_terms(job_corp)]
    if not patterns:
        patterns = [f"%{job_corp.strip()}%"]
    filters = []
    for pattern in patterns:
        filters.extend(
            (
                BpuItem.corps_metier.ilike(pattern),
                BpuItem.designation.ilike(pattern),
                BpuItem.description.ilike(pattern),
                BpuItem.categorie.ilike(pattern),
                BpuItem.sous_categorie.ilike(pattern),
            )
        )
    return (
        select(BpuItem)
        .where(or_(*filters))
        .where(BpuItem.prix_unitaire_ht > 0)
        .order_by(BpuItem.corps_metier, BpuItem.designation)
        .limit(limit)
    )


def _bpu_trade_line_item(item: BpuItem, job_corp: str) -> dict[str, Any]:
    """Map a single :class:`BpuItem` row to a trade-line response dict."""
    designation = item.designation.strip()
    description = (item.description or "").strip()
    if (
        description
        and description != _DEFAULT_DESCRIPTION
        and description.lower() not in designation.lower()
        and len(f"{designation} - {description}") <= 160
    ):
        desc_text = f"{designation} - {description}"
    else:
        desc_text = designation

    pu = float(item.prix_unitaire_ht) if item.prix_unitaire_ht > 0 else 120.0
    tva = _bpu_tva(
        item.taux_tva_defaut,
        item.designation,
        item.description,
        item.categorie,
    )

    return {
        "job_corp": job_corp,
        "description": desc_text,
        "unit": item.unite or "forfait",
        "pu": pu,
        "tva": tva,
    }


def build_reference_trade_line_items(
    job_corp: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic fallback items for known BTP trades."""
    normalised = _normalise_text(job_corp)
    effective_limit = limit if limit is not None and limit > 0 else _TRADE_LINE_LIMIT
    for keywords, items in _REFERENCE_TRADE_ITEMS:
        if any(keyword in normalised for keyword in keywords):
            return [
                {"job_corp": job_corp, **item}
                for item in items[:effective_limit]
            ]
    return []


async def build_trade_line_items(
    db: AsyncSession,
    *,
    job_corp: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return response-ready trade-line items from the **bpu_items** catalog.

    This is the fast path for ``/trade-line/generate``. The endpoint powers a
    picker UI, so catalog rows are already structured enough to return without
    waiting for a remote LLM to rewrite them.

    Source: ``bibliotheque-travaux-ia-v1.json`` (3 000+ items, 30+ trades)
    seeded into the ``bpu_items`` table.
    """
    effective_limit = limit if limit is not None and limit > 0 else _TRADE_LINE_LIMIT
    rows = (
        await db.execute(_bpu_trade_line_stmt(job_corp, effective_limit))
    ).scalars().all()

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bpu_item in rows:
        description_key = bpu_item.designation.strip().casefold()
        if not description_key or description_key in seen:
            continue
        seen.add(description_key)
        items.append(_bpu_trade_line_item(bpu_item, job_corp))
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
    "build_reference_trade_line_items",
    "build_trade_line_context",
    "build_trade_line_items",
    "load_trade_names",
]
