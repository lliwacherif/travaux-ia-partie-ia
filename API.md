# Travaux-IA API Reference

External-facing reference for the Devis Generation API, with ready-to-run
`curl` commands.

- **Base URL (dev):** `http://127.0.0.1:8000`
- **Content-Type:** `application/json; charset=utf-8`
- **Auth:** none at the moment (to be added)
- **Interactive docs:** [Swagger](http://127.0.0.1:8000/docs) • [ReDoc](http://127.0.0.1:8000/redoc)
- **OpenAPI spec:** `GET /api/v1/openapi.json`

> **Windows PowerShell note.** `curl` is an *alias* for `Invoke-WebRequest`,
> which uses a different syntax. Always invoke real curl as **`curl.exe`** on
> Windows. Examples below use plain `curl` and work as-is on macOS, Linux,
> Git Bash, and WSL. On native PowerShell just substitute `curl.exe`.

---

## Endpoints

| Method | Path                       | Description                                      |
| ------ | -------------------------- | ------------------------------------------------ |
| GET    | `/health`                  | Liveness probe                                   |
| POST   | `/api/v1/devis/generate`   | Generate a structured devis from free-form text  |

---

## 1. `GET /health`

Simple JSON liveness check. No auth, no body.

### curl

```bash
curl -i http://127.0.0.1:8000/health
```

### Response (200)

```json
{
  "status": "ok",
  "service": "Devis Generation API",
  "version": "0.1.0",
  "environment": "development"
}
```

---

## 2. `POST /api/v1/devis/generate`

Takes a raw French (or any language) description of a construction job and
returns a structured devis computed by the two-stage AI pipeline
(routing → generation) grounded on the `trades` / `trade_services` catalog.

### Request body

```json
{ "text": "string (required, min_length=1)" }
```

### curl — minimal

```bash
curl -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"text":"Refaire l'\''electricite d'\''un T3 de 65m2, tableau + 12 prises"}'
```

> **Escaping apostrophes in Bash:** `'\''` closes the single-quoted string,
> inserts a literal `'`, then reopens the quoting. Double-quote strings work
> too if you escape the inner double quotes:
> `-d "{\"text\":\"Your text here\"}"`.

### curl — show status + headers + body

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"text":"Pose de parquet massif 40 m2 dans un salon + plinthes"}'
```

### curl — save the response to a file

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"text":"Remplacer une chaudiere gaz 24 kW dans une maison individuelle"}' \
  -o devis.json

# Optional: pretty-print with jq
jq . devis.json
```

### curl — read the body from a file (avoids quoting hell)

```bash
# First, create the payload on disk:
cat > payload.json <<'EOF'
{
  "text": "Ravalement façade enduit taloché 120 m2, nettoyage haute pression préalable inclus."
}
EOF

curl -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  --data-binary @payload.json
```

### curl — fail on any non-2xx status

Handy in CI:

```bash
curl --fail --silent --show-error \
  -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"text":"Installation VMC double flux maison 120 m2"}'
```

### Successful response (200)

Simplified example (real output is longer and depends on the prompt):

```json
{
  "date": "2026-04-23T10:00:00+02:00",
  "montant_ttc": 1527.0,
  "validite": "2026-05-23T23:59:59+02:00",
  "duree": 30,
  "blocs": [
    {
      "title": "Électricité",
      "lots": [
        {
          "title": "Remise en état tableau électrique",
          "ligne_ids": ["E01"],
          "lignes": [
            {
              "num": 1,
              "description": "Fourniture + remise en état de tableau électrique (fusibles, disjoncteurs, bornes)",
              "qte": 1.0,
              "unit": "forfait",
              "pu": 500.0,
              "tva": 20.0,
              "ht": 500.0,
              "ttc": 600.0
            }
          ]
        }
      ]
    }
  ]
}
```

Every `lignes[].description` is copied verbatim from a row of the
`trade_services` table — that's the RAG grounding in action.

### Error responses

