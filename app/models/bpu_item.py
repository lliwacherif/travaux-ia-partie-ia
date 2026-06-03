"""ORM model for a BPU (Bordereau de Prix Unitaires) line item.

The ``bpu_items`` table is the **authoritative price catalog** for the
devis engine.  It is seeded from two JSON files shipped with the project:

* ``bibliotheque-travaux-ia-v1.json`` — 3 000 lines covering 30+ trades
* ``bpu-master-v2.json`` — 325 lines (3 trades) with 5 fallback items

Every row carries a ``prix_unitaire_ht`` sourced from real market data
(Moyenne IDF 2025, estimations IA, etc.).  The ``slug`` column provides a
normalised key for fast fuzzy matching inside :func:`prestations_engine.load_price_map`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class BpuItem(Base):
    """A single priced line from the BPU / bibliothèque."""

    __tablename__ = "bpu_items"

    # Primary key is the human-readable ID from the JSON
    # (e.g. "BIBLIO-00001", "MAC-01-DEM", "GEN-01").
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    corps_metier: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    designation: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    prix_unitaire_ht: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    unite: Mapped[str] = mapped_column(String(64), nullable=False, default="u")
    taux_tva_defaut: Mapped[float] = mapped_column(
        Float, nullable=False, default=10.0, server_default="10"
    )

    type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    categorie: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sous_categorie: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="bibliotheque", server_default="'bibliotheque'"
    )
    slug: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)

    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<BpuItem id={self.id!r} corps_metier={self.corps_metier!r} "
            f"prix={self.prix_unitaire_ht} unite={self.unite!r}>"
        )
