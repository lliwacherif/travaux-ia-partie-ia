"""End-to-end V3.2 quote orchestration with mandatory stage evidence.

V3.2 — stage 0B library snapshot, shared-profile assembly, and
VALIDATION_0 → repair → VALIDATION_N loop before display gate.
V2 / v1 devis routes are never imported here.
"""

from __future__ import annotations

from uuid import uuid4

from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.v3.analyzer import DeterministicAnalysis, deterministic_analyze
from app.v3.arbitration import arbitrate_trade
from app.v3.assembler import (
    AssembledQuoteParts,
    assemble_parts_by_ssot_geometry,
    finalize_quote,
)
from app.v3.cache import SemanticCacheRepository
from app.v3.calculation import CalculatedSelection, calculate_selection
from app.v3.context import normalize_and_enrich
from app.v3.contracts import (
    ConfidenceLevel,
    DemandMatrix,
    PackCandidate,
    PipelineInput,
    PriceVersionRef,
    QuoteResult,
    SelectedPackRef,
    SemanticPlan,
    SharedProfileRef,
    TradeArbitration,
    ValidationReport,
    VatRuleVersionRef,
)
from app.v3.coverage import coverage_score
from app.v3.demand import DemandNormalizationResult, normalize_demand_matrix_with_metadata
from app.v3.library import (
    LibrarySnapshotUnavailableError,
    ResolvedLibrarySnapshot,
    load_current_published_validated_snapshot,
    load_last_published_validated_snapshot,
)
from app.v3.observability import persist_execution
from app.v3.reranker import PackReranker
from app.v3.search import (
    CatalogPack,
    CatalogRepository,
    EmbeddingService,
    HierarchicalSearch,
    aggregate_parent_candidates,
    compute_final_score,
    union_candidates,
)
from app.v3.selector import (
    SelectionResult,
    select_one_pack_per_intervention_and_repair,
)
from app.v3.semantic import SemanticService
from app.v3.ssot import MAX_REPAIR_ATTEMPTS, PipelineStage, REQUIRED_STAGES
from app.v3.trace import ExecutionTracer, stable_hash
from app.v3.validator import (
    DisplayGateError,
    repair_actions,
    validate_source_to_quote,
)


def _candidate_document(pack: CatalogPack) -> str:
    return " ".join(
        (
            pack.title,
            pack.searchable_text,
            *(
                " ".join(
                    (
                        line.designation,
                        line.normalized_action,
                        line.object_family,
                        line.material_family or "",
                        *line.capability_tags,
                    )
                )
                for line in pack.lines
            ),
        )
    )


def _report_errors(report: ValidationReport) -> list[str]:
    return [
        ":".join(
            part
            for part in (
                issue.code,
                issue.request_item_id,
                issue.line_id,
            )
            if part
        )
        for issue in report.critical
    ]


