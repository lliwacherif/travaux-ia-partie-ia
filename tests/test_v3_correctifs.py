"""Correctifs ciblés à intégrer dans la V3.2 — golden / unit regressions."""

from __future__ import annotations

import pytest

from app.v3.eligibility import evaluate_pack_eligibility
from app.v3.interventions import assign_intervention_ids, enrich_normalized_codes
from app.v3.measurements import resolve_quantity
from app.v3.selector import repair_core_pack, select_one_pack_per_intervention_and_repair
from app.v3.signatures import compute_pack_match_signature, line_content_hash
from app.v3.ssot import (
    COPY_PACK_VAT_EXACTLY,
    EligibilityStatus,
    FORBID_HYBRID_PACK_REPAIR,
    FORBID_VAT_CONTEXT_SUBSTITUTION,
    SSOT_VERSION,
)
from app.v3.vat import copy_pack_vat
from app.v3.voice import normalize_voice_transcript


def test_correctifs_ssot_flags_locked() -> None:
    assert SSOT_VERSION.endswith("correctifs")
    assert FORBID_HYBRID_PACK_REPAIR is True
    assert COPY_PACK_VAT_EXACTLY is True
    assert FORBID_VAT_CONTEXT_SUBSTITUTION is True


def test_hybrid_repair_is_hard_forbidden() -> None:
    with pytest.raises(RuntimeError, match="HYBRID_PACK_REPAIR_FORBIDDEN"):
        repair_core_pack(None, (), (), None)


def test_pack_match_signature_stable() -> None:
    pack = {
        "pack_code": "PACK-A",
        "version": 1,
        "flow": "TRAVAUX",
        "trade_code": "CHARPENTE",
        "lines": [
            {
                "phase": "CORE",
                "slot_index": 0,
                "normalized_action": "poser",
                "object_family": "toiture",
                "unit": "M2",
                "capability_tags": ["pose", "toiture"],
                "exclusion_tags": [],
            }
        ],
    }
    first = compute_pack_match_signature(pack)
    second = compute_pack_match_signature(pack)
    assert first == second
    assert first.startswith("PMS|")


def test_hard_incompatibility_eliminates_pack() -> None:
    pack = {
        "trade_code": "CHARPENTE",
        "flow": "TRAVAUX",
        "active": True,
        "status": "PUBLISHED",
        "exclusion_tags": ["acier"],
        "lines": [],
    }
    matrix = {
        "items": [
            {
                "request_item_id": "ITEM-001",
                "status": "REQUIRED",
                "material": "acier",
                "object": "poteaux",
                "action": "fabriquer",
            }
        ]
    }
    decision = evaluate_pack_eligibility(
        pack, matrix, trade_code="CHARPENTE", flow="TRAVAUX"
    )
    assert decision.status is EligibilityStatus.INCOMPATIBLE
    assert decision.hard_exclusion_reasons


def test_missing_info_is_unknown_not_eliminated() -> None:
    pack = {
        "trade_code": "CHARPENTE",
        "flow": "TRAVAUX",
        "active": True,
        "status": "PUBLISHED",
        "exclusion_tags": [],
        "lines": [{"material_family": "bois", "exclusion_tags": []}],
    }
    matrix = {
        "items": [
            {
                "request_item_id": "ITEM-001",
                "status": "REQUIRED",
                "material": None,
                "object": "charpente",
                "action": "poser",
            }
        ]
    }
    decision = evaluate_pack_eligibility(
        pack, matrix, trade_code="CHARPENTE", flow="TRAVAUX"
    )
    assert decision.status is EligibilityStatus.UNKNOWN


def test_vat_copies_pack_exactly_without_standard_substitution() -> None:
    line = {
        "vat_rule_id": "FR_REDUCED_10",
        "vat_rule_version": 2,
        "vat_rate": 10,
    }
    project = {
        "customer_type": None,
        "building_use": None,
        "building_age_years": None,
    }
    vat = copy_pack_vat(line, project=project)
    assert vat.vat_rule_id == "FR_REDUCED_10"
    assert vat.vat_rule_version == 2
    assert vat.vat_rate == 10.0
    assert vat.review_required is True
    assert vat.assumption_code and "REVIEW_REQUIRED" in vat.assumption_code


def test_quantity_binds_single_measurement_fact() -> None:
    line = {
        "line_id": "L1",
        "unit": "M2",
        "quantity_rule": "BOUND_EXPLICIT",
        "default_quantity": 1,
    }
    items = [
        {
            "request_item_id": "ITEM-001",
            "quantity": None,
            "unit": None,
            "measurement_facts": [
                {
                    "fact_id": "FACT-001",
                    "kind": "EXPLICIT_QUANTITY",
                    "value": 42,
                    "unit": "M2",
                    "source_excerpt": "42 m2",
                    "request_item_id": "ITEM-001",
                }
            ],
            "dimensions": [],
        }
    ]
    resolved = resolve_quantity(line, items, None)
    assert resolved.value == 42.0
    assert resolved.source == "EXPLICIT"
    assert resolved.bound_fact_ids == ("FACT-001",)


