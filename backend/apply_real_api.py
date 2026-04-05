"""Real apply-prices endpoint — writes price changes to HostedShop.

Consumes batch manifests persisted by the dry-run endpoint
(:mod:`backend.apply_prices_api`) and applies the price changes via
:meth:`dandomain_api.DanDomainClient.update_prices_batch`.

Guardrails are split into two categories:

**Batch-level hard guardrails** reject the entire request (HTTP 400):
- Number of change rows must be ≤ ``MAX_APPLY_ROWS``.

**Per-row soft guardrails** skip individual rows (partial success):
- ``new_price`` must be positive and finite.
- ``abs(change_pct)`` must be ≤ ``MAX_CHANGE_PCT``.
- ``new_price`` must exceed ``buy_price`` when ``buy_price > 0``
  (selling below cost is blocked).

Skipped rows are reported in the ``skipped`` list of the response.
Valid rows proceed to the write path.

Double-apply is prevented by a ``.applied`` marker file placed next
to the batch manifest after successful completion.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dandomain_api import DanDomainClient
from backend.apply_constants import BATCH_DIR, UUID_RE, AUDIT_LOG
from backend.cache import build_caller_key, invalidate_products_cache
from backend.config import get_settings
from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configurable constants
# ---------------------------------------------------------------------------

MAX_APPLY_ROWS = 100
MAX_CHANGE_PCT = 30


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
    """A single row that failed during the apply (write error)."""

    NUMBER: str
    reason: str


class ApplyResponse(BaseModel):
    """Full response from ``POST /apply-prices/apply``."""

    batch_id: str
    applied_count: int
    skipped: list[FailedRow]
    failed: list[FailedRow]
    started_at: str
    finished_at: str


# ---------------------------------------------------------------------------
# Per-row validation
# ---------------------------------------------------------------------------


def _validate_row(row: dict) -> str | None:
    """Return an error reason if the row fails per-row guardrails, else None."""
    number = row.get("NUMBER", "?")
    price = row.get("new_price")

    # Price must be present, numeric, positive and finite.
    if price is None or not isinstance(price, (int, float)):
        return f"new_price is missing or invalid for {number}."
    if not math.isfinite(price) or price <= 0:
        return f"new_price must be positive and finite for {number} (got {price})."

    # Change percentage within bounds.
    change_pct = row.get("change_pct", 0)
    if abs(change_pct) > MAX_CHANGE_PCT:
        return (
            f"abs(change_pct) = {abs(change_pct):.2f}% exceeds "
            f"maximum of {MAX_CHANGE_PCT}% for {number}."
        )

    # Margin / cost guardrail: don't sell below cost.
    buy_price = row.get("buy_price", 0)
    if isinstance(buy_price, (int, float)) and buy_price > 0 and price <= buy_price:
        return (
            f"new_price {price:.2f} is at or below buy_price {buy_price:.2f} "
            f"for {number}."
        )

    return None


# ---------------------------------------------------------------------------
# Environment gating
# ---------------------------------------------------------------------------

#: Environment variable name that must be ``"true"`` to enable the apply endpoint.
ENABLE_APPLY_ENV = "SB_OPTIMA_ENABLE_APPLY"


def is_apply_enabled() -> bool:
    """Return *True* only when the apply endpoint is explicitly enabled.

    Reads ``SB_OPTIMA_ENABLE_APPLY`` via :func:`~backend.config.get_settings`.
    The value must be the literal string ``"true"`` (case-insensitive) to
    enable writes.  Any other value — including absent — means disabled.

    .. note::
        Tests rely on the ``_clear_settings_cache`` autouse fixture in
        ``tests/conftest.py`` so that ``monkeypatch.setenv`` changes are
        picked up by the next ``get_settings()`` call.
    """
    from backend.config import get_settings  # local import to avoid circular
    return get_settings().enable_apply


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["apply-prices"])


@router.get("/apply-prices/status", dependencies=[Depends(require_role("viewer"))])
def apply_status() -> dict[str, bool]:
    """Report whether the apply endpoint is currently enabled.

    Returns ``{"enabled": true}`` when ``SB_OPTIMA_ENABLE_APPLY=true``
    is set, otherwise ``{"enabled": false}``.
    """
    return {"enabled": is_apply_enabled()}


@router.post("/apply-prices/apply", response_model=ApplyResponse, dependencies=[Depends(require_role("admin"))])
def apply_prices(payload: ApplyRequest) -> ApplyResponse:
    """Apply a previously created batch manifest to the webshop.

    Validates guardrails, delegates writes to
    :meth:`DanDomainClient.update_prices_batch`, records an audit log
    line, and places a ``.applied`` marker to prevent double-apply.

    Per-row guardrails (price, change_pct, margin) skip individual rows
    rather than rejecting the entire batch, enabling partial success.

    Requires ``SB_OPTIMA_ENABLE_APPLY=true`` environment variable to be
    set; returns 403 otherwise.
    """
    # -1. Environment gate -----------------------------------------------
    if not is_apply_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "Apply is disabled.  Set environment variable "
                "SB_OPTIMA_ENABLE_APPLY=true to enable."
            ),
        )

    started_at = datetime.now(timezone.utc).isoformat()

    # 0. confirm must be true -------------------------------------------
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="confirm must be true to apply prices.",
        )

    # 1. Validate batch_id as UUID-4 ------------------------------------
    if not UUID_RE.match(payload.batch_id):
        raise HTTPException(status_code=422, detail="Invalid batch_id format.")

    # 2. Load manifest ---------------------------------------------------
    manifest_path = BATCH_DIR / f"{payload.batch_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Batch not found.")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changes: list[dict] = manifest.get("changes", [])

    # 3. Double-apply prevention ----------------------------------------
    applied_marker = BATCH_DIR / f"{payload.batch_id}.applied"
    if applied_marker.exists():
        raise HTTPException(
            status_code=409,
            detail="Batch has already been applied.",
        )

    # 4. Batch-level hard guardrail ------------------------------------
    if len(changes) > MAX_APPLY_ROWS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Batch contains {len(changes)} rows, "
                f"exceeding the maximum of {MAX_APPLY_ROWS}."
            ),
        )

    # 5. Per-row soft guardrails (skip failing rows) --------------------
    valid_rows: list[dict] = []
    skipped: list[FailedRow] = []

    for row in changes:
        reason = _validate_row(row)
        if reason is not None:
            skipped.append(FailedRow(NUMBER=row.get("NUMBER", "?"), reason=reason))
        else:
            valid_rows.append(row)

    # 6. Apply writes via existing single-write path --------------------
    failed: list[FailedRow] = []
    applied_count = 0

    if valid_rows:
        updates = [
            {
                "product_id": row.get("product_id", ""),
                "product_number": row["NUMBER"],
                "new_price": row["new_price"],
                "variant_id": row.get("variant_id", ""),
                "variant_types": row.get("variant_types", ""),
                **({"buy_price": row["buy_price"]} if row.get("buy_price") else {}),
            }
            for row in valid_rows
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

    finished_at = datetime.now(timezone.utc).isoformat()

    # 7. Write audit log line (JSONL) -----------------------------------
    audit_entry = {
        "timestamp": finished_at,
        "batch_id": payload.batch_id,
        "api_username": payload.api_username,
        "total_rows": len(changes),
        "skipped_count": len(skipped),
        "applied_count": applied_count,
        "failed_count": len(failed),
    }
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(audit_entry) + "\n")

    # 8. Place .applied marker -----------------------------------------
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    applied_marker.write_text(finished_at, encoding="utf-8")

    # 9. Invalidate product cache (best-effort) -------------------------
    try:
        caller_key = build_caller_key(payload.api_username, payload.site_id)
        invalidate_products_cache(caller_key, payload.site_id)
    except Exception:
        logger.debug("Product cache invalidation failed (non-fatal)", exc_info=True)

    return ApplyResponse(
        batch_id=payload.batch_id,
        applied_count=applied_count,
        skipped=skipped,
        failed=failed,
        started_at=started_at,
        finished_at=finished_at,
    )
