"""V3.2 schema upgrade: library snapshots + shared SETUP/FINISH profiles.

Revision ID: v3_0002
Revises: v3_0001
Create Date: 2026-08-04

Never touches the V2 / v1 devis database — this migrates only travaux_v3.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "v3_0002"
down_revision: str | Sequence[str] | None = "v3_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # V3.2 — immutable published library snapshots (stage 0B).
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS library_snapshots (
            snapshot_id uuid PRIMARY KEY,
            library_version varchar(100) NOT NULL UNIQUE,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            validated_at timestamptz,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_v3_library_snapshots_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_library_snapshots_published_validated
                CHECK (
                    status <> 'PUBLISHED'
                    OR (validated_at IS NOT NULL AND published_at IS NOT NULL)
                )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_library_snapshots_status "
        "ON library_snapshots (status)"
    )

    # V3.2 — shared SETUP/FINISH profiles.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_line_profiles (
            profile_id uuid NOT NULL,
            version integer NOT NULL,
            profile_code varchar(150) NOT NULL,
            snapshot_id uuid NOT NULL
                REFERENCES library_snapshots(snapshot_id),
            flow varchar(20) NOT NULL,
            title text NOT NULL,
            applicability_rule jsonb NOT NULL DEFAULT '{}'::jsonb,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (profile_id, version),
            CONSTRAINT ck_v3_shared_line_profiles_flow
                CHECK (flow IN ('TRAVAUX', 'DEPANNAGE')),
            CONSTRAINT ck_v3_shared_line_profiles_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_shared_line_profiles_version_positive
                CHECK (version >= 1),
            CONSTRAINT ck_v3_shared_line_profiles_published_at
                CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL),
            CONSTRAINT uq_shared_line_profiles_snapshot_code_version
                UNIQUE (snapshot_id, profile_code, version)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_profile_lines (
            line_id uuid PRIMARY KEY,
            profile_id uuid NOT NULL,
            profile_version integer NOT NULL,
            phase varchar(20) NOT NULL,
            slot_index integer NOT NULL,
            designation text NOT NULL,
            unit varchar(20) NOT NULL,
            quantity_rule varchar(150) NOT NULL,
            linear_measurement_mode varchar(40),
            linear_formula_id varchar(150),
            linear_params jsonb,
            quantity_precision smallint NOT NULL DEFAULT 3,
            rounding_step numeric(12, 3),
            default_quantity numeric(14, 3) NOT NULL,
            price_id uuid NOT NULL,
            price_version integer NOT NULL,
            vat_rule_id varchar(100) NOT NULL,
            vat_rule_version integer NOT NULL,
            active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_shared_profile_lines_profile
                FOREIGN KEY (profile_id, profile_version)
                REFERENCES shared_line_profiles(profile_id, version),
            CONSTRAINT fk_shared_profile_lines_price_version
                FOREIGN KEY (price_id, price_version)
                REFERENCES price_versions(price_id, version),
            CONSTRAINT fk_shared_profile_lines_vat_rule
                FOREIGN KEY (vat_rule_id, vat_rule_version)
                REFERENCES vat_rules(vat_rule_id, version),
            CONSTRAINT ck_v3_shared_profile_lines_phase
                CHECK (phase IN ('SETUP', 'FINISH')),
            CONSTRAINT ck_v3_shared_profile_lines_unit
                CHECK (unit IN ('M2','ML','M3','UNIT','HOUR','DAY','FORFAIT')),
            CONSTRAINT ck_v3_shared_profile_lines_slot_nonnegative
                CHECK (slot_index >= 0),
            CONSTRAINT ck_v3_shared_profile_lines_default_quantity_positive
                CHECK (default_quantity > 0),
            CONSTRAINT uq_shared_profile_lines_phase_slot
                UNIQUE (profile_id, profile_version, phase, slot_index)
        )
        """
    )

    # V3.2 columns on existing tables (nullable for transitional data).
    op.execute(
        """
        ALTER TABLE trade_catalog
            ADD COLUMN IF NOT EXISTS fallback_pack_version integer,
            ADD COLUMN IF NOT EXISTS snapshot_id uuid
                REFERENCES library_snapshots(snapshot_id)
        """
    )
    op.execute(
        """
        ALTER TABLE quote_packs
            ADD COLUMN IF NOT EXISTS snapshot_id uuid
                REFERENCES library_snapshots(snapshot_id),
            ADD COLUMN IF NOT EXISTS shared_profile_id uuid,
            ADD COLUMN IF NOT EXISTS shared_profile_version integer
        """
    )
    op.execute(
        """
        ALTER TABLE price_versions
            ADD COLUMN IF NOT EXISTS snapshot_id uuid
                REFERENCES library_snapshots(snapshot_id)
        """
    )
    op.execute(
        """
        ALTER TABLE vat_rules
            ADD COLUMN IF NOT EXISTS territory_code varchar(40)
                NOT NULL DEFAULT 'FR-MET',
            ADD COLUMN IF NOT EXISTS snapshot_id uuid
                REFERENCES library_snapshots(snapshot_id)
        """
    )
    # V3.2 — widen VAT rate check from fixed enum to 0–100.
    op.execute(
        """
        ALTER TABLE vat_rules DROP CONSTRAINT IF EXISTS ck_v3_vat_rules_allowed_rate
        """
    )
    op.execute(
        """
        ALTER TABLE vat_rules
            ADD CONSTRAINT ck_v3_vat_rules_allowed_rate
            CHECK (rate >= 0 AND rate <= 100)
        """
    )
    op.execute(
        """
        ALTER TABLE quote_executions
            ADD COLUMN IF NOT EXISTS library_snapshot_id uuid
                REFERENCES library_snapshots(snapshot_id),
            ADD COLUMN IF NOT EXISTS fallback_snapshot_used boolean
                NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS territory_code varchar(40)
        """
    )
    op.execute(
        """
        ALTER TABLE quote_feedback_events
            ADD COLUMN IF NOT EXISTS library_snapshot_id uuid
                REFERENCES library_snapshots(snapshot_id)
        """
    )

    # V3.2 — allow stage 0B_LIBRARY_SNAPSHOT in execution evidence.
    op.execute(
        "ALTER TABLE quote_stage_executions "
        "DROP CONSTRAINT IF EXISTS ck_v3_quote_stage_executions_stage"
    )
    op.execute(
        """
        ALTER TABLE quote_stage_executions
            ADD CONSTRAINT ck_v3_quote_stage_executions_stage CHECK (
                stage IN (
                    '0_CONTEXT', '0B_LIBRARY_SNAPSHOT', '1_PLAN', '2_ANALYSIS',
                    '2BIS_ARBITRATION', '3_EXTRACTION', '3BIS_NORMALIZATION',
                    '4A_LINE_SEARCH', '4B_PARENT_AGGREGATION',
                    '4C_DIRECT_PACK_SEARCH', '4D_CANDIDATE_UNION', '4BIS_RERANK',
                    '5_SELECTION', '6_CALCULATIONS', '7_ASSEMBLY',
                    '8_VALIDATION', '9_OBSERVABILITY'
                )
            )
        """
    )

    # V3.2 — authorized units no longer include TONNE.
    op.execute(
        "UPDATE quote_pack_lines SET unit = 'UNIT' WHERE unit = 'TONNE'"
    )
    op.execute(
        "UPDATE price_versions SET unit = 'UNIT' WHERE unit = 'TONNE'"
    )
    op.execute(
        "ALTER TABLE quote_pack_lines "
        "DROP CONSTRAINT IF EXISTS ck_v3_quote_pack_lines_unit"
    )
    op.execute(
        """
        ALTER TABLE quote_pack_lines
            ADD CONSTRAINT ck_v3_quote_pack_lines_unit CHECK (
                unit IN ('M2','ML','M3','UNIT','HOUR','DAY','FORFAIT')
            )
        """
    )
    op.execute(
        """
        UPDATE trade_catalog
        SET fallback_pack_version = 1
        WHERE fallback_pack_id IS NOT NULL
          AND fallback_pack_version IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE quote_feedback_events "
        "DROP COLUMN IF EXISTS library_snapshot_id"
    )
    op.execute(
        """
        ALTER TABLE quote_executions
            DROP COLUMN IF EXISTS library_snapshot_id,
            DROP COLUMN IF EXISTS fallback_snapshot_used,
            DROP COLUMN IF EXISTS territory_code
        """
    )
    op.execute(
        """
        ALTER TABLE vat_rules
            DROP COLUMN IF EXISTS territory_code,
            DROP COLUMN IF EXISTS snapshot_id
        """
    )
    op.execute("ALTER TABLE price_versions DROP COLUMN IF EXISTS snapshot_id")
    op.execute(
        """
        ALTER TABLE quote_packs
            DROP COLUMN IF EXISTS snapshot_id,
            DROP COLUMN IF EXISTS shared_profile_id,
            DROP COLUMN IF EXISTS shared_profile_version
        """
    )
    op.execute(
        """
        ALTER TABLE trade_catalog
            DROP COLUMN IF EXISTS fallback_pack_version,
            DROP COLUMN IF EXISTS snapshot_id
        """
    )
    op.execute("DROP TABLE IF EXISTS shared_profile_lines")
    op.execute("DROP TABLE IF EXISTS shared_line_profiles")
    op.execute("DROP TABLE IF EXISTS library_snapshots")
