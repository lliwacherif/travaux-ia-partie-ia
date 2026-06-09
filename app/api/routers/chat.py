"""Router for chatbot interactions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIServiceError, ai_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Generate a chatbot response",
    description="Send free-form text to the chatbot and get a generated response based on the core architecture context.",
)
async def generate_chat(payload: ChatRequest) -> ChatResponse:
    """Generate a response from the AI assistant."""
    try:
        response_text = await ai_service.generate_chat_response(
            user_text=payload.text,
            history=payload.history,
        )
        return ChatResponse(text=response_text)
    except AIServiceError as exc:
        logger.warning("AI provider error during chat: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error in chat endpoint.")
        raise HTTPException(status_code=500, detail="Internal server error") from exc
