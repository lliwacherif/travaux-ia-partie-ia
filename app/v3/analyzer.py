"""Layer 2 deterministic flow and trade-signal analysis.

This module contains no model calls.  A semantic plan may be supplied as one
input signal, but it is never treated as catalog authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping
import re

from .context import normalize_text


_DEPANNAGE_SIGNALS: tuple[tuple[str, int], ...] = (
    ("depannage", 6),
    ("urgence", 4),
    ("urgent", 4),
    ("panne", 5),
    ("fuite", 5),
    ("bouche", 5),
    ("court circuit", 5),
    ("ne fonctionne plus", 6),
    ("ne marche plus", 6),
    ("disjoncte", 5),
    ("casse", 4),
    ("reparer", 4),
    ("diagnostic", 2),
)
_TRAVAUX_SIGNALS: tuple[tuple[str, int], ...] = (
    ("construction", 5),
    ("extension", 5),
    ("renovation", 4),
    ("refaire", 4),
    ("installation complete", 5),
    ("structure complete", 5),
    ("fourniture et pose", 4),
    ("pose", 2),
    ("remplacement complet", 5),
    ("creation", 4),
    ("amenagement", 3),
)
_URGENT_SIGNALS = ("urgence", "urgent", "immediat", "au plus vite")


@dataclass(frozen=True, slots=True)
class AnalyzerSignal:
    code: str
    value: str
    weight: int
    evidence: str


@dataclass(frozen=True, slots=True)
class DeterministicAnalysis:
    flow: str
    flow_scores: Mapping[str, int]
    trade_scores: Mapping[str, int]
    service_scores: Mapping[str, int]
    urgency: str
    signals: tuple[AnalyzerSignal, ...]

    @property
    def ranked_trade_codes(self) -> tuple[str, ...]:
        return tuple(
            code
            for code, _ in sorted(
                self.trade_scores.items(),
                key=lambda entry: (-entry[1], entry[0]),
            )
        )

    @property
    def ranked_service_codes(self) -> tuple[str, ...]:
        return tuple(
            code
            for code, _ in sorted(
                self.service_scores.items(),
                key=lambda entry: (-entry[1], entry[0]),
            )
        )


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="python"))
    return {
        name: getattr(value, name)
        for name in dir(value)
        if not name.startswith("_") and not callable(getattr(value, name))
    }


def _description(context: Any) -> str:
    value = getattr(context, "matching_description", None)
    if value is not None:
        return str(value)
    raw = _mapping(context)
    return normalize_text(
        str(raw.get("description") or raw.get("normalized_description") or "")
    ).matching


def _contains(text: str, phrase: str) -> bool:
    phrase = normalize_text(phrase).matching
    return bool(re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text))


def _score_phrases(
    text: str,
    phrases: Iterable[tuple[str, int]],
    signal_prefix: str,
) -> tuple[int, list[AnalyzerSignal]]:
    total = 0
    signals: list[AnalyzerSignal] = []
    for phrase, weight in phrases:
        if _contains(text, phrase):
            total += weight
            signals.append(
                AnalyzerSignal(
                    code=f"{signal_prefix}:{normalize_text(phrase).matching}",
                    value=signal_prefix,
                    weight=weight,
                    evidence=phrase,
                )
            )
    return total, signals


def _score_lexicon(
    text: str,
    lexicon: Mapping[str, Iterable[str]] | None,
    prefix: str,
) -> tuple[dict[str, int], list[AnalyzerSignal]]:
    scores: dict[str, int] = {}
    signals: list[AnalyzerSignal] = []
    for code, terms in sorted((lexicon or {}).items()):
        code_score = 0
        for term in sorted(set(terms)):
            if _contains(text, term):
                # Longer phrases are more discriminating, but the scale remains
                # integral and deterministic.
                weight = min(5, max(1, len(normalize_text(term).matching.split())))
                code_score += weight
                signals.append(
                    AnalyzerSignal(
                        code=f"{prefix}:{code}:{normalize_text(term).matching}",
                        value=code,
                        weight=weight,
                        evidence=term,
                    )
                )
        if code_score:
            scores[str(code)] = code_score
    return scores, signals


def deterministic_analyze(
    context: Any,
    plan: Any | None = None,
    *,
    trade_lexicon: Mapping[str, Iterable[str]] | None = None,
    service_lexicon: Mapping[str, Iterable[str]] | None = None,
) -> DeterministicAnalysis:
    """Return reproducible flow, urgency, trade, and service signals."""

    text = _description(context)
    plan_data = _mapping(plan)
    depannage_score, depannage_signals = _score_phrases(
        text, _DEPANNAGE_SIGNALS, "DEPANNAGE"
    )
    travaux_score, travaux_signals = _score_phrases(
        text, _TRAVAUX_SIGNALS, "TRAVAUX"
    )

    plan_flow = str(plan_data.get("flow_hint") or "").upper()
    signals = depannage_signals + travaux_signals
    if plan_flow in {"TRAVAUX", "DEPANNAGE"}:
        if plan_flow == "DEPANNAGE":
            depannage_score += 1
        else:
            travaux_score += 1
        signals.append(
            AnalyzerSignal(
                code="SEMANTIC_PLAN_FLOW_HINT",
                value=plan_flow,
                weight=1,
                evidence=plan_flow,
            )
        )

    # Product invariant: every non-Dépannage request is Travaux.  An exact tie
    # therefore resolves to TRAVAUX, never to a third flow.
    flow = "DEPANNAGE" if depannage_score > travaux_score else "TRAVAUX"
    urgency = (
        "URGENTE"
        if any(_contains(text, phrase) for phrase in _URGENT_SIGNALS)
        else str(plan_data.get("urgency") or "INCONNUE").upper()
    )
    if urgency not in {"NORMALE", "URGENTE", "INCONNUE"}:
        urgency = "INCONNUE"

    trade_scores, trade_signals = _score_lexicon(text, trade_lexicon, "TRADE")
    service_scores, service_signals = _score_lexicon(
        text, service_lexicon, "SERVICE"
    )
    signals.extend(trade_signals)
    signals.extend(service_signals)

    primary_hint = plan_data.get("primary_trade_hint")
    if primary_hint and str(primary_hint) in (trade_lexicon or {}):
        code = str(primary_hint)
        trade_scores[code] = trade_scores.get(code, 0) + 1
        signals.append(
            AnalyzerSignal(
                code="SEMANTIC_PLAN_PRIMARY_TRADE_HINT",
                value=code,
                weight=1,
                evidence=code,
            )
        )
    service_hint = plan_data.get("service_hint")
    if service_hint and str(service_hint) in (service_lexicon or {}):
        code = str(service_hint)
        service_scores[code] = service_scores.get(code, 0) + 1
        signals.append(
            AnalyzerSignal(
                code="SEMANTIC_PLAN_SERVICE_HINT",
                value=code,
                weight=1,
                evidence=code,
            )
        )

    return DeterministicAnalysis(
        flow=flow,
        flow_scores=MappingProxyType(
            {"TRAVAUX": travaux_score, "DEPANNAGE": depannage_score}
        ),
        trade_scores=MappingProxyType(dict(sorted(trade_scores.items()))),
        service_scores=MappingProxyType(dict(sorted(service_scores.items()))),
        urgency=urgency,
        signals=tuple(signals),
    )


# Orchestrator-compatible alias from the V3 specification.
deterministic_analyzer = deterministic_analyze


__all__ = [
    "AnalyzerSignal",
    "DeterministicAnalysis",
    "deterministic_analyze",
    "deterministic_analyzer",
]
