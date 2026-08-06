"""Correctifs ciblés à intégrer dans la V3.2 — pack_match_signature + generation modes.

Revision ID: v3_0003_correctifs_signatures
Revises: v3_0002_library_snapshots_shared_profiles
Create Date: 2026-08-05
"""

from __future__ import annotations

from alembic import op

revision = "v3_0003_correctifs_signatures"
down_revision = "v3_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Correctifs ciblés à intégrer dans la V3.2 §2 — signature de matching pack.
    op.execute(
        """
        ALTER TABLE quote_packs
            ADD COLUMN IF NOT EXISTS pack_match_signature varchar(200)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_quote_packs_match_signature
            ON quote_packs (pack_match_signature)
            WHERE pack_match_signature IS NOT NULL
        """
    )
    # Correctifs ciblés à intégrer dans la V3.2 §6 — mode reselect (pas d'hybride).
    op.execute(
        "ALTER TABLE quote_executions "
        "DROP CONSTRAINT IF EXISTS quote_executions_generation_mode"
    )
    op.execute(
        "ALTER TABLE quote_executions "
        "DROP CONSTRAINT IF EXISTS ck_v3_quote_executions_generation_mode"
    )
    op.execute(
        """
        ALTER TABLE quote_executions
            ADD CONSTRAINT quote_executions_generation_mode CHECK (
                generation_mode IS NULL OR generation_mode IN (
                    'EXACT_PACK',
                    'RESELECTED_PUBLISHED_PACK',
                    'OFFICIAL_FALLBACK',
                    'REPAIRED_PACK'
                )
            )
        """
    )
    # Correctifs ciblés à intégrer dans la V3.2 §5 / §8 — binding + hash ligne.
    op.execute(
        """
        ALTER TABLE quote_pack_lines
            ADD COLUMN IF NOT EXISTS quantity_rule_id varchar(150),
            ADD COLUMN IF NOT EXISTS quantity_binding_scope varchar(40),
            ADD COLUMN IF NOT EXISTS share_group_id varchar(100),
            ADD COLUMN IF NOT EXISTS source_catalog_row_hash varchar(80)
        """
    )
    op.execute(
        """
        UPDATE quote_pack_lines
           SET quantity_rule_id = quantity_rule
         WHERE quantity_rule_id IS NULL
           AND quantity_rule IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE quote_pack_lines
           SET source_catalog_row_hash = content_hash
         WHERE source_catalog_row_hash IS NULL
           AND content_hash IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE quote_pack_lines "
        "DROP COLUMN IF EXISTS quantity_rule_id, "
        "DROP COLUMN IF EXISTS quantity_binding_scope, "
        "DROP COLUMN IF EXISTS share_group_id, "
        "DROP COLUMN IF EXISTS source_catalog_row_hash"
    )
    op.execute("DROP INDEX IF EXISTS uq_quote_packs_match_signature")
    op.execute(
        "ALTER TABLE quote_packs DROP COLUMN IF EXISTS pack_match_signature"
    )
    op.execute(
        "ALTER TABLE quote_executions "
        "DROP CONSTRAINT IF EXISTS quote_executions_generation_mode"
    )
    op.execute(
        """
        ALTER TABLE quote_executions
            ADD CONSTRAINT quote_executions_generation_mode CHECK (
                generation_mode IS NULL OR generation_mode IN (
                    'EXACT_PACK', 'REPAIRED_PACK', 'OFFICIAL_FALLBACK'
                )
            )
        """
    )
