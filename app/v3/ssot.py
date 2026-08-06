"""Single source of truth for the V3 quote engine.

V3.2 — Consolidation corrective finale (2026-07-31.1).
Correctifs ciblés à intégrer dans la V3.2 — interventions, signatures pack,
compatibilité bloquante, faits mesurés, packs immuables (pas d'hybride),
TVA recopiée depuis le pack, validateur source exacte, entrée vocale.
Ne remplace pas la V3.2 et n'ajoute aucune nouvelle couche IA.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Mapping

# V3.2 + Correctifs ciblés à intégrer dans la V3.2.
SSOT_VERSION: Final = "2026-07-31.1-correctifs"
PIPELINE_VERSION: Final = "V3.2"

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
        # V3.2 — one shared SETUP + one shared FINISH for the whole quote.
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

# V3.2 — explicit allowedUnits from SSOT (TONNE no longer authorized).
# Correctifs ciblés à intégrer dans la V3.2 — aucune unité « mois ».
AUTHORIZED_LIBRARY_UNITS: Final = (
    "M2",
    "ML",
    "M3",
    "UNIT",
    "HOUR",
    "DAY",
    "FORFAIT",
)
ALLOWED_UNITS: Final = AUTHORIZED_LIBRARY_UNITS
FORBIDDEN_UNITS: Final = ("MOIS", "MONTH", "MONTHS")


@dataclass(frozen=True, slots=True)
class SearchLimits:
    """Correctifs ciblés à intégrer dans la V3.2 §3.

    TopK values remain for scoring / batching evidence only.
    They MUST NOT eliminate published packs of the locked trade.
    """

    line_top_k_per_request_item: int = 20
    direct_pack_top_k: int = 40
    parent_pack_top_k: int = 20
    rerank_top_k: int = 20
    coverage_top_k: int = 10
    rrf_k: int = 60
    one_vote_per_request_item_and_pack: bool = True
    # Correctifs ciblés à intégrer dans la V3.2 — TopK non éliminatoire.
    top_k_is_evidence_only: bool = True
    compare_all_trade_packs: bool = True


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
# V3.2 — territorial VAT scope (no overseas rates until a full referential exists).
TERRITORY_SCOPE: Final = "FR_METROPOLE_CORSE"
DEFAULT_TERRITORY_CODE: Final = "FR-MET"
DEFAULT_VAT_RULE: Final = "FR_STANDARD_20"
MAX_REPAIR_ATTEMPTS: Final = 3

REQUIRE_STAGE_EVIDENCE: Final = True
FORBID_SILENT_FALLBACK: Final = True
MINIMUM_STAGE_COMPLETION_RATE: Final = 1.0
ARTIFICIAL_MINIMUM_LATENCY_MS: Final = 0

# V3.2 — library snapshot gates.
REQUIRE_PUBLISHED_SNAPSHOT: Final = True
REQUIRE_LAST_VALIDATED_SNAPSHOT: Final = True
IMMUTABLE_PUBLISHED_VERSIONS: Final = True

AUTO_PUBLISH_OFFICIAL_LIBRARY: Final = False
REQUIRE_HUMAN_APPROVAL: Final = True
REQUIRE_FULL_REGRESSION: Final = True

# Correctifs ciblés à intégrer dans la V3.2 — packs immuables, pas d'hybride.
FORBID_HYBRID_PACK_REPAIR: Final = True
# Correctifs ciblés à intégrer dans la V3.2 — TVA = copie exacte du pack.
COPY_PACK_VAT_EXACTLY: Final = True
FORBID_VAT_CONTEXT_SUBSTITUTION: Final = True
# Correctifs ciblés à intégrer dans la V3.2 — signature obligatoire à la publication.
REQUIRE_PACK_MATCH_SIGNATURE: Final = True


class EligibilityStatus(StrEnum):
    """Correctifs ciblés à intégrer dans la V3.2 §4 — contrôle à trois états."""

    COMPATIBLE = "COMPATIBLE"
    UNKNOWN = "UNKNOWN"
    INCOMPATIBLE = "INCOMPATIBLE"


class PipelineStage(StrEnum):
    CONTEXT = "0_CONTEXT"
    # V3.2 — mandatory library snapshot resolution before semantic work.
    LIBRARY_SNAPSHOT = "0B_LIBRARY_SNAPSHOT"
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
