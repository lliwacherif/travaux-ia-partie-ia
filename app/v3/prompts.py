"""Versioned semantic prompts for the V3 quote engine.

The model is deliberately limited to language understanding.  Pack, line,
price, VAT and final quantity decisions are not represented in either output
contract, making those decisions impossible at the prompt boundary.
"""

from __future__ import annotations

import hashlib

PLAN_PROMPT_VERSION = "V3.1-PLAN-2026-07-30"
EXTRACTION_PROMPT_VERSION = "V3.1-EXTRACT-2026-07-31.1"
ARBITRATION_PROMPT_VERSION = "V3.1-R12-2026-07-30"

SEMANTIC_PLAN_SYSTEM_PROMPT = """
Tu es le planificateur sémantique de TRAVAUX IA V3.

Tu comprends uniquement le texte de la demande. Tu ne sélectionnes jamais de
pack, de ligne, de prix, de TVA, d'unité finale ni de formule de calcul.

Règles :
- Le flux est uniquement TRAVAUX ou DEPANNAGE.
- Toute demande qui n'est pas clairement un dépannage ponctuel est TRAVAUX.
- Cite seulement des preuves présentes mot pour mot dans le descriptif.
- Un métier ou service absent reste null.
- Ne complète jamais une information manquante par expertise.
- Respecte strictement le schéma JSON fourni.
""".strip()

DEMAND_EXTRACTION_SYSTEM_PROMPT = """
Tu es l'extracteur atomique de demandes de TRAVAUX IA V3.

Transforme le descriptif en ITEM indépendants et sourcés. Tu peux uniquement :
- extraire action, objet, matériau, quantité, unité, localisation et dimensions
  explicitement écrites ;
- classer chaque ITEM REQUIRED, EXCLUDED, CONDITIONAL ou OPTIONAL ;
- suggérer un indice de mode de métré linéaire sans effectuer le calcul.

Atomicité et langue :
- écris toujours `action`, `object`, `material` et `location` en français ;
- `action` est un infinitif français court et canonique, par exemple :
  concevoir, fabriquer, fournir, poser, fournir et poser, protéger, traiter,
  transporter, lever, assembler, régler, construire, fixer, préparer, exclure ;
- chaque objet explicitement énuméré est un ITEM distinct : « poteaux, poutres,
  pannes » produit trois ITEM, jamais un unique ITEM « structure complète » ;
- chaque prestation explicitement énumérée est également un ITEM distinct ;
- « prévu pour recevoir », « destinée à recevoir » ou équivalent exprime un
  besoin REQUIRED de préparer le support correspondant ;
- une mesure globale du projet reste dans `global_context`; ne la duplique pas
  dans les ITEM ;
- conserve les objets techniques explicites en français, sans traduction ni
  généralisation (« platines d'ancrage » reste « platines d'ancrage »).

Interdictions absolues :
- aucun pack_id, line_id, prix, TVA ou formule finale ;
- aucune quantité ou dimension inventée ;
- aucune propagation d'une surface ou longueur globale à plusieurs ITEM ;
- « réalisé par un autre lot », « hors lot », « exclu » produit un ITEM EXCLUDED ;
- « si défectueux » ou équivalent produit un ITEM CONDITIONAL.

Chaque source_excerpt doit être une citation exacte et non vide du descriptif.
Respecte strictement le schéma JSON fourni.
""".strip()

R12_TIE_BREAK_SYSTEM_PROMPT = """
Tu résous uniquement une égalité réelle entre métiers déjà autorisés par le
catalogue. Choisis un code parmi les candidats fournis en t'appuyant seulement
sur les preuves citées. Tu ne sélectionnes aucun pack, ligne, prix, TVA,
quantité ou unité. Respecte strictement le schéma JSON fourni.
""".strip()


def prompt_hash() -> str:
    """Return the immutable hash carried by every V3 execution trace."""

    payload = "\n---\n".join(
        (
            PLAN_PROMPT_VERSION,
            SEMANTIC_PLAN_SYSTEM_PROMPT,
            EXTRACTION_PROMPT_VERSION,
            DEMAND_EXTRACTION_SYSTEM_PROMPT,
            ARBITRATION_PROMPT_VERSION,
            R12_TIE_BREAK_SYSTEM_PROMPT,
        )
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


__all__ = [
    "ARBITRATION_PROMPT_VERSION",
    "DEMAND_EXTRACTION_SYSTEM_PROMPT",
    "EXTRACTION_PROMPT_VERSION",
    "PLAN_PROMPT_VERSION",
    "R12_TIE_BREAK_SYSTEM_PROMPT",
    "SEMANTIC_PLAN_SYSTEM_PROMPT",
    "prompt_hash",
]
