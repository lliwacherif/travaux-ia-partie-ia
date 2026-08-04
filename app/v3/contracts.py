"""Strict Pydantic contracts for the isolated V3.2 pipeline.

V3.2 — shared profiles, territory_code, library snapshot trace fields,
PackCandidate scoring fields required, and source_entity_type on lines.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.v3.ssot import (
    EMBEDDING_MODEL,
    PIPELINE_VERSION,
    RERANK_MODEL,
    SEMANTIC_MODEL,
    Flow,
    LinearMeasurementMode,
    Phase,
    PipelineStage,
    expected_geometry,
)

NonEmptyString = Annotated[str, Field(min_length=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveNumber = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
RequestItemId = Annotated[str, Field(pattern=r"^ITEM-[0-9]{3}$")]
DimensionId = Annotated[str, Field(pattern=r"^DIM-[0-9]{3}$")]
# V3.2 — vat_rate is numeric 0–100 (multipleOf 0.01), not a fixed enum.
VatRate = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class StrictModel(BaseModel):
    """Base contract: coercion and undeclared fields are both forbidden."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_assignment=True,
        use_enum_values=False,
    )


class CustomerType(StrEnum):
    PARTICULIER = "PARTICULIER"
    PROFESSIONNEL = "PROFESSIONNEL"


class BuildingUse(StrEnum):
    HABITATION = "HABITATION"
    PROFESSIONNEL = "PROFESSIONNEL"
    MIXTE = "MIXTE"


CustomerTypeValue = Annotated[CustomerType, Field(strict=False)]
BuildingUseValue = Annotated[BuildingUse, Field(strict=False)]
FlowValue = Annotated[Flow, Field(strict=False)]


class CompanyContext(StrictModel):
    primary_trade_code: str
    enabled_service_codes: list[str]


class ProjectContext(StrictModel):
    """V3.2 — territory_code is required for VAT resolution."""

    country: Literal["FR"]
    territory_code: Annotated[str, Field(min_length=2)]
    customer_type: CustomerTypeValue | None
    building_use: BuildingUseValue | None
    building_age_years: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    location: str | None


class PipelineInput(StrictModel):
    request_id: NonEmptyString
    description: Annotated[str, Field(min_length=3)]
    company: CompanyContext
    project: ProjectContext


class Urgency(StrEnum):
    NORMALE = "NORMALE"
    URGENTE = "URGENTE"
    INCONNUE = "INCONNUE"


class SemanticPlan(StrictModel):
    flow_hint: FlowValue
    primary_trade_hint: str | None
    secondary_trade_hints: list[str]
    service_hint: str | None
    urgency: Annotated[Urgency, Field(strict=False)]
    confidence: Score
    evidence: list[str]


class TradeArbitration(StrictModel):
    flow: FlowValue
    primary_trade_code: NonEmptyString
    secondary_trade_codes: list[str]
    service_code: str | None
    rule_id: Annotated[str, Field(pattern=r"^R([1-9]|1[0-2])$")]
    arbitrage_applied: bool
    confidence: Score


class QuantityUnit(StrEnum):
    M2 = "M2"
    ML = "ML"
    M3 = "M3"
    UNIT = "UNIT"
    HOUR = "HOUR"
    DAY = "DAY"
    FORFAIT = "FORFAIT"


class DimensionKind(StrEnum):
    LENGTH = "LENGTH"
    WIDTH = "WIDTH"
    HEIGHT = "HEIGHT"
    AXIS_LENGTH = "AXIS_LENGTH"
    SEGMENT_LENGTH = "SEGMENT_LENGTH"
    PERIMETER = "PERIMETER"
    DEVELOPED_LENGTH = "DEVELOPED_LENGTH"
    SURFACE = "SURFACE"
    COUNT = "COUNT"
    COVERAGE_WIDTH = "COVERAGE_WIDTH"


class DimensionUnit(StrEnum):
    MM = "MM"
    CM = "CM"
    M = "M"
    M2 = "M2"
    UNIT = "UNIT"


