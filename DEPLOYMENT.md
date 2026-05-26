# Travaux-IA — DevOps Deployment Guide

Complete technical brief for redeploying the Devis Generation API on a new server.

---

## 1. Project at a glance

| Item | Value |
|---|---|
| Name | Travaux-IA / Devis Generation API |
| Language | Python 3.11+ |
| Framework | FastAPI + Uvicorn (ASGI) |
| ORM / DB driver | SQLAlchemy 2.0 async + asyncpg (runtime), psycopg2 (Alembic) |
| DB engine | PostgreSQL 15 (Docker) or 16 (native, current setup) |
| Migrations | Alembic |
| AI provider | Scaleway Generative API (OpenAI-compatible), model `gpt-oss-120b` |
| Public surface | `GET /health`, `POST /api/v1/devis/generate`, `/docs`, `/redoc` |
| Listening port | 8000 (uvicorn) |
| Repo size | Small (~12 Python modules, no frontend, no Dockerfile yet) |

There is **no Dockerfile** for the app — only `docker-compose.yml` for Postgres. Current production-style runs are bare-metal `uvicorn`. You'll likely want to containerize the app on the new host (sample Dockerfile below).

---

## 2. Runtime architecture

```
   Client (browser / curl)
          |
          v
   [ Reverse proxy (nginx / Caddy / Traefik) ]   <-- TLS terminates here
          |  proxy_pass http://127.0.0.1:8000
          v
   [ Uvicorn workers : FastAPI app ]
          |                |
          |                +--> HTTPS to api.scaleway.ai (LLM)
          v
   [ PostgreSQL 15/16 ] (local socket or :5432)
```

- The app is **stateless** — all state lives in Postgres; safe to scale horizontally behind a load balancer.
- Outbound HTTPS to `api.scaleway.ai` is **required** for every `/api/v1/devis/generate` call. The firewall must allow egress on 443.
- Each generate call takes 10–30 s (two LLM round-trips) — set client/proxy timeouts to **≥ 120 s**.

---

## 3. System requirements

**Minimum sane VM:** 2 vCPU, 2 GB RAM, 20 GB SSD, Ubuntu 22.04/24.04 LTS (or RHEL 9). The app itself is light; Postgres is the heaviest local dependency.

**OS packages (Debian/Ubuntu):**

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev \
    build-essential libpq-dev \
    postgresql-16 postgresql-client-16 \
    nginx certbot python3-certbot-nginx \
    git curl
```

`libpq-dev` + `build-essential` are needed for `psycopg2-binary` to build cleanly on first install (binary wheels usually cover this, but keep them around in case).

---

## 4. Python dependencies

Pinned in `requirements.txt`:

```
fastapi>=0.131.0,<1.0.0
uvicorn[standard]>=0.32.0,<1.0.0
sqlalchemy[asyncio]>=2.0.48,<2.1.0
alembic>=1.13.0,<2.0.0
asyncpg>=0.30.0,<1.0.0
psycopg2-binary>=2.9.9,<3.0.0
pydantic>=2.13.0,<3.0.0
pydantic-settings>=2.7.0,<3.0.0
openai>=1.50.0,<2.0.0
json-repair>=0.30.0,<1.0.0
python-dotenv>=1.0.1,<2.0.0
```

Notable: `openai>=1.50.0` (used as a generic OpenAI-compatible client pointed at Scaleway), `json-repair` (defensive LLM-output parsing), `python-dotenv` (auto-loaded by pydantic-settings via `model_config`).

No test framework is in `requirements.txt` — there is no test suite to run as a deployment gate.

---

## 5. Environment variables (full list)

The settings model (`app/core/config.py`) is the source of truth. Copy `.env.example` → `.env` on the target server and fill the values below.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PROJECT_NAME` | no | `Devis Generation API` | Cosmetic |
| `ENVIRONMENT` | no | `development` | One of `development`/`staging`/`production` |
| `DEBUG` | no | `true` | **Set to `false` in prod** |
| `API_V1_PREFIX` | no | `/api/v1` | URL prefix for routers |
| `DATABASE_URL` | **yes** | `postgresql+asyncpg://devis:devis@localhost:5432/devis` | Async DSN (asyncpg) |
| `SYNC_DATABASE_URL` | no | derived | Used by Alembic; auto-derived from `DATABASE_URL` by replacing `+asyncpg` → `+psycopg2` |
| `DB_ECHO` | no | `false` | SQL logging |
| `DB_POOL_SIZE` | no | `10` | SQLAlchemy pool |
| `DB_MAX_OVERFLOW` | no | `20` | Burst connections above pool |
| `SCALEWAY_API_KEY` | **yes** | — | Scaleway Generative API key |
| `SCALEWAY_BASE_URL` | **yes** | `https://api.scaleway.ai/v1` | Must include the `/<project-uuid>/v1` suffix from console |
| `SCALEWAY_MODEL` | no | `gpt-oss-120b` | Don't change without retesting prompts |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | no | empty | **Legacy, unused** — leave blank |
| `BACKEND_CORS_ORIGINS` | no | `[]` | Currently **ignored** in code (see §10) |

