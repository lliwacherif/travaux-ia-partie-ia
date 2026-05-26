"""Seed ``trades`` and ``trade_services`` from the project CSV exports.

Usage::

    python -m scripts.seed_csv
    # or, with custom paths:
    python -m scripts.seed_csv --trades path/to/trades.csv --services path/to/services.csv

The script is **idempotent**: rows with an ``id`` that already exists in the
DB are left untouched (``ON CONFLICT (id) DO NOTHING``). Re-running after a
partial failure is safe.

Design notes
------------
* Uses the **sync** DSN (``settings.SYNC_DATABASE_URL``) via psycopg2 - the
  seed runs outside the FastAPI event loop and there is no benefit to
  asyncpg here.
* Empty string fields become SQL ``NULL``.
* CSV ``user_id`` values are empty for the system catalog we are seeding;
  any future tenant-specific export will still ingest cleanly.
* ``is_system`` accepts ``"t"``, ``"true"``, ``"True"`` (and friends).
* ``estimated_price`` is coerced to ``float`` with ``0.0`` as the fallback.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

# Project root on sys.path so ``app.*`` works when invoked as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402

logger = logging.getLogger("seed_csv")

DEFAULT_TRADES_CSV = PROJECT_ROOT / "trades_rows.csv"
DEFAULT_SERVICES_CSV = PROJECT_ROOT / "trade_services_rows.csv"

TRUTHY = {"t", "true", "1", "yes", "y"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean(value: str | None) -> str | None:
    """Collapse empty strings to ``None`` so they end up as SQL ``NULL``."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _as_uuid(value: str | None) -> uuid.UUID | None:
    cleaned = _clean(value)
    return uuid.UUID(cleaned) if cleaned else None


def _as_bool(value: str | None, default: bool = True) -> bool:
    cleaned = _clean(value)
    return cleaned.lower() in TRUTHY if cleaned is not None else default


def _as_float(value: str | None, default: float = 0.0) -> float:
    cleaned = _clean(value)
    if cleaned is None:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


def _as_datetime(value: str | None) -> datetime | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    # Postgres exports often use "2025-07-29 12:27:09.632144+00" which
    # ``fromisoformat`` accepts starting with Python 3.11.
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        logger.warning("Could not parse timestamp %r - falling back to NULL.", cleaned)
        return None


def _iter_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


# ---------------------------------------------------------------------------
# Upserts
# ---------------------------------------------------------------------------
_TRADE_UPSERT_SQL = text(
    """
    INSERT INTO trades
        (id, name, description, user_id, is_system,
         category, subcategory, created_at, updated_at)
    VALUES
        (:id, :name, :description, :user_id, :is_system,
         :category, :subcategory,
         COALESCE(:created_at, NOW()),
         COALESCE(:updated_at, NOW()))
    ON CONFLICT (id) DO NOTHING;
    """
)

_SERVICE_UPSERT_SQL = text(
    """
    INSERT INTO trade_services
        (id, trade_id, designation, description, unit, category,
         estimated_price, user_id, is_system, created_at, updated_at)
    VALUES
        (:id, :trade_id, :designation, :description, :unit, :category,
         :estimated_price, :user_id, :is_system,
         COALESCE(:created_at, NOW()),
         COALESCE(:updated_at, NOW()))
    ON CONFLICT (id) DO NOTHING;
    """
)


def _row_to_trade(row: dict[str, str]) -> dict[str, Any]:
    return {
        "id": _as_uuid(row["id"]),
        "name": _clean(row["name"]),
        "description": _clean(row.get("description")),
        "user_id": _as_uuid(row.get("user_id")),
        "is_system": _as_bool(row.get("is_system")),
        "category": _clean(row.get("category")),
        "subcategory": _clean(row.get("subcategory")),
        "created_at": _as_datetime(row.get("created_at")),
        "updated_at": _as_datetime(row.get("updated_at")),
    }


def _row_to_service(row: dict[str, str]) -> dict[str, Any]:
    return {
        "id": _as_uuid(row["id"]),
        "trade_id": _as_uuid(row["trade_id"]),
        "designation": _clean(row.get("designation")),
        "description": _clean(row.get("description")),
        "unit": _clean(row.get("unit")) or "u",
        "category": _clean(row.get("category")),
        "estimated_price": _as_float(row.get("estimated_price")),
        "user_id": _as_uuid(row.get("user_id")),
        "is_system": _as_bool(row.get("is_system")),
        "created_at": _as_datetime(row.get("created_at")),
        "updated_at": _as_datetime(row.get("updated_at")),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _seed_table(
    engine: Engine,
    csv_path: Path,
    sql,
    row_builder,
    table_label: str,
) -> tuple[int, int]:
    """Return ``(rows_in_csv, rows_inserted)`` for the given table."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    rows = [row_builder(r) for r in _iter_rows(csv_path)]
    if not rows:
        logger.warning("%s: CSV %s is empty.", table_label, csv_path)
        return 0, 0

    with engine.begin() as conn:
        result = conn.execute(sql, rows)

    inserted = result.rowcount if result.rowcount is not None else 0
    logger.info(
        "%s: %d rows in CSV, %d newly inserted.",
        table_label,
        len(rows),
        inserted,
    )
    return len(rows), inserted


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="Seed trades + trade_services from CSV.")
    parser.add_argument(
        "--trades",
        type=Path,
        default=DEFAULT_TRADES_CSV,
        help=f"Path to trades CSV (default: {DEFAULT_TRADES_CSV.name}).",
    )
    parser.add_argument(
        "--services",
        type=Path,
        default=DEFAULT_SERVICES_CSV,
        help=f"Path to trade_services CSV (default: {DEFAULT_SERVICES_CSV.name}).",
    )
    args = parser.parse_args(argv)

    dsn = str(settings.SYNC_DATABASE_URL)
    logger.info("Connecting to %s", dsn.split("@", 1)[-1])  # avoid logging creds
    engine = create_engine(dsn, future=True)

    total_trades, inserted_trades = _seed_table(
        engine, args.trades, _TRADE_UPSERT_SQL, _row_to_trade, "trades"
    )
    total_services, inserted_services = _seed_table(
        engine, args.services, _SERVICE_UPSERT_SQL, _row_to_service, "trade_services"
    )

    logger.info(
        "Seed complete. trades: %d/%d | trade_services: %d/%d",
        inserted_trades, total_trades,
        inserted_services, total_services,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
