"""Background supervisor service for chatbot monitoring.

Records every chatbot interaction (conversation log + aggregated daily metrics)
without blocking the user-facing response.  Called via FastAPI ``BackgroundTasks``
from each chat router.

Usage in a router::

    from fastapi import BackgroundTasks
    from app.services.chat_supervisor import chat_supervisor

    @router.post("")
    async def chat(payload: ..., background_tasks: BackgroundTasks):
        text, usage = await ai_service.generate_chat_response(...)
        background_tasks.add_task(
            chat_supervisor.record,
            chatbot_source="dashboard",
            user_message=payload.text,
            ai_response=text,
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
        )
        return ChatResponse(text=text)
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import text as sa_text

from app.db.database import async_session_factory
from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_metrics import ChatbotDailyMetrics  # noqa: F401 (for clarity)

logger = logging.getLogger(__name__)


class ChatSupervisor:
    """Centralised recorder for chatbot usage metrics and conversation logs."""

    async def record(
        self,
        *,
        chatbot_source: str,
        user_message: str,
        ai_response: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        is_fallback: bool = False,
    ) -> None:
        """Persist a conversation entry and update the daily aggregate counters.

        This method is designed to run inside a FastAPI ``BackgroundTask`` so it
        opens its **own** database session (independent of the request session).

        Parameters
        ----------
        chatbot_source:
            One of ``"dashboard"``, ``"landing"``, ``"mobile"``.
        user_message:
            The raw text the end-user sent.
        ai_response:
            The text the chatbot returned (may be a fallback string).
        prompt_tokens / completion_tokens:
            Token counts reported by the LLM provider.  Zero when the
            response was a local fallback.
        is_fallback:
            ``True`` when the response was a static / error fallback
            (i.e. OpenAI was not called or failed).
        """
        total_tokens = prompt_tokens + completion_tokens
        today = date.today()

        try:
            async with async_session_factory() as session:
                # ---- 1. Insert conversation log row ----
                conversation = ChatbotConversation(
                    chatbot_source=chatbot_source,
                    user_message=user_message,
                    ai_response=ai_response,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    is_fallback=is_fallback,
                )
                session.add(conversation)

                # ---- 2. Upsert daily metrics (atomic increment) ----
                # Using raw SQL for the ON CONFLICT upsert because
                # SQLAlchemy's ORM doesn't have a clean cross-dialect
                # upsert API, and we need atomicity for concurrent tasks.
                upsert_sql = sa_text("""
                    INSERT INTO chatbot_daily_metrics (
                        id, date, chatbot_source,
                        total_conversations, total_messages,
                        total_prompt_tokens, total_completion_tokens,
                        total_tokens, total_errors,
                        created_at, updated_at
                    ) VALUES (
                        gen_random_uuid(), :today, :source,
                        1, 2,
                        :prompt_tokens, :completion_tokens,
                        :total_tokens, :errors,
                        NOW(), NOW()
                    )
                    ON CONFLICT ON CONSTRAINT uq_metrics_date_source
                    DO UPDATE SET
                        total_conversations   = chatbot_daily_metrics.total_conversations + 1,
                        total_messages        = chatbot_daily_metrics.total_messages + 2,
                        total_prompt_tokens   = chatbot_daily_metrics.total_prompt_tokens + EXCLUDED.total_prompt_tokens,
                        total_completion_tokens = chatbot_daily_metrics.total_completion_tokens + EXCLUDED.total_completion_tokens,
                        total_tokens          = chatbot_daily_metrics.total_tokens + EXCLUDED.total_tokens,
                        total_errors          = chatbot_daily_metrics.total_errors + EXCLUDED.total_errors,
                        updated_at            = NOW()
                """)

                await session.execute(
                    upsert_sql,
                    {
                        "today": today,
                        "source": chatbot_source,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "errors": 1 if is_fallback else 0,
                    },
                )

                await session.commit()

            logger.debug(
                "Supervisor recorded: source=%s tokens=%d fallback=%s",
                chatbot_source,
                total_tokens,
                is_fallback,
            )
        except Exception:
            # Never let a monitoring failure crash the background task or
            # propagate back to the user.  Log and move on.
            logger.exception(
                "ChatSupervisor.record failed for source=%s", chatbot_source
            )


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------
chat_supervisor: ChatSupervisor = ChatSupervisor()

__all__ = ["ChatSupervisor", "chat_supervisor"]