### Security note — read before deploying

The committed `.env` currently contains a real Postgres password (`12300liwa`) and a real Scaleway API key (`7bd776fa-…`). Rotate both before/while deploying:

1. Reset the Postgres role: `ALTER ROLE "liwa-travauxia" WITH PASSWORD '<new>';`
2. Revoke + re-issue the Scaleway key in <https://console.scaleway.com/generative-api>.
3. Add `.env` to your secrets manager (Vault / SOPS / AWS SSM / GitLab CI variables) and never re-commit the new values.

---

## 6. Database bootstrap

The app needs:

- A Postgres role and a database (any names — match them in `DATABASE_URL`).
- Two tables: `trades`, `trade_services` (created by Alembic migration `5d8a93629525_initial_trades_and_trade_services_tables.py`).
- Seed data: 29 trades and 100 trade_services from the two CSVs in the repo root (`trades_rows.csv`, `trade_services_rows.csv`).

**One-shot bootstrap on the new server** (run from the project root, venv activated, `.env` filled in):

```bash
# 1. Create role + database (interactive psql as a superuser)
sudo -u postgres psql <<'SQL'
CREATE ROLE travauxia LOGIN PASSWORD '<strong-random-password>';
CREATE DATABASE travauxia_devis OWNER travauxia ENCODING 'UTF8';
SQL

# 2. Apply schema
alembic upgrade head

# 3. Seed reference data (idempotent — ON CONFLICT DO NOTHING)
python -m scripts.seed_csv
```

Expected seeder output:

```
trades: 29 rows in CSV, 29 newly inserted.
trade_services: 100 rows in CSV, 100 newly inserted.
```

The seeder is idempotent and safe to re-run. To verify after seeding, run `psql -f scripts/verify_seed.sql`.

If you migrate from the existing dev DB instead of re-seeding from CSV, a `pg_dump --data-only` of `trades` + `trade_services` is the cleanest path — they have no other dependencies.

---

## 7. Running the app — three options

### Option A: systemd + uvicorn (recommended for a single VM)

`/etc/systemd/system/travaux-ia.service`:

```ini
[Unit]
Description=Travaux-IA Devis Generation API
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=simple
User=travauxia
Group=travauxia
WorkingDirectory=/opt/travaux-ia
EnvironmentFile=/opt/travaux-ia/.env
ExecStart=/opt/travaux-ia/.venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 --port 8000 --workers 4 --proxy-headers \
  --forwarded-allow-ips="*"
Restart=on-failure
RestartSec=3
# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/travaux-ia
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Worker count rule of thumb: `2 × CPU + 1`, capped at memory budget (~150–250 MB resident per worker).

### Option B: Docker (cleanest, no Dockerfile yet — create one)

There is no Dockerfile in the repo, so add one at the project root:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips=*"]
```

Then extend the existing `docker-compose.yml` with an `api` service that depends on `postgres`, mounts `.env`, and runs `alembic upgrade head && python -m scripts.seed_csv` as an init step (a separate one-shot service or an entrypoint wrapper).

### Option C: Plain `uvicorn` in tmux/screen (DEV ONLY)

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Don't run this in production — no restart on crash, no log rotation.

---

## 8. Reverse proxy (nginx)

Minimal config terminating TLS on 443 and forwarding to the local uvicorn:

```nginx
server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate     /etc/letsencrypt/live/api.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.example.com/privkey.pem;

    client_max_body_size 1m;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        proxy_read_timeout  180s;   # LLM calls can run 10-30s, allow margin
        proxy_send_timeout  180s;
        proxy_connect_timeout 10s;
    }
}
server {
    listen 80;
    server_name api.example.com;
    return 301 https://$host$request_uri;
}
```

