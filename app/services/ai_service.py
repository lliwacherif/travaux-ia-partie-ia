"""Two-stage devis generation pipeline backed by OpenAI's Chat Completions API.

The service drives the OpenAI chat completions endpoint. The public entry
point is :meth:`AIService.generate_quote`, which runs:

* **Stage 1 - Routing.** The ``TRADE_DETECTION_PROMPT`` classifies the user's
  free-form text and returns a small JSON object describing whether the
  request is building-related and which trades it involves. The list of
  trades the model is allowed to pick from is pulled live from the
  ``trades`` table (via :func:`catalog_service.load_trade_names`).
* **Stage 2 - Generation.** The ``PRESTATION_ANALYSIS_PROMPT`` is rendered
  with a bulleted ``BIBLIOTHÈQUE DISPONIBLE`` catalog built from the
  ``trade_services`` rows that belong to the trades detected in Stage 1,
  and with the original user text, producing the full devis JSON that
  matches the ``DevisResponse`` Pydantic schema.

Both stages use the exact API parameters mandated by the product spec.
"""

from __future__ import annotations

import asyncio
import logging
import re
from time import perf_counter
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final, TypedDict

from openai import APIError, AsyncOpenAI
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.chat_responses import (
    build_chatbot_provider_fallback_response,
    build_chatbot_static_response,
    build_landing_chatbot_provider_fallback_response,
    build_mobile_chatbot_provider_fallback_response,
)
from app.core.prompts import (
    LANDING_CHATBOT_SYSTEM_PROMPT,
    MOBILE_CHATBOT_SYSTEM_PROMPT,
    SYSTEM_PROMPT_GENERATOR,
    build_chatbot_system_prompt,
)
from app.core.chat_intent import classify_chat_intent
from app.schemas.chat import ChatMessage
from app.core.utils import JSONHealingError, clean_and_parse_json
from app.core.btp_validator import validate_btp_context

# ---------------------------------------------------------------------------
# OpenAI Structured Outputs — JSON Schema for devis generation
# ---------------------------------------------------------------------------
# This schema mirrors EXACTLY the JSON format the AI already returns.
# By passing it via response_format, OpenAI enforces valid JSON at the
# decoding layer — the model no longer wastes reasoning tokens on
# formatting, and JSONHealingError becomes virtually impossible.
# ---------------------------------------------------------------------------
_DEVIS_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "devis_generation",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_btp": {
                    "type": "boolean",
                    "description": (
                        "true only when the request concerns building works "
                        "(construction, renovation, repair of building equipment)."
                    ),
                },
                "client_type": {
                    "type": "string",
                    "enum": ["pro", "particulier"],
                },
                "project_nature": {
                    "type": "string",
                    "enum": ["neuf", "renovation"],
                },
                "lots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "lot_key": {"type": "string"},
                            "metier": {"type": "string"},
                            "zone": {
                                "type": "string",
                                "enum": ["interieur", "exterieur"],
                            },
                            "packs": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "type": {
                                            "type": "string",
                                            "enum": [
                                                "PRESTATION",
                                                "DEPANNAGE",
                                            ],
                                        },
                                        "quantite": {"type": "number"},
                                        "quantite_type": {
                                            "type": "string",
                                            "enum": [
                                                "surface_m2",
                                                "longueur_ml",
                                                "unitaire",
                                                "forfait",
                                                "non_specifie",
                                            ],
                                        },
                                        "source_qte": {"type": "string"},
                                    },
                                    "required": [
                                        "id",
                                        "type",
                                        "quantite",
                                        "quantite_type",
                                        "source_qte",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["lot_key", "metier", "zone", "packs"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["is_btp", "client_type", "project_nature", "lots"],
            "additionalProperties": False,
        },
    },
}

from app.services.prestations_engine import (
    process_ai_lots,
    calculate_global_totals,
    estimate_duration_days,
    get_cached_price_map,
    get_cached_packs_map,
    extract_surface_m2,
)
from app.schemas.devis import DevisResponse
from app.services.catalog_service import (
    build_reference_trade_line_items,
    build_trade_line_items,
)
from app.services.devis_repair import UnrepairableDevisError
from app.core.metier_rules import ALL_METIER_RULES

logger = logging.getLogger(__name__)

class InvalidBuildingRequestError(ValueError):
    """Raised when Stage 1 classifies the request as out-of-scope."""


class AIServiceError(RuntimeError):
    """Raised when the AI call itself fails (network, quota, 5xx, ...)."""


# ---------------------------------------------------------------------------
# Tiny helpers used by the retry loop.
# ---------------------------------------------------------------------------
_RETRY_ERROR_MAX_LEN: Final[int] = 400

# Default values used when the Stage 1 routing payload is missing the
# new structure fields (older model behaviour, defensive coding).
_DEFAULT_REQUEST_TYPE: Final[str] = "travaux"
_VALID_REQUEST_TYPES: Final[frozenset[str]] = frozenset({"travaux", "depannage"})


def _short(s: str, limit: int = 200) -> str:
    """Return ``s`` truncated to ``limit`` chars with an ellipsis."""
    return s if len(s) <= limit else s[:limit] + "..."


# ---------------------------------------------------------------------------
# Forbidden generic line labels ("Autres travaux", "Divers", balancing…)
#
# The deterministic engine may still emit merge / padding labels that look
# like catch-all amounts. Those labels are kept for the calculation path
# (prices untouched) then rewritten by a GPT-4 sub-call just before the
# devis is returned, so the client never sees "Autres / Divers / …".
# ---------------------------------------------------------------------------
_FORBIDDEN_LINE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Catch-all merge / padding labels produced by the engine or legacy paths.
    re.compile(r"(?i)^\s*autres?\b"),
    re.compile(r"(?i)\bautres?\s+(travaux|mise|nettoyage|fournitures|prestations)\b"),
    re.compile(r"(?i)\bdivers\b"),
    re.compile(r"(?i)ajustement\s+forfaitaire"),
    re.compile(r"(?i)travaux\s+compl[eé]mentaires"),
    re.compile(r"(?i)prestations?\s+compl[eé]mentaires"),
    re.compile(r"(?i)ensemble\s+de\s+prestations"),
    re.compile(r"(?i)montant\s+d['']?[eé]quilibrage"),
    re.compile(r"(?i)\b[eé]quilibrage\b"),
)

_line_rewrite_cache: dict[str, str] = {}


def _is_forbidden_line_description(text: str | None) -> bool:
    """Return True when a devis line label must be reformulated."""
    if not text or not str(text).strip():
        return False
    return any(p.search(str(text)) for p in _FORBIDDEN_LINE_PATTERNS)


