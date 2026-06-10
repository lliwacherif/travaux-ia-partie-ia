"""Seed the ``packs_travaux`` table from the project CSV export.

Usage::

    # Create the table + seed in one go:
    python -m scripts.seed_packs --create-table

    # Seed only (table already exists):
    python -m scripts.seed_packs

    # Custom path:
    python -m scripts.seed_packs --csv path/to/packs_travaux_rows.csv

The script is **idempotent**: ``ON CONFLICT (id) DO UPDATE`` refreshes the
data if the row already exists so re-running after edits in the CSV is safe.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402

logger = logging.getLogger("seed_packs")

DEFAULT_CSV = PROJECT_ROOT / "packs_travaux_rows.csv"

# ---------------------------------------------------------------------------
# CREATE TABLE SQL
# ---------------------------------------------------------------------------
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS packs_travaux (
    id              VARCHAR(64) PRIMARY KEY,
    corps_metier    VARCHAR(255) NOT NULL,
    sous_metier_depannage VARCHAR(255),
    code_pack       VARCHAR(128) NOT NULL UNIQUE,
    nom_pack        VARCHAR(512) NOT NULL,
    description     TEXT,
    pack_json       JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    surface_ref     DOUBLE PRECISION,
    unite_ref       VARCHAR(64),
    pack_category   VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(64)
)
"""

_CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS ix_packs_travaux_corps_metier ON packs_travaux (corps_metier)",
    "CREATE INDEX IF NOT EXISTS ix_packs_travaux_code_pack ON packs_travaux (code_pack)",
]

# ---------------------------------------------------------------------------
# UPSERT SQL
# ---------------------------------------------------------------------------
_UPSERT_SQL = text("""
    INSERT INTO packs_travaux
        (id, corps_metier, sous_metier_depannage, code_pack, nom_pack,
         description, pack_json, is_active, surface_ref, unite_ref,
         pack_category, created_at, created_by)
    VALUES
        (:id, :corps_metier, :sous_metier_depannage, :code_pack, :nom_pack,
         :description, :pack_json, :is_active, :surface_ref, :unite_ref,
         :pack_category, :created_at, :created_by)
    ON CONFLICT (id) DO UPDATE SET
        corps_metier = EXCLUDED.corps_metier,
        sous_metier_depannage = EXCLUDED.sous_metier_depannage,
        code_pack = EXCLUDED.code_pack,
        nom_pack = EXCLUDED.nom_pack,
        description = EXCLUDED.description,
        pack_json = EXCLUDED.pack_json,
        is_active = EXCLUDED.is_active,
        surface_ref = EXCLUDED.surface_ref,
        unite_ref = EXCLUDED.unite_ref,
        pack_category = EXCLUDED.pack_category;
""")


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------
def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_csv(path: Path) -> list[dict[str, Any]]:
    """Parse ``packs_travaux_rows.csv`` into insertable dicts."""
    rows: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            try:
                pack_json_str = row.get("pack_json", "[]")
                pack_json = json.loads(pack_json_str)
            except json.JSONDecodeError:
                logger.warning("Row %d: invalid JSON in pack_json, skipping.", row_num)
                continue

            surface_ref = _clean(row.get("surface_ref"))
            try:
                surface_ref_float = float(surface_ref) if surface_ref else None
            except ValueError:
                surface_ref_float = None

            is_active = row.get("is_active", "true").strip().lower() == "true"

            rows.append({
                "id": row["id"],
                "corps_metier": row.get("corps_metier", "Inconnu"),
                "sous_metier_depannage": _clean(row.get("sous_metier_depannage")),
                "code_pack": row.get("code_pack", ""),
                "nom_pack": row.get("nom_pack", ""),
                "description": _clean(row.get("description")),
                "pack_json": json.dumps(pack_json, ensure_ascii=False),
                "is_active": is_active,
                "surface_ref": surface_ref_float,
                "unite_ref": _clean(row.get("unite_ref")),
                "pack_category": _clean(row.get("pack_category")),
                "created_at": row.get("created_at", "2025-01-01 00:00:00+00"),
                "created_by": _clean(row.get("created_by")),
            })

    return rows


# ---------------------------------------------------------------------------
# Seed logic
# ---------------------------------------------------------------------------
def _seed(engine: Engine, rows: list[dict[str, Any]]) -> int:
    if not rows:
        logger.warning("No rows to insert.")
        return 0

    batch_size = 200
    total_inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i: i + batch_size]
        with engine.begin() as conn:
            result = conn.execute(_UPSERT_SQL, batch)
            total_inserted += result.rowcount if result.rowcount else 0

    logger.info("packs_travaux: %d rows processed, %d upserted.", len(rows), total_inserted)
    return total_inserted


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="Seed packs_travaux from CSV.")
    parser.add_argument(
        "--csv", type=Path, default=DEFAULT_CSV,
        help=f"Path to packs CSV (default: {DEFAULT_CSV.name}).",
    )
    parser.add_argument(
        "--create-table", action="store_true",
        help="Run the CREATE TABLE SQL before seeding.",
    )
    args = parser.parse_args(argv)

    dsn = str(settings.SYNC_DATABASE_URL)
    logger.info("Connecting to %s", dsn.split("@", 1)[-1])
    engine = create_engine(dsn, future=True)

    # Optionally create the table
    if args.create_table:
        with engine.begin() as conn:
            conn.execute(text(_CREATE_TABLE_SQL))
            for idx_sql in _CREATE_INDEX_SQL:
                conn.execute(text(idx_sql))
        logger.info("Table packs_travaux created (or already exists).")

    # Parse CSV
    if not args.csv.exists():
        logger.error("CSV file not found: %s", args.csv)
        return 1

    rows = _parse_csv(args.csv)
    logger.info("Parsed %d packs from %s", len(rows), args.csv.name)

    # Seed
    total = _seed(engine, rows)
    logger.info("Seed complete. Total packs upserted: %d", total)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
