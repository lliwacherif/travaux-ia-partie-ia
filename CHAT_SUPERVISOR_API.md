# API Chat Supervisor

Le **Chat Supervisor** est un système de monitoring unifié qui enregistre automatiquement chaque interaction des 3 chatbots (Dashboard, Landing Page, Mobile) en arrière-plan, sans impacter les temps de réponse.

## Fonctionnement

Quand un utilisateur envoie un message à n'importe quel chatbot :

1. Le chatbot génère sa réponse normalement
2. La réponse est envoyée **immédiatement** au client
3. En arrière-plan, le superviseur enregistre :
   - Le message utilisateur + la réponse IA
   - Les tokens consommés (prompt + completion)
   - La source du chatbot (`dashboard`, `landing`, `mobile`)
   - Si c'est une réponse de fallback (erreur IA)

Les données sont stockées dans deux tables PostgreSQL :

| Table | Rôle |
|---|---|
| `chatbot_daily_metrics` | Compteurs agrégés par jour et par source |
| `chatbot_conversations` | Log complet de chaque échange message par message |

---

## Endpoints

Base URL : `/api/v1/chat-supervisor`

### GET `/metrics`

Retourne les métriques journalières de chaque chatbot.

**Paramètres query :**

| Param | Type | Description |
|---|---|---|
| `date` | `YYYY-MM-DD` | Filtrer par jour exact |
| `source` | `string` | `dashboard`, `landing` ou `mobile` |
| `from` | `YYYY-MM-DD` | Début de plage |
| `to` | `YYYY-MM-DD` | Fin de plage |

**Exemple :**
```
GET /api/v1/chat-supervisor/metrics?date=2026-06-25&source=mobile
```

**Réponse :**
```json
[
  {
    "id": "uuid",
    "date": "2026-06-25",
    "chatbot_source": "mobile",
    "total_conversations": 47,
    "total_messages": 94,
    "total_prompt_tokens": 12500,
    "total_completion_tokens": 8300,
    "total_tokens": 20800,
    "total_errors": 2,
    "created_at": "...",
    "updated_at": "..."
  }
]
```

---

### GET `/metrics/summary`

Retourne un résumé agrégé avec le détail par source. Par défaut : les 30 derniers jours.

**Paramètres query :**

| Param | Type | Description |
|---|---|---|
| `from` | `YYYY-MM-DD` | Début de plage |
| `to` | `YYYY-MM-DD` | Fin de plage |

**Exemple :**
```
GET /api/v1/chat-supervisor/metrics/summary?from=2026-06-01&to=2026-06-25
```

**Réponse :**
```json
{
  "date_from": "2026-06-01",
  "date_to": "2026-06-25",
  "total_conversations": 520,
  "total_messages": 1040,
  "total_prompt_tokens": 150000,
  "total_completion_tokens": 98000,
  "total_tokens": 248000,
  "total_errors": 12,
  "breakdown": [
    {
      "chatbot_source": "dashboard",
      "total_conversations": 300,
      "total_tokens": 140000,
      "..."
    },
    {
      "chatbot_source": "landing",
      "..."
    },
    {
      "chatbot_source": "mobile",
      "..."
    }
  ]
}
```

---

### GET `/conversations`

Liste paginée de toutes les conversations enregistrées.

**Paramètres query :**

| Param | Type | Description |
|---|---|---|
| `source` | `string` | Filtrer par chatbot |
| `date` | `YYYY-MM-DD` | Jour exact |
| `from` / `to` | `YYYY-MM-DD` | Plage de dates |
| `page` | `int` | Numéro de page (défaut : 1) |
| `size` | `int` | Éléments par page (défaut : 50, max : 200) |

**Exemple :**
```
GET /api/v1/chat-supervisor/conversations?source=landing&page=1&size=20
```

**Réponse :**
```json
{
  "items": [
    {
      "id": "uuid",
      "chatbot_source": "landing",
      "user_message": "C'est quoi Travaux IA ?",
      "ai_response": "Travaux IA est une plateforme...",
      "prompt_tokens": 250,
      "completion_tokens": 180,
      "total_tokens": 430,
      "is_fallback": false,
      "created_at": "2026-06-25T10:30:00Z"
    }
  ],
  "total": 156,
  "page": 1,
  "size": 20,
  "pages": 8
}
```

---

## Sources des chatbots

| Source | Endpoint chatbot | Description |
|---|---|---|
| `dashboard` | `POST /api/v1/chat` | Assistant IA dans l'app web |
| `landing` | `POST /api/v1/landing-chat` | Chatbot de la landing page |
| `mobile` | `POST /api/v1/mobile-chat` | Chatbot de l'app mobile |
