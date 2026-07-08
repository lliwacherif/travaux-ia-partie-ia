# 📋 Documentation du Pipeline de Génération de Devis — Travaux IA

> **Objectif de ce document** : fournir au testeur une vision claire et complète du pipeline de génération de devis, depuis la réception de la requête HTTP jusqu'à la réponse finale, en passant par la communication avec l'API OpenAI. Ce document permet d'évaluer la capacité du système à gérer des **requêtes simultanées (concurrence)**.

---

## 1. Architecture Générale

```
┌──────────────┐     HTTP POST        ┌──────────────────────────┐
│   Client     │ ───────────────────▶ │   FastAPI (Uvicorn)       │
│  (Frontend)  │                      │   Port: 8000              │
└──────────────┘                      │                           │
                                      │  ┌─ Endpoint Sync ──────┐│
                                      │  │ POST /devis/generate  ││
                                      │  └───────────────────────┘│
                                      │  ┌─ Endpoint SSE ────────┐│
                                      │  │ POST /devis/generate/ ││
                                      │  │       stream          ││
                                      │  └───────────────────────┘│
                                      └────────────┬──────────────┘
                                                   │
                        ┌──────────────────────────┼──────────────────────────┐
                        │                          │                          │
                        ▼                          ▼                          ▼
               ┌────────────────┐      ┌─────────────────┐       ┌───────────────────┐
               │  BTP Validator │      │  OpenAI API     │       │  PostgreSQL (DB)  │
               │  (blacklist)   │      │  (gpt-4/4o/5)   │       │       │via asyncpg│
               └────────────────┘      └─────────────────┘       └───────────────────┘
```

**Stack technique** :

| Composant            | Technologie                    |
|----------------------|--------------------------------|
| Framework web        | FastAPI ≥ 0.131                |
| Serveur ASGI         | Uvicorn ≥ 0.32                 |
| LLM Provider         | OpenAI API (modèle `gpt-4/5`)   |
| Base de données      | PostgreSQL (asyncpg, async)    |
| ORM                  | SQLAlchemy 2.x (async)         |
| Validation données   | Pydantic v2                    |
| Parsing JSON défensif| json-repair                    |

---

## 2. Les Deux Endpoints de Génération de Devis

### 2.1 `POST /api/v1/devis/generate` — Mode Synchrone

- **Body** : `{ "text": "description libre des travaux" }`
- **Réponse** : un objet `DevisResponse` complet (JSON)
- **Usage** : le client envoie la requête, attend la réponse complète

### 2.2 `POST /api/v1/devis/generate/stream` — Mode Streaming (SSE)

- **Body** : identique (même schéma `GenerateRequest`)
- **Réponse** : flux de **Server-Sent Events** (SSE)
- **Usage** : le client reçoit des événements de progression en temps réel

**Format des événements SSE** :

```
event: progress
data: {"step": 1, "total": 4, "label": "Analyse"}

event: progress
data: {"step": 2, "total": 4, "label": "Generate"}

event: progress
data: {"step": 3, "total": 4, "label": "Calculate"}

event: progress
data: {"step": 4, "total": 4, "label": "Finalise"}

event: result
data: { ...DevisResponse JSON complet... }

event: done
data: {}
```

En cas d'erreur :

```
event: error
data: {"status": 400, "detail": "...message d'erreur..."}

event: done
data: {}
```

---

## 3. Pipeline Détaillé — Étape par Étape

Le pipeline est exécuté dans la méthode `_run_pipeline()` du service `AIService`. Voici chaque étape :

### Étape 1 — Validation BTP (Guardrail)

```
Requête utilisateur  ──▶  btp_validator.validate_btp_context()
```

- **Quoi** : vérifie que le texte utilisateur est bien lié au **BTP** (Bâtiment et Travaux Publics)
- **Comment** : recherche de mots d'une **blacklist** (pizza, voiture, voyage, etc.)
- **Résultat** :
  - ✅ Passe → on continue
  - ❌ Mot hors-BTP détecté → `HTTP 400` immédiat, **pas d'appel à OpenAI**
- **Impact concurrence** : ⚡ **très rapide** (regex local, aucun I/O réseau)

### Étape 2 — Chargement du Catalogue (Base de Données)

```
PostgreSQL ──▶ get_cached_price_map(db)
           ──▶ get_cached_packs_map(db)
```