def _local_rewrite_forbidden_label(text: str) -> str:
    """Deterministic offline fallback when the GPT-4 rewrite call fails."""
    cleaned = str(text).strip()
    cleaned = cleaned.replace("–", "-").replace("—", "-").replace("−", "-")
    cleaned = re.sub(
        r"(?i)^\s*ensemble\s+de\s+prestations?\s+compl[eé]mentaires\s*[-:]?\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)^\s*autres?\s+(travaux\s+et\s+fournitures\s+|travaux\s+|)",
        "",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\bet\s+finitions\b", "", cleaned)
    cleaned = re.sub(r"(?i)\(\s*\d+\s*postes?\s+regroup[eé]s?\s*\)", "", cleaned)
    cleaned = re.sub(r"(?i)\bdivers\b", "", cleaned)
    cleaned = re.sub(r"\s*[-:]\s*", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;-/")
    if not cleaned:
        return "Travaux annexes du lot"
    # Keep a concrete BTP feel without the forbidden vocabulary.
    if cleaned.lower().startswith("travaux"):
        return cleaned[0].upper() + cleaned[1:]
    return f"Travaux annexes - {cleaned[0].upper() + cleaned[1:]}"


def _normalise_request_type(value: Any) -> str:
    """Coerce the LLM's ``requestType`` to ``"travaux"`` or ``"depannage"``.

    Accepts common spellings (``"dépannage"``, ``"DEPANNAGE"``, ``"repair"``,
    ``"breakdown"``…) and falls back to ``"travaux"`` for anything else.
    """
    if not isinstance(value, str):
        return _DEFAULT_REQUEST_TYPE
    normalised = (
        value.strip().lower().replace("é", "e").replace("è", "e").replace(" ", "")
    )
    if normalised in {"depannage", "depan", "repair", "breakdown", "urgence"}:
        return "depannage"
    if normalised in _VALID_REQUEST_TYPES:
        return normalised
    return _DEFAULT_REQUEST_TYPE


def _normalise_trade_line_payload(
    parsed: Any,
    *,
    job_corp: str,
    limit: int,
) -> dict[str, Any]:
    """Coerce the LLM's raw payload into ``{job_corp, count, items}``.

    The model sometimes returns a bare list instead of the wrapper, or
    forgets to echo ``job_corp`` consistently. We accept both shapes and
    overwrite ``job_corp`` on every item so the response is predictable.
    Items beyond ``limit`` are trimmed.
    """
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        items = parsed.get("items")
        if items is None:
            # Single-item legacy shape — wrap it so the response is uniform.
            if {"description", "unit", "pu", "tva"}.issubset(parsed.keys()):
                items = [parsed]
            else:
                items = []
    else:
        items = []

    if not isinstance(items, list):
        items = []

    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item["job_corp"] = job_corp
        cleaned.append(item)
        if len(cleaned) >= limit:
            break

    return {"job_corp": job_corp, "count": len(cleaned), "items": cleaned}


# ---------------------------------------------------------------------------
# Depannage-only catalog scoping (Option A)
#
# These helpers ONLY affect requests detected as "dépannage". For every other
# kind of travaux the pipeline keeps building the exact same full catalog as
# before, so standard devis generation is byte-for-byte unchanged.
# ---------------------------------------------------------------------------
_DEPANNAGE_METIER_LABEL: Final[str] = "Dépannage & Interventions rapides"

# Keywords that flag the request as a quick repair / breakdown intervention.
_DEPANNAGE_TRIGGER_KEYWORDS: Final[tuple[str, ...]] = (
    "depannage", "depanage", "urgence", "urgent", "panne", "en panne",
    "fuite", "qui fuit", "debouchage", "bouche", "bouchee", "engorge",
    "reparer", "reparation", "casse", "cassee", "bloque", "bloquee",
    "ne fonctionne", "ne marche", "hs", "en rade", "coince", "coincee",
)

# Maps the exact ``sous_metier_depannage`` DB values to trigger keywords.
_DEPANNAGE_SOUS_METIER_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "Plomberie": (
        "robinet", "mitigeur", "fuite", "wc", "toilette", "chasse", "evier",
        "lavabo", "douche", "baignoire", "siphon", "sanitaire", "chauffe-eau",
        "chauffe eau", "cumulus", "ballon", "flexible", "joint", "eau chaude",
        "plomberie", "vasque", "abattant", "flotteur",
    ),
    "Canalisation / Débouchage": (
        "debouchage", "bouche", "bouchee", "canalisation", "evacuation",
        "egout", "odeur", "engorge", "colonne", "receveur",
    ),
    "Électricité": (
        "electricite", "electrique", "disjoncteur", "prise", "tableau",
        "court-circuit", "court circuit", "courant", "interrupteur", "lumiere",
        "differentiel", "fusible", "compteur", "coupure electrique",
    ),
    "Serrurerie": (
        "serrure", "porte", "cle", "clef", "verrou", "cylindre", "ouverture",
        "claquee", "gache", "barillet", "cadenas",
    ),
    "Vitrerie": (
        "vitre", "carreau", "fenetre", "vitrage", "verre", "double vitrage",
    ),
    "Toiture (urgence / bâchage)": (
        "toiture", "tuile", "gouttiere", "bachage", "infiltration", "toit",
        "faitage", "cheneau", "aretier", "zinguerie",
    ),
    "Chauffage / Chaudière / PAC": (
        "chaudiere", "chauffage", "radiateur", "pac", "pompe a chaleur",
        "thermostat", "circulateur", "vase expansion", "chauffe-eau",
    ),
    "Climatisation – VMC": (
        "clim", "climatisation", "climatiseur", "vmc", "ventilation", "split",
    ),
    "Domotique – Réseaux": (
        "domotique", "box", "wifi", "reseau", "camera", "alarme", "portail",
        "volet roulant", "interphone", "visiophone", "nas", "routeur",
    ),
}


def _normalise_for_match(text: str) -> str:
    """Lowercase + strip accents so keyword matching is accent-insensitive."""
    import unicodedata

    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Strong "real works" signals that veto the depannage-only catalog even when a
# repair keyword is present ("rénovation salle de bain, remplacer le carrelage
# cassé" must NOT be scoped to DEP-* packs). The standard catalog still
# contains every DEP pack, so mixed requests lose nothing.
_RENOVATION_OVERRIDE_KEYWORDS: Final[tuple[str, ...]] = (
    "renovation", "renover", "renovation complete", "refection",
    "extension", "agrandissement", "surelevation", "construction",
    "construire", "creer", "creation", "amenagement", "amenager",
    "installation complete", "isolation", "ravalement", "elevation",
    "maconnerie", "charpente", "carrelage", "peinture des", "peindre",
    "cloison", "faux plafond", "terrassement", "chape", "dalle beton",
)


def _is_depannage_request(user_text: str) -> bool:
    """Return True when the text should use the depannage-scoped catalog.

    Conservative on purpose: requires an explicit repair signal AND the
    absence of strong renovation/construction signals. A renovation text
    that merely mentions a broken element ("carrelage cassé") keeps the
    full catalog, where every DEP-* pack is still listed anyway.
    """
    norm = _normalise_for_match(user_text)
    if not any(kw in norm for kw in _DEPANNAGE_TRIGGER_KEYWORDS):
        return False
    if any(kw in norm for kw in _RENOVATION_OVERRIDE_KEYWORDS):
        return False
    # An explicit surface of works (e.g. "80 m²") signals a real project,
    # not a quick repair intervention.
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[²2]\b", norm)
    if m and float(m.group(1).replace(",", ".")) >= 15:
        return False
    return True


# ---------------------------------------------------------------------------
# Métier hints — lightweight keyword detection appended to the user message.
#
# The full catalog stays in the (cacheable) system prompt; this hint only
# focuses the model's attention on the most probable trades, which greatly
# stabilises pack selection from one run to the next.
# ---------------------------------------------------------------------------
_METIER_HINT_KEYWORDS: Final[dict[str, tuple[str, ...]]] = {
    "Maçonnerie – Gros œuvre": (
        "parpaing", "mur porteur", "dalle beton", "fondation", "linteau",
        "chainage", "agglo", "elevation", "extension", "arase", "maconnerie",
        "ouverture de mur", "beton arme",
    ),
    "Démolition – Curage – Dépose": ("demolition", "demolir", "curage", "abattre"),
    "Terrassement – VRD – Assainissement": (
        "terrassement", "tranchee", "vrd", "assainissement", "fosse septique",
        "empierrement", "decaissement",
    ),
    "Plâtrerie – Cloisons – Doublages – Faux plafonds": (
        "placo", "cloison", "doublage", "faux plafond", "ba13", "platrerie",
    ),
    "Peinture – Finitions – Enduits décoratifs": (
        "peinture", "peindre", "enduit decoratif", "papier peint", "toile de verre",
    ),
    "Plomberie – Sanitaire": (
        "plomberie", "robinet", "lavabo", "evier", "chauffe-eau", "chauffe eau",
        "wc", "sanitaire", "arrivee d'eau",
    ),
    "Chauffage – Chaudières – Radiateurs – Réseaux": (
        "chaudiere", "radiateur", "plancher chauffant", "chauffage central",
    ),
    "Chauffage ENR – PAC – Solaire thermique": (
        "pompe a chaleur", "pac ", "solaire thermique", "ballon thermodynamique",
    ),
    "Climatisation – Ventilation – VMC": (
        "climatisation", "clim ", "split", "vmc", "ventilation",
    ),
    "Électricité – Courants forts": (
        "electricite", "tableau electrique", "prise", "interrupteur",
        "luminaire", "mise aux normes electrique",
    ),
    "Électricité – Courants faibles – Domotique – Réseaux": (
        "domotique", "reseau informatique", "camera", "alarme", "interphone",
        "visiophone", "fibre",
    ),
    "Menuiserie intérieure": (
        "porte interieure", "placard", "dressing", "escalier bois", "plinthe",
        "verriere interieure",
    ),
    "Menuiserie extérieure": (
        "fenetre", "baie vitree", "volet", "porte d'entree", "porte de garage",
        "double vitrage",
    ),
    "Serrurerie – Métallerie": (
        "portail", "garde-corps", "grille", "metallerie", "cloture acier",
        "serrurerie",
    ),
    "Revêtements de sols": (
        "parquet", "stratifie", "moquette", "sol souple", "lino", "vinyle",
    ),
    "Carrelage – Sols & Murs": ("carrelage", "faience", "carreler", "gres cerame"),
    "Isolation intérieure": (
        "isolation interieure", "isolation des combles", "combles", "laine de verre",
        "laine de roche", "isolation phonique",
    ),
    "Isolation thermique extérieure (ITE)": (
        "ite", "isolation exterieure", "isolation par l'exterieur", "sarking",
    ),
    "Calorifugeage": ("calorifugeage", "calorifuge"),
    "Couverture – Toiture – Zinguerie": (
        "toiture", "tuile", "ardoise", "gouttiere", "zinguerie", "couverture",
        "demoussage toiture",
    ),
    "Étanchéité – Toiture terrasse": ("etancheite", "toiture terrasse", "toit plat"),
    "Façade – Ravalement – Enduits extérieurs": (
        "facade", "ravalement", "crepi", "enduit exterieur", "bardage",
    ),
    "Charpente bois – Ossature": ("charpente", "solivage", "ossature bois", "ferme"),
    "Charpente métallique": ("charpente metallique", "structure acier", "ipn"),
    "Panneaux photovoltaïques": ("photovoltaique", "panneau solaire"),
    "Salle de bain – Aménagement complet": (
        "salle de bain", "sdb", "douche italienne", "salle d'eau",
    ),
    "Cuisine – Agencement sur mesure": ("cuisine equipee", "agencement cuisine",),
    "Dépannage & Interventions rapides": ("depannage", "urgence", "panne"),
}


def _detect_metier_hints(user_text: str, limit: int = 4) -> list[str]:
    """Return up to ``limit`` probable trade labels detected in the text."""
    norm = _normalise_for_match(user_text)
    scored: list[tuple[int, str]] = []
    for label, keywords in _METIER_HINT_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in norm)
        if hits:
            scored.append((hits, label))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [label for _, label in scored[:limit]]


