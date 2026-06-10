import logging
import unicodedata
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metier_rules import ALL_METIER_RULES, safe_eval_formula
from app.models.bpu_item import BpuItem
from app.models.pack_travaux import PackTravaux
import math
import re

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
    # ── Plâtrerie ──
    "surface": ["faux plafond", "plafond suspendu"],
    "placo": ["plaque de plâtre", "ba13", "plaque platre", "placo"],
    "ossature": ["ossature", "fourrure", "f530"],
    # ── Maçonnerie ──
    "beton": ["béton armé", "béton dosé"],
    "treillis": ["treillis soudé"],
    "coffrage": ["coffrage"],
    "blocs": ["parpaing", "agglo creux"],
    "chainages": ["chaînage", "chainage"],
    # ── Couverture / Toiture ──
    "toiture": ["toiture", "couverture", "réfection toiture", "refection toiture"],
    "tuiles": ["tuile", "tuiles mécaniques", "tuiles plates"],
    "zinguerie": ["zinguerie", "gouttière", "chéneau"],
    "ardoise": ["ardoise"],
    # ── Climatisation / Ventilation ──
    "climatisation": ["climatisation", "climatiseur", "monosplit", "mono-split"],
    "vmc": ["vmc", "ventilation"],
    "split": ["split", "monosplit", "multisplit"],
    # ── Façade / Ravalement ──
    "ravalement": ["ravalement", "enduit extérieur"],
    "hydrofuge": ["hydrofuge", "imperméabilisant"],
    "antimousse": ["antimousse", "anti-mousse", "démoussage"],
    "facade": ["façade", "facade"],
    # ── Isolation ──
    "ite": ["isolation thermique extérieure", "ite ", "fibre de bois"],
    "isolation": ["isolation", "isolant", "laine de verre", "laine de roche"],
    # ── Cuisine ──
    "cuisine": ["cuisine", "agencement cuisine", "cuisine sur-mesure"],
    # ── Peinture ──
    "peinture": ["peinture", "enduit décoratif"],
    # ── Plomberie / Sanitaire ──
    "plomberie": ["plomberie", "sanitaire", "robinetterie"],
    "salle": ["salle de bain", "douche", "baignoire"],
    # ── Électricité ──
    "electricite": ["électricité", "electricite", "tableau électrique"],
    # ── Menuiserie ──
    "menuiserie": ["menuiserie", "porte", "fenêtre", "volet"],
    # ── Terrassement ──
    "terrassement": ["terrassement", "vrd", "assainissement"],
    # ── Démolition ──
    "demolition": ["démolition", "curage", "dépose"],
    # ── Charpente ──
    "charpente": ["charpente", "ossature bois"],
    # ── Serrurerie ──
    "serrurerie": ["serrurerie", "métallerie", "garde-corps"],
    # ── Revêtements ──
    "revetement": ["revêtement", "parquet", "stratifié", "moquette"],
    # ── Étanchéité ──
    "etancheite": ["étanchéité", "toiture terrasse"],
    # ── Chauffage ──
    "chauffage": ["chauffage", "chaudière", "radiateur", "pac", "pompe à chaleur"],
    # ── Photovoltaïque ──
    "photovoltaique": ["photovoltaïque", "panneau solaire"],
    # ── Dépannage ──
    "depannage": ["dépannage", "intervention rapide"],
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

# Static fallback prices when no DB match is found
_FALLBACK_PRICES: Dict[str, float] = {
    "m²": 45.0,
    "m³": 120.0,
    "ml": 15.0,
    "kg": 5.0,
    "u": 2.5,
    "l": 10.0,
    "forfait": 120.0,
}


def _get_fallback_price(unit: str) -> float:
    """Last-resort fallback price when no DB entry is found."""
    return _FALLBACK_PRICES.get(unit, 50.0)


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