- **Quoi** : charge les prix de référence (BPU) et les packs de travaux depuis PostgreSQL
- **Comment** : requêtes asynchrones via `asyncpg` + **cache en RAM** après le premier appel
- **Résultat** : `price_map`, `concept_map`, `exact_map`, `pack_list`
- **Impact concurrence** : ⚡ **rapide** — le cache RAM fait que seul le **premier appel** touche la DB

### Étape 3 — Appel à l'API OpenAI (Stage IA)

```
                  ┌──────────────────────────────┐
                  │     OpenAI Chat Completions   │
user_text   ──▶   │     Modèle: gpt-4/5             │   ──▶  JSON structuré
catalog_str ──▶   │     max_tokens: 8192           │
                  │     response_format: json      │
                  │     temperature: 1              │
                  └──────────────────────────────┘
```

**Détails de l'appel OpenAI** :

| Paramètre               | Valeur                                  |
|--------------------------|-----------------------------------------|
| Modèle                   | `gpt-4` (configurable via `.env`)       |
| `max_completion_tokens`  | 8192                                    |
| `temperature`            | 1                                       |
| `top_p`                  | 1                                       |
| `presence_penalty`       | 0                                       |
| `stream`                 | `false`                                 |
| `response_format`        | `json_schema` (Structured Outputs)      |

**Prompt système** : le `SYSTEM_PROMPT_GENERATOR` injecte dynamiquement le catalogue des packs disponibles (`{catalog}`) pour que l'IA choisisse parmi les packs existants.

**Sortie de l'IA** : un JSON strict contenant :

```json
{
  "client_type": "pro | particulier",
  "project_nature": "neuf | renovation",
  "lots": [
    {
      "lot_key": "LOT_01",
      "metier": "CARRELAGE",
      "zone": "interieur | exterieur",
      "packs": [
        { "id": "PACK_CARRELAGE_SOL", "type": "PRESTATION", "quantite": 25 }
      ]
    }
  ]
}
```

- **Impact concurrence** : 🐢 **L'ÉTAPE LA PLUS LENTE** — typiquement **5 à 30 secondes** selon la complexité. C'est le **goulot d'étranglement principal** du pipeline.

### Étape 4 — Parsing + Healing du JSON

```
raw (string) ──▶ clean_and_parse_json() ──▶ parsed (dict)
```

- **Quoi** : parse le JSON retourné par l'IA, avec réparation automatique si mal formé
- **Bibliothèque** : `json-repair` pour les cas de JSON tronqué/invalide
- **Impact concurrence** : ⚡ instantané (CPU local)

### Étape 5 — Moteur Déterministe de Calcul

```
parsed (lots de l'IA)  ──▶  process_ai_lots()  ──▶  4 blocs structurés
                       ──▶  calculate_global_totals() ──▶  montant_ttc
```

