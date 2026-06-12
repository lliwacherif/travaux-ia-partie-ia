"""Lightweight keyword-based intent classifier for the chatbot.

Determines whether the user's message is:

* A **UX / navigation question** (→ inject relevant module guides)
* A **pure BTP domain question** (→ no UX context needed, keep prompt lean)

No LLM call is made — classification is a simple keyword scan so it adds
zero latency and zero cost.
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# Module-specific triggers
# ---------------------------------------------------------------------------
# Each key matches a module in ``CHATBOT_UX_MODULES`` (see prompts.py).
# The values are sets of lowercase keywords / short phrases.  A hit on any
# keyword maps the user's question to that module.

_MODULE_KEYWORDS: Final[dict[str, set[str]]] = {
    "dashboard": {
        "dashboard", "tableau de bord", "total de chantiers",
        "équipes actives", "devis générés", "chantiers en cours",
        "statistiques", "stats", "métrique", "métriques", "kpi",
        "timeline", "vue globale",
    },
    "clients": {
        "client", "crm", "ajouter un client", "nouveau client",
        "fiche client", "contacter",
        "rechercher un client", "modifier un client", "supprimer un client",
        "nombre de chantiers",
    },
    "devis": {
        "devis", "devis ia", "offre", "offre détaillée", "offre detaillee",
        "estimation", "générer le devis", "generer un devis",
        "générer une offre", "generer une offre",
        "générer le devis avec l'ia", "valider le devis",
        "envoyer au client", "télécharger en pdf", "description du projet",
        "type de travaux", "budget estimé", "matériaux souhaités",
        "prix unitaire", "total ht", "total ttc",
    },
    "planification": {
        "planification", "planifier chantier", "planifier le chantier",
        "calendrier", "planning", "chantier", "date de début",
        "date de fin", "sélectionner un client", "sélectionner un devis",
        "sélectionner une équipe", "statut chantier", "chantier planifié",
        "modifier chantier planifié",
        "enregistrer les modifications",
    },
    "equipes": {
        "équipe", "équipes", "gérer les équipes", "créer une équipe",
        "modifier l'équipe", "chef d'équipe", "membres",
        "compétences clés", "disponible", "sur un chantier",
        "mettre à jour l'équipe", "nombre de membres",
    },
    "assistant": {
        "accès rapide", "assistant", "aide", "support",
        "suggestion", "copilote",
    },
}

# ---------------------------------------------------------------------------
# Generic UX triggers — if any of these appear without a module-specific hit,
# we inject only the small global assistant guide instead of every module.
# ---------------------------------------------------------------------------
_GENERIC_UX_KEYWORDS: Final[set[str]] = {
    "bouton", "onglet", "sidebar", "barre latérale",
    "où se trouve", "où est", "comment faire", "comment naviguer",
    "interface", "écran", "cliquer", "appuyer", "ouvrir",
    "menu", "navigation", "dans l'application", "l'application",
    "naviguer dans", "je ne trouve pas", "introuvable",
}

def classify_chat_intent(text: str) -> set[str]:
    """Return the set of UX-module keys relevant to *text*.

    Returns
    -------
    set[str]
        A subset of ``{"dashboard", "clients", "devis", "planification",
        "equipes", "assistant"}``.  An **empty set** means the question is a
        pure BTP / domain question and no UX guide should be injected.
    """
    lowered = text.lower()

    def _has(keyword: str) -> bool:
        """Word-boundary aware keyword match to avoid false positives."""
        # Multi-word phrases can safely use substring matching.
        if " " in keyword:
            return keyword in lowered
        # Single words need word boundaries so 'jour' doesn't match 'bonjour'.
        return bool(re.search(rf"\b{re.escape(keyword)}\b", lowered))

    # 1. Check module-specific triggers first. This keeps prompts lean for
    # common questions like "comment créer un devis ?" or "où est Ajouter un
    # client ?" even though they also contain generic UX words.
    matched: set[str] = set()
    for module, keywords in _MODULE_KEYWORDS.items():
        for kw in keywords:
            if _has(kw):
                matched.add(module)
                break  # one hit is enough for this module

    if matched:
        return matched

    # 2. Generic UI question with no clear module: inject only the compact
    # assistant/global navigation guide, not the full application map.
    for kw in _GENERIC_UX_KEYWORDS:
        if _has(kw):
            return {"assistant"}

    return matched


__all__ = ["classify_chat_intent"]
