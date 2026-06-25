"""ORM model for aggregated daily chatbot metrics.

The ``chatbot_daily_metrics`` table stores **one row per (date, chatbot_source)**
combination.  Counters are atomically incremented via PostgreSQL
``ON CONFLICT … DO UPDATE`` so concurrent background tasks never lose data.

Chatbot sources: ``dashboard``, ``landing``, ``mobile``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class ChatbotDailyMetrics(Base):
    """Aggregated daily token and conversation counters per chatbot source."""

    __tablename__ = "chatbot_daily_metrics"
    __table_args__ = (
        UniqueConstraint("date", "chatbot_source", name="uq_metrics_date_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    chatbot_source: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )

    total_conversations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_messages: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_prompt_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_errors: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatbotDailyMetrics date={self.date} source={self.chatbot_source!r} "
            f"convs={self.total_conversations} tokens={self.total_tokens}>"
        )
