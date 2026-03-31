"""Real apply-prices endpoint — writes price changes to HostedShop.

Consumes batch manifests persisted by the dry-run endpoint
(:mod:`backend.apply_prices_api`) and applies the price changes via
:meth:`dandomain_api.DanDomainClient.update_prices_batch`.

Hard guardrails reject the entire request when any limit is violated:
- Number of change rows must be ≤ ``MAX_APPLY_ROWS``.
- Every ``new_price`` must be positive and finite.
- ``abs(change_pct)`` must be ≤ ``MAX_CHANGE_PCT`` for every row.

Double-apply is prevented by a ``.applied`` marker file placed next
to the batch manifest after successful completion.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dandomain_api import DanDomainClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------

MAX_APPLY_ROWS = 100
MAX_CHANGE_PCT = 30

# Directory where batch manifests live (shared with apply_prices_api).
_BATCH_DIR = Path("data/apply_batches")

# Audit log file path.
_AUDIT_LOG = Path("data/apply_audit.log")

# Strict UUID-4 pattern (lowercase hex with hyphens).
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ApplyRequest(BaseModel):
    """Parameters for ``POST /apply-prices/apply``."""

    batch_id: str = Field(..., description="UUID of the batch manifest to apply.")
    confirm: bool = Field(
        ...,
        description="Must be true to proceed.  Safety gate against accidental calls.",
    )
    api_username: str = Field(..., description="DanDomain API username.")
    api_password: str = Field(..., description="DanDomain API password.")
    site_id: int = Field(default=1, description="DanDomain site/language ID.")


class FailedRow(BaseModel):
    """A single row that failed during the apply."""

    NUMBER: str
    reason: str


class ApplyResponse(BaseModel):
    """Full response from ``POST /apply-prices/apply``."""

    batch_id: str
    applied_count: int
    failed: list[FailedRow]
    started_at: str
    finished_at: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["apply-prices"])


@router.post("/apply-prices/apply", response_model=ApplyResponse)
def apply_prices(payload: ApplyRequest) -> ApplyResponse:
    """Apply a previously created batch manifest to the webshop.

    Validates guardrails, delegates writes to
    :meth:`DanDomainClient.update_prices_batch`, records an audit log
    line, and places a ``.applied`` marker to prevent double-apply.
    """
    started_at = datetime.now(timezone.utc).isoformat()

    # 0. confirm must be true -------------------------------------------
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true to apply prices.",
        )

    # 1. Validate batch_id as UUID-4 ------------------------------------
    if not _UUID_RE.match(payload.batch_id):
        raise HTTPException(status_code=422, detail="Invalid batch_id format.")

    # 2. Load manifest ---------------------------------------------------
    manifest_path = _BATCH_DIR / f"{payload.batch_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Batch not found.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changes: list[dict] = manifest.get("changes", [])

    # 6. Double-apply prevention ----------------------------------------
    applied_marker = _BATCH_DIR / f"{payload.batch_id}.applied"
    if applied_marker.exists():
        raise HTTPException(
            status_code=409,
            detail="Batch has already been applied.",
        )

    # 3. Hard guardrails ------------------------------------------------
    if len(changes) > MAX_APPLY_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch contains {len(changes)} rows, "
                f"exceeding the maximum of {MAX_APPLY_ROWS}."
            ),
        )

    for row in changes:
        price = row.get("new_price")
        if price is None or not isinstance(price, (int, float)):
            raise HTTPException(
                status_code=400,
                detail=f"new_price is missing or invalid for {row.get('NUMBER', '?')}.",
            )
        if not math.isfinite(price) or price <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"new_price must be positive and finite for "
                    f"{row.get('NUMBER', '?')} (got {price})."
                ),
            )
        change_pct = row.get("change_pct", 0)
        if abs(change_pct) > MAX_CHANGE_PCT:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"abs(change_pct) = {abs(change_pct):.2f}% exceeds "
                    f"maximum of {MAX_CHANGE_PCT}% for {row.get('NUMBER', '?')}."
                ),
            )

    # 4. Apply writes via existing single-write path ---------------------
    updates = [
        {
            "product_number": row["NUMBER"],
            "new_price": row["new_price"],
        }
        for row in changes
    ]

    client = DanDomainClient(
        username=payload.api_username,
        password=payload.api_password,
    )
    result = client.update_prices_batch(updates, site_id=payload.site_id)

    applied_count = result.get("success", 0)
    errors = result.get("errors", [])

    failed = [
        FailedRow(
            NUMBER=err.get("product_number", "?"),
            reason=err.get("error", "Unknown error"),
        )
        for err in errors
    ]
    failed_count = result.get("failed", 0)

    finished_at = datetime.now(timezone.utc).isoformat()

    # 5. Write audit log line (JSONL) -----------------------------------
    audit_entry = {
        "timestamp": finished_at,
        "batch_id": payload.batch_id,
        "api_username": payload.api_username,
        "total_rows": len(changes),
        "applied_count": applied_count,
        "failed_count": failed_count,
    }
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry) + "\n")

    # 6b. Place .applied marker -----------------------------------------
    _BATCH_DIR.mkdir(parents=True, exist_ok=True)
    applied_marker.write_text(finished_at, encoding="utf-8")

    return ApplyResponse(
        batch_id=payload.batch_id,
        applied_count=applied_count,
        failed=failed,
        started_at=started_at,
        finished_at=finished_at,
    )
