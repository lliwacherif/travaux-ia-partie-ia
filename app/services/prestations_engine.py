import asyncio
import logging
import statistics
import unicodedata
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metier_rules import ALL_METIER_RULES, safe_eval_formula
from app.models.bpu_item import BpuItem
from app.models.pack_travaux import PackTravaux
import math
import re
import difflib

_OFFICIAL_TRADE_LABELS = [
    "Maçonnerie – Gros œuvre",
    "Maçonnerie – Second œuvre / Chapes / Enduits ciment",
    "Démolition – Curage – Dépose",
    "Terrassement – VRD – Assainissement",
    "Plâtrerie – Cloisons – Doublages – Faux plafonds",
    "Peinture – Finitions – Enduits décoratifs",
    "Plomberie – Sanitaire",
    "Chauffage – Chaudières – Radiateurs – Réseaux",
    "Chauffage ENR – PAC – Solaire thermique",
    "Climatisation – Ventilation – VMC",
    "Électricité – Courants forts",
    "Électricité – Courants faibles – Domotique – Réseaux",
    "Menuiserie intérieure",
    "Menuiserie extérieure",
    "Serrurerie – Métallerie",
    "Revêtements de sols",
    "Carrelage – Sols & Murs",
    "Isolation intérieure",
    "Isolation thermique extérieure (ITE)",
    "Calorifugeage",
    "Couverture – Toiture – Zinguerie",
    "Étanchéité – Toiture terrasse",
    "Façade – Ravalement – Enduits extérieurs",
    "Charpente bois – Ossature",
    "Charpente métallique",
    "Panneaux photovoltaïques",
    "Salle de bain – Aménagement complet",
    "Cuisine – Agencement sur mesure",
    "Installation de chantier – Logistique & Sécurité",
    "Dépannage & Interventions rapides"
]

def _normalize_trade_title(raw_metier: str) -> str:
    """Fuzzy match the raw AI metier to the closest official label."""
    # Try exact/case-insensitive match first
    raw_lower = raw_metier.lower().strip()
    for label in _OFFICIAL_TRADE_LABELS:
        if raw_lower in label.lower():
            return label
    
    # Fallback to fuzzy match
    matches = difflib.get_close_matches(raw_lower, [lbl.lower() for lbl in _OFFICIAL_TRADE_LABELS], n=1, cutoff=0.3)
    if matches:
        best_match_lower = matches[0]
        # Find original casing
        for label in _OFFICIAL_TRADE_LABELS:
            if label.lower() == best_match_lower:
                return label
    
    # If all fails, return a capitalized version of what AI said
    return f"{raw_metier.capitalize()}"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Price lookup helpers
# ---------------------------------------------------------------------------

# Maps short rule-keys from metier_rules to search keywords in bpu_items
# designations.  When _resolve_price receives e.g. "carrelage_m2", it strips
# the unit suffix to get "carrelage", then looks here for matching BPU items.
#
# IMPORTANT: Only map concepts that represent FULL OPERATIONS (Fourniture + Pose)
# where the BPU price per m²/ml/u is meaningful.  Do NOT map raw consumables
# here — use _MATERIAL_PRICES below instead.
_KEYWORD_TO_BPU_SEARCH: Dict[str, List[str]] = {
    # ── Carrelage ──
    "carrelage": ["carrelage", "grès cérame", "gres cerame"],
    "faience": ["faïence", "faience", "carrelage mural"],
    "ragreage": ["ragréage", "ragreage", "chape de ragréage"],
    "joint_carrelage": ["joint de carrelage", "jointement", "joint hydrofuge"],
    # ── Plâtrerie ──
    "surface": ["faux plafond", "plafond suspendu"],
    "placo": ["plaque de plâtre", "ba13", "plaque platre", "placo"],
    "ossature": ["ossature", "fourrure", "f530"],
    "cloison": ["cloison", "cloison alvéolaire", "cloison distribution"],
    "doublage": ["doublage", "doublage collé", "doublage isolant"],
    # ── Maçonnerie ──
    "beton": ["béton armé", "béton dosé", "béton", "dalle béton"],
    "treillis": ["treillis soudé"],
    "coffrage": ["coffrage"],
    "blocs": ["parpaing", "agglo creux"],
    "chainages": ["chaînage", "chainage"],
    "dalle": ["dalle", "dalle béton", "chape"],
    "chape": ["chape", "chape fluide", "chape ciment"],
    "enduit_ciment": ["enduit ciment", "enduit monocouche", "crépi"],
    "mur": ["mur porteur", "mur maçonné", "élévation de mur"],
    # ── Couverture / Toiture ──
    "toiture": ["toiture", "couverture", "réfection toiture", "refection toiture"],
    "tuiles": ["tuile", "tuiles mécaniques", "tuiles plates"],
    "zinguerie": ["zinguerie", "gouttière", "chéneau", "descente ep"],
    "ardoise": ["ardoise"],
    "ecran_sous_toiture": ["écran sous-toiture", "sous-toiture", "pare-pluie"],
    # ── Climatisation / Ventilation ──
    "climatisation": ["climatisation", "climatiseur", "monosplit", "mono-split"],
    "vmc": ["vmc", "ventilation", "ventilation mécanique"],
    "split": ["split", "monosplit", "multisplit"],
    "gainable": ["gainable", "climatisation gainable"],
    # ── Façade / Ravalement ──
    "ravalement": ["ravalement", "enduit extérieur"],
    "hydrofuge": ["hydrofuge", "imperméabilisant"],
    "antimousse": ["antimousse", "anti-mousse", "démoussage"],
    "facade": ["façade", "facade", "bardage"],
    # ── Isolation ──
    "ite": ["isolation thermique extérieure", "ite ", "fibre de bois"],
    "isolation": ["isolation", "isolant", "laine de verre", "laine de roche"],
    "combles": ["combles", "combles perdus", "soufflage"],
    # ── Cuisine ──
    "cuisine": ["cuisine", "agencement cuisine", "cuisine sur-mesure"],
    # ── Peinture ──
    "peinture": ["peinture", "enduit décoratif"],
    "enduit": ["enduit", "enduit de lissage", "enduit plâtre"],
    "lasure": ["lasure", "vernis", "vitrificateur"],
    # ── Plomberie / Sanitaire ──
    "plomberie": ["plomberie", "sanitaire", "robinetterie"],
    "salle": ["salle de bain", "douche", "baignoire"],
    "wc": ["wc", "toilette", "cuvette", "chasse d'eau"],
    "chauffe_eau": ["chauffe-eau", "ballon", "cumulus"],
    "evacuation": ["évacuation", "evacuation", "tout-à-l'égout"],
    # ── Électricité ──
    "electricite": ["électricité", "electricite", "tableau électrique"],
    "prise": ["prise électrique", "prise de courant"],
    "luminaire": ["luminaire", "point lumineux", "éclairage"],
    "interrupteur": ["interrupteur", "va-et-vient", "variateur"],
    # ── Menuiserie ──
    "menuiserie": ["menuiserie", "porte", "fenêtre", "volet"],
    "fenetre": ["fenêtre", "baie vitrée", "double vitrage"],
    "porte": ["porte", "bloc-porte", "huisserie"],
    "volet": ["volet", "volet roulant", "volet battant"],
    "parquet": ["parquet", "parquet flottant", "parquet massif"],
    # ── Terrassement ──
    "terrassement": ["terrassement", "vrd", "assainissement"],
    "fouille": ["fouille", "tranchée", "excavation"],
    # ── Démolition ──
    "demolition": ["démolition", "curage", "dépose"],
    "depose": ["dépose", "dépose soignée", "dépose et évacuation"],
    # ── Charpente ──
    "charpente": ["charpente", "ossature bois"],
    # ── Serrurerie ──
    "serrurerie": ["serrurerie", "métallerie", "garde-corps"],
    "portail": ["portail", "portillon", "clôture"],
    # ── Revêtements ──
    "revetement": ["revêtement", "parquet", "stratifié", "moquette"],
    "sol_souple": ["sol souple", "vinyle", "lino", "pvc"],
    # ── Étanchéité ──
    "etancheite": ["étanchéité", "toiture terrasse", "membrane"],
    # ── Chauffage ──
    "chauffage": ["chauffage", "chaudière", "radiateur", "pac", "pompe à chaleur"],
    "plancher_chauffant": ["plancher chauffant", "chauffage au sol"],
    # ── Photovoltaïque ──
    "photovoltaique": ["photovoltaïque", "panneau solaire"],
    # ── Dépannage ──
    "depannage": ["dépannage", "intervention rapide"],
    # ── Nettoyage / Finitions ──
    "nettoyage": ["nettoyage", "nettoyage fin de chantier"],
    "evacuation_gravats": ["évacuation gravats", "benne", "déchets"],
}

