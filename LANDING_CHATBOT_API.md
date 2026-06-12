# TRAVAUX IA - Landing Chatbot API

This endpoint powers the simple chatbot for the public landing page.

It is separate from the in-app chatbot and is restricted to:

- explaining what Travaux IA is;
- answering BTP/product questions about Travaux IA;
- helping visitors choose between the Travaux IA plans.

## Endpoint

- **URL:** `/api/v1/landing-chat`
- **Method:** `POST`
- **Content-Type:** `application/json`
- **Authentication:** None

## Request

```json
{
  "text": "Je suis artisan seul, quel plan choisir ?",
  "history": []
}
```

`history` is optional and accepts the same format as the main chatbot:

```json
[
  { "role": "user", "content": "Je fais environ 20 devis par mois." },
  { "role": "assistant", "content": "Le plan Pro semble adapté." }
]
```

## Response

```json
{
  "text": "Pour un artisan seul avec environ 20 devis par mois, je vous recommande le plan Pro..."
}
```

## Plans Injected In The Bot

The page has `Mensuel` and `Annuel` modes, with prices displayed per month.
It also includes the labels `Populaire`, `Actuel`, `Fonctionnalités`, `Choisir`,
and `Contacter le service commercial`.

- **Découverte:** Gratuit, 1 utilisateur, 3 devis IA / mois.
- **Pro:** 29,90 € HT / mois, 1 utilisateur, 30 devis IA / mois.
- **Expert:** 49,90 € HT / mois, 2 utilisateurs, 100 devis IA / mois.
- **Premium:** 79,90 € HT / mois, 3 utilisateurs, 250 devis IA / mois.
- **Entreprise:** sur devis, utilisateurs et volume de devis IA sur mesure.

The bot also knows the feature differences: dashboards, documents, AI quote generator, 20 000 prestations, 1000 packs métiers, 30 métiers Travaux IA, 10 métiers dépannage, 3000-price library, custom library, CEE folders, GPS Google Maps + Waze, chatbot/email support, WhatsApp support, electronic invoicing 2026-2027, and PC/tablet/smartphone availability.

## curl Example

```bash
curl -X POST "http://localhost:8000/api/v1/landing-chat" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Nous sommes 2 personnes et nous faisons environ 80 devis par mois. Quel plan choisir ?",
    "history": []
  }'
```

## Error Codes

- `400 Bad Request`: empty `text`.
- `422 Unprocessable Entity`: invalid JSON body.
- `500 Internal Server Error`: unexpected server error.

Provider failures are handled with a local Travaux IA fallback response, so normal landing chat requests should not return `503`.