# ---------------------------------------------------------------------------
# Semantic-payload cache — hard determinism for identical prompts.
#
# OpenAI sampling is only best-effort deterministic even at temperature 0
# with a fixed seed. Since the downstream engine is fully deterministic, the
# one remaining variance source is the Stage-2 payload: caching it by
# normalized prompt text guarantees that the same request produces the same
# devis (with fresh dates) for every client hitting this server.
# ---------------------------------------------------------------------------
import copy
from collections import OrderedDict

_SEMANTIC_CACHE_MAX: Final[int] = 512
_semantic_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


def _semantic_cache_key(user_text: str) -> str:
    """Case-, accent- and whitespace-insensitive identity of a request."""
    norm = _normalise_for_match(user_text)
    return re.sub(r"\s+", " ", norm).strip()


def _semantic_cache_get(key: str) -> dict[str, Any] | None:
    cached = _semantic_cache.get(key)
    if cached is None:
        return None
    _semantic_cache.move_to_end(key)
    return copy.deepcopy(cached)


def _semantic_cache_put(key: str, parsed: dict[str, Any]) -> None:
    _semantic_cache[key] = copy.deepcopy(parsed)
    _semantic_cache.move_to_end(key)
    while len(_semantic_cache) > _SEMANTIC_CACHE_MAX:
        _semantic_cache.popitem(last=False)


# ---------------------------------------------------------------------------
# Stage-2 payload sanitation
# ---------------------------------------------------------------------------
_VALID_QTY_TYPES: Final[frozenset[str]] = frozenset(
    {"surface_m2", "longueur_ml", "unitaire", "forfait", "non_specifie"}
)
_MAX_SANE_QUANTITY: Final[float] = 10_000.0


