"""Versioned R1-R12 trade arbitration.

R1-R11 are deterministic and executed in registry order.  R12 is deliberately
not implemented here: callers may provide a callback owned by the semantic
layer, and that callback is invoked only when the deterministic final scores
have a real exact tie.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .contracts import TradeArbitration


ARBITRATION_REGISTRY_VERSION = "2026-07-30.1"


@dataclass(frozen=True, slots=True)
class ArbitrationRule:
    rule_id: str
    version: str
    description: str


ARBITRATION_RULE_REGISTRY: tuple[ArbitrationRule, ...] = (
    ArbitrationRule("R1", ARBITRATION_REGISTRY_VERSION, "Keep active catalog trades in the decided flow."),
    ArbitrationRule("R2", ARBITRATION_REGISTRY_VERSION, "Score exact deterministic analyzer trade evidence."),
    ArbitrationRule("R3", ARBITRATION_REGISTRY_VERSION, "Score a valid semantic primary-trade hint."),
    ArbitrationRule("R4", ARBITRATION_REGISTRY_VERSION, "Score valid semantic secondary-trade hints."),
    ArbitrationRule("R5", ARBITRATION_REGISTRY_VERSION, "Score exact service-hint ownership."),
    ArbitrationRule("R6", ARBITRATION_REGISTRY_VERSION, "Score deterministic analyzer service evidence."),
    ArbitrationRule("R7", ARBITRATION_REGISTRY_VERSION, "Enforce enabled service codes for Dépannage."),
    ArbitrationRule("R8", ARBITRATION_REGISTRY_VERSION, "Prefer the active company primary trade."),
    ArbitrationRule("R9", ARBITRATION_REGISTRY_VERSION, "Prefer a unique owner of the resolved service."),
    ArbitrationRule("R10", ARBITRATION_REGISTRY_VERSION, "Reward high-confidence corroborated semantic evidence."),
    ArbitrationRule("R11", ARBITRATION_REGISTRY_VERSION, "Select the unique highest deterministic score."),
)


@dataclass(frozen=True, slots=True)
class TradeOption:
    trade_code: str
    flow: str
    service_codes: tuple[str, ...] = ()
    active: bool = True


@dataclass(frozen=True, slots=True)
class ArbitrationEvidence:
    flow: str
    tied_trade_codes: tuple[str, ...]
    scores: Mapping[str, Decimal]
    analyzer_trade_scores: Mapping[str, int]
    analyzer_service_scores: Mapping[str, int]
    semantic_primary_hint: str | None
    semantic_service_hint: str | None
    company_primary_trade_code: str
    enabled_service_codes: tuple[str, ...]
    registry_version: str = ARBITRATION_REGISTRY_VERSION


R12Callback = Callable[[tuple[str, ...], ArbitrationEvidence], str | None]


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dict(dump(mode="python"))
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    return {
        key: item
        for key, item in vars(value).items()
        if not key.startswith("_")
    }


def _catalog_options(catalog: Any) -> tuple[TradeOption, ...]:
    if isinstance(catalog, Mapping):
        values: Iterable[tuple[str | None, Any]] = (
            (str(code), value) for code, value in catalog.items()
        )
    else:
        values = ((None, value) for value in (catalog or ()))

    options: list[TradeOption] = []
    for key_code, raw_value in values:
        raw = _mapping(raw_value)
        code = str(
            raw.get("trade_code")
            or raw.get("code")
            or key_code
            or ""
        ).strip()
        if not code:
            continue
        services = raw.get("service_codes")
        if services is None and raw.get("service_code"):
            services = (raw["service_code"],)
        options.append(
            TradeOption(
                trade_code=code,
                flow=str(raw.get("flow") or "TRAVAUX").upper(),
                service_codes=tuple(
                    sorted(
                        {
                            str(service).strip()
                            for service in services or ()
                            if str(service).strip()
                        }
                    )
                ),
                active=bool(raw.get("active", True)),
            )
        )
    return tuple(sorted(options, key=lambda option: option.trade_code))


def _context_parts(context: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    context_data = _mapping(context)
    company_value = getattr(context, "company", context_data.get("company"))
    project_value = getattr(context, "project", context_data.get("project"))
    return _mapping(company_value), _mapping(project_value)


def _analysis_parts(analysis: Any) -> tuple[str, dict[str, int], dict[str, int]]:
    data = _mapping(analysis)
    flow = str(getattr(analysis, "flow", data.get("flow") or "TRAVAUX")).upper()
    trade_scores = dict(
        getattr(analysis, "trade_scores", data.get("trade_scores") or {})
    )
    service_scores = dict(
        getattr(analysis, "service_scores", data.get("service_scores") or {})
    )
    return flow, trade_scores, service_scores


def _resolve_service_code(
    flow: str,
    plan: Mapping[str, Any],
    service_scores: Mapping[str, int],
    enabled_services: tuple[str, ...],
    fallback_service_code: str | None,
) -> str | None:
    if flow != "DEPANNAGE":
        return None
    allowed = set(enabled_services)
    hint = str(plan.get("service_hint") or "").strip()
    if hint and (not allowed or hint in allowed):
        return hint
    ranked = sorted(service_scores.items(), key=lambda entry: (-entry[1], entry[0]))
    for code, _ in ranked:
        if not allowed or code in allowed:
            return code
    if fallback_service_code and (
        not allowed or fallback_service_code in allowed
    ):
        return fallback_service_code
    return enabled_services[0] if enabled_services else None


def arbitrate_r1_r12(
    context: Any,
    analysis: Any,
    plan: Any | None,
    trade_catalog: Any,
    *,
    r12_callback: R12Callback | None = None,
    fallback_service_code: str | None = None,
) -> TradeArbitration:
    """Apply R1-R11, then optionally R12 for an exact highest-score tie."""

    plan_data = _mapping(plan)
    company, _project = _context_parts(context)
    flow, analyzer_trade_scores, analyzer_service_scores = _analysis_parts(analysis)
    if flow not in {"TRAVAUX", "DEPANNAGE"}:
        flow = "TRAVAUX"

    company_trade = str(company.get("primary_trade_code") or "").strip()
    enabled_services = tuple(
        sorted(
            {
                str(code).strip()
                for code in company.get("enabled_service_codes") or ()
                if str(code).strip()
            }
        )
    )
    options = [
        option
        for option in _catalog_options(trade_catalog)
        if option.active and option.flow == flow
    ]
    if not options:
        if not company_trade:
            raise ValueError("No active catalog trade and no company primary trade")
        options = [TradeOption(company_trade, flow)]

    service_code = _resolve_service_code(
        flow,
        plan_data,
        analyzer_service_scores,
        enabled_services,
        fallback_service_code,
    )

    # R7 is a hard gate only when catalog ownership is available.  It never
    # empties the candidate set; that would violate the always-emit strategy.
    if flow == "DEPANNAGE" and service_code:
        service_owned = [
            option for option in options if service_code in option.service_codes
        ]
        if service_owned:
            options = service_owned

    scores: dict[str, Decimal] = {
        option.trade_code: Decimal("0") for option in options
    }
    option_by_code = {option.trade_code: option for option in options}

    # R2: deterministic analyzer evidence.
    for code, analyzer_score in analyzer_trade_scores.items():
        if code in scores:
            scores[code] += Decimal(str(analyzer_score)) * Decimal("10")

    # R3/R4: semantic hints are weak evidence and must exist in the hard-gated
    # catalog set before they receive any score.
    primary_hint = str(plan_data.get("primary_trade_hint") or "").strip() or None
    if primary_hint in scores:
        scores[primary_hint] += Decimal("7")
    for code in {
        str(value).strip()
        for value in plan_data.get("secondary_trade_hints") or ()
        if str(value).strip()
    }:
        if code in scores:
            scores[code] += Decimal("2")

    # R5/R6/R9: service ownership.  Only official catalog relationships count.
    semantic_service_hint = (
        str(plan_data.get("service_hint") or "").strip() or None
    )
    for code, option in option_by_code.items():
        if semantic_service_hint in option.service_codes:
            scores[code] += Decimal("9")
        owned_signal_score = sum(
            analyzer_service_scores.get(service, 0)
            for service in option.service_codes
        )
        scores[code] += Decimal(owned_signal_score) * Decimal("5")
    if service_code:
        owners = [
            option.trade_code
            for option in options
            if service_code in option.service_codes
        ]
        if len(owners) == 1:
            scores[owners[0]] += Decimal("3")

    # R8: account scope is the deterministic safety preference.
    if company_trade in scores:
        scores[company_trade] += Decimal("4")

    # R10: confidence only reinforces a catalog-valid, already explicit hint.
    confidence = Decimal(str(plan_data.get("confidence") or 0))
    if confidence >= Decimal("0.8") and primary_hint in scores:
        scores[primary_hint] += Decimal("2")

    highest = max(scores.values())
    tied = tuple(sorted(code for code, score in scores.items() if score == highest))
    rule_id = "R11"
    chosen: str
    if len(tied) == 1:
        chosen = tied[0]
    else:
        evidence = ArbitrationEvidence(
            flow=flow,
            tied_trade_codes=tied,
            scores=MappingProxyType(dict(scores)),
            analyzer_trade_scores=MappingProxyType(dict(analyzer_trade_scores)),
            analyzer_service_scores=MappingProxyType(dict(analyzer_service_scores)),
            semantic_primary_hint=primary_hint,
            semantic_service_hint=semantic_service_hint,
            company_primary_trade_code=company_trade,
            enabled_service_codes=enabled_services,
        )
        if r12_callback is not None:
            callback_choice = r12_callback(tied, evidence)
            if callback_choice in tied:
                chosen = str(callback_choice)
                rule_id = "R12"
            else:
                # Invalid R12 output has no authority.  Use the documented
                # deterministic company/lexical fallback.
                chosen = company_trade if company_trade in tied else tied[0]
        else:
            chosen = company_trade if company_trade in tied else tied[0]

    secondary = tuple(
        code
        for code, _score in sorted(
            scores.items(), key=lambda entry: (-entry[1], entry[0])
        )
        if code != chosen and _score > 0
    )
    flow_hint = str(plan_data.get("flow_hint") or "").upper()
    arbitrage_applied = (
        (primary_hint is not None and primary_hint != chosen)
        or (flow_hint in {"TRAVAUX", "DEPANNAGE"} and flow_hint != flow)
        or (
            flow == "DEPANNAGE"
            and semantic_service_hint is not None
            and semantic_service_hint != service_code
        )
        or rule_id == "R12"
    )
    if highest <= 0:
        confidence_value = Decimal("0.25")
    else:
        ordered_scores = sorted(scores.values(), reverse=True)
        second = ordered_scores[1] if len(ordered_scores) > 1 else Decimal("0")
        margin = max(Decimal("0"), highest - second)
        confidence_value = min(
            Decimal("1"),
            Decimal("0.5") + (margin / (highest + Decimal("1"))),
        )

    return TradeArbitration.model_validate(
        {
            "flow": flow,
            "primary_trade_code": chosen,
            "secondary_trade_codes": list(secondary),
            "service_code": service_code,
            "rule_id": rule_id,
            "arbitrage_applied": arbitrage_applied,
            "confidence": float(confidence_value),
        }
    )


# Specification-compatible name.
arbitrate_trade = arbitrate_r1_r12


__all__ = [
    "ARBITRATION_REGISTRY_VERSION",
    "ARBITRATION_RULE_REGISTRY",
    "ArbitrationEvidence",
    "ArbitrationRule",
    "R12Callback",
    "TradeOption",
    "arbitrate_r1_r12",
    "arbitrate_trade",
]
