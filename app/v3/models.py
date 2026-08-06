"""Normalized SQLAlchemy models for the isolated V3.2 database.

V3.2 — library_snapshots, shared_line_profiles / shared_profile_lines,
snapshot-scoped catalog entities, and pack→shared-profile references.
V2 / v1 devis tables are never imported or mutated here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.v3.db import Base
from app.v3.ssot import (
    EMBEDDING_DIMENSIONS,
    AUTHORIZED_LIBRARY_UNITS,
    Flow,
    LinearMeasurementMode,
    Phase,
    PipelineStage,
)

PUBLICATION_STATUSES = ("DRAFT", "PUBLISHED", "ARCHIVED")
FLOW_VALUES = tuple(flow.value for flow in Flow)
PHASE_VALUES = tuple(phase.value for phase in Phase)
UNIT_VALUES = AUTHORIZED_LIBRARY_UNITS


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


PUBLICATION_STATUS_SQL = _sql_values(PUBLICATION_STATUSES)
FLOW_SQL = _sql_values(FLOW_VALUES)
PHASE_SQL = _sql_values(PHASE_VALUES)
UNIT_SQL = _sql_values(UNIT_VALUES)
LINEAR_MODE_SQL = _sql_values(tuple(mode.value for mode in LinearMeasurementMode))
STAGE_SQL = _sql_values(tuple(stage.value for stage in PipelineStage))
# V3.2 — shared profiles only hold SETUP / FINISH.
SHARED_PHASE_SQL = _sql_values(("SETUP", "FINISH"))


class LibrarySnapshot(Base):
    """V3.2 — immutable published library snapshot used by stage 0B."""

    __tablename__ = "library_snapshots"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="library_snapshots_status",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR "
            "(validated_at IS NOT NULL AND published_at IS NOT NULL)",
            name="library_snapshots_published_validated",
        ),
        Index("ix_library_snapshots_status", "status"),
    )

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    library_version: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SharedLineProfile(Base):
    """V3.2 — shared SETUP/FINISH profile referenced by CORE packs."""

    __tablename__ = "shared_line_profiles"
    __table_args__ = (
        CheckConstraint(
            f"flow IN ({FLOW_SQL})", name="shared_line_profiles_flow"
        ),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="shared_line_profiles_status",
        ),
        CheckConstraint(
            "version >= 1", name="shared_line_profiles_version_positive"
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="shared_line_profiles_published_at",
        ),
        UniqueConstraint(
            "snapshot_id",
            "profile_code",
            "version",
            name="uq_shared_line_profiles_snapshot_code_version",
        ),
        Index("ix_shared_line_profiles_snapshot", "snapshot_id"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_code: Mapped[str] = mapped_column(String(150), nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_shared_line_profiles_snapshot",
        ),
        nullable=False,
    )
    flow: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    applicability_rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SharedProfileLine(Base):
    """V3.2 — SETUP or FINISH line belonging to a shared profile."""

    __tablename__ = "shared_profile_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "profile_version"],
            ["shared_line_profiles.profile_id", "shared_line_profiles.version"],
            name="fk_shared_profile_lines_profile",
        ),
        ForeignKeyConstraint(
            ["price_id", "price_version"],
            ["price_versions.price_id", "price_versions.version"],
            name="fk_shared_profile_lines_price_version",
        ),
        ForeignKeyConstraint(
            ["vat_rule_id", "vat_rule_version"],
            ["vat_rules.vat_rule_id", "vat_rules.version"],
            name="fk_shared_profile_lines_vat_rule",
        ),
        CheckConstraint(
            f"phase IN ({SHARED_PHASE_SQL})",
            name="shared_profile_lines_phase",
        ),
        CheckConstraint(
            f"unit IN ({UNIT_SQL})", name="shared_profile_lines_unit"
        ),
        CheckConstraint(
            "slot_index >= 0", name="shared_profile_lines_slot_nonnegative"
        ),
        CheckConstraint(
            "default_quantity > 0",
            name="shared_profile_lines_default_quantity_positive",
        ),
        CheckConstraint(
            f"linear_measurement_mode IS NULL OR "
            f"linear_measurement_mode IN ({LINEAR_MODE_SQL})",
            name="shared_profile_lines_linear_mode",
        ),
        CheckConstraint(
            "(unit = 'ML' AND linear_measurement_mode IS NOT NULL "
            "AND linear_formula_id IS NOT NULL) OR "
            "(unit <> 'ML' AND linear_measurement_mode IS NULL "
            "AND linear_formula_id IS NULL)",
            name="shared_profile_lines_linear_integrity",
        ),
        UniqueConstraint(
            "profile_id",
            "profile_version",
            "phase",
            "slot_index",
            name="uq_shared_profile_lines_phase_slot",
        ),
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    designation: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_rule: Mapped[str] = mapped_column(String(150), nullable=False)
    linear_measurement_mode: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    linear_formula_id: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )
    linear_params: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    quantity_precision: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3
    )
    rounding_step: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    default_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False
    )
    price_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    price_version: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    vat_rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TradeCatalog(Base):
    __tablename__ = "trade_catalog"
    __table_args__ = (
        CheckConstraint(f"flow IN ({FLOW_SQL})", name="trade_catalog_flow"),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="trade_catalog_status",
        ),
        CheckConstraint("version >= 1", name="trade_catalog_version_positive"),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="trade_catalog_published_at",
        ),
        # V3.2 — fallback pack identity is versioned.
        CheckConstraint(
            "(fallback_pack_id IS NULL AND fallback_pack_version IS NULL) OR "
            "(fallback_pack_id IS NOT NULL AND fallback_pack_version IS NOT NULL)",
            name="trade_catalog_fallback_pair",
        ),
    )

    trade_code: Mapped[str] = mapped_column(String(100), primary_key=True)
    flow: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    fallback_pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quote_packs.pack_id",
            name="fk_trade_catalog_fallback_pack",
            use_alter=True,
        ),
        nullable=True,
    )
    # V3.2
    fallback_pack_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # V3.2 — optional snapshot scoping (nullable during transitional import).
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_trade_catalog_snapshot",
        ),
        nullable=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class QuotePack(Base):
    __tablename__ = "quote_packs"
    __table_args__ = (
        CheckConstraint(f"flow IN ({FLOW_SQL})", name="quote_packs_flow"),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="quote_packs_status",
        ),
        CheckConstraint("version >= 1", name="quote_packs_version_positive"),
        CheckConstraint(
            "fallback_rank IS NULL OR fallback_rank >= 0",
            name="quote_packs_fallback_rank",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR "
            "(published_at IS NOT NULL AND approved_by IS NOT NULL "
            "AND regression_passed = true)",
            name="quote_packs_publication_evidence",
        ),
        UniqueConstraint(
            "pack_code", "version", name="uq_quote_packs_code_version"
        ),
        Index("ix_quote_packs_trade_status", "trade_code", "status"),
        Index("ix_quote_packs_library_version", "library_version"),
        Index("ix_quote_packs_lexical", "lexical", postgresql_using="gin"),
        Index(
            "ix_quote_packs_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),
        ForeignKeyConstraint(
            ["shared_profile_id", "shared_profile_version"],
            ["shared_line_profiles.profile_id", "shared_line_profiles.version"],
            name="fk_quote_packs_shared_profile",
            use_alter=True,
        ),
    )

    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pack_code: Mapped[str] = mapped_column(String(150), nullable=False)
    flow: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_code: Mapped[str] = mapped_column(
        String(100),
        ForeignKey("trade_catalog.trade_code", name="fk_quote_packs_trade"),
        nullable=False,
    )
    service_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # V3.2 — pack binds to one published shared SETUP/FINISH profile.
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_quote_packs_snapshot",
        ),
        nullable=True,
    )
    shared_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    shared_profile_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    searchable_text: Mapped[str] = mapped_column(Text, nullable=False)
    lexical: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('french'::regconfig, searchable_text)", persisted=True
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    exclusion_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    required_coverage: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    fallback_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    # Correctifs ciblés à intégrer dans la V3.2 §2 — signature obligatoire publication.
    pack_match_signature: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    regression_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    publication_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PriceVersion(Base):
    __tablename__ = "price_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="price_versions_version_positive"),
        CheckConstraint(
            "unit_price_cents >= 0", name="price_versions_amount_nonnegative"
        ),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="price_versions_status",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="price_versions_effective_range",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="price_versions_published_at",
        ),
    )

    price_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    price_code: Mapped[str] = mapped_column(String(150), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR"
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # V3.2 — price rows belong to an immutable library snapshot when published.
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_price_versions_snapshot",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VatRule(Base):
    __tablename__ = "vat_rules"
    __table_args__ = (
        CheckConstraint("version >= 1", name="vat_rules_version_positive"),
        # V3.2 — rate is numeric 0–100 (no longer a fixed enum of four rates).
        CheckConstraint(
            "rate >= 0 AND rate <= 100", name="vat_rules_allowed_rate"
        ),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})", name="vat_rules_status"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="vat_rules_effective_range",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="vat_rules_published_at",
        ),
    )

    vat_rule_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(
        String(2), nullable=False, default="FR"
    )
    # V3.2 — territorial applicability (FR_METROPOLE_CORSE scope).
    territory_code: Mapped[str] = mapped_column(
        String(40), nullable=False, default="FR-MET"
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    applicability_rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_vat_rules_snapshot",
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TechnicalDependency(Base):
    __tablename__ = "technical_dependencies"
    __table_args__ = (
        CheckConstraint(
            "version >= 1", name="technical_dependencies_version_positive"
        ),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="technical_dependencies_status",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="technical_dependencies_published_at",
        ),
    )

    dependency_id: Mapped[str] = mapped_column(String(150), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_code: Mapped[str] = mapped_column(
        String(100),
        ForeignKey(
            "trade_catalog.trade_code",
            name="fk_technical_dependencies_trade",
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    applicability_rule: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuotePackLine(Base):
    __tablename__ = "quote_pack_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["price_id", "price_version"],
            ["price_versions.price_id", "price_versions.version"],
            name="fk_quote_pack_lines_price_version",
        ),
        ForeignKeyConstraint(
            ["vat_rule_id", "vat_rule_version"],
            ["vat_rules.vat_rule_id", "vat_rules.version"],
            name="fk_quote_pack_lines_vat_rule",
        ),
        CheckConstraint(
            f"phase IN ({PHASE_SQL})", name="quote_pack_lines_phase"
        ),
        CheckConstraint(
            f"unit IN ({UNIT_SQL})", name="quote_pack_lines_unit"
        ),
        CheckConstraint(
            f"status IN ({PUBLICATION_STATUS_SQL})",
            name="quote_pack_lines_status",
        ),
        CheckConstraint(
            "version >= 1", name="quote_pack_lines_version_positive"
        ),
        CheckConstraint(
            "slot_index >= 0", name="quote_pack_lines_slot_nonnegative"
        ),
        CheckConstraint(
            "default_quantity > 0",
            name="quote_pack_lines_default_quantity_positive",
        ),
        CheckConstraint(
            "quantity_precision >= 0",
            name="quote_pack_lines_precision_nonnegative",
        ),
        CheckConstraint(
            "rounding_step IS NULL OR rounding_step > 0",
            name="quote_pack_lines_rounding_step_positive",
        ),
        CheckConstraint(
            f"linear_measurement_mode IS NULL OR "
            f"linear_measurement_mode IN ({LINEAR_MODE_SQL})",
            name="quote_pack_lines_linear_mode",
        ),
        CheckConstraint(
            "(unit = 'ML' AND linear_measurement_mode IS NOT NULL "
            "AND linear_formula_id IS NOT NULL) OR "
            "(unit <> 'ML' AND linear_measurement_mode IS NULL "
            "AND linear_formula_id IS NULL)",
            name="quote_pack_lines_linear_integrity",
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR published_at IS NOT NULL",
            name="quote_pack_lines_published_at",
        ),
        UniqueConstraint(
            "pack_id",
            "phase",
            "slot_index",
            name="uq_quote_pack_lines_pack_phase_slot",
        ),
        Index("ix_quote_pack_lines_pack", "pack_id"),
        Index(
            "ix_quote_pack_lines_lexical", "lexical", postgresql_using="gin"
        ),
        Index(
            "ix_quote_pack_lines_embedding",
            "embedding",
            postgresql_using="ivfflat",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"lists": 100},
        ),
    )

    line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    pack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quote_packs.pack_id",
            name="fk_quote_pack_lines_pack",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    phase: Mapped[str] = mapped_column(String(20), nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    designation: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_action: Mapped[str] = mapped_column(Text, nullable=False)
    object_family: Mapped[str] = mapped_column(Text, nullable=False)
    material_family: Mapped[str | None] = mapped_column(Text, nullable=True)
    searchable_text: Mapped[str] = mapped_column(Text, nullable=False)
    lexical: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('french'::regconfig, searchable_text)", persisted=True
        ),
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), nullable=True
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    synonym_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    capability_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    exclusion_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    technical_dependency_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity_rule: Mapped[str] = mapped_column(String(150), nullable=False)
    # Correctifs ciblés à intégrer dans la V3.2 §5 — binding quantité officielle.
    quantity_rule_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    quantity_binding_scope: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    share_group_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    linear_measurement_mode: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    linear_formula_id: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )
    linear_params: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    quantity_precision: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=3
    )
    rounding_step: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 3), nullable=True
    )
    default_quantity: Mapped[Decimal] = mapped_column(
        Numeric(14, 3), nullable=False
    )
    price_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    price_version: Mapped[int] = mapped_column(Integer, nullable=False)
    vat_rule_id: Mapped[str] = mapped_column(String(100), nullable=False)
    vat_rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    replacement_group: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )
    replaceable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="DRAFT"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    # Correctifs ciblés à intégrer dans la V3.2 §8.
    source_catalog_row_hash: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SemanticCache(Base):
    __tablename__ = "semantic_cache"
    __table_args__ = (
        CheckConstraint(
            "response_kind IN ('SEMANTIC_PLAN', 'DEMAND_MATRIX')",
            name="semantic_cache_response_kind",
        ),
        CheckConstraint(
            "status IN ('READY', 'INVALIDATED', 'EXPIRED')",
            name="semantic_cache_status",
        ),
        CheckConstraint("version >= 1", name="semantic_cache_version_positive"),
        UniqueConstraint(
            "input_hash",
            "prompt_hash",
            "ssot_version",
            "library_version",
            "semantic_model",
            "response_kind",
            name="uq_semantic_cache_snapshot",
        ),
        Index("ix_semantic_cache_expires_at", "expires_at"),
    )

    cache_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    input_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    ssot_version: Mapped[str] = mapped_column(String(100), nullable=False)
    library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    semantic_model: Mapped[str] = mapped_column(String(100), nullable=False)
    response_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    response_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="READY"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuoteExecution(Base):
    __tablename__ = "quote_executions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'COMPLETE_PRIMARY', "
            "'COMPLETE_DEGRADED_AUTHORIZED', 'FAILED_INTERNAL')",
            name="quote_executions_status",
        ),
        CheckConstraint(
            "generation_mode IS NULL OR generation_mode IN "
            "('EXACT_PACK', 'RESELECTED_PUBLISHED_PACK', "
            "'OFFICIAL_FALLBACK', 'REPAIRED_PACK')",
            name="quote_executions_generation_mode",
        ),
        CheckConstraint(
            "confidence IS NULL OR confidence IN ('HIGH', 'MEDIUM', 'LOW')",
            name="quote_executions_confidence",
        ),
        CheckConstraint(
            "stage_completion_rate >= 0 AND stage_completion_rate <= 1",
            name="quote_executions_stage_completion",
        ),
        CheckConstraint(
            "unjustified_line_rate >= 0 AND unjustified_line_rate <= 1",
            name="quote_executions_unjustified_rate",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="quote_executions_duration_nonnegative",
        ),
        Index("ix_quote_executions_request_id", "request_id"),
        Index("ix_quote_executions_created_at", "created_at"),
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quote_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True
    )
    request_id: Mapped[str] = mapped_column(String(150), nullable=False)
    pipeline_version: Mapped[str] = mapped_column(String(30), nullable=False)
    ssot_version: Mapped[str] = mapped_column(String(100), nullable=False)
    library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # V3.2 — snapshot used for this execution (current or last-validated fallback).
    library_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_quote_executions_snapshot",
        ),
        nullable=True,
    )
    fallback_snapshot_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    territory_code: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    semantic_model: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    reranker_model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    result_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="RUNNING"
    )
    generation_mode: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    arbitrage_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    stage_completion_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=0
    )
    display_gate_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    selected_pack_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )
    replaced_line_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )
    assumption_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        default=list,
        server_default=text("'{}'::text[]"),
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    semantic_plan: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    demand_matrix: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    document_emitted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    unjustified_line_rate: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=0
    )
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuoteStageExecution(Base):
    __tablename__ = "quote_stage_executions"
    __table_args__ = (
        CheckConstraint(
            f"stage IN ({STAGE_SQL})", name="quote_stage_executions_stage"
        ),
        CheckConstraint(
            "status IN ('PRIMARY', 'DEGRADED_AUTHORIZED')",
            name="quote_stage_executions_status",
        ),
        CheckConstraint(
            "duration_ms >= 0", name="quote_stage_executions_duration_nonnegative"
        ),
        CheckConstraint(
            "attempt >= 1", name="quote_stage_executions_attempt_positive"
        ),
        CheckConstraint(
            "input_count >= 0 AND output_count >= 0",
            name="quote_stage_executions_counts_nonnegative",
        ),
        UniqueConstraint(
            "execution_id",
            "stage",
            "attempt",
            name="uq_quote_stage_execution_attempt",
        ),
        Index("ix_quote_stage_executions_execution", "execution_id"),
    )

    stage_execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "quote_executions.execution_id",
            name="fk_quote_stage_executions_execution",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QuoteFeedbackEvent(Base):
    __tablename__ = "quote_feedback_events"
    __table_args__ = (
        CheckConstraint(
            "correction_scope IN "
            "('PERSONAL_PREFERENCE', 'GENERAL_CANDIDATE')",
            name="quote_feedback_events_scope",
        ),
        CheckConstraint(
            "status IN ('RECORDED', 'PROCESSED', 'REJECTED')",
            name="quote_feedback_events_status",
        ),
        CheckConstraint(
            "schema_version >= 1",
            name="quote_feedback_events_schema_version_positive",
        ),
        Index("ix_quote_feedback_events_quote_id", "quote_id"),
        Index("ix_quote_feedback_events_created_at", "created_at"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    pipeline_version: Mapped[str] = mapped_column(String(30), nullable=False)
    library_version: Mapped[str] = mapped_column(String(100), nullable=False)
    # V3.2 — feedback is tied to the snapshot that produced the quote.
    library_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "library_snapshots.snapshot_id",
            name="fk_quote_feedback_events_snapshot",
        ),
        nullable=True,
    )
    structural_diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    correction_scope: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    quote_outcome: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="RECORDED"
    )
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ImprovementCandidate(Base):
    __tablename__ = "improvement_candidates"
    __table_args__ = (
        CheckConstraint(
            "target_type IN "
            "('PACK', 'LINE', 'SYNONYM', 'QUANTITY_RULE', 'VAT_RULE', 'TEST')",
            name="improvement_candidates_target_type",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED', 'PUBLISHED')",
            name="improvement_candidates_status",
        ),
        CheckConstraint(
            "version >= 1", name="improvement_candidates_version_positive"
        ),
        CheckConstraint(
            "status <> 'PUBLISHED' OR "
            "(approved_by IS NOT NULL AND published_library_version IS NOT NULL "
            "AND published_at IS NOT NULL)",
            name="improvement_candidates_publication_gate",
        ),
        Index("ix_improvement_candidates_status", "status"),
        Index("ix_improvement_candidates_target", "target_type", "target_id"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(150), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="PENDING"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    published_library_version: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


__all__ = [
    "ImprovementCandidate",
    "LibrarySnapshot",
    "PriceVersion",
    "QuoteExecution",
    "QuoteFeedbackEvent",
    "QuotePack",
    "QuotePackLine",
    "QuoteStageExecution",
    "SemanticCache",
    "SharedLineProfile",
    "SharedProfileLine",
    "TechnicalDependency",
    "TradeCatalog",
    "VatRule",
]
