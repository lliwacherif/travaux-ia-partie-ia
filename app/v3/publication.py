"""Pure publication gates and snapshot helpers for the V3.2 catalog.

V3.2 — publishable packs are CORE-only and must reference a published shared
SETUP/FINISH profile. Transitional full-geometry packs (SETUP+CORE+FINISH
embedded) remain acceptable until the importer splits them.

The gate functions operate on immutable snapshots so they can be tested without
a database or embedding provider.  SQL helpers accept an existing synchronous
connection; they never create or discover a V2 connection.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import Connection, bindparam, text

from app.v3.ssot import (
    EMBEDDING_DIMENSIONS,
    FORBIDDEN_LABELS,
    Flow,
    LinearMeasurementMode,
    expected_geometry,
)

PriceKey = tuple[uuid.UUID, int]
VatKey = tuple[str, int]
FallbackKey = tuple[str, str | None]

# Formula IDs are deliberately closed.  Importers may only attach one of these
# formulas and its corresponding SSOT mode; unknown V2 ML metadata remains
# DRAFT and is reported instead of being guessed.
REGISTERED_LINEAR_FORMULAS: Mapping[str, frozenset[str]] = {
    "V2_EXPLICIT_LENGTH_V1": frozenset({LinearMeasurementMode.EXPLICIT.value}),
    "V2_SURFACE_COEFFICIENT_V1": frozenset(
        {LinearMeasurementMode.SURFACE_TO_LINEAR.value}
    ),
}


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    price_id: uuid.UUID
    version: int
    status: str


@dataclass(frozen=True, slots=True)
class VatSnapshot:
    vat_rule_id: str
    version: int
    status: str


@dataclass(frozen=True, slots=True)
class LineSnapshot:
    line_id: uuid.UUID
    phase: str
    slot_index: int
    designation: str
    unit: str
    quantity_rule: str
    default_quantity: Decimal
    price_id: uuid.UUID
    price_version: int
    vat_rule_id: str
    vat_rule_version: int
    embedding: Sequence[float] | None
    embedding_model: str | None
    linear_measurement_mode: str | None = None
    linear_formula_id: str | None = None


@dataclass(frozen=True, slots=True)
class PackSnapshot:
    pack_id: uuid.UUID
    pack_code: str
    flow: str
    trade_code: str
    service_code: str | None
    title: str
    status: str
    embedding: Sequence[float] | None
    embedding_model: str | None
    exclusion_tags: tuple[str, ...]
    required_coverage: tuple[str, ...]
    fallback_rank: int | None
    lines: tuple[LineSnapshot, ...]
    # Correctifs ciblés à intégrer dans la V3.2 §2.
    pack_match_signature: str | None = None
    version: int = 1


@dataclass(frozen=True, slots=True)
class GateIssue:
    code: str
    message: str
    pack_code: str
    line_id: str | None = None


def _issue(
    issues: list[GateIssue],
    code: str,
    message: str,
    pack: PackSnapshot,
    line: LineSnapshot | None = None,
) -> None:
    issues.append(
        GateIssue(
            code=code,
            message=message,
            pack_code=pack.pack_code,
            line_id=str(line.line_id) if line is not None else None,
        )
    )


def _normalized_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(ch for ch in decomposed if not unicodedata.combining(ch)).split()
    )


def _contains_forbidden_label(value: str) -> str | None:
    normalized = _normalized_label(value)
    for label in FORBIDDEN_LABELS:
        candidate = _normalized_label(label)
        if re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", normalized):
            return label
    return None


def _valid_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _as_embedding(value: object) -> list[float] | None:
    """Normalize pgvector payloads from ORM objects or raw SQL strings."""

    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not (text.startswith("[") and text.endswith("]")):
            return None
        body = text[1:-1].strip()
        if not body:
            return []
        return [float(part) for part in body.split(",")]
    if isinstance(value, (list, tuple)):
        return [float(part) for part in value]
    try:
        return [float(part) for part in value]  # type: ignore[arg-type]
    except TypeError:
        return None


def _embedding_present(value: Sequence[float] | None) -> bool:
    embedding = _as_embedding(value)
    return embedding is not None and len(embedding) == EMBEDDING_DIMENSIONS


def validate_publication(
    pack: PackSnapshot,
    *,
    prices: Mapping[PriceKey, PriceSnapshot],
    vat_rules: Mapping[VatKey, VatSnapshot],
    fallback_coverage: set[FallbackKey],
    approved_by: str | uuid.UUID | None,
    regression_passed: bool,
    registered_formulas: Mapping[str, frozenset[str]] = REGISTERED_LINEAR_FORMULAS,
) -> tuple[GateIssue, ...]:
    """Return every publication failure for one pack.

    No state is changed.  Both DRAFT and already-PUBLISHED snapshots can be
    checked, which lets the publisher validate in a transaction before commit.
    """

    issues: list[GateIssue] = []
    try:
        geometry = expected_geometry(Flow(pack.flow))
    except (KeyError, ValueError):
        _issue(issues, "FLOW_UNSUPPORTED", f"Unsupported flow {pack.flow!r}", pack)
        return tuple(issues)

    if not _valid_uuid(pack.pack_id):
        _issue(issues, "PACK_ID_INVALID", "pack_id must be a UUID", pack)
    if not pack.pack_code.strip():
        _issue(issues, "PACK_CODE_MISSING", "pack_code is required", pack)
    if not pack.trade_code.strip():
        _issue(issues, "TRADE_ID_MISSING", "trade_code is required", pack)
    # Correctifs ciblés à intégrer dans la V3.2 §2 — signature obligatoire.
    if not (pack.pack_match_signature or "").strip():
        _issue(
            issues,
            "PACK_MATCH_SIGNATURE_REQUIRED",
            "pack_match_signature is required before PUBLISHED",
            pack,
        )

    phase_counts = {
        phase: sum(1 for line in pack.lines if line.phase == phase)
        for phase in ("SETUP", "CORE", "FINISH")
    }
    expected_counts = {
        "SETUP": geometry.setup,
        "CORE": geometry.core_per_trade,
        "FINISH": geometry.finish,
    }
    if phase_counts != expected_counts:
        _issue(
            issues,
            "GEOMETRY_INVALID",
            f"Expected {expected_counts}, got {phase_counts}",
            pack,
        )

    line_ids = [line.line_id for line in pack.lines]
    if any(not _valid_uuid(line_id) for line_id in line_ids):
        _issue(issues, "LINE_ID_INVALID", "Every line_id must be a UUID", pack)
    if len({str(line_id) for line_id in line_ids}) != len(line_ids):
        _issue(issues, "LINE_ID_DUPLICATE", "line_id values must be unique", pack)

    for phase, expected_count in expected_counts.items():
        slots = sorted(
            line.slot_index for line in pack.lines if line.phase == phase
        )
        if slots != list(range(expected_count)):
            _issue(
                issues,
                "SLOTS_INVALID",
                f"{phase} slots must be contiguous from zero; got {slots}",
                pack,
            )

    forbidden = _contains_forbidden_label(pack.title)
    if forbidden is not None:
        _issue(
            issues,
            "FORBIDDEN_LABEL",
            f"Pack title contains forbidden label {forbidden!r}",
            pack,
        )
    if not _embedding_present(pack.embedding) or not pack.embedding_model:
        _issue(
            issues,
            "PACK_EMBEDDING_MISSING",
            f"Pack embedding must contain {EMBEDDING_DIMENSIONS} dimensions",
            pack,
        )

    fallback_key = (pack.trade_code, pack.service_code)
    if fallback_key not in fallback_coverage:
        _issue(
            issues,
            "FALLBACK_MISSING",
            f"No fallback registered for trade/service {fallback_key!r}",
            pack,
        )

    if approved_by is None or not _valid_uuid(approved_by):
        _issue(
            issues,
            "APPROVAL_MISSING",
            "approved_by must be an explicit UUID",
            pack,
        )
    if regression_passed is not True:
        _issue(
            issues,
            "REGRESSION_REQUIRED",
            "Full regression must pass before publication",
            pack,
        )

    for line in pack.lines:
        forbidden = _contains_forbidden_label(line.designation)
        if forbidden is not None:
            _issue(
                issues,
                "FORBIDDEN_LABEL",
                f"Designation contains forbidden label {forbidden!r}",
                pack,
                line,
            )
        if not line.quantity_rule.strip():
            _issue(
                issues,
                "QUANTITY_RULE_MISSING",
                "quantity_rule is required",
                pack,
                line,
            )
        if line.default_quantity is None or line.default_quantity <= 0:
            _issue(
                issues,
                "DEFAULT_QUANTITY_INVALID",
                "default_quantity must be positive",
                pack,
                line,
            )

        price = prices.get((line.price_id, line.price_version))
        if price is None:
            _issue(
                issues,
                "PRICE_MISSING",
                "Referenced price version does not exist",
                pack,
                line,
            )
        elif price.status != "PUBLISHED":
            _issue(
                issues,
                "PRICE_NOT_PUBLISHED",
                "Referenced price version is not PUBLISHED",
                pack,
                line,
            )

        vat_rule = vat_rules.get((line.vat_rule_id, line.vat_rule_version))
        if vat_rule is None:
            _issue(
                issues,
                "VAT_RULE_MISSING",
                "Referenced VAT rule does not exist",
                pack,
                line,
            )
        elif vat_rule.status != "PUBLISHED":
            _issue(
                issues,
                "VAT_RULE_NOT_PUBLISHED",
                "Referenced VAT rule is not PUBLISHED",
                pack,
                line,
            )

        if not _embedding_present(line.embedding) or not line.embedding_model:
            _issue(
                issues,
                "LINE_EMBEDDING_MISSING",
                f"Line embedding must contain {EMBEDDING_DIMENSIONS} dimensions",
                pack,
                line,
            )

        if line.unit == "ML":
            formula_modes = registered_formulas.get(line.linear_formula_id or "")
            if (
                formula_modes is None
                or line.linear_measurement_mode not in formula_modes
            ):
                _issue(
                    issues,
                    "ML_FORMULA_UNREGISTERED",
                    "ML lines require a registered formula/mode pair",
                    pack,
                    line,
                )
        elif (
            line.linear_measurement_mode is not None
            or line.linear_formula_id is not None
        ):
            _issue(
                issues,
                "NON_ML_FORMULA_PRESENT",
                "Only ML lines may define linear formula metadata",
                pack,
                line,
            )

    return tuple(issues)


def issues_as_dicts(issues: Iterable[GateIssue]) -> list[dict[str, Any]]:
    return [asdict(issue) for issue in issues]


_SNAPSHOT_TABLES: Mapping[str, tuple[str, ...]] = {
    "trade_catalog": ("trade_code",),
    "quote_packs": ("pack_id",),
    "quote_pack_lines": ("line_id",),
    "price_versions": ("price_id", "version"),
    "vat_rules": ("vat_rule_id", "version"),
}


def capture_status_snapshot(connection: Connection) -> dict[str, Any]:
    """Capture publication/archive state for rollback before a mutation."""

    tables: dict[str, list[dict[str, Any]]] = {}
    for table_name, key_columns in _SNAPSHOT_TABLES.items():
        columns = ", ".join((*key_columns, "status", "published_at"))
        rows = connection.execute(
            text(f"SELECT {columns} FROM {table_name}")  # noqa: S608
        )
        tables[table_name] = [
            {
                key: str(row._mapping[key])
                if isinstance(row._mapping[key], uuid.UUID)
                else row._mapping[key]
                for key in (*key_columns, "status", "published_at")
            }
            for row in rows
        ]
    return {"schema": "v3-publication-status-snapshot-v1", "tables": tables}


def restore_status_snapshot(
    connection: Connection, snapshot: Mapping[str, Any]
) -> int:
    """Restore only status/published_at fields from a validated snapshot."""

    if snapshot.get("schema") != "v3-publication-status-snapshot-v1":
        raise ValueError("Unsupported publication snapshot schema")
    raw_tables = snapshot.get("tables")
    if not isinstance(raw_tables, Mapping):
        raise ValueError("Snapshot tables are missing")

    restored = 0
    for table_name, key_columns in _SNAPSHOT_TABLES.items():
        rows = raw_tables.get(table_name, [])
        if not isinstance(rows, list):
            raise ValueError(f"Snapshot table {table_name!r} is invalid")
        predicates = " AND ".join(f"{column} = :{column}" for column in key_columns)
        statement = text(
            f"UPDATE {table_name} "  # noqa: S608
            f"SET status = :status, published_at = :published_at "
            f"WHERE {predicates}"
        )
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(f"Invalid row in snapshot table {table_name!r}")
            result = connection.execute(statement, dict(row))
            restored += result.rowcount or 0
    return restored


def load_pack_snapshots(
    connection: Connection,
    *,
    library_version: str,
    pack_codes: Sequence[str] = (),
) -> tuple[PackSnapshot, ...]:
    """Load candidate snapshots from V3 only."""

    statement = text(
        """
        SELECT pack_id, pack_code, flow, trade_code, service_code, title,
               status, embedding, embedding_model, exclusion_tags,
               required_coverage, fallback_rank, pack_match_signature, version
        FROM quote_packs
        WHERE library_version = :library_version
          AND (:all_codes OR pack_code IN :pack_codes)
        ORDER BY pack_code, version
        """
    ).bindparams(bindparam("pack_codes", expanding=True))
    parameters = {
        "library_version": library_version,
        "all_codes": not pack_codes,
        "pack_codes": list(pack_codes) or ["__NO_PACK_CODE__"],
    }
    packs: list[PackSnapshot] = []
    for pack_row in connection.execute(statement, parameters):
        values = pack_row._mapping
        line_rows = connection.execute(
            text(
                """
                SELECT line_id, phase, slot_index, designation, unit,
                       quantity_rule, default_quantity, price_id, price_version,
                       vat_rule_id, vat_rule_version, embedding, embedding_model,
                       linear_measurement_mode, linear_formula_id
                FROM quote_pack_lines
                WHERE pack_id = :pack_id
                ORDER BY
                    CASE phase
                        WHEN 'SETUP' THEN 1
                        WHEN 'CORE' THEN 2
                        WHEN 'FINISH' THEN 3
                    END,
                    slot_index
                """
            ),
            {"pack_id": values["pack_id"]},
        )
        lines = tuple(
            LineSnapshot(
                line_id=row._mapping["line_id"],
                phase=row._mapping["phase"],
                slot_index=row._mapping["slot_index"],
                designation=row._mapping["designation"],
                unit=row._mapping["unit"],
                quantity_rule=row._mapping["quantity_rule"],
                default_quantity=row._mapping["default_quantity"],
                price_id=row._mapping["price_id"],
                price_version=row._mapping["price_version"],
                vat_rule_id=row._mapping["vat_rule_id"],
                vat_rule_version=row._mapping["vat_rule_version"],
                embedding=_as_embedding(row._mapping["embedding"]),
                embedding_model=row._mapping["embedding_model"],
                linear_measurement_mode=row._mapping[
                    "linear_measurement_mode"
                ],
                linear_formula_id=row._mapping["linear_formula_id"],
            )
            for row in line_rows
        )
        packs.append(
            PackSnapshot(
                pack_id=values["pack_id"],
                pack_code=values["pack_code"],
                flow=values["flow"],
                trade_code=values["trade_code"],
                service_code=values["service_code"],
                title=values["title"],
                status=values["status"],
                embedding=_as_embedding(values["embedding"]),
                embedding_model=values["embedding_model"],
                exclusion_tags=tuple(values["exclusion_tags"] or ()),
                required_coverage=tuple(values["required_coverage"] or ()),
                fallback_rank=values["fallback_rank"],
                lines=lines,
                pack_match_signature=values.get("pack_match_signature"),
                version=int(values.get("version") or 1),
            )
        )
    return tuple(packs)


def load_dependencies(
    connection: Connection, packs: Sequence[PackSnapshot]
) -> tuple[dict[PriceKey, PriceSnapshot], dict[VatKey, VatSnapshot]]:
    price_keys = {
        (line.price_id, line.price_version)
        for pack in packs
        for line in pack.lines
    }
    vat_keys = {
        (line.vat_rule_id, line.vat_rule_version)
        for pack in packs
        for line in pack.lines
    }
    prices: dict[PriceKey, PriceSnapshot] = {}
    vats: dict[VatKey, VatSnapshot] = {}
    for price_id, version in price_keys:
        row = connection.execute(
            text(
                """
                SELECT price_id, version, status
                FROM price_versions
                WHERE price_id = :price_id AND version = :version
                """
            ),
            {"price_id": price_id, "version": version},
        ).one_or_none()
        if row is not None:
            snapshot = PriceSnapshot(**dict(row._mapping))
            prices[(snapshot.price_id, snapshot.version)] = snapshot
    for vat_rule_id, version in vat_keys:
        row = connection.execute(
            text(
                """
                SELECT vat_rule_id, version, status
                FROM vat_rules
                WHERE vat_rule_id = :vat_rule_id AND version = :version
                """
            ),
            {"vat_rule_id": vat_rule_id, "version": version},
        ).one_or_none()
        if row is not None:
            snapshot = VatSnapshot(**dict(row._mapping))
            vats[(snapshot.vat_rule_id, snapshot.version)] = snapshot
    return prices, vats


def fallback_coverage_for(
    connection: Connection, selected_packs: Sequence[PackSnapshot]
) -> set[FallbackKey]:
    """Return fallbacks already published or included in this reviewed batch."""

    selected_ids = [pack.pack_id for pack in selected_packs]
    statement = text(
        """
        SELECT DISTINCT trade_code, service_code
        FROM quote_packs
        WHERE fallback_rank IS NOT NULL
          AND (status = 'PUBLISHED' OR pack_id IN :selected_ids)
        """
    ).bindparams(bindparam("selected_ids", expanding=True))
    rows = connection.execute(
        statement, {"selected_ids": selected_ids or [uuid.uuid4()]}
    )
    return {(row._mapping["trade_code"], row._mapping["service_code"]) for row in rows}


__all__ = [
    "GateIssue",
    "LineSnapshot",
    "PackSnapshot",
    "PriceSnapshot",
    "REGISTERED_LINEAR_FORMULAS",
    "VatSnapshot",
    "capture_status_snapshot",
    "fallback_coverage_for",
    "issues_as_dicts",
    "load_dependencies",
    "load_pack_snapshots",
    "restore_status_snapshot",
    "validate_publication",
]