def _resolve_price(
    key: str,
    unit: str,
    price_map: Optional[Dict[str, float]],
    *,
    concept_map: Optional[Dict[str, Dict[str, float]]] = None,
) -> float:
    """Look up a price in the preloaded maps, falling back to a static price.

    Resolution order:
    1. Direct material price for known consumables (key with unit suffix)
    2. Exact match in ``price_map`` by normalised key
    3. Concept match in ``concept_map`` — tries full concept, then each word
    4. Static fallback price by unit
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
            return price

        # Split on underscores and try each word (e.g. "toiture", "tuiles")
        # Try longer words first — they are more specific.
        words = [w for w in concept.split("_") if len(w) > 2]
        words.sort(key=len, reverse=True)
        for word in words:
            price = _try_concept(word)
            if price:
                return price

    logger.warning("Prix DB non trouvé pour '%s' (unit=%s), utilisation du prix fallback", key, unit)
    return _get_fallback_price(unit)


async def load_price_map(db: AsyncSession) -> tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    """Pre-load all real prices from the ``bpu_items`` table.

    Returns a tuple of:
    1. ``price_map`` — normalised designation/slug → price
    2. ``concept_map`` — material concept → {unit: price}

    The concept_map enables matching short rule keys like ``carrelage_m2``
    to real BPU prices by extracting the keyword ("carrelage") and
    searching for items whose designation contains that keyword.
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
    # concept_map: concept_name -> {unit: best_price}
    concept_map: Dict[str, Dict[str, float]] = {}

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
                    if concept not in concept_map:
                        concept_map[concept] = {}
                    norm_unit = unit.lower().strip()
                    # Keep the first price found per unit (usually the most generic)
                    if norm_unit not in concept_map[concept]:
                        concept_map[concept][norm_unit] = price
                    break  # one keyword match is enough

    logger.info(
        "Loaded %d price keys + %d concept entries from bpu_items (%d rows).",
        len(price_map),
        sum(len(v) for v in concept_map.values()),
        len(rows),
    )
    return price_map, concept_map

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
        }
        exact_map[p.code_pack] = pack_data
        pack_list.append(pack_data)
        
    logger.info("Loaded %d packs from packs_travaux.", len(exact_map))
    return exact_map, pack_list

def _find_pack(pack_id: str, exact_map: Dict[str, dict], pack_list: List[dict]) -> Optional[dict]:
    # Exact match
    if pack_id in exact_map:
        return exact_map[pack_id]
        
    # Fuzzy match
    norm_pack_id = _normalize_key(pack_id)
    if not norm_pack_id:
        return None
        
    for p in pack_list:
        if norm_pack_id == _normalize_key(p["code_pack"]):
            return p
        if norm_pack_id in _normalize_key(p["nom_pack"]):
            return p
    return None

ISOLATION_TVA_KEYWORDS = [
    "isolation", "isolant", "laine", "sarking", "ite",
    "panneau isolant", "ouate", "polystyrene",
    "polyurethane", "rockwool", "isover",
]

def decide_tva_finale(designation: str, lot_label: str, client_type: str) -> float:
    text = (designation or "").lower()
    lot = (lot_label or "").lower()

    is_isolation_lot = "isolat" in lot
    is_isolation_line = any(kw in text for kw in ISOLATION_TVA_KEYWORDS)

    if is_isolation_lot or is_isolation_line:
        return 5.5
    if client_type == "professionnel" or client_type == "pro":
        return 20.0
    return 10.0

UNIT_MAP = {
    "m2": "m²", "m²": "m²",
    "ml": "ml", "m.l": "ml", "m.l.": "ml",
    "m3": "m³", "m³": "m³",
    "u": "u", "u.": "u", "unite": "u", "unité": "u", "piece": "u", "pièce": "u",
    "ens": "ens", "ensemble": "ens",
    "forfait": "forfait", "ft": "forfait",
    "h": "h", "heure": "h", "heures": "h",
    "j": "j", "jour": "j", "jours": "j",
    "kg": "kg", "t": "t", "tonne": "t", "tonnes": "t",
    "lot": "lot",
}

def normalize_unit(raw: str | None) -> str:
    if not raw:
        return "u"
    return UNIT_MAP.get(raw.strip(), UNIT_MAP.get(raw.strip().lower(), "unknown"))

def extract_surface_m2(description: str) -> float:
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:m[²2]|m[eè]tres?\s*carr[eé]s?)", description, re.I)
    return float(m.group(1).replace(",", ".")) if m else 50.0

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