def test_reselect_complete_pack_not_hybrid() -> None:
    matrix = {
        "items": [
            {
                "request_item_id": "ITEM-001",
                "action": "poser",
                "object": "isolation",
                "material": None,
                "unit": "M2",
                "status": "REQUIRED",
                "dimensions": [],
            }
        ]
    }
    good = {
        "pack_id": "P2",
        "pack_code": "GOOD",
        "version": 1,
        "trade_code": "ISO",
        "flow": "TRAVAUX",
        "active": True,
        "lines": [
            {
                "line_id": "L1",
                "phase": "CORE",
                "normalized_action": "poser",
                "object_family": "isolation",
                "material_family": None,
                "unit": "M2",
                "capability_tags": ["poser", "isolation"],
                "synonym_tags": [],
                "exclusion_tags": [],
                "technical_dependency_ids": [],
            }
        ],
    }
    bad = {
        "pack_id": "P1",
        "pack_code": "BAD",
        "version": 1,
        "trade_code": "ISO",
        "flow": "TRAVAUX",
        "active": True,
        "lines": [
            {
                "line_id": "LX",
                "phase": "CORE",
                "normalized_action": "peindre",
                "object_family": "mur",
                "material_family": None,
                "unit": "M2",
                "capability_tags": ["peindre", "mur"],
                "synonym_tags": [],
                "exclusion_tags": [],
                "technical_dependency_ids": [],
            }
        ],
    }
    candidates = [
        {
            "pack_id": "P1",
            "pack_code": "BAD",
            "pack_version": 1,
            "final_score": 0.9,
            "coverage_score": 0.1,
            "line_parent_score": 0.1,
            "extra_scope_penalty": 0,
            "fallback_rank": None,
            "eligibility_status": "UNKNOWN",
        },
        {
            "pack_id": "P2",
            "pack_code": "GOOD",
            "pack_version": 1,
            "final_score": 0.5,
            "coverage_score": 1.0,
            "line_parent_score": 0.5,
            "extra_scope_penalty": 0,
            "fallback_rank": None,
            "eligibility_status": "COMPATIBLE",
        },
    ]
    result = select_one_pack_per_intervention_and_repair(
        candidates,
        matrix,
        {"P1": bad, "P2": good},
        (),
        trade_code="ISO",
        flow="TRAVAUX",
        official_fallback=good,
    )
    assert result.pack_id == "P2"
    assert result.generation_mode in {"EXACT_PACK", "RESELECTED_PUBLISHED_PACK"}
    assert result.generation_mode != "REPAIRED_PACK"


def test_voice_french_number_and_unit_normalization() -> None:
    normalized = normalize_voice_transcript(
        "isolation de quarante deux metres carres"
    )
    assert "42" in normalized
    assert "m2" in normalized


def test_intervention_codes_enriched() -> None:
    from app.v3.contracts import DemandMatrix

    matrix = DemandMatrix.model_validate(
        {
            "items": [
                {
                    "request_item_id": "ITEM-001",
                    "action": "poser",
                    "object": "isolation toiture",
                    "material": "laine de verre",
                    "quantity": 40,
                    "unit": "M2",
                    "dimensions": [],
                    "linear_measurement_hint": None,
                    "location": "combles",
                    "status": "REQUIRED",
                    "condition": None,
                    "source_excerpt": "poser isolation toiture combles 40 m2",
                }
            ],
            "global_context": {
                "global_area_m2": 40,
                "global_length_ml": None,
                "notes": [],
            },
        }
    )
    enriched = enrich_normalized_codes(matrix)
    stamped = assign_intervention_ids(enriched)
    item = stamped.items[0]
    assert item.intervention_id == "INTERVENTION-001"
    assert item.action_code == "ACTION_POSE"
    assert item.material_family_code == "MATERIAL_LAINE_VERRE"
    assert item.location_code == "LOCATION_COMBLES"


def test_line_content_hash_changes_with_price() -> None:
    base = {
        "designation": "Pose isolation",
        "unit": "M2",
        "quantity_rule": "EXPLICIT",
        "default_quantity": 1,
        "price_id": "p1",
        "price_version": 1,
        "vat_rule_id": "FR_STANDARD_20",
        "vat_rule_version": 1,
        "vat_rate": 20,
        "unit_price_cents": 1000,
    }
    other = {**base, "unit_price_cents": 2000}
    assert line_content_hash(base) != line_content_hash(other)
