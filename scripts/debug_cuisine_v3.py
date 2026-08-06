"""Debug V3 cuisine coverage for Renovation cuisine 8m2."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from openai import AsyncOpenAI

from app.core.config import settings
from app.v3.analyzer import deterministic_analyze
from app.v3.arbitration import arbitrate_trade
from app.v3.context import normalize_and_enrich
from app.v3.contracts import CompanyContext, PipelineInput, ProjectContext
from app.v3.coverage import coverage_score, match_item_to_line
from app.v3.db import v3_async_session_factory
from app.v3.demand import normalize_demand_matrix_with_metadata
from app.v3.search import CatalogRepository
from app.v3.semantic import SemanticService


async def main() -> None:
    pipeline_input = PipelineInput(
        request_id=str(uuid4()),
        description="Renovation cuisine 8m2",
        company=CompanyContext(primary_trade_code="", enabled_service_codes=[]),
        project=ProjectContext(
            country="FR",
            customer_type=None,
            building_use=None,
            building_age_years=None,
            location=None,
        ),
    )
    context = normalize_and_enrich(pipeline_input)
    api_key = settings.V3_OPENAI_API_KEY or settings.OPENAI_API_KEY
    semantic = SemanticService(client=AsyncOpenAI(api_key=api_key) if api_key else None)

    async with v3_async_session_factory() as session:
        repo = CatalogRepository(session, library_version=settings.V3_LIBRARY_VERSION)
        trades = await repo.trade_catalog()
        lexicon = await repo.trade_lexicon()
        plan, degraded, reason = await semantic.plan(pipeline_input)
        print("plan_degraded", degraded, reason)
        print("plan", json.dumps(plan.model_dump(mode="json"), ensure_ascii=False, indent=2))
        analysis_llm, _, _ = await semantic.extract(pipeline_input, plan)
        print(
            "analysis_llm",
            json.dumps(analysis_llm.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )
        # Demand is often produced from analysis in orchestrator - mirror that path
        from app.v3.orchestrator import V3QuoteEngine

        engine = V3QuoteEngine(session)
        try:
            quote = await engine.generate(pipeline_input)
            print("quote_ok", quote.totals)
        except Exception as exc:  # noqa: BLE001
            print("engine_error", type(exc).__name__, exc)

        # Coverage against fallback pack using deterministic analysis as fallback
        det = deterministic_analyze(context)
        print("deterministic", det)

        pack = None
        for trade in trades:
            if trade["trade_code"] == "CUISINE":
                from app.v3.contracts import TradeArbitration, Flow

                arb = TradeArbitration(
                    flow=Flow.TRAVAUX,
                    primary_trade_code="CUISINE",
                    secondary_trade_codes=[],
                    service_code=None,
                    confidence=1.0,
                    reason="debug",
                    library_version=settings.V3_LIBRARY_VERSION,
                )
                pack = await repo.load_fallback(arb)
                break
        if pack is None:
            print("no pack")
            return
        print("pack_lines_sample")
        for line in pack.lines[:5]:
            print(
                line.phase,
                line.normalized_action,
                line.object_family,
                line.material_family,
                line.designation[:50],
            )


if __name__ == "__main__":
    asyncio.run(main())
