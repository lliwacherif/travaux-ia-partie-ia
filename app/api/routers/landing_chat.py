"""Router for landing-page chatbot interactions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.chat_responses import (
    build_landing_chatbot_provider_fallback_response,
)
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIServiceError, ai_service
from app.services.chat_supervisor import chat_supervisor

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
async def generate_landing_chat(
    payload: ChatRequest, background_tasks: BackgroundTasks
) -> ChatResponse:
    """Generate a response from the landing-page assistant."""
    is_fallback = False
    try:
        response_text, usage = await ai_service.generate_landing_chat_response(
            user_text=payload.text,
            history=payload.history,
        )
    except AIServiceError as exc:
        logger.warning(
            "AI provider error during landing chat; returning fallback: %s", exc
        )
        response_text = build_landing_chatbot_provider_fallback_response(payload.text)
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        is_fallback = True
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error in landing chat endpoint.")
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    background_tasks.add_task(
        chat_supervisor.record,
        chatbot_source="landing",
        user_message=payload.text,
        ai_response=response_text,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        is_fallback=is_fallback,
    )

    return ChatResponse(text=response_text)