def calculate_quantity_from_unit(
    unite: str,
    surface_m2: float,
    quantite_pack: float,
    mode_calcul_ml: str | None = None,
    coefficient_ml: float | None = None,
    geometry: dict | None = None,
) -> tuple[float, str]:
    u = normalize_unit(unite).lower().strip()

    if u in ("m²", "m2"):
        return max(surface_m2, 0), "GENERIC_M2_SURFACE"

    if u == "ml":
        mode = mode_calcul_ml
        coeff = coefficient_ml or 1.0

        longueur = geometry.get("length_m") if geometry else surface_m2 / math.sqrt(surface_m2 / 2)
        largeur = geometry.get("width_m") if geometry else math.sqrt(surface_m2 / 2)
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
            return quantite_pack or 1, "ML_FIXED_PACK_QTY"
        elif mode == "AUCUN":
            return 0, "ML_NONE"
        else:
            return quantite_pack or 1, f"ML_UNKNOWN_{mode}"

        return round(qty, 2), f"ML_{mode}"

    if u in ("m³", "m3"):
        return round(surface_m2 * 0.12, 2), "GENERIC_M3_SURFACE_012"

    if u in ("forfait", "u", "ensemble", "lot", "ens"):
        return quantite_pack if quantite_pack >= 1 else 1, "GENERIC_FIXED"

    if u in ("jour", "heure", "h", "j"):
        return quantite_pack if quantite_pack >= 1 else 1, "GENERIC_TIME"

    if u in ("kg", "tonnes", "t"):
        return quantite_pack if quantite_pack >= 1 else surface_m2, "GENERIC_WEIGHT"

    return quantite_pack if quantite_pack >= 1 else surface_m2, "GENERIC_DEFAULT"

