"""Correctifs ciblés à intégrer dans la V3.2 — référentiel de codes normalisés.

GPT may extract raw terms; normalized codes come only from this versioned
referential. Uncertain matches stay null / UNKNOWN. No new AI layer.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final, Mapping

from app.v3.context import normalize_text

REFERENTIAL_VERSION: Final = "2026-08-05.correctifs-v1"
UNKNOWN_CODE: Final = "UNKNOWN"

# Minimal seed lexicon — extend via published library without inventing codes at runtime.
_ACTION_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "poser": "ACTION_POSE",
        "pose": "ACTION_POSE",
        "fournir": "ACTION_FOURNITURE",
        "fourniture": "ACTION_FOURNITURE",
        "fabriquer": "ACTION_FABRICATION",
        "fabrication": "ACTION_FABRICATION",
        "demolir": "ACTION_DEMOLITION",
        "demolition": "ACTION_DEMOLITION",
        "deposer": "ACTION_DEPOSE",
        "depose": "ACTION_DEPOSE",
        "isoler": "ACTION_ISOLATION",
        "isolation": "ACTION_ISOLATION",
        "peindre": "ACTION_PEINTURE",
        "peinture": "ACTION_PEINTURE",
        "proteger": "ACTION_PROTECTION",
        "protection": "ACTION_PROTECTION",
        "installer": "ACTION_INSTALLATION",
        "installation": "ACTION_INSTALLATION",
        "remplacer": "ACTION_REMPLACEMENT",
        "remplacement": "ACTION_REMPLACEMENT",
        "reparer": "ACTION_REPARATION",
        "reparation": "ACTION_REPARATION",
    }
)

_OBJECT_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "toiture": "OBJECT_TOITURE",
        "charpente": "OBJECT_CHARPENTE",
        "cuisine": "OBJECT_CUISINE",
        "salle de bain": "OBJECT_SDB",
        "isolation": "OBJECT_ISOLATION",
        "facade": "OBJECT_FACADE",
        "mur": "OBJECT_MUR",
        "sol": "OBJECT_SOL",
        "plafond": "OBJECT_PLAFOND",
        "fenetre": "OBJECT_FENETRE",
        "porte": "OBJECT_PORTE",
        "poteaux": "OBJECT_POTEAUX",
        "pannes": "OBJECT_PANNES",
    }
)

_MATERIAL_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "bois": "MATERIAL_BOIS",
        "acier": "MATERIAL_ACIER",
        "metallique": "MATERIAL_ACIER",
        "metal": "MATERIAL_ACIER",
        "beton": "MATERIAL_BETON",
        "platre": "MATERIAL_PLATRE",
        "laine de verre": "MATERIAL_LAINE_VERRE",
        "laine de roche": "MATERIAL_LAINE_ROCHE",
        "pvc": "MATERIAL_PVC",
        "alu": "MATERIAL_ALUMINIUM",
        "aluminium": "MATERIAL_ALUMINIUM",
    }
)

_LOCATION_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "interieur": "LOCATION_INTERIEUR",
        "exterieur": "LOCATION_EXTERIEUR",
        "combles": "LOCATION_COMBLES",
        "sous sol": "LOCATION_SOUS_SOL",
        "toiture": "LOCATION_TOITURE",
        "facade": "LOCATION_FACADE",
        "cuisine": "LOCATION_CUISINE",
        "salle de bain": "LOCATION_SDB",
    }
)

_WORK_TYPE_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "construction": "WORK_CONSTRUCTION",
        "renovation": "WORK_RENOVATION",
        "extension": "WORK_EXTENSION",
        "depannage": "WORK_DEPANNAGE",
        "entretien": "WORK_ENTRETIEN",
        "isolation": "WORK_ISOLATION",
    }
)

_SYSTEM_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "ossature bois": "SYSTEM_OSSATURE_BOIS",
        "charpente traditionnelle": "SYSTEM_CHARPENTE_TRAD",
        "charpente metallique": "SYSTEM_CHARPENTE_METAL",
        "ite": "SYSTEM_ITE",
        "iti": "SYSTEM_ITI",
        "vmc": "SYSTEM_VMC",
    }
)


def _lookup(raw: str | None, aliases: Mapping[str, str]) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    matching = normalize_text(str(raw)).matching
    if matching in aliases:
        return aliases[matching]
    for alias, code in aliases.items():
        if alias in matching or matching in alias:
            return code
    # Correctifs ciblés à intégrer dans la V3.2 — pas d'invention de code.
    return None


def resolve_action_code(raw: str | None) -> str | None:
    return _lookup(raw, _ACTION_ALIASES)


def resolve_object_family_code(raw: str | None) -> str | None:
    return _lookup(raw, _OBJECT_ALIASES)


def resolve_material_family_code(raw: str | None) -> str | None:
    return _lookup(raw, _MATERIAL_ALIASES)


def resolve_location_code(raw: str | None) -> str | None:
    return _lookup(raw, _LOCATION_ALIASES)


def resolve_work_type_code(raw: str | None) -> str | None:
    return _lookup(raw, _WORK_TYPE_ALIASES)


def resolve_system_code(raw: str | None) -> str | None:
    return _lookup(raw, _SYSTEM_ALIASES)


def resolve_or_unknown(raw: str | None, resolver) -> str:
    code = resolver(raw)
    return code if code is not None else UNKNOWN_CODE


__all__ = [
    "REFERENTIAL_VERSION",
    "UNKNOWN_CODE",
    "resolve_action_code",
    "resolve_location_code",
    "resolve_material_family_code",
    "resolve_object_family_code",
    "resolve_or_unknown",
    "resolve_system_code",
    "resolve_work_type_code",
]
