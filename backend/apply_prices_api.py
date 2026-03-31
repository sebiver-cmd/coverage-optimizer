"""Dry-run apply-prices endpoint.

Reuses the existing optimisation pipeline from :mod:`backend.optimizer_api`
to compute a detailed "change set" (old price vs. new price, percentage
change) **without writing anything** to HostedShop / DanDomain.

This module intentionally does **not** import any write/push/update
functions.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.optimizer_api import OptimizeRequest, run_optimization

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class DryRunRequest(BaseModel):
    """Parameters accepted by ``POST /apply-prices/dry-run``.

    Wraps the standard optimisation parameters and adds an optional
    ``product_numbers`` filter so callers can limit the change set to
    a specific subset of SKUs.
    """

    optimize_payload: OptimizeRequest = Field(
        ...,
        description="Same parameters accepted by POST /optimize/.",
    )
    product_numbers: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of product SKUs (NUMBER column) to include "
            "in the dry-run output.  When null or empty, all products "
            "from the optimisation result are included."
        ),
    )


class DryRunChangeRow(BaseModel):
    """A single product in the dry-run change set."""

    NUMBER: str
    TITLE_DK: str
    old_price: float
    new_price: float
    change_pct: float


class DryRunSummary(BaseModel):
    """Aggregate counts for the dry-run change set."""

    total: int
    increases: int
    decreases: int
    unchanged: int


class DryRunResponse(BaseModel):
    """Full response from ``POST /apply-prices/dry-run``."""

    changes: list[DryRunChangeRow]
    summary: DryRunSummary


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["apply-prices"])


@router.post("/apply-prices/dry-run", response_model=DryRunResponse)
def dry_run_apply(payload: DryRunRequest) -> DryRunResponse:
    """Compute a dry-run change set without writing to the webshop.

    Delegates to the existing ``run_optimization`` function for the
    pricing pipeline, then post-processes the result into an old/new
    comparison with percentage changes.
    """

    # Reuse the existing optimisation endpoint logic (read-only).
    opt_response = run_optimization(payload.optimize_payload)

    # Build the change rows from the optimisation result.
    product_filter: set[str] | None = None
    if payload.product_numbers:
        product_filter = set(payload.product_numbers)

    changes: list[DryRunChangeRow] = []
    for row in opt_response.rows:
        if product_filter is not None and row.item_number not in product_filter:
            continue

        old_price = row.current_price
        new_price = row.suggested_price
        change_pct = (
            0.0
            if old_price == 0
            else round(((new_price - old_price) / old_price) * 100, 2)
        )
        changes.append(
            DryRunChangeRow(
                NUMBER=row.item_number,
                TITLE_DK=row.title,
                old_price=old_price,
                new_price=new_price,
                change_pct=change_pct,
            )
        )

    # Summary counts
    increases = sum(1 for c in changes if c.new_price > c.old_price)
    decreases = sum(1 for c in changes if c.new_price < c.old_price)
    unchanged = sum(1 for c in changes if c.new_price == c.old_price)

    return DryRunResponse(
        changes=changes,
        summary=DryRunSummary(
            total=len(changes),
            increases=increases,
            decreases=decreases,
            unchanged=unchanged,
        ),
    )
