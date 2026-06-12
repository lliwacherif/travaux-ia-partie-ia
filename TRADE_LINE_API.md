# Trade-Line API — Frontend Cheat Sheet

Single endpoint that returns a **dynamic list** of representative billable
prestations for a given corps de métier (e.g. `"Peinture"`). Powers the
"choisir une prestation" picker on the frontend (one card = one option
the user can add to their devis).

Powered primarily by the `trades` and `trade_services` tables. The model is
used only as a fallback when the catalog has no matching prestations.

**Open endpoint — no auth required.**

## Endpoint

| Method | Path                              | Returns                                  |
| ------ | --------------------------------- | ---------------------------------------- |
| POST   | `/api/v1/trade-line/generate`     | One JSON `TradeLineResponse` (catalog-fast; AI fallback only). |

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
              build response-ready catalog items
                       │
                       ▼
        catalog hit? ── yes ──▶ Pydantic validate
             │
             no
             ▼
       compact fallback prompt ──▶ model
             │
             ▼
      JSON heal + parse + normalise
                       │
                       ▼
                TradeLineResponse  (job_corp, count, items[])
```

1. **Catalog lookup** — `build_trade_line_items()` scopes the catalog
   to the corps de métier with a permissive ilike across multiple
   columns and returns response-ready items directly.
2. **Fast path** — when catalog rows exist, there is no model call. This
   keeps the picker endpoint responsive and deterministic.
3. **Fallback AI call** — only when the catalog has no match, the endpoint
   builds a compact prompt and asks the model to reject non-BTP input or
   propose generic trade prestations.
4. **Price rules** — catalog-backed rows use `estimated_price` when it is
   greater than zero; otherwise the API uses deterministic BTP reference
   prices by trade/unit so the frontend does not receive `pu: 0`.
5. **VAT rules** — catalog-backed rows use `5.5` for isolation /
   énergétique keywords and `10` (rénovation) by default. The fallback
   prompt keeps the same VAT contract.
6. If fallback classifies the input as non-building (e.g. `"voyage"`), the
   API answers **HTTP 400**.

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

- Catalog matches return without an LLM round-trip and should feel like a
  normal database-backed API call. Keep a longer client timeout only for rare
  fallback cases where the catalog has no match.
- Output is in French (descriptions reuse catalog designations when
  possible, then are complemented with standard prestations).
- `pu` is **HT** (hors taxes). Multiply by `(1 + tva / 100)` to get the
  TTC price.
- The number of items returned is **dynamic**: the API returns up to `limit`
  distinct catalog prestations, or fewer if the catalog/fallback cannot
  produce that many useful options.
- `count` is always equal to `items.length` — provided as a convenience
  for the frontend.
