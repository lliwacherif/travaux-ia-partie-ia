"""Router for the chatbot supervisor CRM endpoints.

Exposes read-only endpoints for querying aggregated daily chatbot metrics
and browsing individual conversation logs.  All data is written by the
:class:`~app.services.chat_supervisor.ChatSupervisor` background service.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.models.chatbot_conversation import ChatbotConversation
from app.models.chatbot_metrics import ChatbotDailyMetrics
from app.schemas.chat_supervisor import (
    ConversationLogEntry,
    ConversationLogPage,
    DailyMetricsResponse,
    SourceBreakdown,
    SupervisorSummaryResponse,
)

router = APIRouter(prefix="/chat-supervisor", tags=["chat-supervisor"])


# ---------------------------------------------------------------------------
# GET /metrics — daily rows, filterable
# ---------------------------------------------------------------------------
@router.get(
    "/metrics",
    response_model=list[DailyMetricsResponse],
    summary="Daily chatbot metrics",
    description=(
        "Return aggregated daily metrics per chatbot source.  "
        "Filter by date, source, or a date range."
    ),
)
async def get_metrics(
    db: AsyncSession = Depends(get_db),
    date_exact: date | None = Query(None, alias="date", description="Single date filter"),
    source: str | None = Query(None, description="Chatbot source: dashboard, landing, mobile"),
    date_from: date | None = Query(None, alias="from", description="Start of date range"),
    date_to: date | None = Query(None, alias="to", description="End of date range"),
) -> list[DailyMetricsResponse]:
    stmt = select(ChatbotDailyMetrics).order_by(
        ChatbotDailyMetrics.date.desc(), ChatbotDailyMetrics.chatbot_source
    )

    if date_exact is not None:
        stmt = stmt.where(ChatbotDailyMetrics.date == date_exact)
    else:
        if date_from is not None:
            stmt = stmt.where(ChatbotDailyMetrics.date >= date_from)
        if date_to is not None:
            stmt = stmt.where(ChatbotDailyMetrics.date <= date_to)

    if source is not None:
        stmt = stmt.where(ChatbotDailyMetrics.chatbot_source == source.lower())

    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [DailyMetricsResponse.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# GET /metrics/summary — aggregated totals
# ---------------------------------------------------------------------------
@router.get(
    "/metrics/summary",
    response_model=SupervisorSummaryResponse,
    summary="Aggregated metrics summary",
    description=(
        "Return total token usage, conversation counts, and per-source breakdown "
        "for a date range (defaults to last 30 days)."
    ),
)
async def get_metrics_summary(
    db: AsyncSession = Depends(get_db),
    date_from: date | None = Query(None, alias="from", description="Start date"),
    date_to: date | None = Query(None, alias="to", description="End date"),
) -> SupervisorSummaryResponse:
    if date_to is None:
        date_to = date.today()
    if date_from is None:
        date_from = date_to - timedelta(days=30)

    stmt = (
        select(
            ChatbotDailyMetrics.chatbot_source,
            func.sum(ChatbotDailyMetrics.total_conversations).label("total_conversations"),
            func.sum(ChatbotDailyMetrics.total_messages).label("total_messages"),
            func.sum(ChatbotDailyMetrics.total_prompt_tokens).label("total_prompt_tokens"),
            func.sum(ChatbotDailyMetrics.total_completion_tokens).label("total_completion_tokens"),
            func.sum(ChatbotDailyMetrics.total_tokens).label("total_tokens"),
            func.sum(ChatbotDailyMetrics.total_errors).label("total_errors"),
        )
        .where(ChatbotDailyMetrics.date >= date_from)
        .where(ChatbotDailyMetrics.date <= date_to)
        .group_by(ChatbotDailyMetrics.chatbot_source)
    )

    result = await db.execute(stmt)
    rows = result.all()

    breakdown: list[SourceBreakdown] = []
    grand = {
        "total_conversations": 0,
        "total_messages": 0,
        "total_prompt_tokens": 0,
        "total_completion_tokens": 0,
        "total_tokens": 0,
        "total_errors": 0,
    }

    for row in rows:
        src = SourceBreakdown(
            chatbot_source=row.chatbot_source,
            total_conversations=row.total_conversations or 0,
            total_messages=row.total_messages or 0,
            total_prompt_tokens=row.total_prompt_tokens or 0,
            total_completion_tokens=row.total_completion_tokens or 0,
            total_tokens=row.total_tokens or 0,
            total_errors=row.total_errors or 0,
        )
        breakdown.append(src)
        for key in grand:
            grand[key] += getattr(src, key)

    return SupervisorSummaryResponse(
        date_from=date_from,
        date_to=date_to,
        breakdown=breakdown,
        **grand,
    )


# ---------------------------------------------------------------------------
# GET /conversations — paginated log
# ---------------------------------------------------------------------------
@router.get(
    "/conversations",
    response_model=ConversationLogPage,
    summary="Conversation log",
    description=(
        "Paginated list of individual chatbot conversations. "
        "Filter by source, single date, or date range."
    ),
)
async def get_conversations(
    db: AsyncSession = Depends(get_db),
    source: str | None = Query(None, description="Chatbot source filter"),
    date_exact: date | None = Query(None, alias="date", description="Single date filter"),
    date_from: date | None = Query(None, alias="from", description="Start of date range"),
    date_to: date | None = Query(None, alias="to", description="End of date range"),
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(50, ge=1, le=200, description="Items per page"),
) -> ConversationLogPage:
    base = select(ChatbotConversation)

    if source is not None:
        base = base.where(ChatbotConversation.chatbot_source == source.lower())

    if date_exact is not None:
        base = base.where(func.date(ChatbotConversation.created_at) == date_exact)
    else:
        if date_from is not None:
            base = base.where(func.date(ChatbotConversation.created_at) >= date_from)
        if date_to is not None:
            base = base.where(func.date(ChatbotConversation.created_at) <= date_to)

    # Count total matching rows
    count_stmt = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch page
    stmt = (
        base.order_by(ChatbotConversation.created_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    result = await db.execute(stmt)
    items = [ConversationLogEntry.model_validate(r) for r in result.scalars().all()]

    return ConversationLogPage(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=max(1, math.ceil(total / size)),
    )
