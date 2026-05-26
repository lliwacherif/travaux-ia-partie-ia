"""ORM model for a priceable service attached to a :class:`~app.models.trade.Trade`.

Schema mirrors ``trade_services_rows.csv``:

* ``designation``    - free-form label that shows up in the devis (e.g.
  "Fourniture + pose d'un tableau électrique").
* ``description``    - optional clarification or technical notes.
* ``unit``           - unit of measure (``u``, ``m2``, ``ml``, ``forfait``, …).
* ``category``       - sub-classification within a trade for RAG filtering.
* ``estimated_price``- reference HT price used as the PU default.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

if TYPE_CHECKING:
    from app.models.trade import Trade


class TradeService(Base):
    """A specific, billable service belonging to a :class:`Trade`."""

    __tablename__ = "trade_services"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    trade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("trades.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    designation: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    estimated_price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default="0",
    )

    # Ownership & provenance ------------------------------------------------
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

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

    trade: Mapped["Trade"] = relationship("Trade", back_populates="services")

    def __repr__(self) -> str:
        return (
            f"<TradeService id={self.id} trade_id={self.trade_id} "
            f"designation={self.designation!r} unit={self.unit!r}>"
        )
