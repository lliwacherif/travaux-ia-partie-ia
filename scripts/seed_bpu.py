"""Seed the ``bpu_items`` table from the project's JSON price libraries.

Usage::

    # Create the table + seed in one go:
    python -m scripts.seed_bpu --create-table

    # Seed only (table already exists):
    python -m scripts.seed_bpu

    # Custom paths:
    python -m scripts.seed_bpu --biblio path/to/biblio.json --bpu path/to/bpu.json

Sources
-------
* ``bibliotheque-travaux-ia-v1.json`` — 3 000 lines, 30+ trades, real prices
* ``bpu-master-v2.json`` — 325 lines (3 trades) + 5 fallback items

The script is **idempotent**: ``ON CONFLICT (id) DO UPDATE`` refreshes the
price and metadata if the row already exists so re-running after a price
adjustment in the JSON is safe.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402

logger = logging.getLogger("seed_bpu")

DEFAULT_BIBLIO = PROJECT_ROOT / "bibliotheque-travaux-ia-v1.json"
DEFAULT_BPU = PROJECT_ROOT / "bpu-master-v2.json"
CREATE_SQL = PROJECT_ROOT / "scripts" / "create_bpu_items.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slugify(text: str) -> str:
    """Create a URL-safe slug from a French designation."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = "".join(c for c in nfkd if not unicodedata.combining(c))
    ascii_text = ascii_text.lower().strip()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return ascii_text


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def _parse_biblio(path: Path) -> list[dict[str, Any]]:
    """Parse ``bibliotheque-travaux-ia-v1.json`` into insertable dicts."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    lines = data.get("lines", [])
    rows: list[dict[str, Any]] = []

    for item in lines:
        price = item.get("prix_unitaire_ht", 0)
        if price is None:
            price = 0

        designation = item.get("designation", "")
        slug = item.get("slug") or _slugify(designation)

        rows.append({
            "id": item["id"],
            "code": _clean(item.get("code")),
            "corps_metier": item.get("corps_metier", "Inconnu"),
            "designation": designation,
            "description": _clean(item.get("description")),
            "prix_unitaire_ht": float(price),
            "unite": item.get("unite", "u"),
            "taux_tva_defaut": float(item.get("taux_tva_defaut", 10)),
            "type": _clean(item.get("type")),
            "categorie": _clean(item.get("categorie")),
            "sous_categorie": _clean(item.get("sous_categorie")),
            "source": "bibliotheque",
            "slug": slug,
            "is_system": bool(item.get("is_system", True)),
        })

    return rows


def _parse_bpu_master(path: Path) -> list[dict[str, Any]]:
    """Parse ``bpu-master-v2.json`` into insertable dicts."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    rows: list[dict[str, Any]] = []

    # Fallback items
    for item in data.get("fallback_items", []):
        designation = item.get("label", "")
        rows.append({
            "id": item["id"],
            "code": item["id"],
            "corps_metier": "Générique",
            "designation": designation,
            "description": None,
            "prix_unitaire_ht": float(item.get("price", 0)),
            "unite": item.get("unit", "u"),
            "taux_tva_defaut": float(item.get("tva_default", 10)),
            "type": item.get("step"),
            "categorie": "Fallback",
            "sous_categorie": None,
            "source": "bpu_master",
            "slug": _slugify(designation),
            "is_system": True,
        })

    # Trade items
    for trade in data.get("trades", []):
        trade_name = trade.get("trade", "Inconnu")
        for variant in trade.get("variants", []):
            for item in variant.get("items", []):
                designation = item.get("label", "")
                rows.append({
                    "id": item["id"],
                    "code": item["id"],
                    "corps_metier": trade_name,
                    "designation": designation,
                    "description": None,
                    "prix_unitaire_ht": float(item.get("price", 0)),
                    "unite": item.get("unit", "u"),
                    "taux_tva_defaut": float(item.get("tva_default", 10)),
                    "type": item.get("step"),
                    "categorie": variant.get("variant"),
                    "sous_categorie": variant.get("variant_code"),
                    "source": "bpu_master",
                    "slug": _slugify(designation),
                    "is_system": True,
                })

    return rows


# ---------------------------------------------------------------------------
# Upsert SQL
# ---------------------------------------------------------------------------
_UPSERT_SQL = text("""
    INSERT INTO bpu_items
        (id, code, corps_metier, designation, description,
         prix_unitaire_ht, unite, taux_tva_defaut, type,
         categorie, sous_categorie, source, slug, is_system)
    VALUES
        (:id, :code, :corps_metier, :designation, :description,
         :prix_unitaire_ht, :unite, :taux_tva_defaut, :type,
         :categorie, :sous_categorie, :source, :slug, :is_system)
    ON CONFLICT (id) DO UPDATE SET
        prix_unitaire_ht = EXCLUDED.prix_unitaire_ht,
        designation = EXCLUDED.designation,
        description = EXCLUDED.description,
        unite = EXCLUDED.unite,
        taux_tva_defaut = EXCLUDED.taux_tva_defaut,
        type = EXCLUDED.type,
        categorie = EXCLUDED.categorie,
        sous_categorie = EXCLUDED.sous_categorie,
        source = EXCLUDED.source,
        slug = EXCLUDED.slug,
        updated_at = NOW();
""")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def _seed(engine: Engine, rows: list[dict[str, Any]], label: str) -> int:
    if not rows:
        logger.warning("%s: no rows to insert.", label)
        return 0

    # Insert in batches of 500 to avoid overly large statements
    batch_size = 500
    total_inserted = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        with engine.begin() as conn:
            result = conn.execute(_UPSERT_SQL, batch)
            total_inserted += result.rowcount if result.rowcount else 0

    logger.info("%s: %d rows processed, %d upserted.", label, len(rows), total_inserted)
    return total_inserted


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="Seed bpu_items from JSON files.")
    parser.add_argument(
        "--biblio", type=Path, default=DEFAULT_BIBLIO,
        help=f"Path to bibliotheque JSON (default: {DEFAULT_BIBLIO.name}).",
    )
    parser.add_argument(
        "--bpu", type=Path, default=DEFAULT_BPU,
        help=f"Path to BPU master JSON (default: {DEFAULT_BPU.name}).",
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
        if not CREATE_SQL.exists():
            logger.error("SQL file not found: %s", CREATE_SQL)
            return 1
        sql_text = CREATE_SQL.read_text(encoding="utf-8")
        with engine.begin() as conn:
            # Execute each statement separately (multi-statement)
            for stmt in sql_text.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
        logger.info("Table bpu_items created (or already exists).")

    # Parse sources
    biblio_rows: list[dict[str, Any]] = []
    bpu_rows: list[dict[str, Any]] = []

    if args.biblio.exists():
        biblio_rows = _parse_biblio(args.biblio)
        logger.info("Parsed %d lines from %s", len(biblio_rows), args.biblio.name)
    else:
        logger.warning("Biblio file not found: %s", args.biblio)

    if args.bpu.exists():
        bpu_rows = _parse_bpu_master(args.bpu)
        logger.info("Parsed %d lines from %s", len(bpu_rows), args.bpu.name)
    else:
        logger.warning("BPU master file not found: %s", args.bpu)

    # Seed — biblio first (larger), then BPU master (may overlap on IDs)
    total_biblio = _seed(engine, biblio_rows, "bibliotheque")
    total_bpu = _seed(engine, bpu_rows, "bpu_master")

    logger.info(
        "Seed complete. bibliotheque: %d | bpu_master: %d | total: %d",
        total_biblio, total_bpu, total_biblio + total_bpu,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
