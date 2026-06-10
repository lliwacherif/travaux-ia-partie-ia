"""ORM model for a Pack Travaux (pre-built work package).

The ``packs_travaux`` table is the **primary price and line-item source**
for the devis generation engine (V2).  Each pack contains a complete set of
~20 pre-built line items (in ``pack_json``) covering preparation, demolition,
main work, and finishing — with real market prices.

The table is seeded from ``packs_travaux_rows.csv`` (904 packs, 30+ trades).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class PackTravaux(Base):
    """A pre-built work package with complete line items and prices."""

    __tablename__ = "packs_travaux"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    corps_metier: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sous_metier_depannage: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_pack: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    nom_pack: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    pack_json: Mapped[list] = mapped_column(JSONB, nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    surface_ref: Mapped[float | None] = mapped_column(Float, nullable=True)
    unite_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pack_category: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<PackTravaux code={self.code_pack!r} metier={self.corps_metier!r} "
            f"nom={self.nom_pack!r}>"
        )
