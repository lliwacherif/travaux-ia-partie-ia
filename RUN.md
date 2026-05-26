# Travaux-IA — Run & Test Guide

Step-by-step commands to run the API locally and exercise every endpoint.

All commands assume:

- **OS:** Windows + PowerShell
- **Working directory:** `c:\Users\ADMIN\Desktop\travaux-ia`
- **PostgreSQL 16** is installed at `C:\Program Files\PostgreSQL\16\bin\` and the Windows service `postgresql-x64-16` is running
- **Python 3.11+** is on the `PATH`

> Tip — open **two** PowerShell windows: one to run the server, one to call it.

---

## 1. One-time setup (skip if already done)

### 1.1 Install Python dependencies

```powershell
pip install -r requirements.txt
```

### 1.2 Create the PostgreSQL database

> Only needed the very first time. Safe to skip on subsequent runs.

```powershell
$env:PGPASSWORD = "12300liwa"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" `
  -h localhost -U "liwa-travauxia" -d postgres `
  -f scripts\create_database.sql
```

Expected output: `CREATE DATABASE` (or an error saying it already exists — both are fine).

---

## 2. Bring the schema and data up to date

Run these every time the models or the CSVs change. Both are idempotent.

```powershell
# 2.1 Apply migrations (creates / updates `trades` and `trade_services`)
alembic upgrade head

# 2.2 Seed the CSV data into the DB
python -m scripts.seed_csv
```

Expected output from the seeder:

```
trades: 29 rows in CSV, 29 newly inserted.
trade_services: 100 rows in CSV, 100 newly inserted.
Seed complete. trades: 29/29 | trade_services: 100/100
```

---

## 3. Run the API server

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Wait for:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

Leave this window open. Open a **second PowerShell window** for the test commands below.

To stop the server later: `Ctrl + C` in the server window.

---

## 4. Test the API from the terminal

Run these in your **second** PowerShell window.

### 4.1 Health check

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | ConvertTo-Json
```

**Expected:**

```json
{
  "status": "ok",
  "service": "Devis Generation API",
  "version": "0.1.0",
  "environment": "development"
}
```

### 4.2 Generate a devis (real Scaleway `gpt-oss-120b` call)

```powershell
$body = @{
  text = "Refaire l'electricite d'un appartement T3 de 65m2, avec tableau electrique et 12 prises, plus peinture des murs du salon en blanc"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri  "http://127.0.0.1:8000/api/v1/devis/generate" `
  -Method POST `
  -Body $body `
  -ContentType "application/json; charset=utf-8" `
  -TimeoutSec 120 `
  | ConvertTo-Json -Depth 10
```

**Expected:** HTTP 200 with a `DevisResponse` JSON containing `date`, `montant_ttc`, `validite`, `duree`, and a `blocs[].lots[].lignes[]` tree whose `description` values are real designations from the seeded `trade_services` catalog.

> ⏱️ The call typically takes 10-30 seconds because the model does two round-trips (trade detection + devis generation).

> Note: PowerShell may display accents as `Ǹ` / `�` due to its console codepage. The JSON payload itself is valid UTF-8 — it renders correctly in the browser (`/docs`) and in any file you save it to.

### 4.3 Out-of-scope request → HTTP 400

```powershell
$body = @{ text = "What is the capital of France?" } | ConvertTo-Json

try {
    Invoke-RestMethod `
      -Uri "http://127.0.0.1:8000/api/v1/devis/generate" `
      -Method POST -Body $body `
      -ContentType "application/json; charset=utf-8" `
      -TimeoutSec 60
} catch {
    Write-Host ("Status : " + $_.Exception.Response.StatusCode)
    Write-Host ("Body   : " + $_.ErrorDetails.Message)
}
```

**Expected:** `Status : BadRequest` with a French explanation in the body.

### 4.4 Empty input → HTTP 422 (Pydantic validation)

```powershell
$body = @{ text = "" } | ConvertTo-Json

try {
    Invoke-RestMethod `
      -Uri "http://127.0.0.1:8000/api/v1/devis/generate" `
      -Method POST -Body $body `
      -ContentType "application/json; charset=utf-8"
} catch {
    Write-Host ("Status : " + $_.Exception.Response.StatusCode)
    Write-Host ("Body   : " + $_.ErrorDetails.Message)
}
```

**Expected:** `Status : UnprocessableEntity` with a `min_length` error.

### 4.5 Swagger UI (interactive)

Open in your browser:

- Swagger : <http://127.0.0.1:8000/docs>
- ReDoc   : <http://127.0.0.1:8000/redoc>

The `POST /api/v1/devis/generate` operation has a **Try it out** button — great for exploring.

---

## 5. Inspect the database directly (optional)

```powershell
$env:PGPASSWORD = "12300liwa"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" `
  -h localhost -U "liwa-travauxia" -d travauxia_devis `
  -f scripts\verify_seed.sql
```

Shows row counts, a sample join on `Plomberie / Sanitaires`, orphan-FK check, and top 8 trades by service count.

Ad-hoc queries:

```powershell
$env:PGPASSWORD = "12300liwa"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" `
  -h localhost -U "liwa-travauxia" -d travauxia_devis `
  -c "SELECT name, category FROM trades ORDER BY name;"
```

---

## 6. Common commands cheat-sheet

| Action                         | Command                                           |
| ------------------------------ | ------------------------------------------------- |
| Install deps                   | `pip install -r requirements.txt`                 |
| Create new migration           | `alembic revision --autogenerate -m "..."`        |
| Apply migrations               | `alembic upgrade head`                            |
| Roll back last migration       | `alembic downgrade -1`                            |
| Current migration head         | `alembic current`                                 |
| Seed CSVs                      | `python -m scripts.seed_csv`                      |
| Run API (dev, auto-reload)     | `uvicorn app.main:app --reload`                   |
| Run API (prod-style, 4 workers)| `uvicorn app.main:app --host 0.0.0.0 --workers 4` |
| Open psql on project DB        | `psql -h localhost -U liwa-travauxia -d travauxia_devis` |

---

## 7. Troubleshooting

### `alembic: command not found`

The venv isn't active, or `pip install -r requirements.txt` was never run. Run it again.

### `asyncpg.exceptions.InvalidPasswordError` / `password authentication failed`

The password in `.env` doesn't match the role in Postgres. Check:

```powershell
$env:PGPASSWORD = "12300liwa"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h localhost -U "liwa-travauxia" -d postgres -c "SELECT current_user;"
```

If that fails, reset the password via an admin `psql` session:

```sql
ALTER ROLE "liwa-travauxia" WITH PASSWORD '12300liwa';
```

### `connection refused` on port 5432

Postgres isn't running. Start it:

```powershell
Start-Service postgresql-x64-16
```

### `HTTP 503` from `/api/v1/devis/generate`

Scaleway is unreachable or the API key is wrong. Check `SCALEWAY_API_KEY` and `SCALEWAY_BASE_URL` in `.env`.

### `HTTP 502` from `/api/v1/devis/generate`

The LLM returned something the JSON healer + `DevisResponse` schema couldn't coerce. Check the server log for the raw output — this usually means `max_tokens` was too low for a long devis, or the model hallucinated a field name. Re-try the same prompt; the pipeline is non-deterministic.

### Accents look garbled in PowerShell output

Display-only issue. The bytes are correct UTF-8. Use Swagger UI or save the response to a file to see them properly:

```powershell
(Invoke-RestMethod ... ) | ConvertTo-Json -Depth 10 | Out-File -Encoding utf8 devis.json
```