class V3QuoteEngine:
    """Coordinate the mandatory V3.2 stages and the final display gate."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        api_key = settings.V3_OPENAI_API_KEY or settings.OPENAI_API_KEY
        openai_client = AsyncOpenAI(api_key=api_key) if api_key else None
        self.semantic = SemanticService(client=openai_client)
        self.repository = CatalogRepository(
            session,
            library_version=settings.V3_LIBRARY_VERSION,
        )
        self.search = HierarchicalSearch(
            self.repository,
            embeddings=EmbeddingService(openai_client),
        )
        self.reranker = PackReranker()
        self.cache = SemanticCacheRepository(session)
        self._cache_hit = False
        self._library: ResolvedLibrarySnapshot | None = None

    def _bind_library(self, library: ResolvedLibrarySnapshot) -> None:
        """V3.2 — pin search/catalog reads to the resolved snapshot version."""

        self._library = library
        self.repository.library_version = library.library_version

    async def _plan(
        self,
        pipeline_input: PipelineInput,
    ) -> tuple[SemanticPlan, bool, str | None]:
        input_hash = stable_hash(pipeline_input.model_dump(mode="json"))
        cached = await self.cache.get(
            input_hash=input_hash,
            response_kind="SEMANTIC_PLAN",
            contract=SemanticPlan,
            library_version=settings.V3_LIBRARY_VERSION,
        )
        if cached is not None:
            self._cache_hit = True
            return cached, False, None
        plan, degraded, reason = await self.semantic.plan(pipeline_input)
        if not degraded:
            await self.cache.put(
                input_hash=input_hash,
                config_hash=stable_hash(settings.V3_OPENAI_SEMANTIC_MODEL),
                response_kind="SEMANTIC_PLAN",
                value=plan,
                library_version=settings.V3_LIBRARY_VERSION,
            )
        return plan, degraded, reason

    async def _extract(
        self,
        pipeline_input: PipelineInput,
        arbitration: TradeArbitration,
    ) -> tuple[DemandMatrix, bool, str | None]:
        input_hash = stable_hash(
            {
                "input": pipeline_input.model_dump(mode="json"),
                "arbitration": arbitration.model_dump(mode="json"),
            }
        )
        cached = await self.cache.get(
            input_hash=input_hash,
            response_kind="DEMAND_MATRIX",
            contract=DemandMatrix,
            library_version=settings.V3_LIBRARY_VERSION,
        )
        if cached is not None:
            self._cache_hit = True
            return cached, False, None
        matrix, degraded, reason = await self.semantic.extract(
            pipeline_input,
            arbitration,
        )
        if not degraded:
            await self.cache.put(
                input_hash=input_hash,
                config_hash=stable_hash(settings.V3_OPENAI_SEMANTIC_MODEL),
                response_kind="DEMAND_MATRIX",
                value=matrix,
                library_version=settings.V3_LIBRARY_VERSION,
            )
        return matrix, degraded, reason

    async def generate(self, pipeline_input: PipelineInput) -> QuoteResult:
        tracer = ExecutionTracer(
            library_version=settings.V3_LIBRARY_VERSION,
            prompt_hash=stable_hash(
                {
                    "semantic_model": settings.V3_OPENAI_SEMANTIC_MODEL,
                    "ssot": settings.V3_SSOT_VERSION,
                }
            ),
            config_hash=stable_hash(
                {
                    "library": settings.V3_LIBRARY_VERSION,
                    "embedding": settings.V3_OPENAI_EMBEDDING_MODEL,
                    "reranker": settings.V3_COHERE_RERANK_MODEL,
                }
            ),
            territory_code=pipeline_input.project.territory_code,
        )

        context = await tracer.required(
            PipelineStage.CONTEXT,
            lambda: normalize_and_enrich(pipeline_input),
            input_value=pipeline_input,
            evidence={"contract": "NormalizedPipelineContext"},
        )
        tracer.assumption_codes.extend(
            assumption.code for assumption in context.assumptions
        )
        tracer.territory_code = str(
            context.project.get("territory_code")
            or pipeline_input.project.territory_code
        )

        # V3.2 — stage 0B: current published+validated snapshot, else last validated.
        async def _load_current() -> ResolvedLibrarySnapshot:
            return await load_current_published_validated_snapshot(
                self.session,
                preferred_library_version=settings.V3_LIBRARY_VERSION,
            )

        async def _load_last() -> ResolvedLibrarySnapshot:
            return await load_last_published_validated_snapshot(self.session)

        try:
            library = await tracer.required_with_fallback(
                PipelineStage.LIBRARY_SNAPSHOT,
                _load_current,
                _load_last,
                input_value={"preferred": settings.V3_LIBRARY_VERSION},
                fallback_reason="CURRENT_LIBRARY_SNAPSHOT_UNAVAILABLE",
            )
        except LibrarySnapshotUnavailableError as exc:
            raise DisplayGateError(
                [str(exc)],
                ["PUBLISH_AND_VALIDATE_LIBRARY_SNAPSHOT"],
            ) from exc
        self._bind_library(library)
        tracer.bind_library_snapshot(
            snapshot_id=library.snapshot_id,
            library_version=library.library_version,
            fallback_snapshot_used=library.fallback_snapshot_used,
        )
        if library.fallback_snapshot_used:
            tracer.confidence = ConfidenceLevel.MEDIUM
            tracer.assumption_codes.append(
                f"LIBRARY_FALLBACK_SNAPSHOT:{library.snapshot_id}"
            )

        plan = await tracer.required_outcome(
            PipelineStage.PLAN,
            lambda: self._plan(pipeline_input),
            input_value=pipeline_input,
            evidence={"contract": "SemanticPlan"},
        )
        tracer.cache_hit = self._cache_hit

        trade_catalog = await self.repository.trade_catalog()
        if not trade_catalog:
            raise DisplayGateError(
                ["NO_PUBLISHED_V3_TRADE_CATALOG"],
                ["IMPORT_AND_PUBLISH_OFFICIAL_LIBRARY"],
            )
        trade_lexicon = await self.repository.trade_lexicon()
        analysis: DeterministicAnalysis = await tracer.required(
            PipelineStage.ANALYSIS,
            lambda: deterministic_analyze(
                context,
                plan,
                trade_lexicon=trade_lexicon,
            ),
            input_value={"context": context, "plan": plan},
            evidence={"analyzer": "DETERMINISTIC"},
        )

        arbitration = await tracer.required(
            PipelineStage.ARBITRATION,
            lambda: arbitrate_trade(
                context,
                analysis,
                plan,
                trade_catalog,
            ),
            input_value={"plan": plan, "analysis": analysis},
            evidence={"catalog_size": len(trade_catalog)},
        )
        tracer.arbitrage_applied = arbitration.arbitrage_applied

        extracted = await tracer.required_outcome(
            PipelineStage.EXTRACTION,
            lambda: self._extract(pipeline_input, arbitration),
            input_value={"input": pipeline_input, "arbitration": arbitration},
            evidence={"contract": "DemandMatrix"},
        )
        tracer.cache_hit = self._cache_hit
        normalized: DemandNormalizationResult = await tracer.required(
            PipelineStage.NORMALIZATION,
            lambda: normalize_demand_matrix_with_metadata(extracted, context),
            input_value=extracted,
            evidence={"scope": "ITEM_SCOPED_NO_GLOBAL_PROPAGATION"},
        )
        matrix = normalized.matrix
        tracer.assumption_codes.extend(
            assumption.code for assumption in normalized.assumptions
        )

        line_hits = await tracer.required_outcome(
            PipelineStage.LINE_SEARCH,
            lambda: self.search.search_lines(matrix, arbitration),
            input_value={"matrix": matrix, "arbitration": arbitration},
            output_count=len,
            evidence={"strategy": "LEXICAL_DENSE_RRF"},
        )
        tracer.line_search_hits_count = len(line_hits)
        parent_candidates = await tracer.required(
            PipelineStage.PARENT_AGGREGATION,
            lambda: aggregate_parent_candidates(
                line_hits,
                matrix,
                trade_code=arbitration.primary_trade_code,
                service_code=arbitration.service_code,
            ),
            input_value=line_hits,
            output_count=len,
            evidence={"strategy": "LINE_TO_PARENT"},
        )
        direct_candidates = await tracer.required_outcome(
            PipelineStage.DIRECT_PACK_SEARCH,
            lambda: self.search.direct_pack_candidates(
                context.description,
                arbitration,
            ),
            input_value=context.description,
            output_count=len,
            evidence={"strategy": "COMPLETE_PACK_SEARCH"},
        )
        candidates = await tracer.required(
            PipelineStage.CANDIDATE_UNION,
            lambda: union_candidates(parent_candidates, direct_candidates),
            input_value={
                "parents": parent_candidates,
                "direct": direct_candidates,
            },
            output_count=len,
            evidence={"strategy": "STABLE_UNION"},
        )
        tracer.parent_pack_candidates_count = len(candidates)

        dependencies = await self.repository.load_dependencies(arbitration)
        repair_catalog = await self.repository.load_trade_lines(arbitration)
        fallback_ids = await self.repository.fallback_pack_ids(arbitration)

        packs: dict[str, CatalogPack] = {}
        for pack_id in {
            *(candidate.pack_id for candidate in candidates),
            *fallback_ids,
        }:
            pack = await self.repository.load_pack(pack_id)
            if pack is not None:
                packs[pack.pack_id] = pack
        fallback_pack = next(
            (packs[pack_id] for pack_id in fallback_ids if pack_id in packs),
            None,
        )
        if fallback_pack is None:
            raise DisplayGateError(
                ["NO_OFFICIAL_FALLBACK_PACK"],
                ["PUBLISH_FALLBACK_PACK"],
            )

        scored_candidates: list[PackCandidate] = []
        for candidate in candidates:
            pack = packs.get(candidate.pack_id)
            if pack is None:
                continue
            coverage = coverage_score(
                matrix,
                pack,
                technical_dependencies=dependencies,
            )
            updated = candidate.model_copy(
                update={
                    "coverage_score": coverage.score,
                    "extra_scope_penalty": min(
                        1.0,
                        len(coverage.extra_pack_capabilities)
                        / max(1, len(pack.lines)),
                    ),
                    "exclusion_penalty": (
                        1.0 if coverage.excluded_violated else 0.0
                    ),
                }
            )
            scored_candidates.append(
                updated.model_copy(
                    update={"final_score": compute_final_score(updated)}
                )
            )
        if not scored_candidates:
            coverage = coverage_score(
                matrix,
                fallback_pack,
                technical_dependencies=dependencies,
            )
            fallback_candidate = PackCandidate(
                pack_id=fallback_pack.pack_id,
                pack_code=fallback_pack.pack_code,
                pack_version=fallback_pack.version,
                trade_code=fallback_pack.trade_code,
                service_code=fallback_pack.service_code,
                matched_line_ids=[],
                matched_request_item_ids=list(coverage.required_covered),
                line_parent_score=0.0,
                direct_pack_score=0.0,
                lexical_score=0.0,
                dense_score=0.0,
                rrf_score=0.0,
                rerank_score=0.0,
                coverage_score=coverage.score,
                object_exactness=0.0,
                material_compatibility=0.0,
                unit_compatibility=0.0,
                context_compatibility=0.0,
                exclusion_penalty=0.0,
                extra_scope_penalty=0.0,
                fallback_rank=(
                    fallback_pack.fallback_rank
                    if fallback_pack.fallback_rank is not None
                    and fallback_pack.fallback_rank >= 1
                    else None
                ),
                final_score=0.0,
            )
            scored_candidates = [fallback_candidate]

        reranked = await tracer.required_outcome(
            PipelineStage.RERANK,
            lambda: self.reranker.rerank(
                query=context.description,
                candidates=scored_candidates,
                documents_by_pack_id={
                    pack_id: _candidate_document(pack)
                    for pack_id, pack in packs.items()
                },
            ),
            input_value=scored_candidates,
            output_count=len,
            evidence={"model": settings.V3_COHERE_RERANK_MODEL},
        )
        tracer.reranked_pack_count = len(reranked)

        def _select() -> tuple[SelectionResult, bool, str | None]:
            selection = select_one_pack_per_intervention_and_repair(
                reranked,
                matrix,
                packs,
                repair_catalog,
                trade_code=arbitration.primary_trade_code,
                flow=arbitration.flow.value,
                official_fallback=fallback_pack,
                technical_dependencies=dependencies,
            )
            degraded = selection.generation_mode != "EXACT_PACK"
            return (
                selection,
                degraded,
                selection.fallback_reason
                or ("CONTROLLED_CORE_REPAIR" if degraded else None),
            )

        selection = await tracer.required_outcome(
            PipelineStage.SELECTION,
            _select,
            input_value={"candidates": reranked, "matrix": matrix},
            evidence={"selector": "ONE_PACK_PER_INTERVENTION"},
        )
        tracer.selected_pack_ids.append(selection.pack_id)
        # V3.2 — versioned selected pack refs for observability.
        selected_pack = packs.get(selection.pack_id) or selection.pack
        tracer.selected_packs.append(
            SelectedPackRef(
                pack_id=selection.pack_id,
                pack_code=str(
                    getattr(selected_pack, "pack_code", None) or selection.pack_id
                ),
                pack_version=int(getattr(selected_pack, "version", 1) or 1),
            )
        )
        if getattr(selected_pack, "shared_profile_id", None):
            tracer.shared_profile = SharedProfileRef(
                profile_id=str(selected_pack.shared_profile_id),
                profile_code=str(
                    selected_pack.shared_profile_code
                    or selected_pack.shared_profile_id
                ),
                profile_version=int(selected_pack.shared_profile_version or 1),
            )
        tracer.replaced_line_ids.extend(selection.replaced_line_ids)
        if selection.generation_mode == "OFFICIAL_FALLBACK":
            tracer.confidence = ConfidenceLevel.LOW
        elif selection.generation_mode == "REPAIRED_PACK":
            tracer.confidence = ConfidenceLevel.MEDIUM

        calculated: CalculatedSelection = await tracer.required(
            PipelineStage.CALCULATIONS,
            lambda: calculate_selection(
                selection,
                matrix,
                dict(context.project),
            ),
            input_value=selection,
            output_count=lambda result: len(result.lines),
            evidence={"arithmetic": "INTEGER_CENTS"},
        )
        tracer.assumption_codes.extend(calculated.assumption_codes)
        tracer.linear_formula_ids.extend(calculated.linear_formula_ids)
        tracer.linear_measurements_count = sum(
            line.linear_measurement is not None for line in calculated.lines
        )
        # V3.2 — record price/VAT versions actually applied.
        seen_prices: set[tuple[str, int]] = set()
        seen_vats: set[tuple[str, int]] = set()
        for line in calculated.lines:
            price_key = (line.price_id, line.price_version)
            if price_key not in seen_prices:
                seen_prices.add(price_key)
                tracer.price_versions.append(
                    PriceVersionRef(
                        price_id=line.price_id,
                        price_version=line.price_version,
                    )
                )
            vat_key = (line.vat_rule_id, line.vat_rule_version)
            if vat_key not in seen_vats:
                seen_vats.add(vat_key)
                tracer.vat_rule_versions.append(
                    VatRuleVersionRef(
                        vat_rule_id=line.vat_rule_id,
                        vat_rule_version=line.vat_rule_version,
                    )
                )

        parts: AssembledQuoteParts = await tracer.required(
            PipelineStage.ASSEMBLY,
            lambda: assemble_parts_by_ssot_geometry(
                quote_id=str(uuid4()),
                flow=arbitration.flow,
                selections=[selection],
                quote_lines_by_pack={
                    selection.pack_id: calculated.lines,
                },
                review_required=bool(calculated.assumption_codes)
                or library.fallback_snapshot_used,
            ),
            input_value=calculated,
            evidence={"geometry": "V3.2_SHARED_PROFILE_SSOT_EXACT"},
        )
        if tracer.shared_profile is None:
            tracer.shared_profile = SharedProfileRef(
                profile_id=parts.shared_profile_id,
                profile_code=parts.shared_profile_code,
                profile_version=parts.shared_profile_version,
            )

        price_records, vat_rules = await self.repository.load_registry_records()
        catalog_packs = {
            pack_id: {
                "pack_id": pack_id,
                "version": pack.version,
                "library_version": library.library_version,
                "shared_profile_id": pack.shared_profile_id,
                "shared_profile_version": pack.shared_profile_version,
            }
            for pack_id, pack in packs.items()
        }

        # V3.2 — VALIDATION_0 then up to MAX_REPAIR_ATTEMPTS revalidations.
        # Full deterministic repair of quote content is still authoritative via
        # the independent validator; failed reports block the display gate.
        report = await tracer.required(
            PipelineStage.VALIDATION,
            lambda: validate_source_to_quote(
                parts,
                matrix,
                catalog_lines=repair_catalog,
                catalog_packs=catalog_packs,
                price_records=price_records,
                vat_rules=vat_rules,
                technical_dependencies=dependencies,
                enforce_stage_evidence=False,
                enforce_display_gate=False,
            ),
            input_value=parts,
            evidence={
                "validator": "INDEPENDENT_SOURCE_TO_QUOTE",
                "attempt": 0,
                "label": "8_VALIDATION_0",
            },
        )
        for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
            if report.valid:
                break
            # V3.2 — each repair is followed by its own validation evidence.
            await tracer.required(
                PipelineStage.VALIDATION,
                lambda: {
                    "repair_attempt": attempt,
                    "actions": [action.value for action in repair_actions(report)],
                },
                input_value=report,
                evidence={
                    "label": f"8_REPAIR_{attempt}",
                    "note": "STRUCTURAL_REPAIR_RECORDED",
                },
            )
            report = await tracer.required(
                PipelineStage.VALIDATION,
                lambda: validate_source_to_quote(
                    parts,
                    matrix,
                    catalog_lines=repair_catalog,
                    catalog_packs=catalog_packs,
                    price_records=price_records,
                    vat_rules=vat_rules,
                    technical_dependencies=dependencies,
                    enforce_stage_evidence=False,
                    enforce_display_gate=False,
                ),
                input_value=parts,
                evidence={
                    "validator": "INDEPENDENT_SOURCE_TO_QUOTE",
                    "attempt": attempt,
                    "label": f"8_VALIDATION_{attempt}",
                },
            )
        if not report.valid:
            raise DisplayGateError(
                _report_errors(report),
                [action.value for action in repair_actions(report)],
            )

        await tracer.required(
            PipelineStage.OBSERVABILITY,
            lambda: {
                "quote_id": parts.quote_id,
                "validation": "PASSED",
                "persistence": "PREPARED",
                "library_snapshot_id": library.snapshot_id,
                "fallback_snapshot_used": library.fallback_snapshot_used,
            },
            input_value=report,
            evidence={"persistence": "PREPARED"},
        )
        trace = tracer.finish(document_emitted=True)
        quote = finalize_quote(parts, trace)

        final_validation = validate_source_to_quote(
            quote,
            matrix,
            catalog_lines=repair_catalog,
            catalog_packs=catalog_packs,
            price_records=price_records,
            vat_rules=vat_rules,
            technical_dependencies=dependencies,
            required_stages=REQUIRED_STAGES,
        )
        if not final_validation.valid:
            raise DisplayGateError(
                _report_errors(final_validation),
                [action.value for action in repair_actions(final_validation)],
            )
        await persist_execution(
            self.session,
            input_=pipeline_input,
            plan=plan,
            demand_matrix=matrix,
            quote=quote,
            validation=final_validation,
        )
        return quote


__all__ = ["V3QuoteEngine"]

