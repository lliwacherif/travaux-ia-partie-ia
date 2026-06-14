# TRAVAUX IA - Mobile Chatbot API

Dedicated chatbot endpoint for the Travaux IA mobile app UI.

It answers in French and guides users through mobile workflows only: clients, devis IA, chantiers, équipes, documents and follow-up.

## Endpoint

- **URL:** `/api/v1/mobile-chat`
- **Method:** `POST`
- **Content-Type:** `application/json`
- **Authentication:** None

## Request

```json
{
  "text": "Comment valider un devis sur mobile ?",
  "history": []
}
```

## Response

```json
{
  "text": "1. Ouvrez « Devis IA »..."
}
```

## curl Example

```bash
curl -X POST "http://localhost:8000/api/v1/mobile-chat" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Comment planifier un chantier depuis l'application mobile ?",
    "history": []
  }'
```

## Notes

- Use `/api/v1/chat` for the main web app chatbot.
- Use `/api/v1/mobile-chat` for mobile UI guidance.
- Empty `text` returns `400`.
- Invalid JSON returns `422`.
