-- Standalone CREATE TABLE script for the chatbot supervisor tables.
--
-- Usage:
--   psql -U devis -d devis -f scripts/create_chatbot_tables.sql
--
-- These tables are also managed by Alembic migrations but this script
-- allows quick bootstrapping without running the full migration chain.

-- -----------------------------------------------------------------------
-- chatbot_daily_metrics — aggregated daily counters per chatbot source
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chatbot_daily_metrics (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date                    DATE NOT NULL,
    chatbot_source          VARCHAR(32) NOT NULL,
    total_conversations     INTEGER NOT NULL DEFAULT 0,
    total_messages          INTEGER NOT NULL DEFAULT 0,
    total_prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    total_completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens            INTEGER NOT NULL DEFAULT 0,
    total_errors            INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_metrics_date_source UNIQUE (date, chatbot_source)
);

CREATE INDEX IF NOT EXISTS ix_chatbot_daily_metrics_date
    ON chatbot_daily_metrics (date);
CREATE INDEX IF NOT EXISTS ix_chatbot_daily_metrics_chatbot_source
    ON chatbot_daily_metrics (chatbot_source);


-- -----------------------------------------------------------------------
-- chatbot_conversations — flat log of every user ↔ AI message exchange
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chatbot_conversations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chatbot_source      VARCHAR(32) NOT NULL,
    user_message        TEXT NOT NULL,
    ai_response         TEXT NOT NULL,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    is_fallback         BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_chatbot_conversations_chatbot_source
    ON chatbot_conversations (chatbot_source);
CREATE INDEX IF NOT EXISTS ix_chatbot_conversations_created_at
    ON chatbot_conversations (created_at);