def _sanitize_ai_lots(raw_lots: Any) -> list[dict[str, Any]]:
    """Validate and normalise the LLM lots before the deterministic engine.

    Guarantees for downstream code:
    * every lot has a non-empty ``metier`` and at least one pack;
    * packs are deduplicated by id within a lot (LLM repetition guard);
    * ``quantite`` is a positive, sane float; ``quantite_type`` is one of
      :data:`_VALID_QTY_TYPES`; ``source_qte`` is a non-empty string.
    """
    if not isinstance(raw_lots, list):
        return []

    cleaned_lots: list[dict[str, Any]] = []
    for idx, lot in enumerate(raw_lots, 1):
        if not isinstance(lot, dict):
            continue
        packs_in = lot.get("packs") or []
        if not isinstance(packs_in, list):
            continue

        seen_ids: set[str] = set()
        packs_out: list[dict[str, Any]] = []
        for pack in packs_in:
            if not isinstance(pack, dict):
                continue
            pack_id = str(pack.get("id") or "").strip()
            if not pack_id:
                continue
            dedupe_key = pack_id.upper()
            if dedupe_key in seen_ids:
                logger.info("Dropping duplicate pack %r in lot %d.", pack_id, idx)
                continue
            seen_ids.add(dedupe_key)

            try:
                qty = float(pack.get("quantite", 1))
            except (TypeError, ValueError):
                qty = 1.0
            qty_type = str(pack.get("quantite_type") or "").strip().lower()
            if qty_type not in _VALID_QTY_TYPES:
                qty_type = "non_specifie"
            source = str(pack.get("source_qte") or "").strip() or "non spécifié"

            if qty <= 0:
                qty, qty_type = 1.0, "non_specifie"
            elif qty > _MAX_SANE_QUANTITY:
                logger.warning(
                    "Pack %r has absurd quantite=%s — treated as unspecified.",
                    pack_id, qty,
                )
                qty, qty_type = 1.0, "non_specifie"

            packs_out.append(
                {
                    "id": pack_id,
                    "type": str(pack.get("type") or "PRESTATION").strip().upper(),
                    "quantite": qty,
                    "quantite_type": qty_type,
                    "source_qte": source,
                }
            )

        if not packs_out:
            continue
        metier = str(lot.get("metier") or "").strip() or "Métier inconnu"
        cleaned_lots.append(
            {
                "lot_key": str(lot.get("lot_key") or f"LOT_{idx:02d}"),
                "metier": metier,
                "zone": lot.get("zone", "interieur"),
                "packs": packs_out,
            }
        )
    return cleaned_lots


def _detect_depannage_sous_metiers(user_text: str) -> list[str]:
    """Return the ``sous_metier_depannage`` categories relevant to the text."""
    norm = _normalise_for_match(user_text)
    matched = [
        sous_metier
        for sous_metier, keywords in _DEPANNAGE_SOUS_METIER_KEYWORDS.items()
        if any(kw in norm for kw in keywords)
    ]
    return matched


def _build_depannage_catalog(
    pack_list: list[dict[str, Any]], user_text: str
) -> str:
    """Build a SMALL, focused catalog string containing only depannage packs.

    Scopes the candidate packs to the relevant ``sous_metier_depannage``
    when detectable, otherwise falls back to every depannage pack. This is
    what makes the AI pick the correct DEP-* code instead of drowning in
    the full 900-pack catalog.
    """
    depannage_packs = [
        p
        for p in pack_list
        if p.get("corps_metier") == _DEPANNAGE_METIER_LABEL
        or str(p.get("code_pack", "")).startswith("DEP-")
    ]

    target_sous_metiers = _detect_depannage_sous_metiers(user_text)
    if target_sous_metiers:
        scoped = [
            p
            for p in depannage_packs
            if p.get("sous_metier_depannage") in target_sous_metiers
        ]
        # If the keyword scope somehow matched nothing, keep all depannage
        # packs rather than sending an empty catalog.
        if scoped:
            depannage_packs = scoped

    # Group by sous_metier for readability.
    by_sous_metier: dict[str, list[str]] = {}
    for p in depannage_packs:
        key = p.get("sous_metier_depannage") or "Autres interventions"
        by_sous_metier.setdefault(key, []).append(
            f"[{p['code_pack']}] {p['nom_pack']}"
        )

    lines: list[str] = [
        "CONTEXTE : demande de DÉPANNAGE / intervention rapide.",
        "Choisis IMPÉRATIVEMENT le pack le PLUS proche de la demande dans la",
        "liste ci-dessous. N'invente AUCUN pack hors de cette liste.",
        "",
    ]
    for sous_metier, packs in by_sous_metier.items():
        lines.append(f"- Sous-métier: {sous_metier}")
        lines.extend(f"  {p}" for p in packs)
    return "\n".join(lines)


def _format_interventions_block(
    interventions: list[str], user_text: str
) -> str:
    """Render the ``{interventions_block}`` placeholder for the Stage 2 prompt.

    Falls back to a single intervention extracted from ``user_text`` when
    Stage 1 produced an empty list, so the prompt is never structurally
    incomplete.
    """
    cleaned = [s.strip() for s in interventions if isinstance(s, str) and s.strip()]
    if not cleaned:
        cleaned = [_short(user_text, 80) or "Intervention principale"]
    return "\n".join(f"  {idx}. {label}" for idx, label in enumerate(cleaned, 1))


# ---------------------------------------------------------------------------
# Streaming - progress event vocabulary
# ---------------------------------------------------------------------------
class TokenUsage(TypedDict):
    """Token counts returned alongside every chat response."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


ZERO_USAGE: TokenUsage = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}


def _extract_usage(response: Any) -> TokenUsage:
    """Pull token counts from an OpenAI chat completion response."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return dict(ZERO_USAGE)  # type: ignore[return-value]
    return TokenUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


class StreamEvent(TypedDict, total=False):
    """A single event yielded by :meth:`AIService.generate_quote_stream`.

    ``type`` is always present; the other keys depend on the event:

    * ``type="progress"``  -> ``step``, ``total``, ``label``
    * ``type="result"``    -> ``data`` (the parsed devis dict)
    * ``type="title"``     -> ``title`` (short generated devis title)
    * ``type="error"``     -> ``status`` (HTTP-style hint), ``detail``
    """

    type: str
    step: int
    total: int
    label: str
    data: dict[str, Any]
    title: str
    status: int
    detail: str


# Public, ordered list of UI-visible progress labels for the generate flow.
# The frontend can rely on the ``step`` index being stable across versions
# even if the wording of a label evolves.
PROGRESS_STEPS: Final[tuple[str, ...]] = (
    "Analyse",
    "Generate",
    "Calculate",
    "Finalise",
)


ProgressCallback = Callable[[int, str], Awaitable[None]]


def _format_retry_error(exc: Exception) -> str:
    """Make the previous-attempt error compact + safe to embed in the prompt.

    Pydantic's ``ValidationError`` repr is huge; trim it. We also escape
    any backticks that could collide with the model's own markdown.
    """
    if isinstance(exc, ValidationError):
        first_errors = exc.errors()[:3]
        rendered = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in first_errors
        )
        msg = f"DevisResponse validation failed: {rendered}"
    else:
        msg = f"{type(exc).__name__}: {exc}"
    return _short(msg.replace("`", "'"), _RETRY_ERROR_MAX_LEN)


