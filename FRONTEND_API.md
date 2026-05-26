# Devis API — Frontend Cheat Sheet

## Base URL

```
https://<your-ngrok-subdomain>.ngrok-free.app
```

⚠️ **Add this header on every request** (ngrok free tier returns HTML otherwise):

```
ngrok-skip-browser-warning: true
```

## Endpoints

| Method | Path                              | Returns                                               |
| ------ | --------------------------------- | ----------------------------------------------------- |
| POST   | `/api/v1/devis/generate`          | One JSON `DevisResponse` after ~10–30 s.              |
| POST   | `/api/v1/devis/generate/stream`   | SSE stream: 4 progress events, then the same JSON.    |
| GET    | `/health`                         | `{ "status": "ok", ... }`                             |

Both `generate` endpoints accept the same body and return the same `DevisResponse`. Pick streaming if you want a live progress UI.

## Request body

```json
{ "text": "Refaire l'electricite d'un T3 de 65m2" }
```

`text` is required, min 1 char. Free-form French.

## Response (`DevisResponse`)

```json
{
  "date": "2026-05-05T10:00:00+02:00",
  "montant_ttc": 1527.0,
  "validite": "2026-06-05T23:59:59+02:00",
  "duree": 30,
  "blocs": [
    {
      "title": "Électricité",
      "lots": [
        {
          "title": "Tableau électrique",
          "ligne_ids": ["E01"],
          "lignes": [
            {
              "num": 1,
              "description": "Fourniture + remise en état de tableau électrique",
              "qte": 1,
              "unit": "forfait",
              "pu": 500,
              "tva": 20,
              "ht": 500,
              "ttc": 600
            }
          ]
        }
      ]
    }
  ]
}
```

## TypeScript types

```ts
export type Ligne = {
  num: number;
  description: string;
  qte: number;
  unit: string;
  pu: number;
  tva: number;        // 5.5 | 10 | 20
  ht: number;
  ttc: number;
};

export type Lot = {
  title: string;
  ligne_ids?: string[] | null;
  lignes: Ligne[];
};

export type Bloc = { title: string; lots: Lot[] };

export type DevisResponse = {
  date: string;        // ISO 8601
  montant_ttc: number;
  validite: string;    // ISO 8601
  duree: number;       // days
  blocs: Bloc[];
};
```

## Calling it — JSON endpoint (simple)

```ts
const r = await fetch(`${BASE_URL}/api/v1/devis/generate`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "ngrok-skip-browser-warning": "true",
  },
  body: JSON.stringify({ text }),
});
if (!r.ok) throw new Error(`HTTP ${r.status}`);
const devis: DevisResponse = await r.json();
```

## Calling it — streaming endpoint (with progress)

The streaming endpoint sends these events in order:

```
event: progress    data: {"step":1,"total":4,"label":"Analyse"}
event: progress    data: {"step":2,"total":4,"label":"Generate"}
event: progress    data: {"step":3,"total":4,"label":"Calculate"}
event: progress    data: {"step":4,"total":4,"label":"Finalise"}
event: result      data: { ...DevisResponse... }
event: done        data: {}
```

On failure, `result` is replaced by `error`:

```
event: error       data: {"status":400,"detail":"Not a valid building request: ..."}
event: done        data: {}
```

### Reusable client (browser & Node 18+)

```ts
export async function generateDevisStream(
  text: string,
  on: {
    progress: (e: { step: number; total: number; label: string }) => void;
    result:   (devis: DevisResponse) => void;
    error:    (e: { status: number; detail: string }) => void;
  },
) {
  const r = await fetch(`${BASE_URL}/api/v1/devis/generate/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "ngrok-skip-browser-warning": "true",
      "Accept": "text/event-stream",
    },
    body: JSON.stringify({ text }),
  });
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) !== -1) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let evt = "message", data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) evt = line.slice(6).trim();
        else if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (evt === "progress") on.progress(payload);
      else if (evt === "result") on.result(payload.data);
      else if (evt === "error")  on.error(payload);
      // event: done -> ignore, loop ends naturally
    }
  }
}
```

### Usage in React

```tsx
const [step,  setStep]  = useState(0);
const [label, setLabel] = useState("");
const [devis, setDevis] = useState<DevisResponse | null>(null);
const [err,   setErr]   = useState<string | null>(null);

await generateDevisStream(text, {
  progress: ({ step, label }) => { setStep(step); setLabel(label); },
  result:   (d) => setDevis(d),
  error:    ({ detail }) => setErr(detail),
});
```

## Errors

| Status | Meaning                                                |
| ------ | ------------------------------------------------------ |
| 400    | Not a building-related request (out of scope).         |
| 422    | Body invalid (e.g. empty `text`).                      |
| 502    | AI returned something we couldn't fix (very rare now). |
| 503    | AI provider unreachable.                               |

For the streaming endpoint, the HTTP status is **always 200**; check the `event: error` frame for the actual failure.

## Notes

- Each call takes **10–30 s** (LLM round-trips). Use timeout ≥ 60 s.
- Output is in French (descriptions come from the catalog).
- `tva` is always one of `5.5`, `10`, or `20` (French VAT brackets, applied per line).
- `duree` is a plain integer, in days (e.g. `30`, not `"30jours"`).

## Devis architecture (bloc / line counts)

Stage 1 of the pipeline classifies the request as `travaux` (works) or
`depannage` (repair) and lists the distinct interventions. The structure
of `blocs` is then **strictly enforced** by the prompt:

### `travaux` — pattern `3 / 14 × K / 3` (K = number of interventions)

| `blocs[i]` | title                          | total lignes |
| ---------- | ------------------------------ | ------------ |
| `[0]`      | "Mise en place et préparation" | **3**        |
| `[1..K]`   | one per intervention           | **14** each  |
| `[K+1]`    | "Finition et nettoyage"        | **3**        |

Examples:
- 1 travail   → `3 / 14 / 3`              (3 blocs, 20 lines)
- 2 travaux   → `3 / 14 / 14 / 3`         (4 blocs, 34 lines)
- 3 travaux   → `3 / 14 / 14 / 14 / 3`    (5 blocs, 48 lines)

### `depannage` — pattern `1 / 3 × K / 1` (K = number of repairs)

| `blocs[i]` | title                   | total lignes |
| ---------- | ----------------------- | ------------ |
| `[0]`      | "Mise en place"         | **1**        |
| `[1..K]`   | one per dépannage       | **3** each   |
| `[K+1]`    | "Finition et nettoyage" | **1**        |

Examples:
- 1 dépannage   → `1 / 3 / 1`             (3 blocs, 5 lines)
- 2 dépannages  → `1 / 3 / 3 / 1`         (4 blocs, 8 lines)
- 3 dépannages  → `1 / 3 / 3 / 3 / 1`     (5 blocs, 11 lines)

> "Total lignes" counts every `Ligne` in the bloc across all its `lots`.
> A bloc may have one or several `lots`; the sum of `lignes` inside the
> bloc is what's enforced. `num` restarts at `1` inside each lot.

## Quick curl test

```bash
curl -X POST "$BASE_URL/api/v1/devis/generate" \
  -H "Content-Type: application/json" \
  -H "ngrok-skip-browser-warning: true" \
  -d '{"text":"Pose carrelage 25m2 salle de bain"}'
```
