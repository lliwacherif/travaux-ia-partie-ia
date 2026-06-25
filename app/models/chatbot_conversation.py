"""ORM model for individual chatbot conversation logs.

The ``chatbot_conversations`` table stores **every single message exchange**
between a user and one of the three chatbots (dashboard, landing, mobile).

This is a flat log — not session-based — designed so CRM agents can browse
or search what the bots said on any given day.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ChatbotConversation(Base):
    """A single user ↔ AI message exchange, tagged by chatbot source."""

    __tablename__ = "chatbot_conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    chatbot_source: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )

    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    ai_response: Mapped[str] = mapped_column(Text, nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    is_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<ChatbotConversation source={self.chatbot_source!r} "
            f"tokens={self.total_tokens} fallback={self.is_fallback}>"
        )