# ----- Raw material / consumable prices -----
# These are unit prices for INDIVIDUAL materials extracted from rules.
# They must NOT be matched against BPU operations (which are full F+P prices).
# Source: prix moyens IDF 2025 pour fournitures seules.
_MATERIAL_PRICES: Dict[str, float] = {
    # Carrelage consumables
    "colle_kg": 3.50,          # Colle à carrelage ~3.50€/kg
    "joint_kg": 4.00,          # Mortier joint ~4€/kg
    "croisillons_u": 0.15,     # Croisillons ~0.15€/pièce
    "primaire_l": 12.00,       # Primaire d'accrochage ~12€/l
    "profiles_ml": 8.00,       # Profilés d'angle alu ~8€/ml
    # Plâtrerie consumables
    "suspentes_u": 1.50,       # Suspente réglable ~1.50€/u
    "bandes_ml": 1.20,         # Bande à joint papier ~1.20€/ml
    "enduit_kg": 2.50,         # Enduit à joint ~2.50€/kg
    "isolant_m2": 8.00,        # Laine minérale 45mm ~8€/m²
    "rail_ml": 3.50,           # Rail R48 ou montant M48 ~3.50€/ml
    # Maçonnerie consumables
    "polyane_m2": 1.20,        # Film polyane ~1.20€/m²
    "mortier_m3": 95.00,       # Mortier prêt à l'emploi ~95€/m³
    "blocs_u": 1.80,           # Parpaing creux 20x20x50 ~1.80€/u
}

# Static fallback prices — LAST resort when no DB match AND no metier median.
# Values are the measured MEDIANS of the bpu_items catalog (3 330 priced rows),
# so even the worst-case fallback stays market-realistic.
_STATIC_FALLBACK_PRICES: Dict[str, float] = {
    "m²": 45.0,
    "m³": 120.0,
    "ml": 42.0,
    "kg": 12.0,
    "u": 200.0,
    "l": 10.0,
    "forfait": 380.0,
    "ens": 380.0,
    "lot": 380.0,
    "h": 70.0,
    "j": 85.0,
    "t": 550.0,
}

# Type alias for the metier-aware median price map:
# normalised_metier_key → { unit → median_price }
MetierMedianMap = Dict[str, Dict[str, float]]


def _get_fallback_price(
    unit: str,
    *,
    corps_metier: str = "",
    metier_medians: Optional[MetierMedianMap] = None,
) -> float:
    """Return a fallback price, preferring the metier-specific BPU median.

    Resolution:
    1. Metier-specific median price for the requested unit
    2. Metier-specific median across all units (any-unit average)
    3. Static fallback price by unit
    """
    if metier_medians and corps_metier:
        norm_metier = _normalize_key(corps_metier)
        unit_medians = metier_medians.get(norm_metier)
        if unit_medians:
            # Prefer exact unit match
            p = unit_medians.get(unit.lower().strip())
            if p and p > 0:
                return p
            # Any-unit fallback within this metier
            vals = [v for v in unit_medians.values() if v > 0]
            if vals:
                return statistics.median(vals)
    return _STATIC_FALLBACK_PRICES.get(unit, 50.0)


def _normalize_key(text: str) -> str:
    """Normalize a designation or line_key for fuzzy matching.

    Strips accents, lowercases, removes unit suffixes (_m2, _ml, …),
    collapses whitespace and non-alphanum chars to underscores.
    """
    # Remove accents
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Lowercase
    ascii_text = ascii_text.lower().strip()
    # Remove common unit suffixes used in rule keys
    ascii_text = re.sub(r'_(?:m2|m3|ml|kg|u|l)$', '', ascii_text)
    # Collapse non-alphanum to single underscores
    ascii_text = re.sub(r'[^a-z0-9]+', '_', ascii_text).strip('_')
    return ascii_text


def _extract_concept(line_key: str) -> str:
    """Extract the material concept from a rule line_key.

    Examples:
        'carrelage_m2' -> 'carrelage'
        'colle_kg'     -> 'colle'
        'suspentes_u'  -> 'suspentes'
        'surface_m2'   -> 'surface'
    """
    return re.sub(r'_(?:m2|m3|ml|kg|u|l)$', '', line_key.lower().strip())


# Widest acceptable ratio between a resolved price and the metier/unit median.
# Only guards keyword-resolved prices against absurd bindings; curated pack
# prices are trusted and never clamped.
_PRICE_SANITY_BAND: float = 8.0


def _sanity_clamp(
    price: float,
    unit: str,
    *,
    corps_metier: str = "",
    metier_medians: Optional[MetierMedianMap] = None,
    context: str = "",
) -> float:
    """Clamp a keyword-resolved price into a sane band around the metier median."""
    if not metier_medians or not corps_metier or price <= 0:
        return price
    unit_medians = metier_medians.get(_normalize_key(corps_metier))
    if not unit_medians:
        return price
    median = unit_medians.get(unit.lower().strip())
    if not median or median <= 0:
        return price
    lo, hi = median / _PRICE_SANITY_BAND, median * _PRICE_SANITY_BAND
    if price < lo or price > hi:
        clamped = round(min(max(price, lo), hi), 2)
        logger.warning(
            "[PRICE_CLAMP] %s: %.2f€/%s outside [%.2f, %.2f] for metier %r → %.2f€",
            context, price, unit, lo, hi, corps_metier, clamped,
        )
        return clamped
    return price


def _resolve_price(
    key: str,
    unit: str,
    price_map: Optional[Dict[str, float]],
    *,
    concept_map: Optional[Dict[str, Dict[str, float]]] = None,
    corps_metier: str = "",
    metier_medians: Optional[MetierMedianMap] = None,
) -> float:
    """Look up a price in the preloaded maps, falling back intelligently.

    Resolution order:
    1. Direct material price for known consumables (key with unit suffix)
    2. Exact match in ``price_map`` by normalised key
    3. Concept match in ``concept_map`` — tries full concept, then each word
    4. Metier-specific BPU median price (calculated from real market data)
    5. Static fallback price by unit (absolute last resort)
    """
    # 1. Known consumable material?
    key_lower = key.lower().strip()
    material_price = _MATERIAL_PRICES.get(key_lower)
    if material_price is not None:
        return material_price

    # 2. Exact designation / slug match in DB
    if price_map:
        norm = _normalize_key(key)
        price = price_map.get(norm)
        if price is not None and price > 0:
            return price

    # 3. Concept-based resolution
    if concept_map:
        concept = _extract_concept(key)

        def _try_concept(c: str) -> Optional[float]:
            unit_prices = concept_map.get(c)
            if not unit_prices:
                return None
            # Prefer matching unit
            p = unit_prices.get(unit)
            if p and p > 0:
                return p
            # Any available price
            for p in unit_prices.values():
                if p > 0:
                    return p
            return None

        # Try full concept first (e.g. "toiture_tuiles" — unlikely but possible)
        price = _try_concept(concept)
        if price:
            return _sanity_clamp(
                price, unit,
                corps_metier=corps_metier, metier_medians=metier_medians,
                context=f"concept:{concept}",
            )

        # Split on underscores and try each word (e.g. "toiture", "tuiles")
        # Try longer words first — they are more specific.
        words = [w for w in concept.split("_") if len(w) > 2]
        words.sort(key=len, reverse=True)
        for word in words:
            price = _try_concept(word)
            if price:
                return _sanity_clamp(
                    price, unit,
                    corps_metier=corps_metier, metier_medians=metier_medians,
                    context=f"concept:{word}",
                )

    # 4 & 5. Metier median or static fallback
    fallback = _get_fallback_price(
        unit, corps_metier=corps_metier, metier_medians=metier_medians
    )
    has_metier_median = (
        metier_medians
        and corps_metier
        and _normalize_key(corps_metier) in metier_medians
    )
    logger.warning(
        "[FALLBACK_USED] key='%s' unit=%s metier='%s' → %.2f€ (%s)",
        key,
        unit,
        corps_metier,
        fallback,
        "metier_median" if has_metier_median else "static_fallback",
    )
    return fallback