| Status | Body shape                                    | When                                                                                   |
| ------ | --------------------------------------------- | -------------------------------------------------------------------------------------- |
| 400    | `{ "detail": "Not a valid building request: …" }` | Stage 1 classified the prompt as out-of-scope.                                      |
| 422    | `{ "detail": [ { "loc": […], "msg": "…" } ] }`    | Pydantic rejected the request body (e.g. missing / empty `text`).                    |
| 502    | `{ "detail": "AI returned a JSON that does not match DevisResponse." }` | LLM output can't be healed / doesn't match the schema. Retry — generation is non-det. |
| 503    | `{ "detail": "AI provider unavailable: …" }`  | Scaleway API unreachable, 5xx'd, or invalid `SCALEWAY_API_KEY`.                      |

Example of the 400 rejection:

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  -d '{"text":"What is the capital of France?"}'
```

---

## Calling the API from another machine on the LAN

By default the server binds to `127.0.0.1` (loopback only). To let another
machine on the local network reach it, start it on all interfaces:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Then from the other machine:

```bash
curl http://<host-ip>:8000/health
```

Replace `<host-ip>` with the output of `ipconfig` (Windows) or
`ip addr` (Linux) on the server. Open TCP 8000 in the Windows firewall
(`New-NetFirewallRule -DisplayName "travaux-ia 8000" -Direction Inbound
-Protocol TCP -LocalPort 8000 -Action Allow`).

---

## Non-curl examples (for client libraries)

### Python (`requests`)

```python
import requests

response = requests.post(
    "http://127.0.0.1:8000/api/v1/devis/generate",
    json={"text": "Peinture murs 2 couches 35 m2"},
    timeout=120,
)
response.raise_for_status()
devis = response.json()
print(devis["montant_ttc"], "€ TTC")
```

### Python (`httpx`, async)

```python
import asyncio
import httpx


async def main() -> None:
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=120) as c:
        r = await c.post(
            "/api/v1/devis/generate",
            json={"text": "Pose carrelage grès cérame 25 m2 salle de bain"},
        )
        r.raise_for_status()
        print(r.json())


asyncio.run(main())
```

### JavaScript (Node 18+ / browser `fetch`)

```javascript
const res = await fetch("http://127.0.0.1:8000/api/v1/devis/generate", {
  method: "POST",
  headers: { "Content-Type": "application/json; charset=utf-8" },
  body: JSON.stringify({
    text: "Création d'une terrasse bois exotique 30 m2 sur plots",
  }),
});

if (!res.ok) {
  throw new Error(`HTTP ${res.status}: ${await res.text()}`);
}
const devis = await res.json();
console.log(devis);
```

### PowerShell native (no curl)

```powershell
$body = @{ text = "Pose cloisons placo + isolation 40 m2" } | ConvertTo-Json

Invoke-RestMethod `
  -Uri    "http://127.0.0.1:8000/api/v1/devis/generate" `
  -Method POST `
  -Body   $body `
  -ContentType "application/json; charset=utf-8" `
  -TimeoutSec 120
```

---

## Useful curl flags cheat-sheet

| Flag                 | What it does                                                 |
| -------------------- | ------------------------------------------------------------ |
| `-i`                 | Include response headers in the output                       |
| `-v`                 | Verbose: show the full request/response including TLS handshake |
| `-s`                 | Silent (no progress bar)                                     |
| `--show-error`       | Pair with `-s` so real errors still print                    |
| `--fail`             | Exit code != 0 on any 4xx/5xx (CI-friendly)                  |
| `-o FILE`            | Write body to a file                                         |
| `-w "%{http_code}\n"`| Print an arbitrary value (here: the status code) at the end  |
| `-H "Header: value"` | Add a header                                                 |
| `-d '...'`           | Request body (sets `Content-Type: application/x-www-form-urlencoded` by default) |
| `--data-binary @f`   | Send the exact bytes of file `f` as the body                 |
| `--max-time 120`     | Client-side timeout (seconds)                                |

Example combining several:

```bash
curl -s -o devis.json -w "HTTP %{http_code}  in %{time_total}s\n" \
  -X POST http://127.0.0.1:8000/api/v1/devis/generate \
  -H "Content-Type: application/json; charset=utf-8" \
  --max-time 120 \
  -d '{"text":"Installation climatisation split 3.5 kW chambre"}'
```

Output:
```
HTTP 200  in 12.37s
```
