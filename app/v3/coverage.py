"""Deterministic source-to-pack coverage and line justification."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .context import normalize_text, normalize_unit


ConditionResolver = Callable[[Any], bool]

_ACTION_GROUPS = (
    frozenset(
        {
            "poser",
            "pose",
            "installer",
            "installation",
            "mettre en place",
            "fournir",
            "fourniture",
            "fournir et poser",
            "fabriquer et poser",
            "fabriquer",
            "construire",
            "renover",
            "rénover",
            "amenager",
            "aménager",
        }
    ),
    frozenset({"deposer", "depose", "demolir", "retirer", "enlever", "déposer"}),
    frozenset({"reparer", "depanner", "remettre en etat"}),
    frozenset({"remplacer", "changer", "renouveler"}),
    frozenset({"controler", "verifier", "tester", "diagnostiquer"}),
    frozenset({"concevoir", "etudier", "realiser une etude"}),
    frozenset({"proteger", "appliquer une protection", "traiter", "protéger", "preparer", "préparer"}),
    frozenset({"fabriquer", "construire"}),
    frozenset({"assembler", "boulonner"}),
    frozenset({"etancheifier", "étanchéifier", "siliconer"}),
    frozenset({"nettoyer", "evacuation", "évacuer"}),
)
_STOPWORDS = frozenset(
    {"de", "du", "des", "la", "le", "les", "un", "une", "et", "en", "a", "au"}
)
_GENERIC_OBJECTS = frozenset(
    {
        "travaux decrits",
        "travaux",
        "ouvrage",
        "prestation",
        "demande",
        "chantier",
    }
)
_GENERIC_ACTIONS = frozenset(
    {
        "realiser",
        "faire",
        "effectuer",
        "executer",
        "prevoir",
    }
)
_DOMAIN_KEYWORDS = frozenset(
    {
        "tranchee",
        "terrassement",
        "fouille",
        "fourreau",
        "fourreaux",
        "sable",
        "grillage",
        "avertisseur",
        "remblai",
        "remblayage",
        "regard",
        "regards",
        "canalisation",
        "pvc",
        "eau",
        "eaux",
        "usees",
        "electricite",
        "telecom",
        "telecommunications",
        "reseau",
        "reseaux",
        "assainissement",
        "vrd",
    }
)


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: bool
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Coverage:
    required_covered: tuple[str, ...]
    required_missing: tuple[str, ...]
    excluded_violated: tuple[str, ...]
    conditional_activated: tuple[str, ...]
    extra_pack_capabilities: tuple[str, ...]
    score: float
    item_to_line_ids: Mapping[str, tuple[str, ...]]
    line_to_request_item_ids: Mapping[str, tuple[str, ...]]
    line_technical_dependency_ids: Mapping[str, tuple[str, ...]]

    @property
    def blocking(self) -> bool:
        return bool(self.excluded_violated)


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
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"Expected mapping-like coverage data, got {type(value).__name__}")


def _norm(value: Any) -> str:
    return normalize_text(str(value or "")).matching


def _tokens(value: Any) -> frozenset[str]:
    return frozenset(
        token
        for token in _norm(value).replace("_", " ").split()
        if token and token not in _STOPWORDS
    )


def _equivalent(left: Any, right: Any) -> bool:
    left_norm = _norm(left)
    right_norm = _norm(right)
    if not left_norm or not right_norm:
        return False
    if left_norm == right_norm:
        return True
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return False
    smaller, larger = (
        (left_tokens, right_tokens)
        if len(left_tokens) <= len(right_tokens)
        else (right_tokens, left_tokens)
    )
    return smaller.issubset(larger) and len(smaller) / len(larger) >= 0.6


def _action_equivalent(left: Any, right: Any) -> bool:
    if _equivalent(left, right):
        return True
    left_norm = _norm(left)
    right_norm = _norm(right)
    return any(
        left_norm in group and right_norm in group for group in _ACTION_GROUPS
    )


def _line_tags(line: Mapping[str, Any]) -> tuple[str, ...]:
    tags: set[str] = set()
    for field_name in ("synonym_tags", "capability_tags", "tags"):
        tags.update(_norm(value) for value in line.get(field_name) or ())
    return tuple(sorted(tag for tag in tags if tag))


def _unit_compatible(item_unit: Any, line_unit: Any) -> bool:
    if item_unit in (None, ""):
        return True
    item = normalize_unit(str(item_unit))
    line = normalize_unit(str(line_unit))
    if item == "M":
        item = "ML"
    if line == "M":
        line = "ML"
    return item == line


def match_item_to_line(item: Any, line: Any) -> MatchResult:
    """Match on structured semantics, never on one shared word alone."""

    item_data = _mapping(item)
    line_data = _mapping(line)
    tags = _line_tags(line_data)

    action = line_data.get("normalized_action") or line_data.get("action")
    action_match = _action_equivalent(item_data.get("action"), action) or any(
        _action_equivalent(item_data.get("action"), tag) for tag in tags
    )
    object_value = (
        line_data.get("object_family")
        or line_data.get("object")
        or line_data.get("designation")
    )
    object_match = _equivalent(item_data.get("object"), object_value) or any(
        _equivalent(item_data.get("object"), tag) for tag in tags
    )
    material = item_data.get("material")
    line_material = line_data.get("material_family") or line_data.get("material")
    material_match = (
        material in (None, "")
        or _equivalent(material, line_material)
        or any(_equivalent(material, tag) for tag in tags)
    )
    unit_match = _unit_compatible(item_data.get("unit"), line_data.get("unit"))

    item_tokens = (
        _tokens(item_data.get("action"))
        | _tokens(item_data.get("object"))
        | _tokens(item_data.get("material"))
    )
    # Fallback demand items ("réaliser / travaux décrits") bind via excerpt overlap.
    excerpt_tokens = _tokens(item_data.get("source_excerpt"))
    line_tokens = (
        _tokens(line_data.get("designation"))
        | _tokens(object_value)
        | _tokens(" ".join(tags))
    )
    domain_overlap = (excerpt_tokens | item_tokens) & line_tokens & _DOMAIN_KEYWORDS
    if _norm(item_data.get("object")) in _GENERIC_OBJECTS and domain_overlap:
        object_match = True
    if _norm(item_data.get("action")) in _GENERIC_ACTIONS and domain_overlap:
        action_match = True
    exclusion_tags = {
        _norm(value) for value in line_data.get("exclusion_tags") or ()
    }
    exclusion_hit = any(
        tag
        and (
            tag in {_norm(item_data.get("object")), _norm(item_data.get("material"))}
            or _tokens(tag).issubset(item_tokens | excerpt_tokens)
        )
        for tag in exclusion_tags
    )

    matched = (
        action_match
        and object_match
        and material_match
        and unit_match
        and not exclusion_hit
    )
    reasons = tuple(
        reason
        for condition, reason in (
            (action_match, "ACTION"),
            (object_match, "OBJECT"),
            (material_match and material not in (None, ""), "MATERIAL"),
            (unit_match and item_data.get("unit") not in (None, ""), "UNIT"),
            (bool(tags), "TAGS"),
        )
        if condition
    )
    score = (
        (0.30 if action_match else 0)
        + (0.35 if object_match else 0)
        + (0.15 if material_match else 0)
        + (0.10 if unit_match else 0)
        + (0.10 if tags and (action_match or object_match) else 0)
    )
    return MatchResult(
        matched=matched,
        score=round(score, 6),
        reasons=reasons + (("EXCLUSION_TAG",) if exclusion_hit else ()),
    )


def _condition_active(item: Any, resolver: ConditionResolver | None) -> bool:
    if resolver is not None:
        return bool(resolver(item))
    condition = _norm(_mapping(item).get("condition"))
    return condition in {"true", "vrai", "oui", "active", "actif"}


def _pack_lines(pack: Any) -> tuple[Any, ...]:
    data = _mapping(pack)
    if data.get("lines") is not None:
        return tuple(data["lines"])
    lines: list[Any] = []
    for name in ("setup", "core", "finish", "setup_lines", "core_lines", "finish_lines"):
        lines.extend(data.get(name) or ())
    return tuple(lines)


def _registered_dependencies(
    line: Mapping[str, Any],
    pack: Mapping[str, Any],
    dependencies: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    if not dependencies:
        return ()
    result: list[str] = []
    trade_code = str(pack.get("trade_code") or "")
    for dependency_id in line.get("technical_dependency_ids") or ():
        dependency = dependencies.get(str(dependency_id))
        if dependency is None:
            continue
        raw = _mapping(dependency)
        if not bool(raw.get("active", True)):
            continue
        dependency_trade = str(raw.get("trade_code") or trade_code)
        if dependency_trade != trade_code:
            continue
        result.append(str(dependency_id))
    return tuple(sorted(set(result)))


def coverage_score(
    matrix: Any,
    pack: Any,
    *,
    condition_resolver: ConditionResolver | None = None,
    technical_dependencies: Mapping[str, Any] | None = None,
) -> Coverage:
    """Evaluate complete-pack coverage and blocking exclusion violations."""

    matrix_data = _mapping(matrix)
    pack_data = _mapping(pack)
    items = tuple(matrix_data.get("items") or ())
    lines = _pack_lines(pack)

    required_items: list[Any] = []
    excluded_items: list[Any] = []
    conditional_activated: list[str] = []
    for item in items:
        item_data = _mapping(item)
        status = str(item_data.get("status") or "REQUIRED").upper()
        item_id = str(item_data.get("request_item_id") or "")
        if status == "REQUIRED":
            required_items.append(item)
        elif status == "EXCLUDED":
            excluded_items.append(item)
        elif status == "CONDITIONAL" and _condition_active(item, condition_resolver):
            required_items.append(item)
            conditional_activated.append(item_id)

    item_to_lines: dict[str, tuple[str, ...]] = {}
    line_to_items: dict[str, list[str]] = {}
    required_covered: list[str] = []
    required_missing: list[str] = []
    for item in required_items:
        item_data = _mapping(item)
        item_id = str(item_data.get("request_item_id") or "")
        matches: list[tuple[float, str]] = []
        for line in lines:
            line_data = _mapping(line)
            line_id = str(line_data.get("line_id") or "")
            result = match_item_to_line(item, line)
            if result.matched:
                matches.append((result.score, line_id))
        matches.sort(key=lambda value: (-value[0], value[1]))
        # One best proof per ITEM×pack prevents duplicate evidence inflation.
        if matches:
            best_line_id = matches[0][1]
            required_covered.append(item_id)
            item_to_lines[item_id] = (best_line_id,)
            line_to_items.setdefault(best_line_id, []).append(item_id)
        else:
            required_missing.append(item_id)
            item_to_lines[item_id] = ()

    excluded_violated: list[str] = []
    for item in excluded_items:
        item_data = _mapping(item)
        item_id = str(item_data.get("request_item_id") or "")
        if any(match_item_to_line(item, line).matched for line in lines):
            excluded_violated.append(item_id)

    line_dependencies: dict[str, tuple[str, ...]] = {}
    extras: list[str] = []
    for line in lines:
        line_data = _mapping(line)
        line_id = str(line_data.get("line_id") or "")
        dependencies = _registered_dependencies(
            line_data, pack_data, technical_dependencies
        )
        line_dependencies[line_id] = dependencies
        if not line_to_items.get(line_id) and not dependencies:
            extras.append(line_id)

    denominator = len(required_items)
    base_score = len(required_covered) / denominator if denominator else 1.0
    score = 0.0 if excluded_violated else base_score
    return Coverage(
        required_covered=tuple(sorted(required_covered)),
        required_missing=tuple(sorted(required_missing)),
        excluded_violated=tuple(sorted(excluded_violated)),
        conditional_activated=tuple(sorted(conditional_activated)),
        extra_pack_capabilities=tuple(sorted(extras)),
        score=round(score, 6),
        item_to_line_ids=MappingProxyType(dict(sorted(item_to_lines.items()))),
        line_to_request_item_ids=MappingProxyType(
            {
                line_id: tuple(sorted(item_ids))
                for line_id, item_ids in sorted(line_to_items.items())
            }
        ),
        line_technical_dependency_ids=MappingProxyType(
            dict(sorted(line_dependencies.items()))
        ),
    )


def justify_final_lines(
    matrix: Any,
    pack: Any,
    **kwargs: Any,
) -> Mapping[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Map every line to covered ITEM IDs and official dependency IDs."""

    coverage = coverage_score(matrix, pack, **kwargs)
    line_ids = {
        str(_mapping(line).get("line_id") or "") for line in _pack_lines(pack)
    }
    return MappingProxyType(
        {
            line_id: (
                coverage.line_to_request_item_ids.get(line_id, ()),
                coverage.line_technical_dependency_ids.get(line_id, ()),
            )
            for line_id in sorted(line_ids)
        }
    )


__all__ = [
    "ConditionResolver",
    "Coverage",
    "MatchResult",
    "coverage_score",
    "justify_final_lines",
    "match_item_to_line",
]