class DemandStatus(StrEnum):
    REQUIRED = "REQUIRED"
    EXCLUDED = "EXCLUDED"
    CONDITIONAL = "CONDITIONAL"
    OPTIONAL = "OPTIONAL"


QuantityUnitValue = Annotated[QuantityUnit, Field(strict=False)]
DimensionKindValue = Annotated[DimensionKind, Field(strict=False)]
DimensionUnitValue = Annotated[DimensionUnit, Field(strict=False)]
DemandStatusValue = Annotated[DemandStatus, Field(strict=False)]
LinearMeasurementModeValue = Annotated[
    LinearMeasurementMode, Field(strict=False)
]


class DemandDimension(StrictModel):
    dimension_id: DimensionId
    kind: DimensionKindValue
    value: PositiveNumber
    unit: DimensionUnitValue
    source_excerpt: NonEmptyString


class DemandItem(StrictModel):
    request_item_id: RequestItemId
    action: NonEmptyString
    object: NonEmptyString
    material: str | None
    quantity: PositiveNumber | None
    unit: QuantityUnitValue | None
    dimensions: list[DemandDimension]
    linear_measurement_hint: LinearMeasurementModeValue | None
    location: str | None
    status: DemandStatusValue
    condition: str | None
    source_excerpt: NonEmptyString

    @model_validator(mode="after")
    def validate_condition(self) -> "DemandItem":
        if self.status is DemandStatus.CONDITIONAL and not self.condition:
            raise ValueError("CONDITIONAL items require a condition")
        return self


class DemandGlobalContext(StrictModel):
    global_area_m2: PositiveNumber | None
    global_length_ml: PositiveNumber | None
    notes: list[str]


