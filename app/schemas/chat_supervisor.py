"""Pydantic schemas for the chatbot supervisor / CRM monitoring API."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Daily Metrics
# ---------------------------------------------------------------------------
class DailyMetricsResponse(BaseModel):
    """A single day's aggregated metrics for one chatbot source."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    date: date
    chatbot_source: str
    total_conversations: int
    total_messages: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_errors: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Summary (aggregated across multiple days / sources)
# ---------------------------------------------------------------------------
class SourceBreakdown(BaseModel):
    """Token and conversation totals for a single chatbot source."""

    chatbot_source: str
    total_conversations: int = 0
    total_messages: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_errors: int = 0


class SupervisorSummaryResponse(BaseModel):
    """Aggregated summary across a date range, with per-source breakdown."""

    date_from: date
    date_to: date
    total_conversations: int = 0
    total_messages: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_errors: int = 0
    breakdown: list[SourceBreakdown] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Conversation Log
# ---------------------------------------------------------------------------
class ConversationLogEntry(BaseModel):
    """A single recorded user ↔ AI message exchange."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chatbot_source: str
    user_message: str
    ai_response: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    is_fallback: bool
    created_at: datetime


class ConversationLogPage(BaseModel):
    """Paginated list of conversation log entries."""

    items: list[ConversationLogEntry]
    total: int
    page: int
    size: int
    pages: int
