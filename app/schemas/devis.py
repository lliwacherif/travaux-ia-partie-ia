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
    |-- duree: int
    |-- blocs: list[Bloc]
          |-- title: str
          |-- lots: list[Lot]
                |-- title: str
                |-- lignes: list[Ligne]
                      |-- num, description, qte, unit, pu, tva, ht, ttc
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Ligne(BaseModel):
    """A single billable line inside a :class:`Lot`."""

    model_config = ConfigDict(extra="ignore")

    num: int = Field(..., description="1-based line index within the lot.")
    description: str = Field(..., description="Human-readable description of the work.")
    qte: float = Field(..., description="Quantity of units.")
    unit: str = Field(..., description="Unit of measure.")
    pu: float = Field(..., description="Unit price (prix unitaire), excl. tax.")
    tva: float = Field(..., description="VAT rate, in percent.")
    ht: float = Field(..., description="Total excl. tax for the line.")
    ttc: float = Field(..., description="Total incl. tax for the line.")


class Lot(BaseModel):
    """A lot groups together several :class:`Ligne` for a given trade."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Name of the trade / lot.")
    ligne_ids: list[str] | None = Field(
        default=None,
        description="Optional catalog line identifiers referenced by this lot.",
    )
    lignes: list[Ligne] = Field(..., description="Ordered list of billable lines.")


class Bloc(BaseModel):
    """A bloc groups several :class:`Lot` (typically a floor or a zone)."""

    model_config = ConfigDict(extra="ignore")

    title: str = Field(..., description="Name of the bloc.")
    lots: list[Lot] = Field(..., description="Ordered list of lots inside the bloc.")


class DevisResponse(BaseModel):
    """Top-level response returned by the devis generation endpoint."""

    model_config = ConfigDict(extra="ignore")

    date: datetime = Field(..., description="Creation date of the devis.")
    montant_ttc: float = Field(..., description="Total amount of the devis, incl. tax.")
    validite: datetime = Field(..., description="End-of-validity date of the devis.")
    duree: int = Field(..., description="Estimated duration of the project, in days.")
    blocs: list[Bloc] = Field(..., description="Ordered list of blocs composing the devis.")

    @field_validator("duree", mode="before")
    @classmethod
    def _coerce_duree_to_int(cls, value: Any) -> int:
        """Accept the AI's day count as a plain int *or* as a string.

        Strings such as ``"30"``, ``"30jours"``, ``"30 jours"`` and
        ``"30 days"`` are all coerced to ``30``. Anything else raises a
        validation error. ``bool`` is rejected explicitly because Python
        treats it as a subclass of ``int``.
        """
        if isinstance(value, bool):
            raise ValueError("`duree` must be an integer number of days, not a bool.")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            match = re.match(r"\s*(\d+)", value)
            if match is None:
                raise ValueError(
                    f"Cannot extract a day count from {value!r}. "
                    "Expected a number, e.g. 30 or '30jours'."
                )
            return int(match.group(1))
        raise ValueError(
            f"`duree` must be an int or a numeric string, got {type(value).__name__}."
        )
