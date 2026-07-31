"""Strict semantic planning and atomic extraction for V3.

GPT is used only behind contracts that do not contain catalog identifiers or
commercial data.  Deterministic fallbacks preserve the "devis obligatoire"
invariant when the provider is unavailable.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.core.config import settings
from app.v3.contracts import (
    DemandDimension,
    DemandGlobalContext,
    DemandItem,
    DemandMatrix,
    DemandStatus,
    DimensionKind,
    DimensionUnit,
    PipelineInput,
    QuantityUnit,
    SemanticPlan,
    TradeArbitration,
    Urgency,
)
from app.v3.prompts import (
    DEMAND_EXTRACTION_SYSTEM_PROMPT,
    SEMANTIC_PLAN_SYSTEM_PROMPT,
)
from app.v3.ssot import Flow

ContractT = TypeVar("ContractT", bound=BaseModel)
SemanticFallback = Callable[[], ContractT]


class SemanticProviderError(RuntimeError):
    """Raised only when primary and deterministic semantic paths both fail."""


def _json_schema_format(name: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": model.model_json_schema(),
        },
    }


def semantic_cache_key(
    input_: PipelineInput,
    *,
    prompt_hash: str,
    ssot_version: str,
    library_version: str,
) -> str:
    canonical = json.dumps(
        input_.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    payload = "|".join((canonical, prompt_hash, ssot_version, library_version))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _contains_explicit_term(text: str, term: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text))


def deterministic_plan(input_: PipelineInput) -> SemanticPlan:
    """Authorized lexical fallback; it never selects a pack or final line."""

    folded = _fold(input_.description)
    depannage_tokens = (
        "depannage",
        "panne",
        "fuite",
        "urgence",
        "ne fonctionne plus",
        "bouche",
        "court-circuit",
    )
    flow = (
        Flow.DEPANNAGE
        if any(_contains_explicit_term(folded, token) for token in depannage_tokens)
        else Flow.TRAVAUX
    )

    trade_hint: str | None = None
    evidence: list[str] = []
    trade_rules: tuple[tuple[tuple[str, ...], str], ...] = (
        (
            ("structure metallique", "charpente metallique", "poutre acier"),
            "CHARPENTE_METALLIQUE",
        ),
        (
            ("charpente traditionnelle", "charpente bois", "ferme", "chevron"),
            "CHARPENTE_BOIS",
        ),
        (("electric", "tableau", "prise"), "ELECTRICITE"),
        (("plomberie", "canalisation", "sanitaire"), "PLOMBERIE"),
        (("peinture", "enduit"), "PEINTURE"),
        (("cuisine", "renovation cuisine", "rénover cuisine"), "CUISINE"),
    )
    for terms, code in trade_rules:
        matched = next((term for term in terms if term in folded), None)
        if matched:
            trade_hint = code
            evidence.append(matched)
            break
    if trade_hint is None and input_.company.primary_trade_code:
        trade_hint = input_.company.primary_trade_code

    urgency = (
        Urgency.URGENTE
        if any(token in folded for token in ("urgent", "urgence", "immediat"))
        else Urgency.NORMALE
    )
    return SemanticPlan.model_construct(
        flow_hint=flow,
        primary_trade_hint=trade_hint,
        secondary_trade_hints=[],
        service_hint=None,
        urgency=urgency,
        confidence=0.72 if evidence else 0.35,
        evidence=evidence,
    )


_AREA_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:m2|m²)", re.I)
_LENGTH_RE = re.compile(r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:ml|mètre(?:s)?)\b", re.I)


def _explicit_global_context(description: str) -> DemandGlobalContext:
    area_match = _AREA_RE.search(description)
    length_match = _LENGTH_RE.search(description)
    area = (
        float(area_match.group("value").replace(",", "."))
        if area_match
        else None
    )
    length = (
        float(length_match.group("value").replace(",", "."))
        if length_match
        else None
    )
    return DemandGlobalContext.model_construct(
        global_area_m2=area,
        global_length_ml=length,
        notes=[],
    )


def _fallback_item(
    index: int,
    *,
    action: str,
    object_: str,
    material: str | None,
    source: str,
    status: DemandStatus = DemandStatus.REQUIRED,
    quantity: float | None = None,
    unit: QuantityUnit | None = None,
    condition: str | None = None,
) -> DemandItem:
    return DemandItem.model_construct(
        request_item_id=f"ITEM-{index:03d}",
        action=action,
        object=object_,
        material=material,
        quantity=quantity,
        unit=unit,
        dimensions=[],
        linear_measurement_hint=None,
        location=None,
        status=status,
        condition=condition,
        source_excerpt=source,
    )


def _cuisine_renovation_items(description: str) -> list[DemandItem]:
    """Expand a vague kitchen renovation into pack-aligned atomic ITEMs."""

    requested = (
        ("préparer", "chantier"),
        ("déposer", "ancienne cuisine"),
        ("fournir et poser", "meubles bas"),
        ("fournir et poser", "meubles hauts"),
        ("fournir et poser", "plan de travail"),
        ("fournir et poser", "plinthes"),
        ("étanchéifier", "joints"),
        ("nettoyer", "chantier"),
    )
    return [
        _fallback_item(
            index,
            action=action,
            object_=object_,
            material="agencement",
            source=description,
        )
        for index, (action, object_) in enumerate(requested, start=1)
    ]


def _is_vague_cuisine_item(item: DemandItem) -> bool:
    folded_object = _fold(item.object)
    folded_action = _fold(item.action)
    vague_objects = {
        "cuisine",
        "renovation cuisine",
        "travaux decrits",
        "travaux décrits",
        "agencement",
        "ouvrage",
    }
    vague_actions = {
        "realiser",
        "renover",
        "renover",
        "rénover",
        "renovation",
        "amenager",
        "aménager",
    }
    return folded_object in {_fold(value) for value in vague_objects} or folded_action in {
        _fold(value) for value in vague_actions
    }


def maybe_expand_cuisine_renovation(
    input_: PipelineInput,
    arbitration: TradeArbitration,
    matrix: DemandMatrix,
) -> DemandMatrix:
    """Replace coarse cuisine renovation ITEMs with official atomic coverage."""

    if arbitration.primary_trade_code != "CUISINE":
        return matrix
    folded = _fold(input_.description)
    if "cuisine" not in folded:
        return matrix
    if not any(token in folded for token in ("renov", "refaire", "changer", "remplacer")):
        return matrix
    required = [item for item in matrix.items if item.status == DemandStatus.REQUIRED]
    if required and not all(_is_vague_cuisine_item(item) for item in required):
        return matrix
    return DemandMatrix.model_construct(
        items=_cuisine_renovation_items(input_.description),
        global_context=matrix.global_context or _explicit_global_context(input_.description),
    )


def deterministic_extract(
    input_: PipelineInput,
    arbitration: TradeArbitration,
) -> DemandMatrix:
    """Conservative extraction used when GPT is unavailable.

    It intentionally extracts only explicit objects.  Global areas remain in
    ``global_context`` and are never copied into each ITEM.
    """

    description = input_.description
    folded = _fold(description)
    items: list[DemandItem] = []

    if arbitration.primary_trade_code == "CHARPENTE_METALLIQUE":
        requested = (
            ("concevoir", "étude d'exécution", "étude d’exécution"),
            ("fabriquer", "fabrication en atelier", "fabrication en atelier"),
            ("fournir et poser", "poteaux", "poteaux"),
            ("fournir et poser", "poutres principales", "poutres principales"),
            ("fournir et poser", "pannes", "pannes"),
            ("fournir et poser", "contreventements", "contreventements"),
            ("fournir et poser", "platines d'ancrage", "platines d’ancrage"),
            ("protéger", "structure métallique", "protection anticorrosion"),
            ("transporter", "éléments de charpente", "transport"),
            ("lever", "éléments de charpente", "levage"),
            ("assembler", "structure métallique", "boulonnage"),
            ("régler", "structure métallique", "réglage"),
        )
        for action, object_, source in requested:
            if _fold(source) in folded:
                items.append(
                    _fallback_item(
                        len(items) + 1,
                        action=action,
                        object_=object_,
                        material="acier",
                        source=source,
                    )
                )
        for object_, source in (("fondations", "fondations"), ("bardage", "bardage")):
            if source in folded and "autres lots" in folded:
                items.append(
                    _fallback_item(
                        len(items) + 1,
                        action="exclure",
                        object_=object_,
                        material=None,
                        source=source,
                        status=DemandStatus.EXCLUDED,
                    )
                )
    elif arbitration.primary_trade_code == "CHARPENTE_BOIS":
        requested = (
            ("construire", "charpente traditionnelle", "charpente traditionnelle"),
            ("fabriquer et poser", "fermes", "deux fermes"),
            ("fournir et poser", "pannes", "pannes"),
            ("fournir et poser", "chevrons", "chevrons"),
            ("fournir et poser", "contreventements", "contreventements"),
            ("traiter", "pièces de charpente", "Bois traité"),
            ("assembler", "charpente", "assemblages"),
            ("lever", "charpente", "levage"),
            ("fixer", "charpente sur murs porteurs", "fixation sur les murs porteurs"),
            ("préparer", "support de couverture en tuiles", "couverture en tuiles"),
        )
        for action, object_, source in requested:
            if _fold(source) in folded:
                quantity = 2.0 if source == "deux fermes" else None
                unit = QuantityUnit.UNIT if quantity is not None else None
                items.append(
                    _fallback_item(
                        len(items) + 1,
                        action=action,
                        object_=object_,
                        material="bois",
                        source=source,
                        quantity=quantity,
                        unit=unit,
                    )
                )
    elif arbitration.primary_trade_code == "CUISINE":
        if any(token in folded for token in ("renov", "refaire", "changer", "remplacer")):
            items.extend(_cuisine_renovation_items(description))
        else:
            items.append(
                _fallback_item(
                    1,
                    action="fournir et poser",
                    object_="cuisine",
                    material="agencement",
                    source=description,
                )
            )
    else:
        items.append(
            _fallback_item(
                1,
                action="réaliser",
                object_="travaux décrits",
                material=None,
                source=description,
            )
        )

    if not items:
        items.append(
            _fallback_item(
                1,
                action="réaliser",
                object_="travaux décrits",
                material=None,
                source=description,
            )
        )
    return DemandMatrix.model_construct(
        items=items,
        global_context=_explicit_global_context(description),
    )


class SemanticService:
    """OpenAI-backed semantic boundary with explicit authorized fallbacks."""

    def __init__(self, client: AsyncOpenAI | None = None) -> None:
        self._client = client or AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

    async def _structured(
        self,
        *,
        name: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        contract: type[ContractT],
        fallback: SemanticFallback[ContractT],
    ) -> tuple[ContractT, bool, str | None]:
        try:
            response = await self._client.chat.completions.create(
                model=settings.V3_OPENAI_SEMANTIC_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": json.dumps(user_payload, ensure_ascii=False),
                    },
                ],
                response_format=_json_schema_format(name, contract),
                temperature=0,
            )
            raw = response.choices[0].message.content
            if not raw:
                raise SemanticProviderError("empty structured response")
            return contract.model_validate_json(raw), False, None
        except Exception as exc:
            try:
                return fallback(), True, f"{type(exc).__name__}:{exc}"
            except Exception as fallback_exc:  # pragma: no cover - catastrophic
                raise SemanticProviderError(
                    f"semantic primary and fallback failed: {fallback_exc}"
                ) from fallback_exc

    async def plan(
        self,
        input_: PipelineInput,
    ) -> tuple[SemanticPlan, bool, str | None]:
        return await self._structured(
            name="semantic_plan_v1",
            system_prompt=SEMANTIC_PLAN_SYSTEM_PROMPT,
            user_payload=input_.model_dump(mode="json"),
            contract=SemanticPlan,
            fallback=lambda: deterministic_plan(input_),
        )

    async def extract(
        self,
        input_: PipelineInput,
        arbitration: TradeArbitration,
    ) -> tuple[DemandMatrix, bool, str | None]:
        matrix, degraded, reason = await self._structured(
            name="demand_matrix_v1",
            system_prompt=DEMAND_EXTRACTION_SYSTEM_PROMPT,
            user_payload={
                "description": input_.description,
                "arbitration": arbitration.model_dump(mode="json"),
            },
            contract=DemandMatrix,
            fallback=lambda: deterministic_extract(input_, arbitration),
        )
        return (
            maybe_expand_cuisine_renovation(input_, arbitration, matrix),
            degraded,
            reason,
        )

    async def aclose(self) -> None:
        await self._client.close()


__all__ = [
    "SemanticProviderError",
    "SemanticService",
    "deterministic_extract",
    "deterministic_plan",
    "maybe_expand_cuisine_renovation",
    "semantic_cache_key",
]
