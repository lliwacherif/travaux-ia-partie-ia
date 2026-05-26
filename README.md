# Devis Generation API

AI-driven quote (devis) generation API built with **FastAPI**, **SQLAlchemy 2
(async + asyncpg)** and **PostgreSQL 15**.

This repository contains the **Step 1** scaffolding: project layout, database
layer, SQLAlchemy models, Pydantic schemas and a basic health endpoint. The AI
logic will be plugged in later inside `app/services/`.

## Stack

- Python 3.11+
- FastAPI + Uvicorn
- SQLAlchemy 2.0 (async) + asyncpg
- Alembic (migrations)
- PostgreSQL 15 (via Docker Compose)
- Pydantic v2 + pydantic-settings
- OpenAI Python SDK

## Project layout

```
.
├── app/
│   ├── api/                 # HTTP routers (empty for now)
│   ├── core/
│   │   └── config.py        # pydantic-settings - env-driven configuration
│   ├── db/
│   │   └── database.py      # Async engine, session factory, Base, get_db()
│   ├── models/
│   │   ├── trade.py         # Trade ORM model
│   │   └── trade_service.py # TradeService ORM model
│   ├── schemas/
│   │   └── devis.py         # Ligne / Lot / Bloc / DevisResponse
│   ├── services/            # Business logic (AI, pricing, ...)
│   └── main.py              # FastAPI entry point + /health
├── docker-compose.yml       # PostgreSQL 15 service
├── requirements.txt
├── .env.example
└── README.md
```

## Quick start

### 1. Clone and install dependencies

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# macOS / Linux
# source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env        # macOS / Linux
Copy-Item .env.example .env # PowerShell
```

Edit `.env` and fill in `OPENAI_API_KEY` (and any other value you want to
override).

### 3. Start PostgreSQL

```bash
docker compose up -d
```

The database is reachable at `localhost:5432` with user/password/database all
equal to `devis` (matching `.env.example`).

### 4. Run the API

```bash
uvicorn app.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- ReDoc:      http://localhost:8000/redoc
- Health:     http://localhost:8000/health

## Response schema (`DevisResponse`)

```json
{
  "date": "2026-04-21T00:00:00.000Z",
  "montant_ttc": 12500,
  "validite": "2026-05-21T00:00:00.000Z",
  "duree": 21,
  "blocs": [
    {
      "title": "Bloc RDC",
      "lots": [
        {
          "title": "Electricite",
          "ligne_ids": ["661f2c6a9b1a4b2d1f4e8c24"],
          "lignes": [
            {
              "num": 1,
              "description": "Pose tableau electrique",
              "qte": 1,
              "unit": "u",
              "pu": 1200,
              "tva": 20,
              "ht": 1200,
              "ttc": 1440
            }
          ]
        }
      ]
    }
  ]
}
```

## What's next

- Wire Alembic (`alembic init alembic`) using `settings.SYNC_DATABASE_URL`.
- Add routers under `app/api/` and include them in `app/main.py`.
- Implement the AI devis generation service in `app/services/`.
