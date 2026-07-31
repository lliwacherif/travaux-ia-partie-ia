"""Create the isolated V3 quote-engine schema.

Revision ID: v3_0001
Revises:
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op

revision: str = "v3_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = ("v3",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable pgvector and create every V3 table and retrieval index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE trade_catalog (
            trade_code varchar(100) PRIMARY KEY,
            flow varchar(20) NOT NULL,
            label text NOT NULL,
            active boolean NOT NULL DEFAULT true,
            fallback_pack_id uuid,
            version integer NOT NULL,
            catalog_version varchar(100) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_v3_trade_catalog_flow
                CHECK (flow IN ('TRAVAUX', 'DEPANNAGE')),
            CONSTRAINT ck_v3_trade_catalog_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_trade_catalog_version_positive CHECK (version >= 1),
            CONSTRAINT ck_v3_trade_catalog_published_at
                CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE price_versions (
            price_id uuid NOT NULL,
            version integer NOT NULL,
            price_code varchar(150) NOT NULL,
            unit varchar(20) NOT NULL,
            unit_price_cents bigint NOT NULL,
            currency varchar(3) NOT NULL DEFAULT 'EUR',
            effective_from date NOT NULL,
            effective_to date,
            source_ref text NOT NULL,
            library_version varchar(100) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_v3_price_versions PRIMARY KEY (price_id, version),
            CONSTRAINT ck_v3_price_versions_version_positive CHECK (version >= 1),
            CONSTRAINT ck_v3_price_versions_amount_nonnegative
                CHECK (unit_price_cents >= 0),
            CONSTRAINT ck_v3_price_versions_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_price_versions_effective_range
                CHECK (effective_to IS NULL OR effective_to > effective_from),
            CONSTRAINT ck_v3_price_versions_published_at
                CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE vat_rules (
            vat_rule_id varchar(100) NOT NULL,
            version integer NOT NULL,
            country varchar(2) NOT NULL DEFAULT 'FR',
            label text NOT NULL,
            rate numeric(5, 2) NOT NULL,
            applicability_rule jsonb NOT NULL,
            effective_from date NOT NULL,
            effective_to date,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_v3_vat_rules PRIMARY KEY (vat_rule_id, version),
            CONSTRAINT ck_v3_vat_rules_version_positive CHECK (version >= 1),
            CONSTRAINT ck_v3_vat_rules_allowed_rate CHECK (rate IN (0, 5.5, 10, 20)),
            CONSTRAINT ck_v3_vat_rules_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_vat_rules_effective_range
                CHECK (effective_to IS NULL OR effective_to > effective_from),
            CONSTRAINT ck_v3_vat_rules_published_at
                CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE technical_dependencies (
            dependency_id varchar(150) NOT NULL,
            version integer NOT NULL,
            trade_code varchar(100) NOT NULL,
            label text NOT NULL,
            applicability_rule jsonb NOT NULL,
            active boolean NOT NULL DEFAULT true,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_v3_technical_dependencies
                PRIMARY KEY (dependency_id, version),
            CONSTRAINT fk_v3_technical_dependencies_trade
                FOREIGN KEY (trade_code) REFERENCES trade_catalog (trade_code),
            CONSTRAINT ck_v3_technical_dependencies_version_positive
                CHECK (version >= 1),
            CONSTRAINT ck_v3_technical_dependencies_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_technical_dependencies_published_at
                CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quote_packs (
            pack_id uuid PRIMARY KEY,
            pack_code varchar(150) NOT NULL,
            flow varchar(20) NOT NULL,
            trade_code varchar(100) NOT NULL,
            service_code varchar(100),
            title text NOT NULL,
            version integer NOT NULL,
            library_version varchar(100) NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            searchable_text text NOT NULL,
            lexical tsvector GENERATED ALWAYS AS
                (to_tsvector('french'::regconfig, searchable_text)) STORED,
            embedding vector(1536),
            embedding_model varchar(100),
            exclusion_tags text[] NOT NULL DEFAULT '{}'::text[],
            required_coverage text[] NOT NULL DEFAULT '{}'::text[],
            fallback_rank integer,
            content_hash varchar(80) NOT NULL,
            source_hash varchar(80) NOT NULL,
            approved_by uuid,
            regression_passed boolean NOT NULL DEFAULT false,
            publication_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_v3_quote_packs_trade
                FOREIGN KEY (trade_code) REFERENCES trade_catalog (trade_code),
            CONSTRAINT uq_quote_packs_code_version UNIQUE (pack_code, version),
            CONSTRAINT ck_v3_quote_packs_flow
                CHECK (flow IN ('TRAVAUX', 'DEPANNAGE')),
            CONSTRAINT ck_v3_quote_packs_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_quote_packs_version_positive CHECK (version >= 1),
            CONSTRAINT ck_v3_quote_packs_fallback_rank
                CHECK (fallback_rank IS NULL OR fallback_rank >= 0),
            CONSTRAINT ck_v3_quote_packs_publication_evidence CHECK (
                status <> 'PUBLISHED' OR (
                    published_at IS NOT NULL
                    AND approved_by IS NOT NULL
                    AND regression_passed = true
                )
            )
        )
        """
    )

    op.execute(
        """
        ALTER TABLE trade_catalog
        ADD CONSTRAINT fk_trade_catalog_fallback_pack
        FOREIGN KEY (fallback_pack_id) REFERENCES quote_packs (pack_id)
        """
    )

    op.execute(
        """
        CREATE TABLE quote_pack_lines (
            line_id uuid PRIMARY KEY,
            pack_id uuid NOT NULL,
            version integer NOT NULL,
            library_version varchar(100) NOT NULL,
            phase varchar(20) NOT NULL,
            slot_index integer NOT NULL,
            designation text NOT NULL,
            normalized_action text NOT NULL,
            object_family text NOT NULL,
            material_family text,
            searchable_text text NOT NULL,
            lexical tsvector GENERATED ALWAYS AS
                (to_tsvector('french'::regconfig, searchable_text)) STORED,
            embedding vector(1536),
            embedding_model varchar(100),
            synonym_tags text[] NOT NULL DEFAULT '{}'::text[],
            capability_tags text[] NOT NULL DEFAULT '{}'::text[],
            exclusion_tags text[] NOT NULL DEFAULT '{}'::text[],
            technical_dependency_ids text[] NOT NULL DEFAULT '{}'::text[],
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
            replacement_group varchar(150),
            replaceable boolean NOT NULL DEFAULT false,
            active boolean NOT NULL DEFAULT true,
            status varchar(20) NOT NULL DEFAULT 'DRAFT',
            content_hash varchar(80) NOT NULL,
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_quote_pack_lines_pack
                FOREIGN KEY (pack_id) REFERENCES quote_packs (pack_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_quote_pack_lines_price_version
                FOREIGN KEY (price_id, price_version)
                REFERENCES price_versions (price_id, version),
            CONSTRAINT fk_quote_pack_lines_vat_rule
                FOREIGN KEY (vat_rule_id, vat_rule_version)
                REFERENCES vat_rules (vat_rule_id, version),
            CONSTRAINT uq_quote_pack_lines_pack_phase_slot
                UNIQUE (pack_id, phase, slot_index),
            CONSTRAINT ck_v3_quote_pack_lines_phase
                CHECK (phase IN ('SETUP', 'CORE', 'FINISH')),
            CONSTRAINT ck_v3_quote_pack_lines_unit
                CHECK (unit IN (
                    'M2', 'ML', 'M3', 'UNIT', 'HOUR', 'DAY', 'FORFAIT', 'TONNE'
                )),
            CONSTRAINT ck_v3_quote_pack_lines_status
                CHECK (status IN ('DRAFT', 'PUBLISHED', 'ARCHIVED')),
            CONSTRAINT ck_v3_quote_pack_lines_version_positive CHECK (version >= 1),
            CONSTRAINT ck_v3_quote_pack_lines_slot_nonnegative CHECK (slot_index >= 0),
            CONSTRAINT ck_v3_quote_pack_lines_default_quantity_positive
                CHECK (default_quantity > 0),
            CONSTRAINT ck_v3_quote_pack_lines_precision_nonnegative
                CHECK (quantity_precision >= 0),
            CONSTRAINT ck_v3_quote_pack_lines_rounding_step_positive
                CHECK (rounding_step IS NULL OR rounding_step > 0),
            CONSTRAINT ck_v3_quote_pack_lines_linear_mode CHECK (
                linear_measurement_mode IS NULL OR linear_measurement_mode IN (
                    'EXPLICIT', 'AXIAL', 'LONGITUDINAL', 'PERIMETRIC',
                    'DEVELOPED', 'CUMULATED', 'SURFACE_TO_LINEAR',
                    'COUNT_TIMES_LENGTH'
                )
            ),
            CONSTRAINT ck_v3_quote_pack_lines_linear_integrity CHECK (
                (
                    unit = 'ML'
                    AND linear_measurement_mode IS NOT NULL
                    AND linear_formula_id IS NOT NULL
                ) OR (
                    unit <> 'ML'
                    AND linear_measurement_mode IS NULL
                    AND linear_formula_id IS NULL
                )
            ),
            CONSTRAINT ck_v3_quote_pack_lines_published_at
                CHECK (status <> 'PUBLISHED' OR published_at IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE semantic_cache (
            cache_id uuid PRIMARY KEY,
            input_hash varchar(80) NOT NULL,
            prompt_hash varchar(80) NOT NULL,
            config_hash varchar(80) NOT NULL,
            ssot_version varchar(100) NOT NULL,
            library_version varchar(100) NOT NULL,
            semantic_model varchar(100) NOT NULL,
            response_kind varchar(30) NOT NULL,
            response_payload jsonb NOT NULL,
            response_hash varchar(80) NOT NULL,
            version integer NOT NULL DEFAULT 1,
            status varchar(20) NOT NULL DEFAULT 'READY',
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz,
            CONSTRAINT uq_semantic_cache_snapshot UNIQUE (
                input_hash, prompt_hash, ssot_version, library_version,
                semantic_model, response_kind
            ),
            CONSTRAINT ck_v3_semantic_cache_response_kind
                CHECK (response_kind IN ('SEMANTIC_PLAN', 'DEMAND_MATRIX')),
            CONSTRAINT ck_v3_semantic_cache_status
                CHECK (status IN ('READY', 'INVALIDATED', 'EXPIRED')),
            CONSTRAINT ck_v3_semantic_cache_version_positive CHECK (version >= 1)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quote_executions (
            execution_id uuid PRIMARY KEY,
            quote_id uuid UNIQUE,
            request_id varchar(150) NOT NULL,
            pipeline_version varchar(30) NOT NULL,
            ssot_version varchar(100) NOT NULL,
            library_version varchar(100) NOT NULL,
            semantic_model varchar(100) NOT NULL,
            embedding_model varchar(100) NOT NULL,
            reranker_model varchar(100) NOT NULL,
            input_hash varchar(80) NOT NULL,
            prompt_hash varchar(80) NOT NULL,
            config_hash varchar(80) NOT NULL,
            result_hash varchar(80),
            status varchar(40) NOT NULL DEFAULT 'RUNNING',
            generation_mode varchar(30),
            cache_hit boolean NOT NULL DEFAULT false,
            arbitrage_applied boolean NOT NULL DEFAULT false,
            stage_completion_rate numeric(5, 4) NOT NULL DEFAULT 0,
            display_gate_passed boolean NOT NULL DEFAULT false,
            selected_pack_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
            replaced_line_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
            assumption_codes text[] NOT NULL DEFAULT '{}'::text[],
            input_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            semantic_plan jsonb,
            demand_matrix jsonb,
            result_payload jsonb,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            validation_report jsonb,
            confidence varchar(20),
            review_required boolean NOT NULL DEFAULT false,
            document_emitted boolean NOT NULL DEFAULT false,
            unjustified_line_rate numeric(5, 4) NOT NULL DEFAULT 0,
            duration_ms bigint,
            created_at timestamptz NOT NULL DEFAULT now(),
            completed_at timestamptz,
            CONSTRAINT ck_v3_quote_executions_status CHECK (
                status IN (
                    'RUNNING', 'COMPLETE_PRIMARY',
                    'COMPLETE_DEGRADED_AUTHORIZED', 'FAILED_INTERNAL'
                )
            ),
            CONSTRAINT ck_v3_quote_executions_generation_mode CHECK (
                generation_mode IS NULL OR generation_mode IN (
                    'EXACT_PACK', 'REPAIRED_PACK', 'OFFICIAL_FALLBACK'
                )
            ),
            CONSTRAINT ck_v3_quote_executions_confidence
                CHECK (confidence IS NULL OR confidence IN ('HIGH', 'MEDIUM', 'LOW')),
            CONSTRAINT ck_v3_quote_executions_stage_completion
                CHECK (stage_completion_rate >= 0 AND stage_completion_rate <= 1),
            CONSTRAINT ck_v3_quote_executions_unjustified_rate
                CHECK (unjustified_line_rate >= 0 AND unjustified_line_rate <= 1),
            CONSTRAINT ck_v3_quote_executions_duration_nonnegative
                CHECK (duration_ms IS NULL OR duration_ms >= 0)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quote_stage_executions (
            stage_execution_id uuid PRIMARY KEY,
            execution_id uuid NOT NULL,
            stage varchar(40) NOT NULL,
            attempt smallint NOT NULL DEFAULT 1,
            status varchar(30) NOT NULL,
            duration_ms bigint NOT NULL,
            fallback_reason text,
            input_count integer NOT NULL DEFAULT 0,
            output_count integer NOT NULL DEFAULT 0,
            input_hash varchar(80),
            output_hash varchar(80),
            evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_quote_stage_executions_execution
                FOREIGN KEY (execution_id)
                REFERENCES quote_executions (execution_id) ON DELETE CASCADE,
            CONSTRAINT uq_quote_stage_execution_attempt
                UNIQUE (execution_id, stage, attempt),
            CONSTRAINT ck_v3_quote_stage_executions_stage CHECK (
                stage IN (
                    '0_CONTEXT', '1_PLAN', '2_ANALYSIS', '2BIS_ARBITRATION',
                    '3_EXTRACTION', '3BIS_NORMALIZATION', '4A_LINE_SEARCH',
                    '4B_PARENT_AGGREGATION', '4C_DIRECT_PACK_SEARCH',
                    '4D_CANDIDATE_UNION', '4BIS_RERANK', '5_SELECTION',
                    '6_CALCULATIONS', '7_ASSEMBLY', '8_VALIDATION',
                    '9_OBSERVABILITY'
                )
            ),
            CONSTRAINT ck_v3_quote_stage_executions_status
                CHECK (status IN ('PRIMARY', 'DEGRADED_AUTHORIZED')),
            CONSTRAINT ck_v3_quote_stage_executions_duration_nonnegative
                CHECK (duration_ms >= 0),
            CONSTRAINT ck_v3_quote_stage_executions_attempt_positive
                CHECK (attempt >= 1),
            CONSTRAINT ck_v3_quote_stage_executions_counts_nonnegative
                CHECK (input_count >= 0 AND output_count >= 0)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE quote_feedback_events (
            event_id uuid PRIMARY KEY,
            quote_id uuid NOT NULL,
            company_id uuid NOT NULL,
            pipeline_version varchar(30) NOT NULL,
            library_version varchar(100) NOT NULL,
            structural_diff jsonb NOT NULL,
            correction_scope varchar(30) NOT NULL,
            reason_code varchar(100) NOT NULL,
            quote_outcome varchar(50),
            schema_version integer NOT NULL DEFAULT 1,
            status varchar(20) NOT NULL DEFAULT 'RECORDED',
            content_hash varchar(80) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_v3_quote_feedback_events_scope CHECK (
                correction_scope IN ('PERSONAL_PREFERENCE', 'GENERAL_CANDIDATE')
            ),
            CONSTRAINT ck_v3_quote_feedback_events_status
                CHECK (status IN ('RECORDED', 'PROCESSED', 'REJECTED')),
            CONSTRAINT ck_v3_quote_feedback_events_schema_version_positive
                CHECK (schema_version >= 1)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE improvement_candidates (
            candidate_id uuid PRIMARY KEY,
            target_type varchar(30) NOT NULL,
            target_id varchar(150) NOT NULL,
            evidence jsonb NOT NULL,
            proposed_change jsonb NOT NULL,
            status varchar(20) NOT NULL DEFAULT 'PENDING',
            version integer NOT NULL DEFAULT 1,
            content_hash varchar(80) NOT NULL,
            approved_by uuid,
            published_library_version varchar(100),
            published_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_v3_improvement_candidates_target_type CHECK (
                target_type IN (
                    'PACK', 'LINE', 'SYNONYM', 'QUANTITY_RULE',
                    'VAT_RULE', 'TEST'
                )
            ),
            CONSTRAINT ck_v3_improvement_candidates_status CHECK (
                status IN ('PENDING', 'APPROVED', 'REJECTED', 'PUBLISHED')
            ),
            CONSTRAINT ck_v3_improvement_candidates_version_positive
                CHECK (version >= 1),
            CONSTRAINT ck_v3_improvement_candidates_publication_gate CHECK (
                status <> 'PUBLISHED' OR (
                    approved_by IS NOT NULL
                    AND published_library_version IS NOT NULL
                    AND published_at IS NOT NULL
                )
            )
        )
        """
    )

    index_statements = (
        "CREATE INDEX ix_quote_packs_trade_status "
        "ON quote_packs (trade_code, status)",
        "CREATE INDEX ix_quote_packs_library_version "
        "ON quote_packs (library_version)",
        "CREATE INDEX ix_quote_packs_lexical "
        "ON quote_packs USING gin (lexical)",
        "CREATE INDEX ix_quote_packs_embedding ON quote_packs "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)",
        "CREATE INDEX ix_quote_pack_lines_pack ON quote_pack_lines (pack_id)",
        "CREATE INDEX ix_quote_pack_lines_lexical "
        "ON quote_pack_lines USING gin (lexical)",
        "CREATE INDEX ix_quote_pack_lines_embedding ON quote_pack_lines "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)",
        "CREATE INDEX ix_semantic_cache_expires_at "
        "ON semantic_cache (expires_at)",
        "CREATE INDEX ix_quote_executions_request_id "
        "ON quote_executions (request_id)",
        "CREATE INDEX ix_quote_executions_created_at "
        "ON quote_executions (created_at)",
        "CREATE INDEX ix_quote_stage_executions_execution "
        "ON quote_stage_executions (execution_id)",
        "CREATE INDEX ix_quote_feedback_events_quote_id "
        "ON quote_feedback_events (quote_id)",
        "CREATE INDEX ix_quote_feedback_events_created_at "
        "ON quote_feedback_events (created_at)",
        "CREATE INDEX ix_improvement_candidates_status "
        "ON improvement_candidates (status)",
        "CREATE INDEX ix_improvement_candidates_target "
        "ON improvement_candidates (target_type, target_id)",
    )
    for statement in index_statements:
        op.execute(statement)


def downgrade() -> None:
    """Drop the isolated V3 schema and its owned pgvector extension."""
    op.execute("DROP TABLE IF EXISTS improvement_candidates CASCADE")
    op.execute("DROP TABLE IF EXISTS quote_feedback_events CASCADE")
    op.execute("DROP TABLE IF EXISTS quote_stage_executions CASCADE")
    op.execute("DROP TABLE IF EXISTS quote_executions CASCADE")
    op.execute("DROP TABLE IF EXISTS semantic_cache CASCADE")
    op.execute("DROP TABLE IF EXISTS quote_pack_lines CASCADE")
    op.execute(
        "ALTER TABLE trade_catalog "
        "DROP CONSTRAINT IF EXISTS fk_trade_catalog_fallback_pack"
    )
    op.execute("DROP TABLE IF EXISTS quote_packs CASCADE")
    op.execute("DROP TABLE IF EXISTS technical_dependencies CASCADE")
    op.execute("DROP TABLE IF EXISTS vat_rules CASCADE")
    op.execute("DROP TABLE IF EXISTS price_versions CASCADE")
    op.execute("DROP TABLE IF EXISTS trade_catalog CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
