"""One-shot script to populate trade_services.estimated_price from bpu_items.

Phase 3 of the price improvement plan. Matches each TradeService to the most
similar BpuItem by designation (fuzzy string matching) and copies the BPU price.

Usage:
    python scripts/backfill_trade_service_prices.py

Requirements:
    - Database must be reachable via DATABASE_URL in .env
    - Both bpu_items and trade_services tables must be populated
"""

import asyncio
import difflib
import logging
import os
import sys
import unicodedata
import re

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models.bpu_item import BpuItem
from app.models.trade_service import TradeService
from app.models.trade import Trade

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    """Normalize text for comparison: strip accents, lowercase, collapse spaces."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_text = ascii_text.lower().strip()
    ascii_text = re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()
    return ascii_text


async def backfill_prices():
    from dotenv import load_dotenv
    load_dotenv()

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL not set in environment.")
        return

    # Ensure async driver
    if "postgresql://" in database_url and "postgresql+asyncpg://" not in database_url:
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Load all BPU items with prices
        bpu_rows = (
            await db.execute(
                select(BpuItem.designation, BpuItem.prix_unitaire_ht, BpuItem.unite)
                .where(BpuItem.prix_unitaire_ht > 0)
            )
        ).all()

        if not bpu_rows:
            logger.error("No BPU items found with prices > 0.")
            return

        logger.info("Loaded %d BPU items with prices.", len(bpu_rows))

        # Build normalised lookup
        bpu_by_norm: dict[str, tuple[float, str]] = {}
        bpu_norm_names: list[str] = []
        for designation, price, unit in bpu_rows:
            norm = _normalize(designation)
            if norm and norm not in bpu_by_norm:
                bpu_by_norm[norm] = (price, unit)
                bpu_norm_names.append(norm)

        # Load trade services with zero prices
        ts_rows = (
            await db.execute(
                select(TradeService, Trade.name)
                .join(Trade, Trade.id == TradeService.trade_id)
                .where(TradeService.estimated_price <= 0)
            )
        ).all()

        logger.info("Found %d trade services with estimated_price <= 0.", len(ts_rows))

        updated = 0
        not_found = 0

        for service, trade_name in ts_rows:
            norm_desig = _normalize(service.designation)
            if not norm_desig:
                continue

            # Try exact match first
            if norm_desig in bpu_by_norm:
                price, _ = bpu_by_norm[norm_desig]
                service.estimated_price = price
                updated += 1
                continue

            # Fuzzy match
            matches = difflib.get_close_matches(
                norm_desig, bpu_norm_names, n=1, cutoff=0.6
            )
            if matches:
                price, _ = bpu_by_norm[matches[0]]
                confidence = difflib.SequenceMatcher(
                    None, norm_desig, matches[0]
                ).ratio()
                service.estimated_price = price
                updated += 1
                logger.info(
                    "  Matched: '%s' -> BPU '%s' (%.0f EUR, confidence=%.2f)",
                    service.designation[:60],
                    matches[0][:60],
                    price,
                    confidence,
                )
            else:
                not_found += 1
                logger.warning(
                    "  No match: '%s' (trade=%s)",
                    service.designation[:60],
                    trade_name,
                )

        await db.commit()
        logger.info(
            "Done. Updated: %d, Not found: %d, Total: %d",
            updated,
            not_found,
            len(ts_rows),
        )

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(backfill_prices())