class AIService:
    """High-level client for the OpenAI-backed devis generation pipeline."""

    # Lifted out of the call site so tests can monkey-patch a single dict.
    #
    # NOTE on ``max_tokens``: 8192 gives enough headroom for the detailed
    # devis JSON output (15-20 lines with descriptions, prices, TVA) plus
    # the structured prompt context.
    _COMPLETION_PARAMS: Final[dict[str, Any]] = {
        "max_completion_tokens": 8192,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "service_tier": settings.OPENAI_SERVICE_TIER,
        "prompt_cache_key": settings.OPENAI_PROMPT_CACHE_KEY,
        "stream": False,
    }
    _CHAT_COMPLETION_PARAMS: Final[dict[str, Any]] = {
        "max_completion_tokens": 4096,
        "temperature": 1,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
    }
    _DEVIS_TITLE_MODEL: Final[str] = "gpt-4"
    _DEVIS_TITLE_PARAMS: Final[dict[str, Any]] = {
        "max_tokens": 60,
        # Deterministic title: the same request must render the same devis
        # heading run after run.
        "temperature": 0,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
    }
    # Sub-model used only to rewrite forbidden generic labels
    # ("Autres travaux", "Divers", "Ensemble de prestations complémentaires"…)
    # into concrete BTP designations. Prices / quantities stay untouched.
    _LINE_REWRITE_MODEL: Final[str] = "gpt-4"
    _LINE_REWRITE_PARAMS: Final[dict[str, Any]] = {
        "max_tokens": 400,
        "temperature": 0,
        "top_p": 1,
        "presence_penalty": 0,
        "stream": False,
        "seed": 42,
    }
    _CHAT_HISTORY_MAX_MESSAGES: Final[int] = 6
    _CHAT_HISTORY_MESSAGE_MAX_CHARS: Final[int] = 700

    # How many Stage-2 attempts before giving up. 1 initial + (N-1) retries.
    # Each retry passes the previous error back to the model so it can
    # self-correct.
    _STAGE2_MAX_ATTEMPTS: Final[int] = 2

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._model: str = model or settings.OPENAI_MODEL
        self._chatbot_model: str = settings.OPENAI_CHATBOT_MODEL
        self._mobile_model: str = settings.OPENAI_MOBILE_MODEL
        self._landing_model: str = settings.OPENAI_LANDING_MODEL
        # Devis-generation params, adapted to the model family:
        # * reasoning models (gpt-5 / o*): temperature is locked to 1 by the
        #   API, but reasoning effort is configurable — "minimal" cuts latency
        #   for this structured-extraction task.
        # * sampling models (gpt-4o...): pin the sampling (temperature 0 +
        #   fixed seed) so the same prompt maps to the same packs run after
        #   run, on any machine — the engine being deterministic, the whole
        #   devis becomes reproducible.
        self._devis_params: dict[str, Any] = dict(self._COMPLETION_PARAMS)
        if self._model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
            self._devis_params["reasoning_effort"] = settings.OPENAI_REASONING_EFFORT
        else:
            self._devis_params["temperature"] = 0
            self._devis_params["seed"] = 42
        self._client: AsyncOpenAI = AsyncOpenAI(
            api_key=api_key or settings.OPENAI_API_KEY,
            base_url=base_url or "https://api.openai.com/v1",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def aclose(self) -> None:
        """Release the underlying httpx client. Call on app shutdown."""
        await self._client.close()


    # ------------------------------------------------------------------
    # Low-level call
    # ------------------------------------------------------------------
    async def _chat(
        self,
        system_prompt: str,
        user_text: str,
        *,
        completion_params: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Single chat completion call with the mandated parameters.

        Parameters
        ----------
        response_format
            Optional OpenAI ``response_format`` dict.  When set to a
            ``{"type": "json_schema", ...}`` payload, the API enforces
            valid JSON output at the decoding layer — no post-hoc
            healing needed.
        """
        params = dict(completion_params or self._devis_params)
        if response_format is not None:
            params["response_format"] = response_format
        started_at = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                **params,
            )
        except APIError as exc:
            elapsed_ms = (perf_counter() - started_at) * 1000
            logger.exception("OpenAI API call failed after %.0f ms.", elapsed_ms)
            raise AIServiceError(f"OpenAI API error: {exc}") from exc

        elapsed_ms = (perf_counter() - started_at) * 1000
        usage = getattr(response, "usage", None)
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        completion_details = getattr(usage, "completion_tokens_details", None)
        logger.info(
            (
                "OpenAI devis completion model=%s elapsed_ms=%.0f "
                "service_tier=%s prompt_tokens=%s cached_tokens=%s "
                "completion_tokens=%s reasoning_tokens=%s total_tokens=%s"
            ),
            self._model,
            elapsed_ms,
            getattr(response, "service_tier", None),
            getattr(usage, "prompt_tokens", None),
            getattr(prompt_details, "cached_tokens", None),
            getattr(usage, "completion_tokens", None),
            getattr(completion_details, "reasoning_tokens", None),
            getattr(usage, "total_tokens", None),
        )

        if not response.choices:
            raise AIServiceError("OpenAI returned no choices.")

        content = response.choices[0].message.content
        if not content:
            raise AIServiceError("OpenAI returned an empty completion.")
        return content

    def _compact_chat_history(
        self,
        history: list[ChatMessage] | None,
    ) -> list[dict[str, str]]:
        """Keep recent chat context small enough for fast, grounded replies."""
        if not history:
            return []

        compacted: list[dict[str, str]] = []
        for msg in history[-self._CHAT_HISTORY_MAX_MESSAGES:]:
            content = msg.content.strip()
            if not content:
                continue
            compacted.append(
                {
                    "role": msg.role,
                    "content": _short(content, self._CHAT_HISTORY_MESSAGE_MAX_CHARS),
                }
            )
        return compacted

    # ------------------------------------------------------------------
    # (Obsolete _detect_trades and _generate_devis methods removed for V2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def generate_quote(
        self,
        user_text: str,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Run the full two-stage pipeline and return the parsed devis ``dict``.

        Non-streaming entry point. Use :meth:`generate_quote_stream` to
        observe progress as the pipeline runs.

        Raises
        ------
        InvalidBuildingRequestError
            When Stage 1 decides the user's request is not a building request.
        AIServiceError
            On any transport / provider-side failure.
        JSONHealingError
            When the AI JSON cannot be parsed even after auto-healing.
        """
        return await self._run_pipeline(user_text, db, on_progress=None)

    async def generate_trade_line(
        self,
        job_corp: str,
        db: AsyncSession,
        *,
        limit: int,
    ) -> dict[str, Any]:
        """Return a LIST of representative billable prestations for a corps de métier.

        Powers ``POST /api/v1/trade-line/generate``. Fast pipeline:

        1. Fuzzy-load response-ready catalog rows for ``job_corp`` from
           ``trades`` / ``trade_services`` and return them directly.
        2. If the catalog has no match, return deterministic BTP reference
           items for known trades.
        3. If neither source can classify the input as BTP, raise
           :class:`InvalidBuildingRequestError` so the router returns 400.
        """
        job_corp = job_corp.strip()
        if not job_corp:
            raise ValueError("`job_corp` must not be empty.")
        if limit <= 0:
            raise ValueError("`limit` must be a positive integer.")

        catalog_items = await build_trade_line_items(
            db, job_corp=job_corp, limit=limit
        )
        if catalog_items:
            logger.info(
                "Trade-line fast path used for job_corp=%r (%d items).",
                job_corp,
                len(catalog_items),
            )
            return {
                "job_corp": job_corp,
                "count": len(catalog_items),
                "items": catalog_items,
            }

        reference_items = build_reference_trade_line_items(job_corp, limit=limit)
        if reference_items:
            logger.info(
                "Trade-line reference fallback used for job_corp=%r (%d items).",
                job_corp,
                len(reference_items),
            )
            return {
                "job_corp": job_corp,
                "count": len(reference_items),
                "items": reference_items,
            }

        raise InvalidBuildingRequestError(
            f"{job_corp!r} is not a recognised building trade."
        )

    async def generate_chat_response(
        self,
        user_text: str,
        history: list[ChatMessage] | None = None,
    ) -> tuple[str, TokenUsage]:
        """Run the chatbot pipeline and return ``(text, token_usage)``.

        The system prompt is assembled dynamically:

        * The core persona (``CHATBOT_SYSTEM_BASE``) is **always** included.
        * UX module guides are injected **only** when the user's question
          is about app navigation (detected by keyword classification).
        * Previous conversation turns are prepended so the model has
          multi-turn context.
        """
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        compact_history = self._compact_chat_history(history)

        # --- 1. Classify intent (zero-cost keyword scan) ---
        relevant_modules = classify_chat_intent(user_text)
        if relevant_modules == {"assistant"} and compact_history:
            recent_user_text = " ".join(
                msg["content"] for msg in compact_history if msg["role"] == "user"
            )
            history_modules = classify_chat_intent(recent_user_text)
            specific_history_modules = history_modules - {"assistant"}
            if specific_history_modules:
                relevant_modules = relevant_modules | specific_history_modules

        static_response = build_chatbot_static_response(relevant_modules)
        if static_response:
            logger.debug(
                "Chat static response used for UX modules=%s",
                relevant_modules,
            )
            return static_response, dict(ZERO_USAGE)  # type: ignore[return-value]

        system_prompt = build_chatbot_system_prompt(relevant_modules or None)

        logger.debug(
            "Chat intent: UX modules=%s, prompt size=%d chars, history=%d messages",
            relevant_modules or "(none — BTP domain)",
            len(system_prompt),
            len(compact_history),
        )

        # --- 2. Assemble messages with history ---
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]
        messages.extend(compact_history)
        messages.append({"role": "user", "content": user_text})

        # --- 3. Call the model ---
        try:
            response = await self._client.chat.completions.create(
                model=self._chatbot_model,
                messages=messages,
                **self._CHAT_COMPLETION_PARAMS,
            )
        except APIError as exc:
            logger.exception("OpenAI chat call failed; returning local fallback.")
            return build_chatbot_provider_fallback_response(user_text), dict(ZERO_USAGE)  # type: ignore[return-value]

        if not response.choices:
            logger.warning("OpenAI returned no choices for chat; returning local fallback.")
            return build_chatbot_provider_fallback_response(user_text), _extract_usage(response)

        content = response.choices[0].message.content
        if not content:
            logger.warning("OpenAI returned empty chat completion; returning local fallback.")
            return build_chatbot_provider_fallback_response(user_text), _extract_usage(response)
        return content, _extract_usage(response)

    async def generate_landing_chat_response(
        self,
        user_text: str,
        history: list[ChatMessage] | None = None,
    ) -> tuple[str, TokenUsage]:
        """Return ``(text, token_usage)`` for the landing-page chatbot.

        This bot is intentionally narrower than the in-app assistant: it can
        explain the product and help visitors choose between the public offers.
        """
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        compact_history = self._compact_chat_history(history)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": LANDING_CHATBOT_SYSTEM_PROMPT},
        ]
        messages.extend(compact_history)
        messages.append({"role": "user", "content": user_text})

        try:
            response = await self._client.chat.completions.create(
                model=self._landing_model,
                messages=messages,
                **self._CHAT_COMPLETION_PARAMS,
            )
        except APIError:
            logger.exception("OpenAI landing chat call failed; returning local fallback.")
            return build_landing_chatbot_provider_fallback_response(user_text), dict(ZERO_USAGE)  # type: ignore[return-value]

        if not response.choices:
            logger.warning(
                "OpenAI returned no choices for landing chat; returning local fallback."
            )
            return build_landing_chatbot_provider_fallback_response(user_text), _extract_usage(response)

        content = response.choices[0].message.content
        if not content:
            logger.warning(
                "OpenAI returned empty landing chat completion; returning local fallback."
            )
            return build_landing_chatbot_provider_fallback_response(user_text), _extract_usage(response)
        return content, _extract_usage(response)

    async def generate_mobile_chat_response(
        self,
        user_text: str,
        history: list[ChatMessage] | None = None,
    ) -> tuple[str, TokenUsage]:
        """Return ``(text, token_usage)`` for the mobile-app chatbot."""
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        compact_history = self._compact_chat_history(history)
        messages: list[dict[str, str]] = [
            {"role": "system", "content": MOBILE_CHATBOT_SYSTEM_PROMPT},
        ]
        messages.extend(compact_history)
        messages.append({"role": "user", "content": user_text})

        try:
            response = await self._client.chat.completions.create(
                model=self._mobile_model,
                messages=messages,
                **self._CHAT_COMPLETION_PARAMS,
            )
        except APIError:
            logger.exception("OpenAI mobile chat call failed; returning local fallback.")
            return build_mobile_chatbot_provider_fallback_response(user_text), dict(ZERO_USAGE)  # type: ignore[return-value]

        if not response.choices:
            logger.warning(
                "OpenAI returned no choices for mobile chat; returning local fallback."
            )
            return build_mobile_chatbot_provider_fallback_response(user_text), _extract_usage(response)

        content = response.choices[0].message.content
        if not content:
            logger.warning(
                "OpenAI returned empty mobile chat completion; returning local fallback."
            )
            return build_mobile_chatbot_provider_fallback_response(user_text), _extract_usage(response)
        return content, _extract_usage(response)

    def _normalise_devis_title(self, title: str | None) -> str:
        """Force a short title beginning exactly with ``Travaux de``."""
        cleaned = (title or "").strip().strip("\"'`“”‘’")
        cleaned = " ".join(cleaned.split())
        if not cleaned:
            cleaned = "rénovation"

        prefix = "Travaux de "
        lowered = cleaned.lower()
        if lowered.startswith(prefix.lower()):
            body = cleaned[len(prefix) :].strip()
        elif lowered.startswith("travaux d'"):
            body = cleaned[len("travaux d'") :].strip()
        elif lowered.startswith("travaux de"):
            body = cleaned[len("travaux de") :].strip()
        elif lowered.startswith("travaux "):
            body = cleaned[len("travaux ") :].strip()
        else:
            body = cleaned

        body = body.strip(" .,:;-/")
        body = re.sub(
            r"\b\d+(?:[.,]\d+)?\s*(?:m2|m²|m3|m³|ml|cm|mm|m|kg|l|u|unités?|pieces?|pièces?)\b",
            "",
            body,
            flags=re.IGNORECASE,
        )
        body = re.sub(r"\b\d+(?:[.,]\d+)?\b", "", body)
        body = re.sub(r"\s+", " ", body).strip(" .,:;-/")
        if not body:
            body = "rénovation"
        return f"{prefix}{body[:80]}".rstrip(" .,:;-/")

    async def generate_devis_title(self, user_text: str) -> str:
        """Generate a short French devis title in parallel with the devis."""
        fallback = self._normalise_devis_title("rénovation")
        try:
            response = await self._client.chat.completions.create(
                model=self._DEVIS_TITLE_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Tu génères uniquement un titre court en français "
                            "pour un devis BTP. Le titre doit commencer "
                            "exactement par \"Travaux de \". Retourne uniquement "
                            "le titre, sans guillemets, sans explication, sans "
                            "ponctuation finale. Fais un titre simple et générique: "
                            "garde seulement le type de travaux et la pièce ou le "
                            "métier principal. Supprime les surfaces, dimensions, "
                            "quantités, nombres, matériaux trop précis, gammes et "
                            "détails techniques. Exemple: \"Rénovation cuisine 20m2\" "
                            "devient \"Travaux de rénovation cuisine\"."
                        ),
                    },
                    {"role": "user", "content": user_text},
                ],
                **self._DEVIS_TITLE_PARAMS,
            )
        except Exception:
            logger.exception("OpenAI title generation failed; using fallback title.")
            return fallback

        if not response.choices:
            logger.warning("OpenAI returned no choices for devis title.")
            return fallback

        content = response.choices[0].message.content
        return self._normalise_devis_title(content)

    async def _rewrite_forbidden_line_labels(
        self,
        blocs: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rewrite forbidden generic labels via GPT-4; keep prices/qty intact.

        Detects lines whose description matches ``Autres`` / ``Divers`` /
        ``prestations complémentaires`` / balancing wording, then asks GPT-4
        to produce a concrete BTP designation. Failures fall back to a local
        deterministic rewrite so the devis never ships the forbidden wording.
        """
        targets: list[tuple[dict[str, Any], str]] = []
        for bloc in blocs:
            for lot in bloc.get("lots", []) or []:
                for ligne in lot.get("lignes", []) or []:
                    desc = str(ligne.get("description") or "")
                    if _is_forbidden_line_description(desc):
                        targets.append((ligne, desc))

        if not targets:
            return blocs

        # Serve already-rewritten phrases from the process cache first.
        pending: list[tuple[dict[str, Any], str]] = []
        for ligne, desc in targets:
            cached = _line_rewrite_cache.get(desc)
            if cached:
                ligne["description"] = cached
            else:
                pending.append((ligne, desc))

        if not pending:
            return blocs

        unique_descs = list(dict.fromkeys(desc for _, desc in pending))
        numbered = "\n".join(
            f"{idx}. {desc}" for idx, desc in enumerate(unique_descs, 1)
        )
        system_prompt = (
            "Tu reformules des libellés de lignes de devis BTP en français. "
            "Chaque libellé d'entrée est un libellé générique interdit "
            "(Autres travaux, Divers, Ensemble de prestations complémentaires, "
            "ajustement forfaitaire, montant d'équilibrage…). "
            "Remplace-le par une désignation technique concrète, professionnelle "
            "et spécifique au métier mentionné dans le libellé. "
            "Interdictions absolues dans ta réponse: les mots Autres, Divers, "
            "complémentaires, équilibrage, ajustement forfaitaire, ensemble de "
            "prestations. "
            "Ne change pas le sens métier. Pas de prix, pas de quantité, pas de "
            "markdown, pas d'explication. "
            "Réponds UNIQUEMENT par un JSON objet: "
            "{\"rewrites\": [{\"i\": 1, \"label\": \"...\"}, ...]} "
            "avec un item par libellé numéroté, dans le même ordre."
        )
        try:
            response = await self._client.chat.completions.create(
                model=self._LINE_REWRITE_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            "Reformule ces libellés interdits:\n" + numbered
                        ),
                    },
                ],
                # gpt-4 does not support response_format=json_object; we still
                # ask for pure JSON and parse it with the healer.
                **self._LINE_REWRITE_PARAMS,
            )
            raw = (response.choices[0].message.content or "").strip()
            parsed = clean_and_parse_json(raw)
            rewrites = parsed.get("rewrites") if isinstance(parsed, dict) else None
            mapping: dict[str, str] = {}
            if isinstance(rewrites, list):
                for item in rewrites:
                    if not isinstance(item, dict):
                        continue
                    try:
                        idx = int(item.get("i"))
                    except (TypeError, ValueError):
                        continue
                    label = str(item.get("label") or "").strip().strip("\"'`")
                    if 1 <= idx <= len(unique_descs) and label:
                        # Reject if the model still produced forbidden wording.
                        if _is_forbidden_line_description(label):
                            label = _local_rewrite_forbidden_label(unique_descs[idx - 1])
                        mapping[unique_descs[idx - 1]] = label
            for original in unique_descs:
                if original not in mapping:
                    mapping[original] = _local_rewrite_forbidden_label(original)
            logger.info(
                "Rewrote %d forbidden line label(s) via GPT-4.", len(mapping)
            )
        except Exception:
            logger.exception(
                "GPT-4 line-label rewrite failed; using local fallback."
            )
            mapping = {
                original: _local_rewrite_forbidden_label(original)
                for original in unique_descs
            }

        for original, rewritten in mapping.items():
            _line_rewrite_cache[original] = rewritten

        for ligne, desc in pending:
            ligne["description"] = mapping.get(
                desc, _local_rewrite_forbidden_label(desc)
            )
        return blocs

    async def generate_quote_stream(
        self,
        user_text: str,
        db: AsyncSession,
    ) -> AsyncIterator[StreamEvent]:
        """Run the pipeline and yield UI-friendly events as they happen.

        The generator yields exactly :data:`PROGRESS_STEPS` ``progress`` events
        in order, then a single terminal event:

        * ``{"type": "result", "data": <devis>}`` on success;
        * ``{"type": "error",  "status": <int>, "detail": <str>}`` otherwise.

        Cancelling the consumer (e.g. when the HTTP client disconnects)
        cancels the background pipeline task cleanly.
        """
        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        title_task = asyncio.create_task(self.generate_devis_title(user_text))

        async def _on_progress(step: int, label: str) -> None:
            await queue.put(
                StreamEvent(
                    type="progress",
                    step=step,
                    total=len(PROGRESS_STEPS),
                    label=label,
                )
            )

        async def _runner() -> None:
            try:
                devis = await self._run_pipeline(
                    user_text, db, on_progress=_on_progress
                )
            except InvalidBuildingRequestError as exc:
                await queue.put(StreamEvent(type="error", status=400, detail=str(exc)))
            except AIServiceError as exc:
                await queue.put(StreamEvent(type="error", status=503, detail=str(exc)))
            except (JSONHealingError, UnrepairableDevisError) as exc:
                await queue.put(StreamEvent(type="error", status=502, detail=str(exc)))
            except ValidationError as exc:
                await queue.put(
                    StreamEvent(
                        type="error",
                        status=502,
                        detail=f"DevisResponse validation failed: {exc.errors()[:3]}",
                    )
                )
            except Exception as exc:  # pragma: no cover - last-resort safety net
                logger.exception("Unexpected error in streaming pipeline.")
                await queue.put(StreamEvent(type="error", status=500, detail=str(exc)))
            else:
                await queue.put(StreamEvent(type="result", data=devis))
                title = await title_task
                await queue.put(StreamEvent(type="title", title=title))
            finally:
                if not title_task.done():
                    title_task.cancel()
                await queue.put(None)  # sentinel

        runner_task = asyncio.create_task(_runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not runner_task.done():
                runner_task.cancel()
                try:
                    await runner_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------
    # Internal pipeline shared by both entry points.
    # ------------------------------------------------------------------
    async def _run_pipeline(
        self,
        user_text: str,
        db: AsyncSession,
        *,
        on_progress: ProgressCallback | None,
    ) -> dict[str, Any]:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("`user_text` must not be empty.")

        # Step 1: BTP Guardrail
        if on_progress is not None:
            await on_progress(1, PROGRESS_STEPS[0])
        
        validate_btp_context(user_text)
        
        # Load price maps and packs (cached in RAM after first call)
        price_map, concept_map, metier_medians = await get_cached_price_map(db)
        exact_map, pack_list = await get_cached_packs_map(db)
        
        # Step 2: Generation (Semantic Mapping)
        if on_progress is not None:
            await on_progress(2, PROGRESS_STEPS[1])
            
        # DEPANNAGE-ONLY: scope the catalog to the relevant repair packs so the
        # AI can pick the correct DEP-* code. Every other request falls through
        # to the original full-catalog builder below — unchanged.
        if _is_depannage_request(user_text):
            catalog_str = _build_depannage_catalog(pack_list, user_text)
        else:
            catalog_by_metier = {}
            for p in pack_list:
                cm = p["corps_metier"]
                if cm not in catalog_by_metier:
                    catalog_by_metier[cm] = []
                catalog_by_metier[cm].append(f"[{p['code_pack']}] {p['nom_pack']}")

            catalog_lines = []
            for cm, packs in catalog_by_metier.items():
                catalog_lines.append(f"- Métier: {cm}")
                for p in packs:
                    catalog_lines.append(f"  {p}")
            catalog_str = "\n".join(catalog_lines)

        prompt = SYSTEM_PROMPT_GENERATOR.replace("{catalog}", catalog_str)

        # Metier hints ride along the user message so the (large) system
        # prompt stays byte-stable and prompt-cacheable.
        hints = _detect_metier_hints(user_text)
        user_message = user_text
        if hints:
            user_message = (
                f"{user_text}\n\n"
                f"(Métiers les plus probables pour cette demande : {', '.join(hints)})"
            )

        # Deterministic replay: an identical request (case/accent/whitespace
        # insensitive) reuses the cached semantic payload instead of
        # re-sampling the LLM, so the same prompt always yields the same
        # devis regardless of client, account or machine.
        cache_key = _semantic_cache_key(user_text)
        cached_payload = _semantic_cache_get(cache_key)
        if cached_payload is not None:
            logger.info("Stage-2 semantic cache hit — deterministic replay.")
            parsed = cached_payload
            lots = _sanitize_ai_lots(parsed.get("lots", []))
            return await self._finalize_devis(
                parsed, lots, user_text, on_progress=on_progress,
                price_map=price_map, concept_map=concept_map,
                metier_medians=metier_medians,
                exact_map=exact_map, pack_list=pack_list,
            )

        # Stage-2 with one self-correcting retry: a payload with zero usable
        # lots (or unparseable JSON) gets a second chance with the error
        # context appended, before falling back to the minimal generic lot.
        parsed: dict[str, Any] = {}
        lots: list[dict[str, Any]] = []
        last_error = ""
        for attempt in range(1, self._STAGE2_MAX_ATTEMPTS + 1):
            message = user_message
            if attempt > 1 and last_error:
                message = (
                    f"{user_message}\n\n"
                    f"IMPORTANT — ta réponse précédente était invalide ({last_error}). "
                    "Retourne un JSON conforme avec au moins un lot contenant un pack."
                )
            try:
                raw = await self._chat(
                    prompt,
                    message,
                    response_format=_DEVIS_RESPONSE_FORMAT,
                )
                # Structured Outputs guarantees valid JSON, but we keep the
                # healer as a safety net — it's a no-op when input is clean.
                parsed = clean_and_parse_json(raw)
            except JSONHealingError as exc:
                last_error = _short(str(exc), 150)
                logger.warning("Stage-2 attempt %d unparseable: %s", attempt, last_error)
                if attempt >= self._STAGE2_MAX_ATTEMPTS:
                    raise
                continue

            if parsed.get("is_btp") is False:
                raise InvalidBuildingRequestError(
                    "La demande ne concerne pas des travaux de bâtiment."
                )

            lots = _sanitize_ai_lots(parsed.get("lots", []))
            if lots:
                break
            last_error = "aucun lot exploitable dans la réponse"
            logger.warning("Stage-2 attempt %d returned no usable lots.", attempt)

        # Only successful, usable payloads enter the deterministic cache.
        if lots:
            _semantic_cache_put(cache_key, parsed)

        return await self._finalize_devis(
            parsed, lots, user_text, on_progress=on_progress,
            price_map=price_map, concept_map=concept_map,
            metier_medians=metier_medians,
            exact_map=exact_map, pack_list=pack_list,
        )

    async def _finalize_devis(
        self,
        parsed: dict[str, Any],
        lots: list[dict[str, Any]],
        user_text: str,
        *,
        on_progress: ProgressCallback | None,
        price_map: dict[str, Any],
        concept_map: dict[str, Any],
        metier_medians: dict[str, Any],
        exact_map: dict[str, Any],
        pack_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Deterministic tail of the pipeline: engine, totals, assembly."""
        # Step 3: Calculation (Deterministic engine)
        if on_progress is not None:
            await on_progress(3, PROGRESS_STEPS[2])

        logger.info(
            "AI returned %d lots: %s", len(lots), [l.get("metier", "?") for l in lots]
        )
        client_type = parsed.get("client_type", "particulier")
        project_nature = parsed.get("project_nature", "renovation")

        surface_m2 = extract_surface_m2(user_text)

        # Last-resort fallback: always emit a devis instead of returning empty.
        if not lots:
            logger.warning("AI returned 0 lots after retry — injecting fallback lot.")
            lots = [{
                "lot_key": "LOT_01",
                "metier": "Travaux généraux",
                "zone": "interieur",
                "packs": [{
                    "id": "TRAVAUX_GENERAUX",
                    "type": "PRESTATION",
                    "quantite": surface_m2 if surface_m2 else 1,
                    "quantite_type": "surface_m2" if surface_m2 else "non_specifie",
                    "source_qte": "fallback",
                }],
            }]

        four_blocks = process_ai_lots(
            lots, 
            client_type, 
            project_nature, 
            surface_m2=surface_m2,
            user_text=user_text,
            price_map=price_map, 
            concept_map=concept_map,
            metier_medians=metier_medians,
            packs_maps=(exact_map, pack_list)
        )

        # Post-pass: rewrite forbidden generic labels ("Autres travaux",
        # "Divers", "prestations complémentaires"…) via GPT-4. Prices,
        # quantities and structure stay exactly as the engine produced them.
        four_blocks = await self._rewrite_forbidden_line_labels(four_blocks)
        
        from datetime import datetime, timedelta, timezone

        # Flat lines for global totals
        flat_lines = []
        for b in four_blocks:
            for lot in b.get("lots", []):
                flat_lines.extend(lot.get("lignes", []))
                
        totals = calculate_global_totals(flat_lines)
        
        now = datetime.now(timezone.utc)

        devis = {
            "date": now.isoformat(),
            "validite": (now + timedelta(days=30)).isoformat(),
            "duree": estimate_duration_days(totals["total_ht"], four_blocks),
            "montant_ttc": totals["total_ttc"],
            "blocs": four_blocks,
        }

        if on_progress is not None:
            await on_progress(4, PROGRESS_STEPS[3])

        return devis


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
ai_service: AIService = AIService()


__all__ = [
    "AIService",
    "AIServiceError",
    "InvalidBuildingRequestError",
    "PROGRESS_STEPS",
    "StreamEvent",
    "TokenUsage",
    "UnrepairableDevisError",
    "ZERO_USAGE",
    "ai_service",
]
