"""Router for landing-page chatbot interactions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.chat_responses import (
    build_landing_chatbot_provider_fallback_response,
)
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIServiceError, ai_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/landing-chat", tags=["landing-chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Generate a landing-page chatbot response",
    description=(
        "Send a visitor question to the landing-page chatbot. The assistant "
        "only answers about Travaux IA, BTP use cases and public plan selection."
    ),
)
async def generate_landing_chat(payload: ChatRequest) -> ChatResponse:
    """Generate a response from the landing-page assistant."""
    try:
        response_text = await ai_service.generate_landing_chat_response(
            user_text=payload.text,
            history=payload.history,
        )
        return ChatResponse(text=response_text)
    except AIServiceError as exc:
        logger.warning(
            "AI provider error during landing chat; returning fallback: %s", exc
        )
        return ChatResponse(
            text=build_landing_chatbot_provider_fallback_response(payload.text)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error in landing chat endpoint.")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
