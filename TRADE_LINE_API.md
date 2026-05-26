# Trade-Line API — Frontend Cheat Sheet

Single endpoint that returns a **dynamic list** of representative billable
prestations for a given corps de métier (e.g. `"Peinture"`). Powers the
"choisir une prestation" picker on the frontend (one card = one option
the user can add to their devis).

Powered by Scaleway `gpt-oss-120b`, grounded on the `trades` and
`trade_services` tables.

**Open endpoint — no auth required.**

## Endpoint

| Method | Path                              | Returns                                  |
| ------ | --------------------------------- | ---------------------------------------- |
| POST   | `/api/v1/trade-line/generate`     | One JSON `TradeLineResponse` (~5–20 s).  |

Base URL is the same as the rest of the API. If you hit it through ngrok,
keep the `ngrok-skip-browser-warning: true` header.

## Request

```json
{ "job_corp": "Peinture" }
```

Optional `limit`:

```json
{ "job_corp": "Peinture", "limit": 20 }
```

| Field      | Type   | Constraints                                      | Default |
| ---------- | ------ | ------------------------------------------------ | ------- |
| `job_corp` | string | required, 1–255 chars, free-form French          | —       |
| `limit`    | int    | optional, 1–30                                   | `12`    |

`job_corp` matching is **fuzzy** (case-insensitive substring on
`Trade.name`, `Trade.description`, `Trade.category`, `Trade.subcategory`,
`TradeService.designation`, `TradeService.description`,
`TradeService.category`). So `"Peinture"`, `"peinture"`, `"Plomberie"`,
`"électricité"`, etc. all resolve correctly even if the literal trade
name is something else (e.g. `"Peinture"` → `Revêtements murs`).

## Response — `TradeLineResponse`

```json
{
  "job_corp": "Peinture",
  "count": 5,
  "items": [
    {
      "job_corp": "Peinture",
      "description": "Application de badigeon de chaux pigmentée, 2 couches",
      "unit": "m2",
      "pu": 18,
      "tva": 10
    },
    {
      "job_corp": "Peinture",
      "description": "Peinture acrylique mate blanche murs intérieurs, 2 couches",
      "unit": "m2",
      "pu": 24,
      "tva": 10
    },
    {
      "job_corp": "Peinture",
      "description": "Enduit de lissage murs avant peinture",
      "unit": "m2",
      "pu": 12,
      "tva": 10
    },
    {
      "job_corp": "Peinture",
      "description": "Peinture décorative effet velours, 2 couches",
      "unit": "m2",
      "pu": 38,
      "tva": 10
    },
    {
      "job_corp": "Peinture",
      "description": "Création de faux plafond décoratif suspendu",
      "unit": "m2",
      "pu": 65,
      "tva": 10
    }
  ]
}
```

| Field      | Type                   | Notes                                           |
| ---------- | ---------------------- | ----------------------------------------------- |
| `job_corp` | string                 | Echoes the request verbatim.                    |
| `count`    | int                    | `== items.length`. Convenience field.           |
| `items`    | `TradeLineItem[]`      | Between 1 and `limit` distinct prestations.     |

### `TradeLineItem`

| Field         | Type             | Notes                                                 |
| ------------- | ---------------- | ----------------------------------------------------- |
| `job_corp`    | string           | Same `job_corp` for every item (mirrors the request). |
| `description` | string           | Short French label (6–25 words).                      |
| `unit`        | string           | `"m2"`, `"ml"`, `"u"`, `"forfait"`, …                 |
| `pu`          | number           | Reference unit price, **HT** (excl. tax), in euros.   |
| `tva`         | `5.5 \| 10 \| 20`| French VAT rate. `10` is the default (rénovation).    |

### TypeScript