def _pad_or_truncate_lines(lines: List[Dict[str, Any]], target_count: int, default_designation: str, tva: float, metier: str = "") -> List[Dict[str, Any]]:
    """Enforces exactly target_count lines. Sums prices if truncated, injects proportional lines if padded."""
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
            
        kept = lines[:target_count-1]
        dropped = lines[target_count-1:]
        dropped_ht = round(sum(l.get("total_ht", 0) for l in dropped), 2)
        kept.append({
            "designation": f"Autres {default_designation.lower()} et finitions",
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

        # Compute proportional padding price based on existing real lines.
        # Ancillary services (prep, cleanup, etc.) are typically ~10-15% of
        # the main work, spread across the padding lines.
        total_real_ht = sum(l.get("total_ht", 0) for l in lines) or 0
        if total_real_ht > 0 and needed > 0:
            # ~15% of total work spread across padding lines
            pad_pu = round(max(25.0, min(85.0, (total_real_ht * 0.15) / needed)), 2)
        elif "Nettoyage" in default_designation:
            pad_pu = 75.0
        elif "Mise en place" in default_designation:
            pad_pu = 95.0
        else:
            pad_pu = 45.0  # low default — better than 120€

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
            
            padded.append({
                "designation": label_suffix,
                "unite": "forfait",
                "quantite": qte,
                "pu_ht": pad_pu,
                "tva": tva,
                "total_ht": round(pad_pu * qte, 2)
            })
        return padded
    return lines

def process_ai_lots(
    lots: List[Dict[str, Any]],
    client_type: str = "particulier",
    project_nature: str = "renovation",
    *,
    surface_m2: float = 50.0,
    user_text: str = "",
    price_map: Optional[Dict[str, float]] = None,
    concept_map: Optional[Dict[str, Dict[str, float]]] = None,
    packs_maps: Optional[tuple[Dict[str, dict], List[dict]]] = None,
) -> List[Dict[str, Any]]:
    """
    Takes the pure semantic AI JSON, evaluates rules, and structures it into
    the strict K+2 blocs architecture with exact line counts.
    """
    if not lots:
        return []

    # Determine global type based on the first pack of the first lot
    global_type = "PRESTATION"
    for lot in lots:
        for pack in lot.get("packs", []):
            if pack.get("type", "").upper() == "DEPANNAGE":
                global_type = "DEPANNAGE"
                break

    is_depannage = (global_type == "DEPANNAGE")
    target_mise_en_place = 1 if is_depannage else 3
    target_intervention = 3 if is_depannage else 14
    target_finition = 1 if is_depannage else 3
    
    global_mise_en_place_lines = []
    global_finition_lines = []
    intervention_blocks = []
    
    for lot in lots:
        metier = lot.get("metier", "Métier inconnu")
        lot_key = lot.get("lot_key", "LOT_01")
        packs = lot.get("packs", [])
        tva = decide_tva_finale("", metier, client_type)
        
        matched_rules = next((rules for code, rules in ALL_METIER_RULES.items() if rules["metier"].lower() in metier.lower()), None)
        
        lot_intervention_lines = []
        
        lot_qty = lot.get("quantite", lot.get("qte", 1))
        try:
            effective_surface = float(lot_qty) if float(lot_qty) > 1 else surface_m2
        except (ValueError, TypeError):
            effective_surface = surface_m2
            
        geometry = compute_geometry(effective_surface, metier, user_text)
        
        for pack in packs:
            pack_id = pack.get("id", "INCONNU")
            quantite_brute = pack.get("quantite", 1)
            
            # Try packs_travaux first
            matched_pack = None
            if packs_maps:
                exact_map, pack_list = packs_maps
                matched_pack = _find_pack(pack_id, exact_map, pack_list)
                
            if matched_pack:
                for line in matched_pack["pack_json"]:
                    qty, rule = calculate_quantity_from_unit(
                        unite=line.get("unite", "forfait"),
                        surface_m2=effective_surface,
                        quantite_pack=line.get("quantite", 1.0),
                        mode_calcul_ml=line.get("mode_calcul_ml"),
                        coefficient_ml=line.get("coefficient_ml"),
                        geometry=geometry
                    )
                    
                    qte_calc = max(round(qty, 2), 0.01)
                    
                    pu_ht = line.get("prix_unitaire_ht", 0.0)
                    total_ht = round(qte_calc * pu_ht, 2)
                    tva = decide_tva_finale(line.get("designation", ""), metier, client_type)
                    
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
                    tva = decide_tva_finale(designation, metier, client_type)
                    
                    qte_calc = safe_eval_formula(rule["formula"], {"surface": quantite_brute, "longueur": quantite_brute, "hauteur": 2.5})
                    pu_ht = _resolve_price(line_key, rule["unit"], price_map, concept_map=concept_map)
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
                clean_pack_id = str(pack_id).replace("_", " ").capitalize()
                fallback_designation = f"Fourniture et pose : {clean_pack_id}"
                tva = decide_tva_finale(fallback_designation, metier, client_type)
                # Determine the correct unit based on quantity
                # If qty > 1, it's most likely m² (surface-based packs)
                if quantite_brute == 1:
                    pack_unit = "forfait"
                elif quantite_brute > 10:
                    pack_unit = "m²"  # Surface-based (toiture, façade, ITE...)
                else:
                    pack_unit = "u"
                # Resolve price from DB using pack keywords
                pu_ht = _resolve_price(pack_id, pack_unit, price_map, concept_map=concept_map)
                total_ht = round(pu_ht * quantite_brute, 2)
                lot_intervention_lines.append({
                    "designation": fallback_designation,
                    "unite": pack_unit,
                    "quantite": quantite_brute,
                    "pu_ht": pu_ht,
                    "tva": tva,
                    "total_ht": total_ht
                })
        
        # Enforce exact line count for THIS intervention block
        base_tva = decide_tva_finale("", metier, client_type)
        lot_intervention_lines = _pad_or_truncate_lines(
            lot_intervention_lines, 
            target_intervention, 
            f"Travaux et fournitures {metier}",
            base_tva,
            metier
        )
        
        intervention_blocks.append({
            "title": f"Intervention: {metier}",
            "lines": lot_intervention_lines
        })
        
    # Enforce exact line counts for global blocks
    global_tva = decide_tva_finale("", "", client_type)
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

    def _map_lines(lines_in: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        for i, l in enumerate(lines_in, 1):
            tva = l.get("tva", 10.0)
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
        "title": "Mise en place et préparation",
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
        "title": "Finition et nettoyage",
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
