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
        "fiche client", "contacter", "particulier", "professionnel",
        "rechercher un client", "modifier un client", "supprimer un client",
        "nombre de chantiers", "email", "téléphone",
    },
    "devis": {
        "devis", "devis ia", "estimation", "générer le devis",
        "générer le devis avec l'ia", "valider le devis",
        "envoyer au client", "télécharger en pdf", "description du projet",
        "type de travaux", "budget estimé", "matériaux souhaités",
        "quantité", "prix unitaire", "total ht", "tva", "total ttc",
        "ligne", "projet",
    },
    "planification": {
        "planification", "planifier chantier", "planifier le chantier",
        "calendrier", "planning", "chantier", "date de début",
        "date de fin", "sélectionner un client", "sélectionner un devis",
        "sélectionner une équipe", "statut", "planifié", "en cours",
        "terminé", "modifier chantier planifié",
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
# Generic UX triggers — if any of these appear we return ALL modules because
# the user is asking a spatial / navigation question whose scope is unclear.
# ---------------------------------------------------------------------------
_GENERIC_UX_KEYWORDS: Final[set[str]] = {
    "bouton", "onglet", "sidebar", "barre latérale",
    "où se trouve", "où est", "comment faire", "comment naviguer",
    "interface", "écran", "cliquer", "appuyer", "ouvrir",
    "menu", "navigation", "dans l'application", "l'application",
    "naviguer dans", "je ne trouve pas", "introuvable",
}

# All known module keys (cached for the "return everything" path).
_ALL_MODULES: Final[frozenset[str]] = frozenset(_MODULE_KEYWORDS)


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

    # 1. Check generic UX triggers first (returns all modules).
    for kw in _GENERIC_UX_KEYWORDS:
        if _has(kw):
            return set(_ALL_MODULES)

    # 2. Check module-specific triggers.
    matched: set[str] = set()
    for module, keywords in _MODULE_KEYWORDS.items():
        for kw in keywords:
            if _has(kw):
                matched.add(module)
                break  # one hit is enough for this module

    return matched


__all__ = ["classify_chat_intent"]
