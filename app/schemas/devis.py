"""Pydantic schemas for a generated devis (quote) response.

These models describe ONLY the structural shape of a devis: field names,
types, nesting and optionality. No business values are hardcoded here; any
literal values (prices, titles, dates, etc.) are produced at runtime by the
service layer.

Shape overview::

    DevisResponse
    |-- date: datetime
    |-- montant_ttc: float
    |-- validite: datetime
    |-- duree: str
    |-- blocs: list[Bloc]
          |-- title: str
          |-- lots: list[Lot]
                |-- title: str
                |-- ligne_ids: Optional[list[str]]
                |-- lignes: list[Ligne]
                      |-- num, description, qte, unit, pu, tva, ht, ttc
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Line(BaseModel):
    """A single billable line inside a :class:`SubCategory`."""

    model_config = ConfigDict(extra="ignore")

    designation: str = Field(..., description="Human-readable description of the work.")
    quantite: float = Field(..., description="Quantity of units.")
    unite: str = Field(..., description="Unit of measure.")
    pu_ht: float = Field(..., description="Unit price (prix unitaire), excl. tax.")
    tva: float = Field(..., description="VAT rate, in percent.")
    total_ht: float = Field(..., description="Total excl. tax for the line.")
    
    # Legacy V1 fields (for frontend backward compatibility during transition)
    num: int | None = Field(default=None)
    description: str | None = Field(default=None)
    qte: float | None = Field(default=None)
    unit: str | None = Field(default=None)
    pu: float | None = Field(default=None)
    ht: float | None = Field(default=None)
    ttc: float | None = Field(default=None)


class SubCategory(BaseModel):
    """A sub_category groups together several :class:`Line` for a given trade."""

    model_config = ConfigDict(extra="ignore")

    sub_label: str = Field(..., description="Name of the trade / sub_category.")
    lines: list[Line] = Field(..., description="Ordered list of billable lines.")


class Block(BaseModel):
    """A block groups several :class:`SubCategory` (typically a floor or a zone)."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Name of the block.")
    sub_categories: list[SubCategory] = Field(..., description="Ordered list of sub_categories inside the block.")
    total_lot_ht: float = Field(default=0.0, description="Total amount for this block")


class DevisResponse(BaseModel):
    """Top-level response returned by the devis generation endpoint."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Title of the devis.")
    blocks: list[Block] = Field(..., description="Ordered list of blocks composing the devis.")
    montant_ht: float = Field(..., description="Total amount of the devis, excl. tax.")
    montant_ttc: float = Field(..., description="Total amount of the devis, incl. tax.")
    tva_breakdown: dict[str, Any] = Field(..., description="Breakdown of VAT by rate.")