# Type alias for the full price tuple returned by load_price_map
PriceMapTuple = tuple[Dict[str, float], Dict[str, Dict[str, float]], MetierMedianMap]


async def load_price_map(db: AsyncSession) -> PriceMapTuple:
    """Pre-load all real prices from the ``bpu_items`` table.

    Returns a tuple of:
    1. ``price_map`` — normalised designation/slug → price
    2. ``concept_map`` — material concept → {unit: price}
    3. ``metier_medians`` — normalised corps_metier → {unit: median_price}

    The concept_map enables matching short rule keys like ``carrelage_m2``
    to real BPU prices by extracting the keyword ("carrelage") and
    searching for items whose designation contains that keyword.

    The metier_medians map provides intelligent fallback prices by
    computing the median BPU price per (corps_metier, unit) combination.
    """
    stmt = select(
        BpuItem.designation,
        BpuItem.slug,
        BpuItem.prix_unitaire_ht,
        BpuItem.unite,
        BpuItem.corps_metier,
    ).where(BpuItem.prix_unitaire_ht > 0)

    rows = (await db.execute(stmt)).all()

    price_map: Dict[str, float] = {}
    # concept prices are accumulated then reduced to the MEDIAN per unit, so
    # a single expensive/cheap outlier row can no longer define the concept.
    _concept_prices: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    # Collect all prices per (metier, unit) for median calculation
    _metier_unit_prices: Dict[str, Dict[str, List[float]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for designation, slug, price, unit, corps_metier in rows:
        # 1. Index by slug
        if slug:
            price_map[slug] = price
        # 2. Index by normalised designation
        norm = _normalize_key(designation)
        if norm not in price_map:
            price_map[norm] = price

        # 3. Build concept map by scanning keywords
        desig_lower = designation.lower()
        for concept, keywords in _KEYWORD_TO_BPU_SEARCH.items():
            for kw in keywords:
                if kw.lower() in desig_lower:
                    _concept_prices[concept][unit.lower().strip()].append(price)
                    break  # one keyword match is enough

        # 4. Accumulate prices for metier median calculation
        norm_metier = _normalize_key(corps_metier)
        norm_unit = unit.lower().strip()
        _metier_unit_prices[norm_metier][norm_unit].append(price)

    # Reduce concept price lists to medians
    concept_map: Dict[str, Dict[str, float]] = {
        concept: {
            u: round(statistics.median(prices), 2)
            for u, prices in unit_prices.items()
            if prices
        }
        for concept, unit_prices in _concept_prices.items()
    }

    # Compute medians per (metier, unit)
    metier_medians: MetierMedianMap = {}
    for metier_key, unit_prices_dict in _metier_unit_prices.items():
        metier_medians[metier_key] = {
            u: round(statistics.median(prices), 2)
            for u, prices in unit_prices_dict.items()
            if prices
        }

    logger.info(
        "Loaded %d price keys + %d concept entries + %d metier median groups "
        "from bpu_items (%d rows).",
        len(price_map),
        sum(len(v) for v in concept_map.values()),
        len(metier_medians),
        len(rows),
    )
    return price_map, concept_map, metier_medians

async def load_packs_map(db: AsyncSession) -> tuple[Dict[str, dict], List[dict]]:
    """Pre-load all active packs from the packs_travaux table."""
    stmt = select(PackTravaux).where(PackTravaux.is_active == True)
    rows = (await db.execute(stmt)).scalars().all()
    
    exact_map: Dict[str, dict] = {}
    pack_list: List[dict] = []
    
    for p in rows:
        pack_data = {
            "code_pack": p.code_pack,
            "nom_pack": p.nom_pack,
            "pack_json": p.pack_json,
            "corps_metier": p.corps_metier,
            # Additive field — only consumed by the depannage-scoped catalog
            # builder in ai_service. Non-depannage paths never read it, so
            # this does not affect standard devis generation.
            "sous_metier_depannage": p.sous_metier_depannage,
        }
        exact_map[p.code_pack] = pack_data
        pack_list.append(pack_data)
        
    logger.info("Loaded %d packs from packs_travaux.", len(exact_map))
    return exact_map, pack_list


# ---------------------------------------------------------------------------
# In-memory cache — prices & packs are static BPU data that only change
# when the seed script runs (which restarts the server, clearing this).
# ---------------------------------------------------------------------------
_cache_lock = asyncio.Lock()
_cached_prices: PriceMapTuple | None = None
_cached_packs: tuple[Dict[str, dict], List[dict]] | None = None


async def get_cached_price_map(
    db: AsyncSession,
) -> PriceMapTuple:
    """Return the price_map, concept_map, and metier_medians, loading from DB only once."""
    global _cached_prices
    if _cached_prices is not None:
        return _cached_prices
    async with _cache_lock:
        if _cached_prices is not None:  # double-check after acquiring lock
            return _cached_prices
        _cached_prices = await load_price_map(db)
        logger.info("Price map cached in RAM.")
        return _cached_prices


async def get_cached_packs_map(
    db: AsyncSession,
) -> tuple[Dict[str, dict], List[dict]]:
    """Return the packs exact_map and pack_list, loading from DB only once."""
    global _cached_packs
    if _cached_packs is not None:
        return _cached_packs
    async with _cache_lock:
        if _cached_packs is not None:  # double-check after acquiring lock
            return _cached_packs
        _cached_packs = await load_packs_map(db)
        logger.info("Packs map cached in RAM.")
        return _cached_packs

def _metier_compatible(pack_metier: str, lot_metier: str) -> bool:
    """Loose compatibility check between a pack's trade and the lot's trade."""
    if not pack_metier or not lot_metier:
        return False
    a, b = _normalize_key(pack_metier), _normalize_key(lot_metier)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # Token overlap (e.g. "maconnerie_gros_oeuvre" vs "maconnerie")
    tokens_a = {t for t in a.split("_") if len(t) > 3}
    tokens_b = {t for t in b.split("_") if len(t) > 3}
    return bool(tokens_a & tokens_b)


def _find_pack(
    pack_id: str,
    exact_map: Dict[str, dict],
    pack_list: List[dict],
    *,
    corps_metier: str = "",
) -> Optional[dict]:
    """Find a matching pack using multi-level resolution.

    Resolution order:
    1. Exact match by ``code_pack``
    2. Normalised key match on ``code_pack``
    3. Substring match in ``nom_pack`` — same-metier candidates first
    4. Fuzzy match (difflib) on ``nom_pack``: same-metier candidates at
       cutoff 0.6, then global at cutoff 0.75 AND metier compatibility
       (or near-certainty >= 0.85). A wrong-metier pack is worse than the
       unknown-pack fallback, which at least prices the right trade.

    Returns ``None`` only when no pack can be matched with sufficient confidence.
    """
    # 1. Exact code_pack match
    if pack_id in exact_map:
        return exact_map[pack_id]

    # 2. Normalised code_pack match
    norm_pack_id = _normalize_key(pack_id)
    if not norm_pack_id:
        return None

    for p in pack_list:
        if norm_pack_id == _normalize_key(p["code_pack"]):
            return p

    # Build the same-metier candidate pool once.
    if corps_metier:
        metier_candidates = [
            p for p in pack_list
            if _metier_compatible(p.get("corps_metier", ""), corps_metier)
        ]
    else:
        metier_candidates = []

    # 3. Substring match in nom_pack — metier scope first, then global.
    for candidates in (metier_candidates, pack_list):
        for p in candidates:
            if norm_pack_id in _normalize_key(p["nom_pack"]):
                return p

    # 4. Fuzzy match on nom_pack using difflib (confidence-scored).
    for candidates, scope_name, cutoff in [
        (metier_candidates, "metier_scoped", 0.6),
        (pack_list, "global", 0.75),
    ]:
        if not candidates:
            continue

        candidate_names = [_normalize_key(p["nom_pack"]) for p in candidates]
        matches = difflib.get_close_matches(
            norm_pack_id, candidate_names, n=1, cutoff=cutoff
        )
        if not matches:
            continue
        best_match_name = matches[0]
        for p in candidates:
            if _normalize_key(p["nom_pack"]) != best_match_name:
                continue
            confidence = difflib.SequenceMatcher(
                None, norm_pack_id, best_match_name
            ).ratio()
            if scope_name == "global":
                # Global scope: require metier agreement unless the match
                # is nearly certain.
                if (
                    confidence < 0.85
                    and corps_metier
                    and not _metier_compatible(
                        p.get("corps_metier", ""), corps_metier
                    )
                ):
                    logger.info(
                        "[PACK_FUZZY_REJECT] '%s' → '%s' (code=%s, conf=%.2f) "
                        "rejected: metier '%s' incompatible with lot '%s'",
                        pack_id, p["nom_pack"], p["code_pack"], confidence,
                        p.get("corps_metier", "?"), corps_metier,
                    )
                    break
            logger.info(
                "[PACK_FUZZY_MATCH] '%s' → '%s' (code=%s, scope=%s, "
                "confidence=%.2f, metier='%s')",
                pack_id,
                p["nom_pack"],
                p["code_pack"],
                scope_name,
                confidence,
                p.get("corps_metier", "?"),
            )
            return p

    logger.warning(
        "[PACK_NOT_FOUND] pack_id='%s' metier='%s' — no match in %d packs",
        pack_id,
        corps_metier,
        len(pack_list),
    )
    return None

ISOLATION_TVA_KEYWORDS = [
    "isolation", "isolant", "laine", "sarking", "ite",
    "panneau isolant", "ouate", "polystyrene",
    "polyurethane", "rockwool", "isover",
]

def decide_tva_finale(designation: str, lot_label: str, client_type: str, project_nature: str = "renovation") -> float:
    text = (designation or "").lower()
    lot = (lot_label or "").lower()

    is_isolation_lot = "isolat" in lot
    is_isolation_line = any(kw in text for kw in ISOLATION_TVA_KEYWORDS)

    if is_isolation_lot or is_isolation_line:
        return 5.5
    if client_type == "professionnel" or client_type == "pro":
        return 20.0
    # V2 Enhancement: construction neuve → TVA standard 20%
    if project_nature == "neuf":
        return 20.0
    if client_type == "particulier":
        return 10.0
    # Unknown client_type → default to 10% (renovation particulier is the most common case)
    return 10.0

UNIT_MAP = {
    "m2": "m²", "m²": "m²", "m² ": "m²", "metre carre": "m²", "mètre carré": "m²",
    "ml": "ml", "m.l": "ml", "m.l.": "ml", "m": "ml",
    "metre": "ml", "mètre": "ml", "metres": "ml", "mètres": "ml",
    "metre lineaire": "ml", "mètre linéaire": "ml", "metre linéaire": "ml",
    "mètre lineaire": "ml", "metres lineaires": "ml", "mètres linéaires": "ml",
    "m3": "m³", "m³": "m³", "metre cube": "m³", "mètre cube": "m³",
    "u": "u", "u.": "u", "unite": "u", "unité": "u", "unites": "u", "unités": "u",
    "piece": "u", "pièce": "u", "pieces": "u", "pièces": "u",
    "point": "u", "sac": "u", "paire": "u", "jeu": "u", "rouleau": "u",
    "poste": "u", "circuit": "u", "zone": "u", "niveau": "u", "bidon": "u",
    "cartouche": "u", "filtre": "u", "disjoncteur": "u", "coffret": "u",
    "radiateur": "u", "repartiteur": "u", "répartiteur": "u", "prise": "u",
    "tremie": "u", "trémie": "u", "angle": "u", "kwc": "u", "kwh": "u",
    "ens": "ens", "ensemble": "ens",
    "forfait": "forfait", "ft": "forfait", "intervention": "forfait",
    "voyage": "forfait", "semaine": "forfait", "mois": "forfait",
    "h": "h", "heure": "h", "heures": "h",
    "j": "j", "jour": "j", "jours": "j",
    "kg": "kg", "t": "t", "tonne": "t", "tonnes": "t",
    "l": "l", "litre": "l", "litres": "l",
    "lot": "lot",
}

def normalize_unit(raw: str | None) -> str:
    if not raw:
        return "u"
    return UNIT_MAP.get(raw.strip(), UNIT_MAP.get(raw.strip().lower(), "unknown"))

_SURFACE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(?:m[²2]\b|m[eè]tres?\s*carr[eé]s?)", re.I
)


def extract_surface_m2(description: str) -> float | None:
    """Extract the first explicit surface (m²) from free text, or ``None``.

    Returning ``None`` (instead of a blind 50 m² default) lets the engine
    distinguish "the user gave a surface" from "we would be inventing one".
    """
    if not description:
        return None
    m = _SURFACE_RE.search(description)
    return float(m.group(1).replace(",", ".")) if m else None


# Conservative default surfaces per trade, used ONLY when a surface-driven
# pack is requested without any usable dimension anywhere in the text.
_DEFAULT_SURFACE_BY_METIER: Dict[str, float] = {
    "salle_de_bain": 5.0,
    "cuisine": 10.0,
    "peinture": 30.0,
    "revetement": 20.0,
    "sol": 20.0,
    "carrelage": 15.0,
    "platrerie": 25.0,
    "cloison": 25.0,
    "isolation": 40.0,
    "ite": 90.0,
    "facade": 90.0,
    "ravalement": 90.0,
    "toiture": 80.0,
    "couverture": 80.0,
    "etancheite": 60.0,
    "maconnerie": 30.0,
    "demolition": 30.0,
    "terrassement": 40.0,
    "charpente": 80.0,
}
_GENERIC_DEFAULT_SURFACE: float = 20.0


def _default_surface_for(metier: str) -> float:
    """Return a conservative default surface for the given trade."""
    norm = _normalize_key(metier)
    for key, surface in _DEFAULT_SURFACE_BY_METIER.items():
        if key in norm:
            return surface
    return _GENERIC_DEFAULT_SURFACE


def _dominant_unit(pack_json: List[dict]) -> str:
    """Return the dominant normalised unit among a pack's work lines (blocs 2-3)."""
    counts: Dict[str, int] = {}
    for line in pack_json:
        if line.get("bloc", 2) not in (2, 3):
            continue
        u = normalize_unit(line.get("unite"))
        counts[u] = counts.get(u, 0) + 1
    if not counts:
        return "forfait"
    return max(counts.items(), key=lambda kv: kv[1])[0]

def clamp(v, mn, mx):
    return max(mn, min(mx, v))

def classify_zone(metier: str) -> str:
    m = metier.lower()
    if "toiture terrasse" in m or "terrasse" in m:
        return "exterieur"
    if "couverture" in m or "toiture" in m or "charpente" in m:
        return "toiture"
    if "facade" in m or "ravalement" in m or "ite" in m or "terrassement" in m or "vrd" in m:
        return "exterieur"
    return "interieur"

def compute_geometry(surface_m2: float, metier: str, description: str, user_height_m=None, user_nb_pans=None) -> dict:
    surface = max(surface_m2, 1)
    zone = classify_zone(metier)

    aspect = 3.0 if any(x in metier.lower() for x in ["facade", "ravalement", "ite"]) else 2.0
    if any(x in metier.lower() for x in ["terrassement", "vrd", "gros oeuvre"]):
        aspect = 1.5

    width = clamp(math.sqrt(surface / aspect), 1.0, 30.0)
    length = clamp(surface / width, 1.0, 50.0)
    perimeter = round(2 * (length + width), 2)
    height = user_height_m or 2.5

    roof_values = None
    if zone == "toiture":
        cos35 = 0.8192
        s_h = surface * cos35
        roof_w = math.sqrt(s_h / 1.5)
        roof_l = 1.5 * roof_w
        rampant = (roof_w / 2) / cos35
        roof_values = {
            "faitage_ml": round(roof_l, 2),
            "rives_ml": round(2 * rampant, 2),
            "egouts_ml": round(2 * roof_l, 2),
            "rampant_ml": round(rampant, 2),
            "emprise_l": round(roof_l, 2),
            "emprise_w": round(roof_w, 2),
        }
        length = roof_values["emprise_l"]
        width = roof_values["emprise_w"]
        perimeter = round(2 * (length + width), 2)

    return {
        "zone": zone,
        "area_m2": surface,
        "length_m": round(length, 2),
        "width_m": round(width, 2),
        "perimeter_ml": perimeter,
        "height_m": height,
        "roof_values": roof_values,
    }

# Designation keywords whose m³ volume genuinely scales with the surface
# (slab-like works ~12 cm thick, or rubble ~8 cm equivalent).
_VOLUME_SLAB_KEYWORDS = ("dalle", "beton", "béton", "chape", "ragréage", "ragreage")
_VOLUME_RUBBLE_KEYWORDS = ("gravat", "déblai", "deblai", "terre", "évacuation", "evacuation")

# Hard ceiling for any single computed line quantity (absurdity guard).
_MAX_LINE_QUANTITY: float = 10_000.0


def calculate_quantity_from_unit(
    unite: str,
    surface_m2: float,
    quantite_pack: float,
    mode_calcul_ml: str | None = None,
    coefficient_ml: float | None = None,
    geometry: dict | None = None,
    *,
    unit_count: float = 1.0,
    longueur_ml: float | None = None,
    surface_known: bool = True,
    line_bloc: int = 2,
    designation: str = "",
) -> tuple[float, str]:
    """Compute the billable quantity for one pack line.

    Parameters (new, all optional so legacy calls keep working)
    ----------
    unit_count:
        Discrete multiplier extracted by the AI ("5 splits" → 5). Applied
        to per-unit work lines (blocs 2-3) only; prep/finish lines stay x1.
    longueur_ml:
        Explicit length from the AI ("25 ml de clôture" → 25). Overrides
        geometry estimates for linear lines.
    surface_known:
        True when the surface comes from the user text (per-pack or global).
        When False, m² lines fall back to the pack quantity instead of
        billing an invented surface.
    line_bloc:
        The pack line's bloc (1=prep, 2/3=work, 4=finish).
    """
    u = normalize_unit(unite).lower().strip()
    is_work_line = line_bloc in (2, 3)

    def _cap(qty: float, rule: str) -> tuple[float, str]:
        if qty > _MAX_LINE_QUANTITY:
            logger.warning(
                "[QTY_CAP] %s: quantity %.2f capped to %.0f (%s)",
                designation[:60], qty, _MAX_LINE_QUANTITY, rule,
            )
            return _MAX_LINE_QUANTITY, f"{rule}_CAPPED"
        return qty, rule

    if u in ("m²", "m2"):
        if surface_known:
            return _cap(max(surface_m2, 0), "M2_SURFACE")
        # No surface signal anywhere: bill the pack's own quantity rather
        # than inventing square meters of work.
        return max(quantite_pack, 1), "M2_PACK_QTY_NO_SURFACE"

    if u == "ml":
        mode = mode_calcul_ml
        coeff = coefficient_ml or 1.0

        # An explicit user length beats every geometric estimate for the
        # lines that directly follow the length of the work.
        if longueur_ml and (not mode or mode in ("LONGUEUR", "FIXE", "MANUEL")):
            qty = longueur_ml * (coeff if mode == "LONGUEUR" else 1.0)
            return _cap(round(qty, 2), "ML_FROM_AI_LENGTH")

        longueur = geometry.get("length_m") if geometry else surface_m2 / math.sqrt(max(surface_m2, 1) / 2)
        largeur = geometry.get("width_m") if geometry else math.sqrt(max(surface_m2, 1) / 2)
        perimetre = geometry.get("perimeter_ml") if geometry else 2 * (longueur + largeur)
        hauteur = geometry.get("height_m") if geometry else 2.5

        if not mode:
            return quantite_pack or 1, "ML_NO_MODE_PACK_QTY"

        if mode == "PERIMETRE":
            qty = perimetre * coeff
        elif mode == "LONGUEUR":
            qty = longueur * coeff
        elif mode == "LARGEUR":
            qty = largeur * coeff
        elif mode == "RATIO_SURFACE":
            qty = surface_m2 * coeff
        elif mode == "HAUTEUR":
            qty = hauteur * coeff
        elif mode == "RAMPANT":
            rampant = (geometry or {}).get("roof_values", {}).get("rampant_ml")
            qty = (rampant if rampant else (largeur / 2) / 0.8192) * coeff
        elif mode in ("FIXE", "MANUEL"):
            qty = (quantite_pack or 1) * (unit_count if is_work_line else 1)
            return _cap(round(qty, 2), "ML_FIXED_PACK_QTY")
        elif mode == "AUCUN":
            return 0, "ML_NONE"
        else:
            return quantite_pack or 1, f"ML_UNKNOWN_{mode}"

        return _cap(round(qty, 2), f"ML_{mode}")

    if u in ("m³", "m3"):
        desig = (designation or "").lower()
        if surface_known and any(kw in desig for kw in _VOLUME_SLAB_KEYWORDS):
            return _cap(round(surface_m2 * 0.12, 2), "M3_SLAB_012")
        if surface_known and any(kw in desig for kw in _VOLUME_RUBBLE_KEYWORDS):
            return _cap(round(max(surface_m2 * 0.08, 0.5), 2), "M3_RUBBLE_008")
        return quantite_pack if quantite_pack >= 1 else 1, "M3_PACK_QTY"

    if u == "u":
        base = quantite_pack if quantite_pack >= 1 else 1
        if unit_count > 1 and is_work_line:
            return _cap(round(base * unit_count, 2), "U_SCALED_BY_COUNT")
        return base, "GENERIC_FIXED"

    if u in ("forfait", "ensemble", "lot", "ens"):
        return quantite_pack if quantite_pack >= 1 else 1, "GENERIC_FIXED"

    if u in ("jour", "heure", "h", "j"):
        return quantite_pack if quantite_pack >= 1 else 1, "GENERIC_TIME"

    if u in ("kg", "tonnes", "t", "l"):
        return quantite_pack if quantite_pack >= 1 else 1, "GENERIC_WEIGHT"

    # Unknown units: never fall back to the surface — bill the pack quantity.
    return quantite_pack if quantite_pack >= 1 else 1, "GENERIC_DEFAULT"

def _pad_or_truncate_lines(
    lines: List[Dict[str, Any]],
    target_count: int,
    default_designation: str,
    tva: float,
    metier: str = "",
    *,
    total_neutral: bool = False,
) -> List[Dict[str, Any]]:
    """Enforce exactly ``target_count`` lines.

    Truncation keeps the highest-value lines (original order preserved) and
    merges only the smallest ones, so an expensive real prestation can no
    longer disappear into an opaque merge line.

    Padding fills missing lines with metier-specific ancillary labels. In
    ``total_neutral`` mode (sparse lots, e.g. a single fallback line) the
    padding budget is carved OUT of the main line instead of being added on
    top — the lot total stays market-accurate while looking fully detailed.
    """
    if target_count <= 0:
        return []
        
    if len(lines) > target_count:
        if target_count == 1:
            total_ht = round(sum(l.get("total_ht", 0) for l in lines), 2)
            return [{
                "designation": lines[0].get("designation", default_designation),
                "unite": "forfait",
                "quantite": 1,
                "pu_ht": total_ht,
                "tva": tva,
                "total_ht": total_ht
            }]

        # Keep the (target-1) highest-HT lines, preserving their original
        # order; merge the smallest remainder into one summary line.
        indexed = list(enumerate(lines))
        by_value = sorted(
            indexed, key=lambda t: t[1].get("total_ht", 0), reverse=True
        )
        keep_idx = {i for i, _ in by_value[: target_count - 1]}
        kept = [l for i, l in indexed if i in keep_idx]
        dropped = [l for i, l in indexed if i not in keep_idx]
        dropped_ht = round(sum(l.get("total_ht", 0) for l in dropped), 2)
        context = (metier or default_designation).strip()
        kept.append({
            "designation": (
                f"Ensemble de prestations complémentaires — {context} "
                f"({len(dropped)} postes regroupés)"
            ),
            "unite": "forfait",
            "quantite": 1,
            "pu_ht": dropped_ht,
            "tva": tva,
            "total_ht": dropped_ht
        })
        return kept
    elif len(lines) < target_count:
        needed = target_count - len(lines)
        padded = list(lines)

        target_quantite = 1
        primary_unit = "forfait"
        if lines:
            # Detect the primary unit from the first (main) line
            primary_unit = lines[0].get("unite", "forfait").lower().strip()
            target_quantite = max((l.get("quantite", 1) for l in lines), default=1)

        # SMART QUANTITY RULE:
        # - Unit "u" (discrete items: splits, radiateurs, fenêtres...) →
        #   ancillary work is done PER UNIT → inherit quantity
        # - Units "m²", "ml", "m³", "kg", "l" (surface/volume/weight) →
        #   ancillary work is done ONCE for the whole job → QTE = 1
        # - Unit "forfait" → already a lump sum → QTE = 1
        _DISCRETE_UNITS = {"u"}
        use_inherited_qty = primary_unit in _DISCRETE_UNITS

        total_real_ht = sum(l.get("total_ht", 0) for l in lines) or 0

        # ---- Total-neutral mode: carve the detail budget out of the main
        # line so the lot total is preserved exactly. -----------------------
        pad_budget: float | None = None
        if total_neutral and total_real_ht >= 300 and needed > 0 and lines:
            main = max(padded, key=lambda l: l.get("total_ht", 0))
            main_total = float(main.get("total_ht", 0) or 0)
            qte_main = float(main.get("quantite", 1) or 1)
            desired_budget = round(total_real_ht * 0.15, 2)
            if main_total > desired_budget * 2 and qte_main > 0:
                new_main_total = round(main_total - desired_budget, 2)
                main["pu_ht"] = round(new_main_total / qte_main, 2)
                main["total_ht"] = round(main["pu_ht"] * qte_main, 2)
                pad_budget = round(main_total - main["total_ht"], 2)
                use_inherited_qty = False  # detail lines are global forfaits

        # Compute proportional padding price based on existing real lines.
        # Ancillary services (prep, cleanup, etc.) are typically ~15% of
        # the main work, spread across the padding lines.
        if pad_budget is not None:
            pad_pu_base = max(1.0, pad_budget / needed)
        elif total_real_ht > 0 and needed > 0:
            # Distribute 15% of the total budget across the needed padding lines.
            # We REMOVE the max(25.0, ...) limit to prevent artificially inflating small devis!
            pad_pu_base = (total_real_ht * 0.15) / needed
            # When padding lines inherit a discrete quantity (e.g. 5 splits →
            # per-unit ancillary ops), the UNIT price must be divided by that
            # quantity — otherwise the 15% envelope gets multiplied by the
            # count and silently inflates the devis.
            if use_inherited_qty and target_quantite > 1:
                pad_pu_base = pad_pu_base / target_quantite
            # Still put a very soft minimum so we don't have 0.50€ lines if possible
            pad_pu_base = max(5.0, pad_pu_base)
        elif "Nettoyage" in default_designation:
            pad_pu_base = 75.0
        elif "Mise en place" in default_designation:
            pad_pu_base = 95.0
        else:
            pad_pu_base = 45.0

        # Deterministic variance factors to make the prices look human-generated (not robotic).
        # They average to ~1.0.
        _VARIANCE_FACTORS = [1.25, 0.82, 1.15, 0.88, 1.05, 0.92, 1.30, 0.75, 1.10, 0.90, 1.20, 0.80, 0.95, 1.08, 0.85]

        # Pick specific generic labels based on the default designation
        if "Mise en place" in default_designation:
            generic_labels = [
                "Balisage et sécurisation de la zone de travail",
                "Mise en place des protections au sol et murales",
                "Acheminement de l'outillage et préparation du poste",
                "Vérification des supports et repérages initiaux"
            ]
        elif "Nettoyage" in default_designation:
            generic_labels = [
                "Évacuation des gravats et déchets résiduels",
                "Nettoyage approfondi de la zone d'intervention",
                "Retrait des protections et remise en ordre",
                "Réception technique et contrôle final"
            ]
        else:
            metier_lower = metier.lower()
            if "climatisation" in metier_lower or "chauffage" in metier_lower or "cvc" in metier_lower:
                generic_labels = [
                    "Repérage et tracé des parcours frigorifiques ou hydrauliques",
                    "Percements et carottages pour passages de liaisons",
                    "Mise en place des supports et fixations anti-vibratiles",
                    "Pose et raccordement des liaisons et réseaux",
                    "Câblage électrique d'interconnexion interne",
                    "Pose et raccordement des évacuations de condensats",
                    "Mise sous pression pour test d'étanchéité",
                    "Tirage au vide de l'installation (si applicable)",
                    "Appoint de fluide ou traitement des réseaux",
                    "Tests d'étanchéité et relevés de pressions",
                    "Vérification des écoulements et pentes",
                    "Contrôle de l'isolation thermique des liaisons",
                    "Mise en service, réglages et relevés de températures",
                    "Vérification de la conformité aux normes",
                    "Mise en propreté de la zone d'intervention"
                ]
            elif "plomberie" in metier_lower or "sanitaire" in metier_lower:
                generic_labels = [
                    "Repérage et tracé des parcours de canalisations",
                    "Percements et saignées pour passages de tuyauteries",
                    "Fourniture et pose des colliers et supports de fixation",
                    "Découpes, ébavurages et préparation des tubes",
                    "Réalisation des assemblages (soudures, sertissages, collages)",
                    "Raccordement des réseaux d'alimentation (EF/EC)",
                    "Raccordement des réseaux d'évacuation (EU/EV)",
                    "Mise en eau et purge des canalisations",
                    "Tests de mise en pression et recherche de fuites",
                    "Contrôle des débits et des pentes d'écoulement",
                    "Isolation thermique ou acoustique ponctuelle des tubes",
                    "Rebouchage technique des traversées de cloisons",
                    "Vérification de la conformité des raccordements",
                    "Paramétrage des organes de régulation",
                    "Mise en propreté de la zone d'intervention"
                ]
            elif "electri" in metier_lower:
                generic_labels = [
                    "Repérage et tracé des cheminements électriques",
                    "Réalisation des saignées et percements",
                    "Fourniture et pose des cheminements (gaines, goulottes)",
                    "Tirage des câbles et conducteurs",
                    "Dénudage et repérage des extrémités de câbles",
                    "Raccordements des appareillages et boîtes de dérivation",
                    "Repérage et raccordement au tableau de répartition",
                    "Vérification de la continuité des conducteurs de protection",
                    "Mesure de la résistance de prise de terre",
                    "Contrôle d'isolement des circuits",
                    "Tests de déclenchement des dispositifs différentiels",
                    "Rebouchage technique des saignées et scellements",
                    "Mise sous tension et essais fonctionnels",
                    "Identification formelle des circuits (étiquetage)",
                    "Mise en propreté de la zone d'intervention"
                ]
            elif "peinture" in metier_lower or "revetement" in metier_lower:
                generic_labels = [
                    "Reconnaissance et sondage des supports",
                    "Lessivage et dégraissage des surfaces",
                    "Grattage et égrenage des parties non adhérentes",
                    "Ouverture et traitement des fissures",
                    "Application d'un enduit de rebouchage ponctuel",
                    "Application d'un enduit de lissage ou repassage",
                    "Ponçage soigné et dépoussiérage des fonds",
                    "Mise en place de bandes de masquage",
                    "Protection spécifique des menuiseries et appareillages",
                    "Application d'une couche d'impression ou primaire",
                    "Traitement spécifique des joints ou réchampissage",
                    "Vérification de l'opacité et retouches intermédiaires",
                    "Dépose minutieuse des adhésifs de masquage",
                    "Contrôle visuel de l'homogénéité du rendu",
                    "Mise en propreté de la zone d'intervention"
                ]
            else:
                generic_labels = [
                    "Mise en sécurité spécifique des éléments de l'ouvrage",
                    "Repérage, traçage et implantation des ouvrages",
                    "Préparation technique de la zone de travail",
                    "Manutention et répartition des matériaux à pied d'œuvre",
                    "Ajustement et calibrage des éléments d'assemblage",
                    "Fourniture et pose des petits consommables techniques",
                    "Vérification des alignements, niveaux et aplombs",
                    "Protection ponctuelle des ouvrages adjacents",
                    "Contrôle technique des assemblages et raccordements",
                    "Tests de résistance et de bon fonctionnement",
                    "Acheminement et tri des déchets d'intervention",
                    "Vérification de la conformité aux DTU en vigueur",
                    "Réglages finaux et paramétrages techniques",
                    "Réception technique intermédiaire de l'ouvrage",
                    "Mise en propreté de la zone d'intervention"
                ]
        
        remaining_budget = pad_budget
        for i in range(needed):
            label_suffix = generic_labels[i] if i < len(generic_labels) else f"Prestation annexe {i+1}"
            
            is_global = "Mise en place" in default_designation or "Nettoyage" in default_designation
            # Global blocks (prep/cleanup) always QTE=1.
            # Intervention blocks: inherit qty only for discrete units (u).
            if is_global:
                qte = 1
            elif use_inherited_qty:
                qte = target_quantite
            else:
                qte = 1
            
            # Apply deterministic variance to make prices look natural, rounded to integer for cleaner look
            factor = _VARIANCE_FACTORS[i % len(_VARIANCE_FACTORS)]
            pad_pu_human = round(pad_pu_base * factor)
            # Ensure at least 1€
            pad_pu_human = float(max(1, pad_pu_human))

            if remaining_budget is not None:
                # Total-neutral mode: the last line absorbs rounding drift so
                # the padding sums exactly to the carved-out budget.
                lines_left_after = needed - 1 - i
                if lines_left_after == 0:
                    pad_pu_human = float(max(1.0, round(remaining_budget, 2)))
                else:
                    ceiling = remaining_budget - lines_left_after * 1.0
                    pad_pu_human = float(max(1.0, min(pad_pu_human, round(ceiling, 2))))
                remaining_budget = round(remaining_budget - pad_pu_human, 2)

            padded.append({
                "designation": label_suffix,
                "unite": "forfait",
                "quantite": qte,
                "pu_ht": pad_pu_human,
                "tva": tva,
                "total_ht": round(pad_pu_human * qte, 2)
            })
        return padded
    return lines

def process_ai_lots(
    lots: List[Dict[str, Any]],
    client_type: str = "particulier",
    project_nature: str = "renovation",
    *,
    surface_m2: float | None = None,
    user_text: str = "",
    price_map: Optional[Dict[str, float]] = None,
    concept_map: Optional[Dict[str, Dict[str, float]]] = None,
    metier_medians: Optional[MetierMedianMap] = None,
    packs_maps: Optional[tuple[Dict[str, dict], List[dict]]] = None,
) -> List[Dict[str, Any]]:
    """
    Takes the pure semantic AI JSON, evaluates rules, and structures it into
    the strict K+2 blocs architecture with exact line counts.
    """
    if not lots:
        return []

    # Per-lot depannage detection. Intervention sizing is decided lot by lot
    # (a mixed DEPANNAGE + PRESTATION request no longer crushes prestation
    # lots down to 3 lines); the global prep/finish blocks use the compact
    # depannage sizing only when the WHOLE request is a depannage.
    lot_is_dep: List[bool] = [
        any(p.get("type", "").upper() == "DEPANNAGE" for p in lot.get("packs", []))
        for lot in lots
    ]
    all_depannage = all(lot_is_dep) if lot_is_dep else False
    target_mise_en_place = 1 if all_depannage else 3
    target_finition = 1 if all_depannage else 3

    # Global surface extracted from the raw user text (None when absent).
    global_surface = surface_m2 if surface_m2 and surface_m2 > 0 else None

    global_mise_en_place_lines = []
    global_finition_lines = []
    intervention_blocks = []
    
    for lot, is_dep_lot in zip(lots, lot_is_dep):
        metier = lot.get("metier", "Métier inconnu")
        lot_key = lot.get("lot_key", "LOT_01")
        packs = lot.get("packs", [])
        target_intervention = 3 if is_dep_lot else 14
        tva = decide_tva_finale("", metier, client_type, project_nature)
        
        matched_rules = next((rules for code, rules in ALL_METIER_RULES.items() if rules["metier"].lower() in metier.lower()), None)
        
        lot_intervention_lines = []
        
        for pack in packs:
            pack_id = pack.get("id", "INCONNU")
            quantite_brute = pack.get("quantite", 1)
            # Clamp invalid quantities to 1
            if not isinstance(quantite_brute, (int, float)) or quantite_brute <= 0:
                logger.warning("Pack %r has invalid quantite=%r — defaulting to 1", pack_id, quantite_brute)
                quantite_brute = 1
            quantite_brute = float(quantite_brute)

            # ---- Quantity semantics --------------------------------------
            # quantite_type tells the engine what the AI's number MEANS.
            # Legacy payloads without it fall back to a magnitude heuristic.
            qty_type = str(pack.get("quantite_type") or "").strip().lower()
            if qty_type not in ("surface_m2", "longueur_ml", "unitaire", "forfait", "non_specifie"):
                if quantite_brute > 10:
                    qty_type = "surface_m2"
                elif quantite_brute > 1:
                    qty_type = "unitaire"
                else:
                    qty_type = "non_specifie"

            source_qte = str(pack.get("source_qte") or "")

            # Per-pack surface: the AI value, else a surface quoted in the
            # pack's own source passage, else the global text surface.
            pack_surface: float | None = None
            if qty_type == "surface_m2":
                pack_surface = quantite_brute
            else:
                source_surface = extract_surface_m2(source_qte)
                if source_surface:
                    pack_surface = source_surface

            unit_count = quantite_brute if qty_type == "unitaire" else 1.0
            longueur_ml = quantite_brute if qty_type == "longueur_ml" else None

            surface_known = pack_surface is not None or global_surface is not None
            effective_surface = (
                pack_surface
                if pack_surface is not None
                else (global_surface if global_surface is not None else _default_surface_for(metier))
            )

            # Try packs_travaux first
            matched_pack = None
            if packs_maps:
                exact_map, pack_list = packs_maps
                matched_pack = _find_pack(pack_id, exact_map, pack_list, corps_metier=metier)
                
            if matched_pack:
                # A surface-driven pack requested without any dimension in
                # the text gets the conservative per-metier default surface
                # instead of billing qty-1 lines.
                if not surface_known and _dominant_unit(matched_pack["pack_json"]) == "m²":
                    surface_known = True
                    logger.info(
                        "[QTY_DEFAULT_SURFACE] pack=%s metier=%r → default %.0f m²",
                        matched_pack["code_pack"], metier, effective_surface,
                    )

                # Geometry is computed per pack from ITS effective surface.
                geometry = compute_geometry(effective_surface, metier, user_text)

                for line in matched_pack["pack_json"]:
                    qty, rule = calculate_quantity_from_unit(
                        unite=line.get("unite", "forfait"),
                        surface_m2=effective_surface,
                        quantite_pack=line.get("quantite", 1.0),
                        mode_calcul_ml=line.get("mode_calcul_ml"),
                        coefficient_ml=line.get("coefficient_ml"),
                        geometry=geometry,
                        unit_count=unit_count,
                        longueur_ml=longueur_ml,
                        surface_known=surface_known,
                        line_bloc=line.get("bloc", 2),
                        designation=line.get("designation", ""),
                    )
                    
                    qte_calc = max(round(qty, 2), 0.01)
                    
                    pu_ht = line.get("prix_unitaire_ht") or 0.0
                    if pu_ht <= 0:
                        # Curated pack line without a price: resolve through
                        # the price cascade instead of emitting a 0 € line.
                        pu_ht = _resolve_price(
                            line.get("designation", pack_id),
                            line.get("unite", "forfait"),
                            price_map,
                            concept_map=concept_map,
                            corps_metier=metier,
                            metier_medians=metier_medians,
                        )
                    total_ht = round(qte_calc * pu_ht, 2)
                    tva = decide_tva_finale(line.get("designation", ""), metier, client_type, project_nature)
                    
                    line_data = {
                        "designation": line.get("designation", ""),
                        "unite": line.get("unite", "forfait"),
                        "quantite": round(qte_calc, 2),
                        "pu_ht": pu_ht,
                        "tva": tva,
                        "total_ht": total_ht
                    }
                    
                    bloc = line.get("bloc", 2)
                    if bloc == 1:
                        global_mise_en_place_lines.append(line_data)
                    elif bloc == 4:
                        global_finition_lines.append(line_data)
                    else:
                        lot_intervention_lines.append(line_data)
                continue
            
            pack_lines_def = None
            if matched_rules and pack_id in matched_rules["rules"]:
                pack_lines_def = matched_rules["rules"][pack_id]
            
            if pack_lines_def:
                for line_key, rule in pack_lines_def.items():
                    # Format designation nicely: e.g. "carrelage_m2 (+10% coupes/chutes)"
                    desc = rule.get("description", "")
                    clean_key = line_key.replace("_m2", "").replace("_ml", "").replace("_u", "").replace("_kg", "").replace("_l", "").replace("_m3", "").capitalize()
                    designation = f"{clean_key} ({desc})" if desc else clean_key
                    tva = decide_tva_finale(designation, metier, client_type, project_nature)
                    
                    qte_calc = safe_eval_formula(rule["formula"], {"surface": quantite_brute, "longueur": quantite_brute, "hauteur": 2.5})
                    pu_ht = _resolve_price(line_key, rule["unit"], price_map, concept_map=concept_map, corps_metier=metier, metier_medians=metier_medians)
                    total_ht = round(qte_calc * pu_ht, 2)
                    
                    line_data = {
                        "designation": designation,
                        "unite": rule["unit"],
                        "quantite": round(qte_calc, 2),
                        "pu_ht": pu_ht,
                        "tva": tva,
                        "total_ht": total_ht
                    }
                    
                    # Heuristics to dispatch into global vs intervention
                    key_lower = line_key.lower()
                    if "nettoyage" in key_lower or "repli" in key_lower:
                        global_finition_lines.append(line_data)
                    elif "protection" in key_lower or "installation" in key_lower and "chantier" in key_lower:
                        global_mise_en_place_lines.append(line_data)
                    else:
                        lot_intervention_lines.append(line_data)
            else:
                # Unknown pack (invented id): one main line priced from the
                # BPU cascade. The unit now follows the AI's declared
                # quantity semantics instead of a magnitude guess.
                logger.warning(
                    "Pack ID %r not found in catalog — using fallback line for metier=%r",
                    pack_id, metier,
                )
                clean_pack_id = str(pack_id).replace("_", " ").capitalize()
                fallback_designation = f"Fourniture et pose : {clean_pack_id}"
                tva = decide_tva_finale(fallback_designation, metier, client_type, project_nature)
                if qty_type == "surface_m2":
                    pack_unit = "m²"
                elif qty_type == "longueur_ml":
                    pack_unit = "ml"
                elif qty_type == "unitaire":
                    pack_unit = "u"
                elif quantite_brute == 1:
                    pack_unit = "forfait"
                elif quantite_brute > 10:
                    pack_unit = "m²"  # Surface-based (toiture, façade, ITE...)
                else:
                    pack_unit = "u"
                # Resolve price from DB using pack keywords
                pu_ht = _resolve_price(pack_id, pack_unit, price_map, concept_map=concept_map, corps_metier=metier, metier_medians=metier_medians)
                total_ht = round(pu_ht * quantite_brute, 2)
                lot_intervention_lines.append({
                    "designation": fallback_designation,
                    "unite": pack_unit,
                    "quantite": quantite_brute,
                    "pu_ht": pu_ht,
                    "tva": tva,
                    "total_ht": total_ht
                })
        
        # Enforce exact line count for THIS intervention block.
        # A single-line lot (unknown-pack fallback) is padded total-neutrally:
        # the detail lines are carved out of the main price, not added on top.
        base_tva = decide_tva_finale("", metier, client_type, project_nature)
        lot_intervention_lines = _pad_or_truncate_lines(
            lot_intervention_lines, 
            target_intervention, 
            f"Travaux et fournitures {metier}",
            base_tva,
            metier,
            total_neutral=(len(lot_intervention_lines) == 1),
        )
        
        intervention_blocks.append({
            "title": _normalize_trade_title(metier),
            "lines": lot_intervention_lines
        })
        
    # Enforce exact line counts for global blocks
    global_tva = decide_tva_finale("", "", client_type, project_nature)
    global_mise_en_place_lines = _pad_or_truncate_lines(
        global_mise_en_place_lines,
        target_mise_en_place,
        "Mise en place, balisage et protection du chantier",
        global_tva
    )
    
    global_finition_lines = _pad_or_truncate_lines(
        global_finition_lines,
        target_finition,
        "Nettoyage fin de chantier et repli",
        global_tva
    )

    force_tva_55 = "isolation" in user_text.lower()

    def _map_lines(lines_in: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for i, l in enumerate(lines_in, 1):
            tva = 5.5 if force_tva_55 else l.get("tva", 10.0)
            ht = l.get("total_ht", 0.0)
            out.append({
                "num": i,
                "description": l.get("designation", ""),
                "qte": l.get("quantite", 1.0),
                "unit": l.get("unite", "forfait"),
                "pu": l.get("pu_ht", 0.0),
                "tva": tva,
                "ht": ht,
                "ttc": round(ht * (1 + tva / 100.0), 2)
            })
        return out

    # Assemble the final strict structure (V1 format: blocs/lots/lignes)
    final_blocks = []
    
    # 1. Mise en place
    final_blocks.append({
        "title": "Mise en place du chantier",
        "lots": [{"title": "Préparation", "lignes": _map_lines(global_mise_en_place_lines)}]
    })
    
    # 2..K. Interventions
    for block in intervention_blocks:
        final_blocks.append({
            "title": block["title"],
            "lots": [{"title": "Travaux principaux", "lignes": _map_lines(block["lines"])}]
        })
        
    # K+1. Finition
    final_blocks.append({
        "title": "Finitions et nettoyage",
        "lots": [{"title": "Nettoyage", "lignes": _map_lines(global_finition_lines)}]
    })
    
    return final_blocks

def calculate_global_totals(lines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculates global TVA, HT and TTC according to frontend logic."""
    total_ht = 0.0
    total_tva = 0.0
    by_rate = {}

    for line in lines:
        qty = float(line.get("qty") or line.get("quantite") or line.get("qte") or 1)
        pu_ht = float(line.get("pu") or line.get("pu_ht") or 0)
        tva = float(line.get("tva_pct") or line.get("tva") or 10)

        line_ht = round(qty * pu_ht, 2)
        line_tva = round(line_ht * tva / 100, 2)

        total_ht += line_ht
        total_tva += line_tva

        by_rate.setdefault(tva, {"base_ht": 0.0, "montant_tva": 0.0})
        by_rate[tva]["base_ht"] = round(by_rate[tva]["base_ht"] + line_ht, 2)
        by_rate[tva]["montant_tva"] = round(by_rate[tva]["montant_tva"] + line_tva, 2)

    return {
        "total_ht": round(total_ht, 2),
        "total_tva": round(total_tva, 2),
        "total_ttc": round(total_ht + total_tva, 2),
        "tva_breakdown": {
            str(rate): {"base_ht": values["base_ht"], "tva_amount": values["montant_tva"]}
            for rate, values in by_rate.items()
        }
    }


# Average HT value produced per crew-day, used for the duration estimate.
_HT_PER_CREW_DAY: float = 700.0


def estimate_duration_days(total_ht: float, blocks: Optional[List[Dict[str, Any]]] = None) -> int:
    """Deterministic project-duration estimate (in days) from the devis value.

    Replaces the hardcoded 30-day constant: ~700 € HT of works per crew-day,
    plus one transition day per additional trade, clamped to [1, 90].
    """
    if not total_ht or total_ht <= 0:
        return 1
    n_interventions = 1
    if blocks:
        # blocks = [prep] + interventions + [finish]
        n_interventions = max(1, len(blocks) - 2)
    days = math.ceil(total_ht / _HT_PER_CREW_DAY) + (n_interventions - 1)
    return int(clamp(days, 1, 90))