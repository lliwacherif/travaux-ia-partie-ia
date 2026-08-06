"""Correctifs ciblés à intégrer dans la V3.2 §9 — normalisation entrée vocale.

French spoken numbers and units → canonical text. No invented units (no « mois »).
"""

from __future__ import annotations

import re
from typing import Final

from app.v3.ssot import FORBIDDEN_UNITS

_NUMBER_WORDS: Final[dict[str, str]] = {
    "zero": "0",
    "un": "1",
    "une": "1",
    "deux": "2",
    "trois": "3",
    "quatre": "4",
    "cinq": "5",
    "six": "6",
    "sept": "7",
    "huit": "8",
    "neuf": "9",
    "dix": "10",
    "onze": "11",
    "douze": "12",
    "treize": "13",
    "quatorze": "14",
    "quinze": "15",
    "seize": "16",
    "vingt": "20",
    "trente": "30",
    "quarante": "40",
    "cinquante": "50",
    "soixante": "60",
    "cent": "100",
}

_UNIT_WORDS: Final[dict[str, str]] = {
    "metres carres": "m2",
    "metre carre": "m2",
    "mètres carrés": "m2",
    "mètre carré": "m2",
    "metres": "m",
    "metre": "m",
    "mètres": "m",
    "mètre": "m",
    "metres lineaires": "ml",
    "metre lineaire": "ml",
    "heures": "heure",
    "jours": "jour",
    "unites": "unité",
    "unite": "unité",
}


def _strip_accents(text: str) -> str:
    import unicodedata

    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def normalize_voice_transcript(transcript: str) -> str:
    """Correctifs ciblés à intégrer dans la V3.2 — transcript → texte normalisé."""

    text = " ".join(str(transcript or "").strip().split())
    if not text:
        return ""
    lowered = _strip_accents(text).lower()
    for spoken, canonical in sorted(_UNIT_WORDS.items(), key=lambda item: -len(item[0])):
        lowered = re.sub(rf"\b{re.escape(_strip_accents(spoken))}\b", canonical, lowered)

    # Tens + units compounds: « quarante deux » → 42
    tens = {
        "vingt": 20,
        "trente": 30,
        "quarante": 40,
        "cinquante": 50,
        "soixante": 60,
    }
    units = {
        "un": 1,
        "une": 1,
        "deux": 2,
        "trois": 3,
        "quatre": 4,
        "cinq": 5,
        "six": 6,
        "sept": 7,
        "huit": 8,
        "neuf": 9,
    }
    for ten_word, ten_value in tens.items():
        for unit_word, unit_value in units.items():
            pattern = rf"\b{ten_word}\s+{unit_word}\b"
            lowered = re.sub(pattern, str(ten_value + unit_value), lowered)

    tokens = lowered.split()
    normalized_tokens: list[str] = []
    for token in tokens:
        normalized_tokens.append(_NUMBER_WORDS.get(token, token))
    result = " ".join(normalized_tokens)
    for forbidden in FORBIDDEN_UNITS:
        pattern = rf"\b{re.escape(forbidden.lower())}\b"
        if re.search(pattern, result, flags=re.IGNORECASE):
            raise ValueError(f"FORBIDDEN_VOICE_UNIT:{forbidden}")
    return result


def resolve_pipeline_description(
    *,
    description: str,
    input_mode: str = "TEXT",
    voice_transcript: str | None = None,
) -> tuple[str, str | None]:
    """Return (working_description, normalized_voice_transcript)."""

    mode = str(input_mode or "TEXT").upper()
    if mode != "VOICE":
        return description, None
    source = voice_transcript or description
    normalized = normalize_voice_transcript(source)
    return normalized or description, normalized


__all__ = [
    "normalize_voice_transcript",
    "resolve_pipeline_description",
]