class DemandMatrix(StrictModel):
    items: Annotated[list[DemandItem], Field(min_length=1)]
    global_context: DemandGlobalContext

    @model_validator(mode="after")
    def validate_owned_identifiers(self) -> "DemandMatrix":
        item_ids = [item.request_item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("request_item_id values must be unique")
        dimension_ids = [
            dimension.dimension_id
            for item in self.items
            for dimension in item.dimensions
        ]
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValueError("dimension_id values must be globally unique")
        return self


class ResolutionSource(StrEnum):
    EXPLICIT = "EXPLICIT"
    FORMULA = "FORMULA"
    PROJECT_CONTEXT = "PROJECT_CONTEXT"
    PACK_DEFAULT = "PACK_DEFAULT"


class LinearMeasurementResolution(StrictModel):
    line_id: NonEmptyString
    request_item_ids: list[RequestItemId]
    mode: LinearMeasurementModeValue
    formula_id: str | None
    input_dimension_ids: list[DimensionId]
    value_ml: PositiveNumber
    source: Annotated[ResolutionSource, Field(strict=False)]
    assumption_code: str | None

    @model_validator(mode="after")
    def validate_formula_trace(self) -> "LinearMeasurementResolution":
        if self.source is ResolutionSource.FORMULA and not self.formula_id:
            raise ValueError("FORMULA resolutions require formula_id")
        if self.source is ResolutionSource.PACK_DEFAULT and not self.assumption_code:
            raise ValueError("PACK_DEFAULT resolutions require assumption_code")
        return self


class LineSearchHit(StrictModel):
    request_item_id: RequestItemId
    line_id: NonEmptyString
    pack_id: NonEmptyString
    pack_version: Annotated[int, Field(ge=1)]
    lexical_score: Annotated[float, Field(allow_inf_nan=False)]
    dense_score: Annotated[float, Field(allow_inf_nan=False)]
    rrf_score: Annotated[float, Field(allow_inf_nan=False)]
    match_reasons: list[str]


class PackCandidate(StrictModel):
    """V3.2 PackCandidate.v2 — all scoring / tie-breaker fields are required."""

    pack_id: NonEmptyString
    pack_code: NonEmptyString
    pack_version: Annotated[int, Field(ge=1)]
    trade_code: NonEmptyString
    service_code: str | None
    matched_line_ids: list[str]
    matched_request_item_ids: list[RequestItemId]
    line_parent_score: Annotated[float, Field(allow_inf_nan=False)]
    direct_pack_score: Annotated[float, Field(allow_inf_nan=False)]
    lexical_score: Annotated[float, Field(allow_inf_nan=False)]
    dense_score: Annotated[float, Field(allow_inf_nan=False)]
    rrf_score: Annotated[float, Field(allow_inf_nan=False)]
    rerank_score: Annotated[float, Field(allow_inf_nan=False)]
    coverage_score: Score
    object_exactness: Score
    material_compatibility: Score
    unit_compatibility: Score
    context_compatibility: Score
    exclusion_penalty: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    extra_scope_penalty: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    fallback_rank: Annotated[int, Field(ge=1)] | None
    final_score: Annotated[float, Field(allow_inf_nan=False)]


class GenerationMode(StrEnum):
    EXACT_PACK = "EXACT_PACK"
    REPAIRED_PACK = "REPAIRED_PACK"
    OFFICIAL_FALLBACK = "OFFICIAL_FALLBACK"


class PipelineStatus(StrEnum):
    COMPLETE_PRIMARY = "COMPLETE_PRIMARY"
    COMPLETE_DEGRADED_AUTHORIZED = "COMPLETE_DEGRADED_AUTHORIZED"


class StageStatus(StrEnum):
    PRIMARY = "PRIMARY"
    DEGRADED_AUTHORIZED = "DEGRADED_AUTHORIZED"


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SourceEntityType(StrEnum):
    """V3.2 — line origin: CORE pack vs shared SETUP/FINISH profile."""

    PACK = "PACK"
    SHARED_PROFILE = "SHARED_PROFILE"


GenerationModeValue = Annotated[GenerationMode, Field(strict=False)]
PipelineStatusValue = Annotated[PipelineStatus, Field(strict=False)]
StageStatusValue = Annotated[StageStatus, Field(strict=False)]
ConfidenceLevelValue = Annotated[ConfidenceLevel, Field(strict=False)]
PipelineStageValue = Annotated[PipelineStage, Field(strict=False)]
PhaseValue = Annotated[Phase, Field(strict=False)]
ResolutionSourceValue = Annotated[ResolutionSource, Field(strict=False)]
SourceEntityTypeValue = Annotated[SourceEntityType, Field(strict=False)]


class StageEvidence(StrictModel):
    stage: PipelineStageValue
    status: StageStatusValue
    duration_ms: NonNegativeInt
    fallback_reason: str | None
    input_count: NonNegativeInt
    output_count: NonNegativeInt
    input_hash: str | None = None
    output_hash: str | None = None
    evidence: dict[str, object] = Field(default_factory=dict)


class SelectedPackRef(StrictModel):
    """V3.2 — versioned pack reference in the execution trace."""

    pack_id: NonEmptyString
    pack_code: NonEmptyString
    pack_version: Annotated[int, Field(ge=1)]


class SharedProfileRef(StrictModel):
    """V3.2 — single shared SETUP/FINISH profile for the quote."""

    profile_id: NonEmptyString
    profile_code: NonEmptyString
    profile_version: Annotated[int, Field(ge=1)]


class PriceVersionRef(StrictModel):
    price_id: NonEmptyString
    price_version: Annotated[int, Field(ge=1)]


class VatRuleVersionRef(StrictModel):
    vat_rule_id: NonEmptyString
    vat_rule_version: Annotated[int, Field(ge=1)]


class ExecutionTrace(StrictModel):
    ssot_version: NonEmptyString
    library_version: NonEmptyString
    # V3.2 — snapshot identity and territory for reproducibility.
    library_snapshot_id: NonEmptyString
    fallback_snapshot_used: bool
    territory_code: NonEmptyString
    prompt_hash: NonEmptyString
    config_hash: NonEmptyString
    arbitrage_applied: bool
    pipeline_status: PipelineStatusValue
    display_gate_passed: Literal[True]
    pipeline_version: str = PIPELINE_VERSION
    semantic_model: str = SEMANTIC_MODEL
    embedding_model: str = EMBEDDING_MODEL
    reranker_model: str = RERANK_MODEL
    cache_hit: bool = False
    stage_completion_rate: Score = 1.0
    stage_executions: list[StageEvidence] = Field(default_factory=list)
    line_search_hits_count: NonNegativeInt = 0
    parent_pack_candidates_count: NonNegativeInt = 0
    reranked_pack_count: NonNegativeInt = 0
    selected_packs: list[SelectedPackRef] = Field(default_factory=list)
    shared_profile: SharedProfileRef | None = None
    price_versions: list[PriceVersionRef] = Field(default_factory=list)
    vat_rule_versions: list[VatRuleVersionRef] = Field(default_factory=list)
    # Legacy-compatible pack id list derived from selected_packs for persistence.
    selected_pack_ids: list[str] = Field(default_factory=list)
    replaced_line_ids: list[str] = Field(default_factory=list)
    assumption_codes: list[str] = Field(default_factory=list)
    linear_measurements_count: NonNegativeInt = 0
    linear_formula_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevelValue = ConfidenceLevel.HIGH
    document_emitted: bool = True
    duration_ms: NonNegativeInt = 0


class QuoteLine(StrictModel):
    line_id: NonEmptyString
    # V3.2 — distinguish pack CORE vs shared SETUP/FINISH lines.
    source_entity_type: SourceEntityTypeValue
    pack_id: str | None
    pack_code: str | None
    pack_version: Annotated[int, Field(ge=1)] | None
    shared_profile_id: str | None
    shared_profile_code: str | None
    shared_profile_version: Annotated[int, Field(ge=1)] | None
    phase: PhaseValue
    slot_index: NonNegativeInt = 0
    designation: NonEmptyString
    quantity: PositiveNumber
    unit: Annotated[
        QuantityUnit,
        Field(strict=False),
    ]
    price_id: NonEmptyString
    price_version: Annotated[int, Field(ge=1)]
    unit_price_cents: NonNegativeInt
    vat_rule_id: NonEmptyString
    vat_rule_version: Annotated[int, Field(ge=1)]
    vat_rate: VatRate
    total_ht_cents: NonNegativeInt
    covered_request_item_ids: list[RequestItemId]
    technical_dependency_ids: list[str]
    quantity_source: ResolutionSourceValue
    linear_measurement: LinearMeasurementResolution | None

    @model_validator(mode="after")
    def validate_justification_and_linear_trace(self) -> "QuoteLine":
        if not self.covered_request_item_ids and not self.technical_dependency_ids:
            raise ValueError("every quote line requires demand or dependency justification")
        if self.unit == QuantityUnit.ML and self.linear_measurement is None:
            raise ValueError("ML lines require a linear_measurement trace")
        if self.unit != QuantityUnit.ML and self.linear_measurement is not None:
            raise ValueError("only ML lines may carry a linear_measurement trace")
        # V3.2 — entity provenance must match phase ownership.
        if self.source_entity_type is SourceEntityType.PACK:
            if not self.pack_id or self.pack_code is None or self.pack_version is None:
                raise ValueError("PACK lines require pack_id/code/version")
            if self.phase is not Phase.CORE:
                raise ValueError("PACK lines must be CORE")
        if self.source_entity_type is SourceEntityType.SHARED_PROFILE:
            if (
                not self.shared_profile_id
                or self.shared_profile_code is None
                or self.shared_profile_version is None
            ):
                raise ValueError(
                    "SHARED_PROFILE lines require shared_profile_id/code/version"
                )
            if self.phase is Phase.CORE:
                raise ValueError("SHARED_PROFILE lines cannot be CORE")
        return self


class TradeBlock(StrictModel):
    intervention_id: NonEmptyString
    trade_code: NonEmptyString
    pack_id: NonEmptyString
    # V3.2 — pack identity fully versioned on each trade block.
    pack_code: NonEmptyString
    pack_version: Annotated[int, Field(ge=1)]
    lines: list[QuoteLine]


class QuoteTotals(StrictModel):
    ht_cents: NonNegativeInt
    vat_cents: NonNegativeInt
    ttc_cents: NonNegativeInt

    @model_validator(mode="after")
    def validate_total(self) -> "QuoteTotals":
        if self.ht_cents + self.vat_cents != self.ttc_cents:
            raise ValueError("ttc_cents must equal ht_cents + vat_cents")
        return self


class QuoteResult(StrictModel):
    quote_id: NonEmptyString
    flow: FlowValue
    generation_mode: GenerationModeValue
    review_required: bool
    # V3.2 — one shared profile frames all trade blocks.
    shared_profile_id: NonEmptyString
    shared_profile_code: NonEmptyString
    shared_profile_version: Annotated[int, Field(ge=1)]
    setup_lines: list[QuoteLine]
    trade_blocks: Annotated[list[TradeBlock], Field(min_length=1)]
    finish_lines: list[QuoteLine]
    totals: QuoteTotals
    trace: ExecutionTrace

    @model_validator(mode="after")
    def validate_geometry(self) -> "QuoteResult":
        expected = expected_geometry(self.flow)
        if len(self.setup_lines) != expected.setup:
            raise ValueError("invalid SETUP geometry")
        if len(self.finish_lines) != expected.finish:
            raise ValueError("invalid FINISH geometry")
        if any(
            len(block.lines) != expected.core_per_trade
            for block in self.trade_blocks
        ):
            raise ValueError("invalid CORE geometry")
        if any(line.phase is not Phase.SETUP for line in self.setup_lines):
            raise ValueError("setup_lines may only contain SETUP lines")
        if any(
            line.phase is not Phase.CORE
            for block in self.trade_blocks
            for line in block.lines
        ):
            raise ValueError("trade block lines may only contain CORE lines")
        if any(line.phase is not Phase.FINISH for line in self.finish_lines):
            raise ValueError("finish_lines may only contain FINISH lines")
        if self.flow is Flow.DEPANNAGE and len(self.trade_blocks) != 1:
            raise ValueError("DEPANNAGE requires exactly one trade block")
        # V3.2 — SETUP/FINISH never repeated per métier.
        total = (
            len(self.setup_lines)
            + sum(len(block.lines) for block in self.trade_blocks)
            + len(self.finish_lines)
        )
        if total != expected.line_count(len(self.trade_blocks)):
            raise ValueError("quote line count does not match shared-profile geometry")
        return self


class RepairAction(StrEnum):
    RESELECT_PACK = "RESELECT_PACK"
    REPLACE_OFFICIAL_LINE = "REPLACE_OFFICIAL_LINE"
    RECOMPUTE_QUANTITY = "RECOMPUTE_QUANTITY"
    RECOMPUTE_VAT = "RECOMPUTE_VAT"
    USE_OFFICIAL_FALLBACK = "USE_OFFICIAL_FALLBACK"


class ValidationIssue(StrictModel):
    code: NonEmptyString
    request_item_id: RequestItemId | None
    line_id: str | None
    repair_action: Annotated[RepairAction, Field(strict=False)]


class ValidationMetrics(StrictModel):
    required_coverage: Score
    explicit_quantity_accuracy: Score
    linear_measurement_accuracy: Score
    unjustified_line_rate: Score
    stage_completion_rate: Score
    assumptions_count: NonNegativeInt


class ValidationReport(StrictModel):
    valid: bool
    critical: list[ValidationIssue]
    warnings: list[str]
    metrics: ValidationMetrics

    @model_validator(mode="after")
    def validate_outcome(self) -> "ValidationReport":
        if self.valid and self.critical:
            raise ValueError("a valid report cannot contain critical issues")
        if not self.valid and not self.critical:
            raise ValueError("an invalid report requires at least one critical issue")
        return self
