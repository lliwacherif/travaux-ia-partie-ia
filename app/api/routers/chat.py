"""Router for chatbot interactions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.core.chat_responses import build_chatbot_provider_fallback_response
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.ai_service import AIServiceError, ai_service
from app.services.chat_supervisor import chat_supervisor

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "",
    response_model=ChatResponse,
    summary="Generate a chatbot response",
    description=(
        "Send free-form text to the chatbot and get a Travaux IA workflow "
        "response. Known UI workflows and provider failures are handled locally."
    ),
)
async def generate_chat(
    payload: ChatRequest, background_tasks: BackgroundTasks
) -> ChatResponse:
    """Generate a response from the AI assistant."""
    is_fallback = False
    try:
        response_text, usage = await ai_service.generate_chat_response(
            user_text=payload.text,
            history=payload.history,
        )
    except AIServiceError as exc:
        logger.warning("AI provider error during chat; returning fallback: %s", exc)
        response_text = build_chatbot_provider_fallback_response(payload.text)
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        is_fallback = True
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error in chat endpoint.")
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    # Fire-and-forget: record this exchange in the background.
    background_tasks.add_task(
        chat_supervisor.record,
        chatbot_source="dashboard",
        user_message=payload.text,
        ai_response=response_text,
        prompt_tokens=usage["prompt_tokens"],
        completion_tokens=usage["completion_tokens"],
        is_fallback=is_fallback,
    )

    return ChatResponse(text=response_text)