```ts
export type TradeLineItem = {
  job_corp: string;
  description: string;
  unit: string;
  pu: number;
  tva: 5.5 | 10 | 20;
};

export type TradeLineResponse = {
  job_corp: string;
  count: number;
  items: TradeLineItem[];
};

export async function generateTradeLine(
  jobCorp: string,
  baseUrl: string,
  limit?: number,
): Promise<TradeLineResponse> {
  const r = await fetch(`${baseUrl}/api/v1/trade-line/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
    },
    body: JSON.stringify({ job_corp: jobCorp, ...(limit ? { limit } : {}) }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
```

## How it works (under the hood)

```
job_corp ──▶ fuzzy ilike on trades + trade_services
                       │
                       ▼
              build RAG bibliothèque (≤ 2 × limit rows)
                       │
                       ▼
       TRADE_LINE_PROMPT + job_corp + limit ──▶ Scaleway gpt-oss-120b
                       │
                       ▼
     JSON heal + parse + normalise + Pydantic validate
                       │
                       ▼
                TradeLineResponse  (job_corp, count, items[])
```

1. **Catalog lookup** — `build_trade_line_context()` scopes the catalog
   to the corps de métier with a permissive ilike across multiple
   columns. The fetched window is sized to ~`2 × limit` so the model has
   breadth to generate `limit` distinct items without repeating itself.
2. **One AI call** — single shot, single stage. No retry loop, no
   streaming.
3. **Prompt rules** — the model is asked to reuse catalog designations
   verbatim first, then complement with standard prestations from its
   2025 pricing matrix to reach `limit`. No duplicates, no trivial
   variants.
4. **VAT rules** baked into the prompt: `5.5` for isolation /
   énergétique, `20` for neuf / B2B, `10` (rénovation) by default.
5. If the input isn't a building trade (e.g. `"voyage"`), the model
   returns `{"isValidBuildingRequest": false, ...}` and the API
   answers **HTTP 400**.

## Errors

| Status | Meaning                                                  |
| ------ | -------------------------------------------------------- |
| 400    | `job_corp` is not a recognised building trade.           |
| 422    | Body invalid (empty `job_corp`, `limit` out of bounds…). |
| 502    | AI returned something we couldn't parse / validate.      |
| 503    | AI provider unreachable.                                 |

## Quick test

### curl

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/trade-line/generate" \
  -H "Content-Type: application/json" \
  -d '{"job_corp":"Peinture"}'

# With explicit limit
curl -X POST "http://127.0.0.1:8000/api/v1/trade-line/generate" \
  -H "Content-Type: application/json" \
  -d '{"job_corp":"Peinture","limit":20}'
```

### Through ngrok

```bash
curl -X POST "https://<your-ngrok-subdomain>.ngrok-free.app/api/v1/trade-line/generate" \
  -H "Content-Type: application/json" \
  -H "ngrok-skip-browser-warning: true" \
  -d '{"job_corp":"Peinture","limit":15}'
```

### PowerShell

```powershell
$body = @{ job_corp = "Peinture"; limit = 15 } | ConvertTo-Json
Invoke-RestMethod `
  -Uri  "http://127.0.0.1:8000/api/v1/trade-line/generate" `
  -Method POST `
  -Body $body `
  -ContentType "application/json; charset=utf-8" `
  -TimeoutSec 60 `
  | ConvertTo-Json -Depth 5
```

### Swagger

`POST /api/v1/trade-line/generate` is also available at
<http://127.0.0.1:8000/docs> with a **Try it out** button (under the
**trade-line** tag).

## Notes

- A call typically takes **5–20 s** (one LLM round-trip; longer if you
  ask for `limit=30`). Use a 60 s timeout on the client.
- Output is in French (descriptions reuse catalog designations when
  possible, then are complemented with standard prestations).
- `pu` is **HT** (hors taxes). Multiply by `(1 + tva / 100)` to get the
  TTC price.
- The number of items returned is **dynamic**: the model targets
  `limit` but may return fewer if the catalog + its expertise can't
  produce that many distinct, non-trivial prestations for that trade.
- `count` is always equal to `items.length` — provided as a convenience
  for the frontend.
