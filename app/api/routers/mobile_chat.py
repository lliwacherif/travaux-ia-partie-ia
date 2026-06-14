"""Router for mobile-app chatbot interactions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.core.chat_responses import build_mobile_chatbot_provider_fallback_response
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIServiceError, ai_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mobile-chat", tags=["mobile-chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Generate a mobile-app chatbot response",
    description=(
        "Send a mobile user question to the Travaux IA mobile chatbot. "
        "The assistant answers with mobile UI guidance only."
    ),
)
async def generate_mobile_chat(payload: ChatRequest) -> ChatResponse:
    """Generate a response from the mobile-app assistant."""
    try:
        response_text = await ai_service.generate_mobile_chat_response(
            user_text=payload.text,
            history=payload.history,
        )
        return ChatResponse(text=response_text)
    except AIServiceError as exc:
        logger.warning("AI provider error during mobile chat; returning fallback: %s", exc)
        return ChatResponse(
            text=build_mobile_chatbot_provider_fallback_response(payload.text)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error in mobile chat endpoint.")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
