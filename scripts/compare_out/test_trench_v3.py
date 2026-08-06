"""Local V3 trench smoke test."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from app.v3.contracts import CompanyContext, PipelineInput, ProjectContext
from app.v3.db import v3_async_session_factory
from app.v3.orchestrator import V3QuoteEngine
from app.v3.presentation import present_quote
from app.v3.validator import DisplayGateError
from sqlalchemy import text

PROMPT = (
    "je dois réaliser 32 ml de tranchées entre la limite de propriété et le "
    "bâtiment pour l’eau, l’électricité et les télécommunications. Prévoir "
    "terrassement, lit de sable, fourreaux réglementaires, grillages "
    "avertisseurs, remblai compacté et deux regards.canalisation d’eaux "
    "usées PVC sur 18 ml"
)


async def main() -> None:
    async with v3_async_session_factory() as session:
        n = (
            await session.execute(
                text("SELECT COUNT(*) FROM quote_packs WHERE status='PUBLISHED'")
            )
        ).scalar()
        print("published_packs", n)
        pipeline = PipelineInput(
            request_id=str(uuid4()),
            description=PROMPT,
            company=CompanyContext(primary_trade_code="", enabled_service_codes=[]),
            project=ProjectContext(
                country="FR",
                territory_code="FR-MET",
                customer_type=None,
                building_use=None,
                building_age_years=None,
                location=None,
            ),
        )
        try:
            quote = await V3QuoteEngine(session).generate(pipeline)
            await session.commit()
            devis = present_quote(quote)
            data = quote.model_dump(mode="python")
            out = {
                "ok": True,
                "ttc": devis.get("montant_ttc"),
                "generation_mode": data.get("generation_mode"),
                "trade_blocks": [
                    {
                        "trade": block.get("trade_code"),
                        "n": len(block.get("lines") or []),
                    }
                    for block in (data.get("trade_blocks") or [])
                ],
                "n_setup": len(data.get("setup_lines") or []),
                "n_finish": len(data.get("finish_lines") or []),
                "pack_codes": [
                    pack.get("pack_code")
                    for pack in (
                        (data.get("trace") or {}).get("selected_packs") or []
                    )
                ],
                "lines": [
                    {
                        "phase": "SETUP",
                        "d": line.get("designation"),
                        "q": line.get("quantity"),
                        "u": line.get("unit"),
                    }
                    for line in (data.get("setup_lines") or [])
                ]
                + [
                    {
                        "phase": "CORE",
                        "d": line.get("designation"),
                        "q": line.get("quantity"),
                        "u": line.get("unit"),
                    }
                    for block in (data.get("trade_blocks") or [])
                    for line in (block.get("lines") or [])
                ]
                + [
                    {
                        "phase": "FINISH",
                        "d": line.get("designation"),
                        "q": line.get("quantity"),
                        "u": line.get("unit"),
                    }
                    for line in (data.get("finish_lines") or [])
                ],
            }
            path = "scripts/compare_out/trench_v3_ok.json"
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(out, handle, ensure_ascii=False, indent=2, default=str)
            print(
                json.dumps(
                    {key: value for key, value in out.items() if key != "lines"},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            print("N_LINES", len(out["lines"]))
            for line in out["lines"]:
                print(
                    f"- [{line['phase']}] {str(line['d'])[:75]} | "
                    f"{line['q']} {line['u']}"
                )
        except DisplayGateError as exc:
            print("GATE", exc.errors[:8], "...", exc.allowed_repairs)


if __name__ == "__main__":
    asyncio.run(main())