- **Quoi** : transforme les lots de l'IA en blocs chiffrés avec lignes de facturation
- **Comment** : résolution des prix BPU, application des règles métier, calcul HT/TTC/TVA
- **Résultat** : 4 blocs (`blocs`) avec leurs lots et lignes détaillées
- **Impact concurrence** : ⚡ **rapide** (calcul pur en mémoire, pas d'I/O)

### Étape 6 — Construction de la Réponse Finale

```python
devis = {
    "date":        datetime.now(),
    "validite":    datetime.now() + 30 jours,
    "duree":       30,
    "montant_ttc": totals["total_ttc"],
    "blocs":       four_blocks,
}
```

- **Validation** : le dict est validé contre le schéma Pydantic `DevisResponse`
- **Structure** : `DevisResponse` → `Bloc[]` → `Lot[]` → `Ligne[]`

---

## 4. Diagramme de Séquence Complet

```
Client                 FastAPI              BTP Validator      PostgreSQL        OpenAI API         Prestations Engine
  │                      │                      │                 │                  │                    │
  │── POST /devis/generate ──▶                  │                 │                  │                    │
  │                      │                      │                 │                  │                    │
  │                      │── validate_btp() ───▶│                 │                  │                    │
  │                      │◀── OK ──────────────│                 │                  │                    │
  │                      │                      │                 │                  │                    │
  │                      │── get_cached_price_map() ─────────────▶│                  │                    │
  │                      │◀── price_map (cache) ──────────────────│                  │                    │
  │                      │                      │                 │                  │                    │
  │                      │── get_cached_packs_map() ─────────────▶│                  │                    │
  │                      │◀── packs_map (cache) ──────────────────│                  │                    │
  │                      │                      │                 │                  │                    │
  │                      │── chat.completions.create() ──────────────────────────────▶│                    │
  │                      │                      │                 │   (5-30 sec)      │                    │
  │                      │◀── JSON structuré ────────────────────────────────────────│                    │
  │                      │                      │                 │                  │                    │
  │                      │── clean_and_parse_json() ──▶ (local)   │                  │                    │
  │                      │                      │                 │                  │                    │
  │                      │── process_ai_lots() ──────────────────────────────────────────────────────────▶│
  │                      │◀── 4 blocs chiffrés ──────────────────────────────────────────────────────────│
  │                      │                      │                 │                  │                    │
  │                      │── calculate_global_totals() ──▶ (local)│                  │                    │
  │                      │                      │                 │                  │                    │
  │◀── DevisResponse (JSON) ─│                  │                 │                  │                    │
  │                      │                      │                 │                  │                    │
```

---

## 5. Analyse de la Concurrence (Requêtes Simultanées)

### 5.1 Ce qui fonctionne bien en concurrence

| Composant              | Mode              | Goulot ? | Explication                                                          |
|------------------------|-------------------|----------|----------------------------------------------------------------------|
| **FastAPI / Uvicorn**  | Async (asyncio)   | ❌ Non   | Gère nativement des milliers de connexions simultanées               |
| **BTP Validator**      | Sync (regex)      | ❌ Non   | Exécution < 1ms, pas d'I/O                                          |
| **PostgreSQL (asyncpg)** | Async + pool    | ❌ Non   | Pool de `10` connexions + `20` overflow, avec cache RAM              |
| **Prestations Engine** | Sync (calcul)     | ❌ Non   | Calcul CPU pur en mémoire, pas de blocage                           |
| **JSON Parsing**       | Sync (CPU)        | ❌ Non   | Instantané                                                           |

### 5.2 Le goulot d'étranglement principal : l'API OpenAI

| Aspect                 | Détail                                                                            |
|------------------------|-----------------------------------------------------------------------------------|
| **Appel réseau**       | Chaque requête fait **1 appel HTTP** vers `api.openai.com`                        |
| **Latence**            | **5 à 30 secondes** par appel (dépend de la complexité et de la charge OpenAI)    |
| **Client HTTP**        | `AsyncOpenAI` — les appels sont **non-bloquants** (`await`)                       |
| **Singleton**          | Un seul `AIService` instance avec un seul client `AsyncOpenAI` (pool httpx interne)|
| **Rate limits OpenAI** | Limites par tier (TPM, RPM) — ex: Tier 1 = ~500 RPM, Tier 5 = ~10 000 RPM        |

### 5.3 Comment FastAPI gère les requêtes simultanées

```
Requête A ──▶  [Validation] ──▶ [DB Cache] ──▶ [AWAIT OpenAI ════════════] ──▶ [Calcul] ──▶ Réponse A
Requête B ──▶  [Validation] ──▶ [DB Cache] ──▶ [AWAIT OpenAI ════════════] ──▶ [Calcul] ──▶ Réponse B
Requête C ──▶  [Validation] ──▶ [DB Cache] ──▶ [AWAIT OpenAI ════════════] ──▶ [Calcul] ──▶ Réponse C
                                                     ▲
                                                     │
                                      Pendant que A attend OpenAI,
                                      B et C démarrent en parallèle
                                      grâce à asyncio (pas de blocage)
```

**Mécanisme** :

1. Tous les endpoints sont déclarés `async def` → exécutés dans la **boucle asyncio** de Uvicorn
2. L'appel OpenAI est `await self._client.chat.completions.create(...)` → **non-bloquant**
3. Pendant qu'une requête attend la réponse d'OpenAI, le serveur peut traiter d'autres requêtes
4. Le pool de connexions DB (`pool_size=10`, `max_overflow=20`) permet jusqu'à **30 sessions DB simultanées**

### 5.4 Limites et Points de Vigilance pour les Tests

| Risque                           | Impact                                              | Seuil estimé                    |
|----------------------------------|-----------------------------------------------------|---------------------------------|
| **Rate limit OpenAI**            | HTTP 429 côté OpenAI → `AIServiceError` → HTTP 503  | Dépend du tier OpenAI           |
| **Token quota OpenAI (TPM)**     | Refus si dépassement tokens/minute                   | Dépend du tier OpenAI           |
| **Pool DB épuisé**               | Timeout si > 30 requêtes simultanées sur la DB       | ~30 connexions max              |
| **Mémoire serveur**              | Chaque requête en vol consomme RAM pour le contexte   | Dépend de la RAM disponible     |
| **Uvicorn single-worker**        | 1 worker = 1 boucle asyncio = 1 thread               | OK pour async, limité en CPU    |
| **Timeout réseau OpenAI**        | Si OpenAI met > 60s, le client peut timeout           | Configurable côté httpx         |

### 5.5 Recommandations pour les Tests de Charge

#### Scénario 1 : Test de base (5-10 requêtes simultanées)

```bash
# Envoyer 10 requêtes en parallèle
for i in $(seq 1 10); do
  curl -X POST http://localhost:8000/api/v1/devis/generate \
    -H "Content-Type: application/json" \
    -d '{"text": "rénovation salle de bain 15m2 avec carrelage et plomberie"}' &
done
wait
```

**Résultat attendu** : les 10 requêtes devraient toutes réussir, en ~5-30 secondes chacune.

#### Scénario 2 : Test de stress (50+ requêtes simultanées)

**Résultat attendu** : les premières requêtes réussissent, mais au-delà du rate limit OpenAI, certaines retourneront HTTP 503 (`AI provider unavailable`).

#### Scénario 3 : Test SSE (streaming)

Vérifier que les événements `progress` arrivent en temps réel pendant que le pipeline s'exécute, même sous charge.

#### Ce qu'il faut surveiller :

- ✅ Les requêtes se traitent-elles en parallèle ? (les temps ne doivent **pas** être séquentiels)
- ✅ Les réponses sont-elles correctes et complètes sous charge ?
- ✅ Les erreurs OpenAI sont-elles correctement remontées en HTTP 503 ?
- ✅ Le pool DB ne bloque-t-il pas ?
- ✅ Le streaming SSE continue-t-il de fonctionner sous charge ?

---

## 6. Gestion des Erreurs

| Exception                    | Code HTTP | Cause                                        |
|------------------------------|-----------|----------------------------------------------|
| `InvalidBuildingRequestError`| 400       | Texte hors-BTP détecté par le validateur      |
| `JSONHealingError`           | 502       | JSON de l'IA impossible à parser/réparer      |
| `UnrepairableDevisError`     | 502       | Devis tronqué irrécupérable                   |
| `ValidationError` (Pydantic) | 502       | JSON valide mais ne match pas `DevisResponse` |
| `AIServiceError`             | 503       | Erreur réseau/quota/timeout vers OpenAI       |

---

## 7. Fichiers Clés du Pipeline

| Fichier                                        | Rôle                                              |
|------------------------------------------------|---------------------------------------------------|
| `app/main.py`                                  | Point d'entrée FastAPI, enregistrement des routers |
| `app/api/routers/devis.py`                     | Endpoints HTTP `/devis/generate` et `/devis/generate/stream` |
| `app/services/ai_service.py`                   | Service IA : appel OpenAI, pipeline complet        |
| `app/services/prestations_engine.py`           | Moteur déterministe de calcul des prix             |
| `app/core/btp_validator.py`                    | Validateur BTP (blacklist)                         |
| `app/core/prompts.py`                          | Prompts système envoyés à OpenAI                   |
| `app/core/config.py`                           | Configuration (clés API, modèle, pool DB)          |
| `app/schemas/devis.py`                         | Schéma Pydantic de la réponse (`DevisResponse`)    |
| `app/db/database.py`                           | Engine async + pool de connexions PostgreSQL        |
| `app/core/utils.py`                            | Parsing JSON défensif (`clean_and_parse_json`)      |

---

## 8. Résumé pour le Testeur

> **La FastAPI peut-elle gérer plusieurs requêtes simultanées ?**

**OUI**, grâce à :
1. **Asyncio natif** — tous les endpoints et appels I/O sont `async`/`await`
2. **Client OpenAI async** — `AsyncOpenAI` utilise `httpx` en mode async
3. **Pool DB async** — `asyncpg` avec pool de 10+20 connexions
4. **Cache en mémoire** — les catalogues/prix sont chargés une seule fois

**MAIS**, les limites viennent de :
1. **L'API OpenAI** — rate limits (RPM/TPM) et latence (5-30s par appel)
2. **Le pool DB** — limité à 30 connexions simultanées
3. **Un seul worker Uvicorn** par défaut — pour du CPU-bound, ajouter `--workers N`

> Pour les tests : concentrer l'analyse sur le **temps de réponse sous charge** et la **gestion des erreurs OpenAI (429/503)** lors de requêtes simultanées.
