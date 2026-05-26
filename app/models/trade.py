"""ORM model for a construction trade (corps d'état).

A ``Trade`` is a top-level category such as ``Plomberie / Sanitaires`` or
``Électricité`` that groups a list of :class:`TradeService` entries priced
individually. The schema mirrors the CSV export we seed from
(``trades_rows.csv``) so ingestion is straightforward.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.trade_service import TradeService


class Trade(Base):
    """High-level trade / corps d'état grouping priceable services."""

    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    # Ownership & provenance ------------------------------------------------
    # ``user_id`` is set for tenant-specific trades; NULL for system ones.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Human-friendly classification used for UI grouping and RAG filtering.
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    subcategory: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Timestamps ------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    services: Mapped[list["TradeService"]] = relationship(
        "TradeService",
        back_populates="trade",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Trade id={self.id} name={self.name!r} category={self.category!r}>"
