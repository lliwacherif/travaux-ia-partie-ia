# Intégration du Chat Supervisor

Guide d'intégration des endpoints du Chat Supervisor dans le CRM / Dashboard.

**Base URL :** `https://votre-domaine/api/v1/chat-supervisor`

---

## 1. Aucune intégration côté chatbot

Les 3 chatbots (`/chat`, `/landing-chat`, `/mobile-chat`) enregistrent **automatiquement** chaque échange en arrière-plan. Aucun changement n'est nécessaire côté frontend pour les chatbots existants.

---

## 2. Afficher les métriques dans le CRM

### Métriques du jour

```javascript
const today = new Date().toISOString().split("T")[0]; // "2026-06-25"

const res = await fetch(
  `${API_URL}/chat-supervisor/metrics?date=${today}`
);
const metrics = await res.json();

// metrics = [
//   { chatbot_source: "dashboard", total_conversations: 30, total_tokens: 15000, ... },
//   { chatbot_source: "landing",   total_conversations: 12, total_tokens: 5200, ... },
//   { chatbot_source: "mobile",    total_conversations: 8,  total_tokens: 3100, ... }
// ]
```

### Métriques par source spécifique

```javascript
const res = await fetch(
  `${API_URL}/chat-supervisor/metrics?date=${today}&source=mobile`
);
```

### Métriques sur une plage de dates

```javascript
const res = await fetch(
  `${API_URL}/chat-supervisor/metrics?from=2026-06-01&to=2026-06-25`
);
```

---

## 3. Résumé global (Dashboard principal)

Idéal pour afficher les totaux sur une carte ou un widget.

```javascript
const res = await fetch(
  `${API_URL}/chat-supervisor/metrics/summary?from=2026-06-01&to=2026-06-30`
);
const summary = await res.json();

// summary.total_conversations  → 520
// summary.total_tokens          → 248000
// summary.total_errors          → 12
// summary.breakdown             → détail par source (dashboard, landing, mobile)
```

**Exemple d'affichage :**

```
┌─────────────────────────────────────────────┐
│  Chatbots — Juin 2026                       │
│                                             │
│  💬 520 conversations                       │
│  🔤 248,000 tokens consommés                │
│  ❌ 12 erreurs                              │
│                                             │
│  Dashboard: 300  │ Landing: 150 │ Mobile: 70│
└─────────────────────────────────────────────┘
```

---

## 4. Historique des conversations

Pour afficher les échanges dans un tableau CRM.

### Page 1, 20 résultats

```javascript
const res = await fetch(
  `${API_URL}/chat-supervisor/conversations?page=1&size=20`
);
const data = await res.json();

// data.items  → tableau de conversations
// data.total  → nombre total
// data.pages  → nombre de pages
```

### Filtrer par source

```javascript
const res = await fetch(
  `${API_URL}/chat-supervisor/conversations?source=landing&page=1&size=20`
);
```

### Filtrer par date

```javascript
const res = await fetch(
  `${API_URL}/chat-supervisor/conversations?date=2026-06-25&page=1&size=50`
);
```

### Exemple de rendu tableau

| Heure | Source | Message utilisateur | Réponse IA | Tokens | Fallback |
|---|---|---|---|---|---|
| 10:30 | landing | C'est quoi Travaux IA ? | Travaux IA est une plateforme... | 430 | ❌ |
| 10:32 | mobile | Comment créer un devis ? | Pour créer un devis... | 380 | ❌ |
| 10:35 | dashboard | aide moi | Je suis là pour vous... | 0 | ✅ |

---

## 5. Résumé des endpoints

| Méthode | Endpoint | Usage |
|---|---|---|
| `GET` | `/metrics` | Métriques journalières par source |
| `GET` | `/metrics/summary` | Totaux agrégés + breakdown |
| `GET` | `/conversations` | Log paginé des conversations |

**Paramètres communs :**

| Param | Type | Exemple |
|---|---|---|
| `date` | `YYYY-MM-DD` | `?date=2026-06-25` |
| `from` | `YYYY-MM-DD` | `?from=2026-06-01` |
| `to` | `YYYY-MM-DD` | `?to=2026-06-30` |
| `source` | `string` | `?source=dashboard` |
| `page` | `int` | `?page=2` |
| `size` | `int` | `?size=50` (max 200) |
