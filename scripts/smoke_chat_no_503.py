"""Smoke checks for chatbot local responses and provider-failure fallback.

Run from the repository root:

    python scripts/smoke_chat_no_503.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.routers import chat as chat_router
from app.core.chat_intent import classify_chat_intent
from app.schemas.chat import ChatRequest
from app.services.ai_service import AIService, AIServiceError


async def _assert_static_response(text: str, expected_fragment: str) -> None:
    service = AIService(api_key="test")
    response = await service.generate_chat_response(text)
    assert expected_fragment in response, (text, response)


async def _assert_router_provider_error_is_not_503() -> None:
    original = chat_router.ai_service.generate_chat_response

    async def failing_generate_chat_response(*args, **kwargs) -> str:
        raise AIServiceError("simulated provider failure")

    chat_router.ai_service.generate_chat_response = failing_generate_chat_response
    try:
        response = await chat_router.generate_chat(
            ChatRequest(text="question ouverte sur le bâtiment")
        )
    finally:
        chat_router.ai_service.generate_chat_response = original

    assert response.text
    assert "Travaux IA" in response.text or "Devis IA" in response.text


async def main() -> None:
    assert classify_chat_intent("Suivre l’avancement et préparer la facturation") == {
        "assistant",
        "planification",
    }

    await _assert_static_response(
        "Suivre l’avancement et préparer la facturation",
        "Pour suivre l'avancement et préparer la facturation",
    )
    await _assert_static_response(
        "Comment générer une offre détaillée ?",
        "Pour générer une offre détaillée",
    )
    await _assert_static_response(
        "devis",
        "Pour générer une offre détaillée",
    )
    await _assert_router_provider_error_is_not_503()
    print("chat no-503 smoke checks passed")


if __name__ == "__main__":
    asyncio.run(main())
