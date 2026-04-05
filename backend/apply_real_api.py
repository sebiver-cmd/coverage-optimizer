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
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from dandomain_api import DanDomainClient
from backend.apply_constants import BATCH_DIR, UUID_RE, AUDIT_LOG
from backend.billing_gate import check_billing_gate
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
    api_username: str = Field(default="", description="DanDomain API username.")
    api_password: str = Field(default="", description="DanDomain API password.")
    site_id: int = Field(default=1, description="DanDomain site/language ID.")
    credential_id: Optional[UUID] = Field(
        default=None,
        description="UUID of a stored vault credential (used when auth is enabled).",
    )


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


@router.post("/apply-prices/apply", response_model=ApplyResponse, dependencies=[Depends(require_role("admin")), Depends(check_billing_gate)])
def apply_prices(payload: ApplyRequest, request: Request) -> ApplyResponse:
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

    # -0.5. Quota enforcement (Task 7.1) — only when auth enabled --------
    settings = get_settings()
    if settings.sboptima_auth_required:
        _check_apply_quota(request)

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

    # 6b. Persist apply batch to DB when auth enabled -------------------
    _persist_apply_batch_to_db(
        request,
        payload.batch_id,
        applied_count=applied_count,
        skipped_count=len(skipped),
        failed_count=len(failed),
        total_rows=len(changes),
        started_at_str=started_at,
        finished_at_str=finished_at,
    )

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


# ---------------------------------------------------------------------------
# Quota enforcement helper for real apply (Task 7.1)
# ---------------------------------------------------------------------------


def _check_apply_quota(request: Request) -> None:
    """Enforce daily apply quota (raises HTTPException 429 on limit)."""
    try:
        from backend.db import get_db
        from backend.models import Tenant
        from backend.quotas import check_quota

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return

        get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
        db_gen = get_db_fn()
        db = next(db_gen)
        try:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant is None:
                return
            check_quota(db, tenant, "apply")
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except HTTPException:
        raise
    except Exception:
        logger.debug("Quota check failed (non-fatal)", exc_info=True)


# ---------------------------------------------------------------------------
# DB persistence helper for real apply
# ---------------------------------------------------------------------------


def _persist_apply_batch_to_db(
    request: Request,
    batch_id: str,
    *,
    applied_count: int,
    skipped_count: int,
    failed_count: int,
    total_rows: int,
    started_at_str: str,
    finished_at_str: str,
) -> None:
    """Write an ApplyBatch row for the real apply operation (best-effort)."""
    try:
        settings = get_settings()
        if not settings.sboptima_auth_required:
            return

        from backend.db import get_db
        from backend.repositories import batches_repo

        user = getattr(request.state, "user", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return

        import uuid as _uuid

        get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
        db_gen = get_db_fn()
        db = next(db_gen)
        try:
            started_at_dt = datetime.fromisoformat(started_at_str)
            finished_at_dt = datetime.fromisoformat(finished_at_str)

            batch = batches_repo.create_batch(
                db,
                batch_id=_uuid.UUID(batch_id),
                tenant_id=tenant_id,
                user_id=user.id if user else None,
                mode="apply",
            )
            summary = {
                "total_rows": total_rows,
                "applied_count": applied_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
            }
            status = "completed" if failed_count == 0 else "completed_with_errors"
            batches_repo.update_batch_status(
                db,
                batch_id=_uuid.UUID(batch_id),
                tenant_id=tenant_id,
                status=status,
                started_at=started_at_dt,
                finished_at=finished_at_dt,
                summary=summary,
            )
            batches_repo.emit_batch_audit(
                db,
                tenant_id=tenant_id,
                user_id=user.id if user else None,
                event_type="apply.apply.completed",
                meta={"batch_id": batch_id, **summary},
            )
            try:
                from backend.repositories import usage_repo
                usage_repo.emit_usage_event(
                    db,
                    tenant_id=tenant_id,
                    event_type="batch.apply",
                    meta={"batch_id": batch_id, "applied_count": applied_count},
                )
            except Exception:
                logger.debug("Failed to emit usage event (non-fatal)", exc_info=True)
            try:
                from backend.models import Tenant
                from backend.stripe_billing import report_usage_to_stripe
                tenant_obj = db.query(Tenant).filter(Tenant.id == tenant_id).first()
                if tenant_obj:
                    report_usage_to_stripe(
                        tenant_obj, "batch.apply", settings=get_settings()
                    )
            except Exception:
                logger.debug("Stripe usage reporting failed (non-fatal)", exc_info=True)
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except Exception:
        logger.debug("Failed to persist apply batch to DB (non-fatal)", exc_info=True)