Issue the cert with `certbot --nginx -d api.example.com`. Keep `/docs` and `/redoc` reachable (or block them in production with an `nginx` location block if you don't want public Swagger).

---

## 9. Health checks & observability

- **Liveness:** `GET /health` → 200 with `{"status":"ok", ...}`. Use this for k8s `livenessProbe`, ALB target groups, uptime monitors.
- **Logs:** the app uses Python `logging` only (stdout/stderr). With systemd → `journalctl -u travaux-ia -f`. With Docker → `docker logs -f`. Ship to your aggregator (Loki/CloudWatch/Datadog).
- **Notable log lines worth alerting on:**
  - `Stage 2 attempt N/M failed` (warns) — model retried
  - `Scaleway AI call failed` (error) — provider issue → produces 503 to the client
  - `AI produced unparseable JSON` / `AI devis was too truncated` → produces 502
- **Metrics:** none built in. If you need Prometheus, add `prometheus-fastapi-instrumentator` (1 line of code).

---

## 10. Important behavioral / security caveats

1. **CORS is wide open.** The runtime config ignores `BACKEND_CORS_ORIGINS` and uses `allow_origin_regex=".*"` with `allow_credentials=True` (in `app/main.py`). Tighten this before exposing the API publicly to anything other than your own frontend — it currently accepts cross-origin requests from any browser.
2. **No authentication.** `/api/v1/devis/generate` is open to anyone who can reach the port. At minimum, put it behind an API gateway, mTLS, or an `Authorization` header check at the proxy until app-level auth lands.
3. **Swagger/ReDoc are exposed in production by default.** `app.main` always mounts `/docs`, `/redoc`, `/api/v1/openapi.json`. Block them at the proxy if undesired.
4. **Outbound egress required.** The container/VM **must** be allowed to reach `https://api.scaleway.ai/*`. Without it every devis call returns 503.
5. **Long requests.** Anything in front of the app (CDN, ALB, Cloudflare) needs ≥ 60 s read timeout. 120–180 s is safe. Default ALB 60 s **will** truncate calls.
6. **CSV files are in the repo and shipped at deploy time.** `scripts/seed_csv.py` reads `trades_rows.csv` + `trade_services_rows.csv` from the project root. Don't `.dockerignore` them.
7. **`ElevenLabs_2026-04-23T09_37_36__s50_v3.mp3`** in the project root is ~1.9 MB and unused at runtime — safe to exclude from the deploy artifact / Docker image.

---

## 11. End-to-end deploy checklist

```text
[ ] Provision VM (2 vCPU / 2 GB / 20 GB / Ubuntu 22.04+)
[ ] Install OS packages (python3.11, postgresql-16, nginx, certbot, libpq-dev)
[ ] Create OS user `travauxia`, clone repo to /opt/travaux-ia, chown
[ ] python3.11 -m venv .venv && pip install -r requirements.txt
[ ] Provision Postgres role + db, fill /opt/travaux-ia/.env with rotated secrets
[ ] alembic upgrade head
[ ] python -m scripts.seed_csv  (verify "29/29" + "100/100")
[ ] curl -fsS http://127.0.0.1:8000/health  (with uvicorn running manually first)
[ ] Install systemd unit, enable + start travaux-ia.service
[ ] journalctl -u travaux-ia -n 50    (sanity-check logs)
[ ] Configure nginx vhost, request cert with certbot
[ ] DNS: A record api.example.com -> VM public IP
[ ] Test:
      curl https://api.example.com/health
      curl -X POST https://api.example.com/api/v1/devis/generate \
        -H "Content-Type: application/json" \
        -d '{"text":"Pose carrelage 25 m2 salle de bain"}'
[ ] Tighten CORS, decide on /docs exposure, set up uptime + log alerts
[ ] Schedule pg_dump backup of travauxia_devis (cron / managed snapshot)
```

---

## 12. Quick reference docs already in the repo

- `README.md` — stack overview and original quickstart (slightly stale: mentions OpenAI; the active provider is Scaleway).
- `RUN.md` — full Windows/PowerShell run + verification recipe; the Linux equivalents are essentially the same minus the `psql.exe` paths and the `Start-Service` line.
- `API.md` — public API reference with curl examples, error codes, and timeouts.
- `FRONTEND_API.md` — extra notes for frontend integrators.

---

## 13. Useful follow-ups

If needed, the following can be added next:

- **(a)** A production-grade `Dockerfile` + multi-service `docker-compose.prod.yml` with the API service wired in.
- **(b)** A one-shot bootstrap shell script (`deploy.sh`) that runs the §11 checklist end-to-end.
- **(c)** Tighten CORS and gate `/docs` behind `ENVIRONMENT != "production"`.
