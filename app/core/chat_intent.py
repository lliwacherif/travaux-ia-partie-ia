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
    "finance": {
        "finance", "dashboard", "tableau de bord", "chiffre d'affaires",
        "ca ", "bénéfice", "profit", "kpi", "graphique", "courbe",
        "transaction", "acompte", "facturé", "signé",
    },
    "clients": {
        "client", "crm", "ajouter un client", "nouveau client",
        "fiche client", "contacter", "particulier", "professionnel",
        "rechercher un client",
    },
    "devis": {
        "devis", "estimation", "générer le devis", "ligne",
        "bibliothèque", "dictée vocale", "microphone",
        "ajouter une ligne", "projet",
    },
    "planification": {
        "planification", "calendrier", "planning", "chantier",
        "équipe", "vue jour", "vue semaine", "vue mois",
        "statut", "archiver",
    },
    "assistant": {
        "accès rapide", "assistant", "aide", "support",
        "suggestion", "optimiser les trajets",
    },
}

# ---------------------------------------------------------------------------
# Generic UX triggers — if any of these appear we return ALL modules because
# the user is asking a spatial / navigation question whose scope is unclear.
# ---------------------------------------------------------------------------
_GENERIC_UX_KEYWORDS: Final[set[str]] = {
    "bouton", "menu hamburger", "onglet",
    "où se trouve", "où est", "comment faire", "comment naviguer",
    "interface", "écran", "cliquer", "appuyer",
    "sur mobile", "sur desktop", "sur le web", "l'application",
    "naviguer dans",
}

# All known module keys (cached for the "return everything" path).
_ALL_MODULES: Final[frozenset[str]] = frozenset(_MODULE_KEYWORDS)


def classify_chat_intent(text: str) -> set[str]:
    """Return the set of UX-module keys relevant to *text*.

    Returns
    -------
    set[str]
        A subset of ``{"finance", "clients", "devis", "planification",
        "assistant"}``.  An **empty set** means the question is a pure BTP /
        domain question and no UX guide should be injected.
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
