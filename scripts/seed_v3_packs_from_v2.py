"""Seed V3 quote_packs from V2 20-line packs (prices = bpu / pack library).

Usage::

    python -m scripts.seed_v3_packs_from_v2 --publish --approved-by <UUID> --regression-passed
    python -m scripts.seed_v3_packs_from_v2 --publish --approved-by <UUID> --regression-passed --only-prefix TER-
    python -m scripts.seed_v3_packs_from_v2 --publish --approved-by <UUID> --regression-passed --limit 50

Imports every active Travaux pack with exact 20-line geometry into the isolated
V3 library as PUBLISHED packs (trade_catalog, prices, VAT, embeddings, signatures).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAI
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.v3.curated_library import (  # noqa: E402
    CURATED_PACKS,
    CuratedPackSpec,
    pack_line,
)
from app.v3.models import QuotePack  # noqa: E402
from scripts.import_v3_library import (  # noqa: E402
    LIBRARY_VERSION,
    _ensure_library_snapshot,
    _import_curated_pack,
    _upsert_vat_rules,
)
from app.v3.trace import stable_hash  # noqa: E402

logger = logging.getLogger("seed_v3_packs")


def _load_v2_20_line_specs(
    connection: Any,
    *,
    only_prefix: str | None,
    limit: int | None,
) -> list[CuratedPackSpec]:
    rows = connection.execute(
        text(
            """
            SELECT code_pack, nom_pack, corps_metier, pack_json
            FROM packs_travaux
            WHERE COALESCE(is_active, true) IS TRUE
            ORDER BY corps_metier, code_pack
            """
        )
    ).mappings().all()

    specs: list[CuratedPackSpec] = []
    for row in rows:
        code = str(row["code_pack"] or "")
        if only_prefix and not code.startswith(only_prefix):
            continue
        payload = row["pack_json"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, list) or len(payload) != 20:
            continue
        line_codes: list[str] = []
        for item in payload:
            line_code = str(item.get("code") or "").strip()
            if not line_code:
                break
            line_codes.append(line_code)
        if len(line_codes) != 20:
            continue
        specs.append(
            CuratedPackSpec(
                pack_code=f"V3-{code}",
                title=str(row["nom_pack"] or code),
                trade_label=str(row["corps_metier"] or "Inconnu"),
                setup=tuple(pack_line(code, c) for c in line_codes[0:3]),
                core=tuple(pack_line(code, c) for c in line_codes[3:17]),
                finish=tuple(pack_line(code, c) for c in line_codes[17:20]),
                exclusion_tags=(),
                required_coverage=tuple(
                    token
                    for token in (
                        str(row["nom_pack"] or "").lower(),
                        str(row["corps_metier"] or "").lower(),
                    )
                    if token
                ),
            )
        )
        if limit is not None and len(specs) >= limit:
            break
    return specs


def seed(
    *,
    publish: bool,
    approved_by: uuid.UUID | None,
    regression_passed: bool,
    only_prefix: str | None,
    limit: int | None,
    include_curated: bool,
) -> dict[str, Any]:
    if publish and (approved_by is None or not regression_passed):
        raise ValueError("Publishing requires --approved-by and --regression-passed")
    api_key = settings.V3_OPENAI_API_KEY or settings.OPENAI_API_KEY
    if not api_key:
        raise ValueError("OpenAI key is required to create embeddings")

    v2_engine = create_engine(str(settings.SYNC_DATABASE_URL), future=True)
    v3_engine = create_engine(str(settings.V3_SYNC_DATABASE_URL), future=True)
    embeddings = OpenAI(api_key=api_key)
    now = datetime.now(timezone.utc)
    imported: list[str] = []
    skipped: list[dict[str, str]] = []

    try:
        with v2_engine.connect() as v2_connection, Session(v3_engine) as session:
            snapshot = _ensure_library_snapshot(session, publish=publish, now=now)
            _upsert_vat_rules(session, now, publish=publish)

            specs: list[CuratedPackSpec] = []
            if include_curated:
                specs.extend(CURATED_PACKS)
            specs.extend(
                _load_v2_20_line_specs(
                    v2_connection, only_prefix=only_prefix, limit=limit
                )
            )

            # Deduplicate by pack_code (curated wins).
            seen: set[str] = set()
            unique_specs: list[CuratedPackSpec] = []
            for spec in specs:
                if spec.pack_code in seen:
                    continue
                seen.add(spec.pack_code)
                unique_specs.append(spec)

            logger.info("Importing %d pack specs into V3…", len(unique_specs))
            for index, spec in enumerate(unique_specs, start=1):
                try:
                    with session.begin_nested():
                        pack_id = _import_curated_pack(
                            v2_connection=v2_connection,
                            session=session,
                            embeddings=embeddings,
                            spec=spec,
                            publish=publish,
                            approved_by=approved_by,
                            regression_passed=regression_passed,
                            now=now,
                        )
                        pack = session.get(QuotePack, pack_id)
                        if pack is not None:
                            pack.snapshot_id = snapshot.snapshot_id
                    imported.append(spec.pack_code)
                    if index % 5 == 0 or index == len(unique_specs):
                        session.commit()
                        snapshot = session.merge(snapshot)
                        logger.info(
                            "Progress %d/%d (last=%s)",
                            index,
                            len(unique_specs),
                            spec.pack_code,
                        )
                except Exception as exc:  # noqa: BLE001
                    skipped.append(
                        {"pack_code": spec.pack_code, "error": str(exc)[:300]}
                    )
                    logger.warning("Skip %s: %s", spec.pack_code, exc)
                    session.rollback()
                    snapshot = _ensure_library_snapshot(
                        session, publish=publish, now=now
                    )
                    _upsert_vat_rules(session, now, publish=publish)

            snapshot = _ensure_library_snapshot(session, publish=publish, now=now)
            snapshot.content_hash = stable_hash(
                {
                    "library_version": LIBRARY_VERSION,
                    "packs": imported,
                }
            )
            session.commit()
    finally:
        v2_engine.dispose()
        v3_engine.dispose()

    return {
        "library_version": LIBRARY_VERSION,
        "imported": len(imported),
        "skipped": len(skipped),
        "pack_codes": imported,
        "skip_details": skipped[:50],
        "status": "PUBLISHED" if publish else "DRAFT",
    }


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--approved-by", type=uuid.UUID)
    parser.add_argument("--regression-passed", action="store_true")
    parser.add_argument("--only-prefix", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--skip-curated",
        action="store_true",
        help="Do not re-import CURATED_PACKS (only V2 20-line packs).",
    )
    args = parser.parse_args(argv)
    result = seed(
        publish=args.publish,
        approved_by=args.approved_by,
        regression_passed=args.regression_passed,
        only_prefix=args.only_prefix,
        limit=args.limit,
        include_curated=not args.skip_curated,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "pack_codes"}, ensure_ascii=False, indent=2))
    print(f"imported_codes={len(result['pack_codes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
