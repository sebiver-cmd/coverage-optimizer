"""Dry-run apply-prices endpoint.

Reuses the existing optimisation pipeline from :mod:`backend.optimizer_api`
to compute a detailed "change set" (old price vs. new price, percentage
change) **without writing anything** to HostedShop / DanDomain.

This module intentionally does **not** import any write/push/update
functions.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.optimizer_api import OptimizeRequest, run_optimization
from backend.rbac import require_role
from backend.apply_constants import BATCH_DIR, UUID_RE

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
    buy_price: float = 0.0
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

    batch_id: str
    changes: list[DryRunChangeRow]
    summary: DryRunSummary


# ---------------------------------------------------------------------------
# Create-manifest models (used by the UI to submit pre-computed changes)
# ---------------------------------------------------------------------------


class ManifestChangeEntry(BaseModel):
    """A single change entry submitted to ``POST /apply-prices/create-manifest``.

    Mirrors the dict structure produced by the UI's push-updates helper
    and extends ``DryRunChangeRow`` with optional variant/product identifiers
    so that the backend apply endpoint can route to the correct SOAP method.
    """

    NUMBER: str = Field(..., description="Product SKU / item number.")
    TITLE_DK: str = Field(default="", description="Product title (for display/audit).")
    product_id: str = Field(default="", description="DanDomain internal product ID.")
    variant_id: str = Field(default="", description="DanDomain variant ID, if this is a variant row.")
    variant_types: str = Field(default="", description="Variant type string (e.g. 'Color/Size').")
    old_price: float = Field(default=0.0, description="Current sell price in the shop.")
    new_price: float = Field(default=0.0, description="New sell price to apply.")
    change_pct: float = Field(default=0.0, description="Percentage price change.")
    buy_price: float = Field(default=0.0, description="Cost/buy price.")
    old_buy_price: float = Field(default=0.0, description="Previous cost/buy price (for audit).")


class CreateManifestRequest(BaseModel):
    """Parameters accepted by ``POST /apply-prices/create-manifest``."""

    changes: list[ManifestChangeEntry] = Field(
        ...,
        description="Pre-computed list of price changes from the UI.",
    )


class CreateManifestResponse(BaseModel):
    """Response from ``POST /apply-prices/create-manifest``."""

    batch_id: str
    changes: list[ManifestChangeEntry]
    summary: DryRunSummary


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["apply-prices"])


@router.post("/apply-prices/dry-run", response_model=DryRunResponse, dependencies=[Depends(require_role("operator"))])
def dry_run_apply(payload: DryRunRequest) -> DryRunResponse:
    """Compute a dry-run change set without writing to the webshop.

    Delegates to the existing ``run_optimization`` function for the
    pricing pipeline, then post-processes the result into an old/new
    comparison with percentage changes.  A manifest is persisted to disk
    under ``data/apply_batches/{batch_id}.json``.
    """

    # Reuse the existing optimization endpoint logic (read-only).
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
                buy_price=row.buy_price,
                old_price=old_price,
                new_price=new_price,
                change_pct=change_pct,
            )
        )

    # Summary counts
    increases = sum(1 for c in changes if c.new_price > c.old_price)
    decreases = sum(1 for c in changes if c.new_price < c.old_price)
    unchanged = sum(1 for c in changes if c.new_price == c.old_price)

    summary = DryRunSummary(
        total=len(changes),
        increases=increases,
        decreases=decreases,
        unchanged=unchanged,
    )

    # Generate batch_id and persist the manifest.
    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    manifest = {
        "batch_id": batch_id,
        "created_at": created_at,
        "optimize_payload": payload.optimize_payload.model_dump(),
        "product_numbers": payload.product_numbers,
        "changes": [c.model_dump() for c in changes],
        "summary": summary.model_dump(),
    }

    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = BATCH_DIR / f"{batch_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Persisted dry-run manifest: %s", manifest_path)

    return DryRunResponse(
        batch_id=batch_id,
        changes=changes,
        summary=summary,
    )


@router.get("/apply-prices/batch/{batch_id}", dependencies=[Depends(require_role("viewer"))])
def get_batch(batch_id: str) -> dict:
    """Return a previously persisted dry-run manifest.

    ``batch_id`` is strictly validated as a UUID-4 string to prevent
    path-traversal attacks.
    """
    if not UUID_RE.match(batch_id):
        raise HTTPException(status_code=422, detail="Invalid batch_id format.")

    manifest_path = BATCH_DIR / f"{batch_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Batch not found.")

    return json.loads(manifest_path.read_text(encoding="utf-8"))


@router.post("/apply-prices/create-manifest", response_model=CreateManifestResponse, dependencies=[Depends(require_role("operator"))])
def create_manifest(payload: CreateManifestRequest) -> CreateManifestResponse:
    """Persist a pre-computed change set as a batch manifest.

    Accepts the list of change entries already computed by the UI,
    assigns a new ``batch_id``, writes a manifest to disk under
    ``data/apply_batches/{batch_id}.json``, and returns the
    ``batch_id`` so the caller can later invoke
    ``POST /apply-prices/apply``.

    This endpoint does **not** write to HostedShop — it is a read-only
    persistence step that records what *would* be applied.  The manifest
    intentionally preserves ``variant_id`` and ``product_id`` so that the
    real-apply step can route to the correct SOAP method
    (``Product_UpdateVariant`` vs ``Product_Update``).
    """
    changes = payload.changes

    increases = sum(1 for c in changes if c.new_price > c.old_price)
    decreases = sum(1 for c in changes if c.new_price < c.old_price)
    unchanged = sum(1 for c in changes if c.new_price == c.old_price)

    summary = DryRunSummary(
        total=len(changes),
        increases=increases,
        decreases=decreases,
        unchanged=unchanged,
    )

    batch_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()

    manifest = {
        "batch_id": batch_id,
        "created_at": created_at,
        "source": "ui-create-manifest",
        "changes": [c.model_dump() for c in changes],
        "summary": summary.model_dump(),
    }

    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = BATCH_DIR / f"{batch_id}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("Persisted UI-created manifest: %s", manifest_path)

    return CreateManifestResponse(
        batch_id=batch_id,
        changes=changes,
        summary=summary,
    )
