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
from app.api.routers import landing_chat as landing_chat_router
from app.api.routers import mobile_chat as mobile_chat_router
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


async def _assert_landing_router_provider_error_is_not_503() -> None:
    original = landing_chat_router.ai_service.generate_landing_chat_response

    async def failing_generate_landing_chat_response(*args, **kwargs) -> str:
        raise AIServiceError("simulated provider failure")

    landing_chat_router.ai_service.generate_landing_chat_response = (
        failing_generate_landing_chat_response
    )
    try:
        response = await landing_chat_router.generate_landing_chat(
            ChatRequest(text="quel plan choisir pour 2 utilisateurs ?")
        )
    finally:
        landing_chat_router.ai_service.generate_landing_chat_response = original

    assert response.text
    assert "Travaux IA" in response.text
    assert "Expert" in response.text or "Premium" in response.text


async def _assert_mobile_router_provider_error_is_not_503() -> None:
    original = mobile_chat_router.ai_service.generate_mobile_chat_response

    async def failing_generate_mobile_chat_response(*args, **kwargs) -> str:
        raise AIServiceError("simulated provider failure")

    mobile_chat_router.ai_service.generate_mobile_chat_response = (
        failing_generate_mobile_chat_response
    )
    try:
        response = await mobile_chat_router.generate_mobile_chat(
            ChatRequest(text="comment planifier un chantier sur mobile ?")
        )
    finally:
        mobile_chat_router.ai_service.generate_mobile_chat_response = original

    assert response.text
    assert "application mobile Travaux IA" in response.text
    assert "Chantiers" in response.text or "Devis IA" in response.text


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
    await _assert_landing_router_provider_error_is_not_503()
    await _assert_mobile_router_provider_error_is_not_503()
    print("chat no-503 smoke checks passed")


if __name__ == "__main__":
    asyncio.run(main())
