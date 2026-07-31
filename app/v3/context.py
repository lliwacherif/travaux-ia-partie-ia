"""Layer 0: lossless, deterministic request normalization.

The normalized forms in this module are search aids only.  The original
description is always retained and remains the source of truth for excerpts,
auditing, and display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping
import re
import unicodedata


_DASHES = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
        "\u202f": " ",
    }
)
_SPACE_RE = re.compile(r"\s+")
_MATCH_PUNCTUATION_RE = re.compile(r"[^\w.+/-]+", re.UNICODE)

_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?<!\w)(?:m(?:e|è)tres?\s*(?:carr(?:e|é)s?)?|m\s*[²2]|m2)(?!\w)",
            re.IGNORECASE,
        ),
        "M2",
    ),
    (
        re.compile(
            r"(?<!\w)(?:m(?:e|è)tres?\s*(?:cubes?|cubiques?)|m\s*[³3]|m3)(?!\w)",
            re.IGNORECASE,
        ),
        "M3",
    ),
    (
        re.compile(
            r"(?<!\w)(?:m(?:e|è)tres?\s*lin(?:e|é)aires?|ml)(?!\w)",
            re.IGNORECASE,
        ),
        "ML",
    ),
    (
        re.compile(r"(?<!\w)(?:millim(?:e|è)tres?|mm)(?!\w)", re.IGNORECASE),
        "MM",
    ),
    (
        re.compile(r"(?<!\w)(?:centim(?:e|è)tres?|cm)(?!\w)", re.IGNORECASE),
        "CM",
    ),
)

UNIT_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "M²": "M2",
        "M2": "M2",
        "METRE CARRE": "M2",
        "METRES CARRES": "M2",
        "M³": "M3",
        "M3": "M3",
        "METRE CUBE": "M3",
        "METRES CUBES": "M3",
        "ML": "ML",
        "METRE LINEAIRE": "ML",
        "METRES LINEAIRES": "ML",
        "M": "M",
        "METRE": "M",
        "METRES": "M",
        "MM": "MM",
        "MILLIMETRE": "MM",
        "MILLIMETRES": "MM",
        "CM": "CM",
        "CENTIMETRE": "CM",
        "CENTIMETRES": "CM",
        "U": "UNIT",
        "UN": "UNIT",
        "UNITE": "UNIT",
        "UNITES": "UNIT",
        "UNIT": "UNIT",
        "H": "HOUR",
        "HEURE": "HOUR",
        "HEURES": "HOUR",
        "J": "DAY",
        "JOUR": "DAY",
        "JOURS": "DAY",
        "FORFAIT": "FORFAIT",
        "T": "TONNE",
        "TONNE": "TONNE",
        "TONNES": "TONNE",
    }
)


@dataclass(frozen=True, slots=True)
class Assumption:
    """An explicit default used while enriching otherwise missing context."""

    code: str
    field: str
    value: Any
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedText:
    """Lossless source plus canonical and accent-insensitive search forms."""

    source: str
    canonical: str
    matching: str


@dataclass(frozen=True, slots=True)
class NormalizedPipelineContext:
    """Pure layer-0 output consumed by deterministic V3 stages."""

    request_id: str
    description: str
    normalized_description: str
    matching_description: str
    company: Mapping[str, Any]
    project: Mapping[str, Any]
    assumptions: tuple[Assumption, ...] = field(default_factory=tuple)

    @property
    def original_description(self) -> str:
        return self.description


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    raise TypeError(f"Expected a mapping-like value, got {type(value).__name__}")


def strip_accents(value: str) -> str:
    """Return an accent-insensitive representation without changing source."""

    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_unit(unit: str | None) -> str | None:
    """Normalize a standalone French/metric unit to its V3 code."""

    if unit is None:
        return None
    key = strip_accents(unicodedata.normalize("NFKC", str(unit))).upper()
    key = _SPACE_RE.sub(" ", key.translate(_DASHES)).strip(" .")
    return UNIT_ALIASES.get(key, key)


def normalize_text(source: str) -> NormalizedText:
    """Create deterministic Unicode, unit, and matching forms of ``source``."""

    if not isinstance(source, str):
        raise TypeError("source must be a string")
    canonical = unicodedata.normalize("NFKC", source).translate(_DASHES)
    canonical = _SPACE_RE.sub(" ", canonical).strip()
    for pattern, replacement in _UNIT_PATTERNS:
        canonical = pattern.sub(replacement, canonical)

    matching = strip_accents(canonical).casefold()
    matching = _MATCH_PUNCTUATION_RE.sub(" ", matching)
    matching = _SPACE_RE.sub(" ", matching).strip()
    return NormalizedText(source=source, canonical=canonical, matching=matching)


def normalize_and_enrich(pipeline_input: Any) -> NormalizedPipelineContext:
    """Normalize a ``PipelineInput`` without mutating it or hiding defaults.

    Only the SSOT country default is applied here.  Unknown customer, building,
    age, and location values remain unknown; inventing them would affect fiscal
    decisions downstream.
    """

    raw = _as_mapping(pipeline_input)
    description = str(raw.get("description") or "")
    normalized = normalize_text(description)
    company = _as_mapping(raw.get("company"))
    project = _as_mapping(raw.get("project"))
    assumptions: list[Assumption] = []

    country = project.get("country")
    if country in (None, ""):
        project["country"] = "FR"
        assumptions.append(
            Assumption(
                code="CONTEXT_DEFAULT_COUNTRY_FR",
                field="project.country",
                value="FR",
                reason="The request omitted the country; the V3 SSOT default is FR.",
            )
        )
    else:
        project["country"] = str(country).upper()

    company["primary_trade_code"] = str(
        company.get("primary_trade_code") or ""
    ).strip()
    company["enabled_service_codes"] = tuple(
        sorted(
            {
                str(code).strip()
                for code in company.get("enabled_service_codes", ())
                if str(code).strip()
            }
        )
    )

    for field_name in (
        "customer_type",
        "building_use",
        "location",
    ):
        if field_name not in project:
            project[field_name] = None
    if "building_age_years" not in project:
        project["building_age_years"] = None

    return NormalizedPipelineContext(
        request_id=str(raw.get("request_id") or "").strip(),
        description=description,
        normalized_description=normalized.canonical,
        matching_description=normalized.matching,
        company=MappingProxyType(company),
        project=MappingProxyType(project),
        assumptions=tuple(assumptions),
    )


__all__ = [
    "Assumption",
    "NormalizedPipelineContext",
    "NormalizedText",
    "UNIT_ALIASES",
    "normalize_and_enrich",
    "normalize_text",
    "normalize_unit",
    "strip_accents",
]
