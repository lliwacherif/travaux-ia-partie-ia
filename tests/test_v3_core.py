from __future__ import annotations

import asyncio

import pytest

from app.v3.context import normalize_unit
from app.v3.contracts import (
    CompanyContext,
    DemandMatrix,
    PipelineInput,
    ProjectContext,
)
from app.v3.coverage import match_item_to_line
from app.v3.curated_library import CURATED_PACKS
from app.v3.demand import normalize_demand_matrix
from app.v3.semantic import deterministic_plan
from app.v3.ssot import PipelineStage
from app.v3.trace import ExecutionTracer, stable_hash


def test_curated_packs_have_exact_travaux_geometry_and_unique_sources() -> None:
    all_source_keys: list[str] = []
    for pack in CURATED_PACKS:
        assert len(pack.setup) == 3
        assert len(pack.core) == 14
        assert len(pack.finish) == 3
        assert len(pack.all_lines) == 20
        assert len({reference.key for reference in pack.all_lines}) == 20
        assert pack.required_coverage
        all_source_keys.extend(reference.key for reference in pack.all_lines)
    assert len(all_source_keys) == len(set(all_source_keys))


def test_provider_vocabulary_drift_is_canonicalized_deterministically() -> None:
    raw = {
        "items": [
            {
                "request_item_id": "ITEM-999",
                "action": "fabricate",
                "object": "poteaux",
                "material": "métallique",
                "quantity": None,
                "unit": None,
                "dimensions": [],
                "linear_measurement_hint": None,
                "location": None,
                "status": "REQUIRED",
                "condition": None,
                "source_excerpt": "fabrication de poteaux métalliques",
            },
            {
                "request_item_id": "ITEM-998",
                "action": "apply",
                "object": "protection anticorrosion",
                "material": "anticorrosion",
                "quantity": 135,
                "unit": "M2",
                "dimensions": [],
                "linear_measurement_hint": None,
                "location": None,
                "status": "REQUIRED",
                "condition": None,
                "source_excerpt": "protection anticorrosion sur 135 m²",
            },
        ],
        "global_context": {
            "global_area_m2": 135,
            "global_length_ml": None,
            "notes": [],
        },
    }
    matrix = normalize_demand_matrix(raw)
    assert matrix.items[0].request_item_id == "ITEM-001"
    assert matrix.items[0].action == "fabriquer"
    assert matrix.items[0].material == "acier"
    assert matrix.items[1].action == "protéger"
    assert matrix.items[1].material is None
    assert matrix.items[1].unit.value == "M2"


def test_structured_coverage_requires_action_object_material_and_unit() -> None:
    item = {
        "action": "fabriquer",
        "object": "poteaux",
        "material": "acier",
        "unit": None,
    }
    line = {
        "normalized_action": "fabriquer",
        "object_family": "fabrication en atelier",
        "material_family": "acier",
        "capability_tags": [
            "fabriquer",
            "poteaux",
            "fabrication en atelier",
        ],
        "synonym_tags": [],
        "exclusion_tags": [],
        "unit": "TONNE",
    }
    assert match_item_to_line(item, line).matched
    assert not match_item_to_line(
        {**item, "material": "bois"},
        line,
    ).matched
    assert normalize_unit("tonnes") == "TONNE"


def test_trace_rejects_missing_stage_and_accepts_complete_evidence() -> None:
    async def exercise() -> None:
        tracer = ExecutionTracer(
            library_version="TEST",
            prompt_hash=stable_hash("prompt"),
            config_hash=stable_hash("config"),
        )
        await tracer.required(PipelineStage.CONTEXT, lambda: {"ok": True})
        with pytest.raises(RuntimeError, match="DISPLAY_GATE_STAGE_EVIDENCE_MISSING"):
            tracer.finish()
        for stage in PipelineStage:
            if stage is PipelineStage.CONTEXT:
                continue
            await tracer.required(stage, lambda: {"ok": True})
        trace = tracer.finish()
        assert trace.display_gate_passed
        assert trace.stage_completion_rate == 1
        assert len(trace.stage_executions) == len(PipelineStage)

    asyncio.run(exercise())


def test_simple_pipeline_input_contract_for_parallel_v3_api() -> None:
    value = PipelineInput(
        request_id="REQ-001",
        description="Charpente traditionnelle bois de 42 m² avec pannes",
        company=CompanyContext(
            primary_trade_code="",
            enabled_service_codes=[],
        ),
        project=ProjectContext(
            country="FR",
            customer_type=None,
            building_use=None,
            building_age_years=None,
            location=None,
        ),
    )
    assert "42 m²" in value.description
    assert DemandMatrix.model_fields["items"].is_required()
    assert deterministic_plan(value).flow_hint.value == "TRAVAUX"

