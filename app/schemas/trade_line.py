"""Pydantic schemas for the trade-line catalog endpoint.

Used by ``POST /api/v1/trade-line/generate``: given a corps de métier
(e.g. ``"Peinture"``) the AI returns a DYNAMIC LIST of representative
billable prestations (5 to ~25 items), priced from the catalog
(`trades` / `trade_services`) when possible and from the 2025 pricing
matrix otherwise. Powers the "choisir une prestation" picker on the
frontend.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Bounds on the number of items the AI is allowed to return.
# ---------------------------------------------------------------------------
TRADE_LINE_MIN_ITEMS: int = 1
TRADE_LINE_MAX_ITEMS: int = 30
TRADE_LINE_DEFAULT_LIMIT: int = 12


class TradeLineRequest(BaseModel):
    """Input body for ``POST /trade-line/generate``."""

    model_config = ConfigDict(extra="forbid")

    job_corp: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description=(
            "Corps de métier (free-form French label, e.g. 'Peinture', "
            "'Plomberie', 'Électricité'). Loose match against the catalog."
        ),
    )
    limit: int | None = Field(
        default=None,
        ge=TRADE_LINE_MIN_ITEMS,
        le=TRADE_LINE_MAX_ITEMS,
        description=(
            "Soft cap on the number of items returned. The AI is asked to "
            "produce up to this many representative prestations. Defaults "
            f"to {TRADE_LINE_DEFAULT_LIMIT} when omitted."
        ),
    )


class TradeLineItem(BaseModel):
    """A single representative billable prestation for a corps de métier."""

    # Permissive on extra keys: if the LLM adds noise we drop it instead
    # of 502-ing the whole list.
    model_config = ConfigDict(extra="ignore")

    job_corp: str = Field(..., description="Echoes the requested corps de métier.")
    description: str = Field(
        ...,
        description=(
            "Short French label describing the prestation. Mirrors the "
            "concatenation of `designation` + `description` from the "
            "catalog when both exist."
        ),
    )
    unit: str = Field(
        ...,
        description="Unit of measure ('m2', 'ml', 'u', 'forfait', ...).",
    )
    pu: float = Field(
        ...,
        ge=0,
        description="Reference unit price, HT (excl. tax), in euros.",
    )
    tva: Literal[5.5, 10, 20] = Field(
        ...,
        description="French VAT rate: 5.5 (énergétique), 10 (réno), 20 (neuf/B2B).",
    )


class TradeLineResponse(BaseModel):
    """List of representative prestations for a given corps de métier."""

    model_config = ConfigDict(extra="ignore")

    job_corp: str = Field(
        ...,
        description="Echoes the requested corps de métier (verbatim).",
    )
    count: int = Field(
        ...,
        ge=0,
        description="Number of items returned (== len(items)).",
    )
    items: list[TradeLineItem] = Field(
        ...,
        description="Representative prestations for the corps de métier.",
    )
