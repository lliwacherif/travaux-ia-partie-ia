-- Create the bpu_items table for storing real BPU prices
-- Run with: psql $DATABASE_URL -f scripts/create_bpu_items.sql
-- Or via seed_bpu.py --create-table

CREATE TABLE IF NOT EXISTS bpu_items (
    id              TEXT PRIMARY KEY,
    code            TEXT,
    corps_metier    TEXT NOT NULL,
    designation     TEXT NOT NULL,
    description     TEXT,
    prix_unitaire_ht DOUBLE PRECISION NOT NULL DEFAULT 0,
    unite           TEXT NOT NULL DEFAULT 'u',
    taux_tva_defaut DOUBLE PRECISION NOT NULL DEFAULT 10,
    type            TEXT,
    categorie       TEXT,
    sous_categorie  TEXT,
    source          TEXT NOT NULL DEFAULT 'bibliotheque',
    slug            TEXT,
    is_system       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bpu_items_corps_metier ON bpu_items(corps_metier);
CREATE INDEX IF NOT EXISTS idx_bpu_items_slug ON bpu_items(slug);
CREATE INDEX IF NOT EXISTS idx_bpu_items_categorie ON bpu_items(categorie);
CREATE INDEX IF NOT EXISTS idx_bpu_items_unite ON bpu_items(unite);
