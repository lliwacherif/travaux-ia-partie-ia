"""Single source of truth for the V3 quote engine.

The values in this module mirror the final V3.1 specification.  Runtime code,
publication tooling, migrations, and tests must import them instead of copying
geometry, retrieval, or execution constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping

SSOT_VERSION: Final = "2026-07-30.3"
PIPELINE_VERSION: Final = "V3.1"

SEMANTIC_MODEL: Final = "gpt-4o-2024-11-20"
EMBEDDING_MODEL: Final = "text-embedding-3-small"
RERANK_MODEL: Final = "rerank-v4.0-pro"
EMBEDDING_DIMENSIONS: Final = 1536


class Flow(StrEnum):
    TRAVAUX = "TRAVAUX"
    DEPANNAGE = "DEPANNAGE"


FLOWS: Final = tuple(flow.value for flow in Flow)


class Phase(StrEnum):
    SETUP = "SETUP"
    CORE = "CORE"
    FINISH = "FINISH"


PHASES: Final = tuple(phase.value for phase in Phase)


@dataclass(frozen=True, slots=True)
class Geometry:
    setup: int
    core_per_trade: int
    finish: int
    exact: bool = True

    @property
    def mono_trade_line_count(self) -> int:
        return self.setup + self.core_per_trade + self.finish

    def line_count(self, trade_count: int = 1) -> int:
        if trade_count < 1:
            raise ValueError("trade_count must be at least 1")
        return self.setup + (self.core_per_trade * trade_count) + self.finish


TRAVAUX_GEOMETRY: Final = Geometry(setup=3, core_per_trade=14, finish=3)
DEPANNAGE_GEOMETRY: Final = Geometry(setup=1, core_per_trade=3, finish=1)
GEOMETRIES: Final[Mapping[Flow, Geometry]] = MappingProxyType(
    {
        Flow.TRAVAUX: TRAVAUX_GEOMETRY,
        Flow.DEPANNAGE: DEPANNAGE_GEOMETRY,
    }
)
REMOVED_FLOWS: Final = ("INTERVENTION_1_5_1",)
FORBIDDEN_GEOMETRIES: Final = ((1, 5, 1),)


@dataclass(frozen=True, slots=True)
class SearchLimits:
    line_top_k_per_request_item: int = 20
    direct_pack_top_k: int = 40
    parent_pack_top_k: int = 20
    rerank_top_k: int = 20
    coverage_top_k: int = 10
    rrf_k: int = 60
    one_vote_per_request_item_and_pack: bool = True


SEARCH: Final = SearchLimits()

SEARCH_WEIGHTS: Final[Mapping[str, float]] = MappingProxyType(
    {
        "coverage_score": 0.26,
        "rerank_score": 0.18,
        "line_parent_score": 0.16,
        "direct_pack_score": 0.10,
        "object_exactness": 0.08,
        "material_compatibility": 0.06,
        "unit_compatibility": 0.05,
        "context_compatibility": 0.05,
        "dense_score": 0.03,
        "lexical_score": 0.03,
    }
)


class LinearMeasurementMode(StrEnum):
    EXPLICIT = "EXPLICIT"
    AXIAL = "AXIAL"
    LONGITUDINAL = "LONGITUDINAL"
    PERIMETRIC = "PERIMETRIC"
    DEVELOPED = "DEVELOPED"
    CUMULATED = "CUMULATED"
    SURFACE_TO_LINEAR = "SURFACE_TO_LINEAR"
    COUNT_TIMES_LENGTH = "COUNT_TIMES_LENGTH"


LINEAR_MEASUREMENT_MODES: Final = tuple(
    mode.value for mode in LinearMeasurementMode
)
LINEAR_MEASUREMENT_UNIT: Final = "ML"
LINEAR_MEASUREMENT_PRECISION: Final = 3
FORBID_UNREGISTERED_LINEAR_CONVERSION: Final = True
AUTHORIZED_LIBRARY_UNITS: Final = (
    "M2",
    "ML",
    "M3",
    "UNIT",
    "HOUR",
    "DAY",
    "FORFAIT",
    # The official BPU contains steel fabrication priced by metric tonne.
    # This is a catalog unit, not an inferred user-input conversion.
    "TONNE",
)

FORBIDDEN_LABELS: Final = (
    "autres travaux",
    "autres fournitures",
    "prestations diverses",
    "divers",
    "ajustement forfaitaire",
)

MONEY_STORAGE: Final = "CENTS"
MONEY_SCALE: Final = 2
DEFAULT_COUNTRY: Final = "FR"
DEFAULT_VAT_RULE: Final = "FR_STANDARD_20"
MAX_REPAIR_ATTEMPTS: Final = 3

REQUIRE_STAGE_EVIDENCE: Final = True
FORBID_SILENT_FALLBACK: Final = True
MINIMUM_STAGE_COMPLETION_RATE: Final = 1.0
ARTIFICIAL_MINIMUM_LATENCY_MS: Final = 0
AUTO_PUBLISH_OFFICIAL_LIBRARY: Final = False
REQUIRE_HUMAN_APPROVAL: Final = True
REQUIRE_FULL_REGRESSION: Final = True


class PipelineStage(StrEnum):
    CONTEXT = "0_CONTEXT"
    PLAN = "1_PLAN"
    ANALYSIS = "2_ANALYSIS"
    ARBITRATION = "2BIS_ARBITRATION"
    EXTRACTION = "3_EXTRACTION"
    NORMALIZATION = "3BIS_NORMALIZATION"
    LINE_SEARCH = "4A_LINE_SEARCH"
    PARENT_AGGREGATION = "4B_PARENT_AGGREGATION"
    DIRECT_PACK_SEARCH = "4C_DIRECT_PACK_SEARCH"
    CANDIDATE_UNION = "4D_CANDIDATE_UNION"
    RERANK = "4BIS_RERANK"
    SELECTION = "5_SELECTION"
    CALCULATIONS = "6_CALCULATIONS"
    ASSEMBLY = "7_ASSEMBLY"
    VALIDATION = "8_VALIDATION"
    OBSERVABILITY = "9_OBSERVABILITY"


REQUIRED_STAGES: Final = tuple(stage.value for stage in PipelineStage)


def expected_geometry(flow: Flow | str) -> Geometry:
    """Return the only authorized geometry for ``flow``."""
    return GEOMETRIES[Flow(flow)]
