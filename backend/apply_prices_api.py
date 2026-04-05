"""Dry-run apply-prices endpoint.

Reuses the existing optimisation pipeline from :mod:`backend.optimizer_api`
to compute a detailed "change set" (old price vs. new price, percentage
change) **without writing anything** to HostedShop / DanDomain.

This module intentionally does **not** import any write/push/update
functions.

When ``SBOPTIMA_AUTH_REQUIRED=true``, batch records are persisted to the
database (durable, tenant-scoped) in addition to disk manifests.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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


# ---------------------------------------------------------------------------
# Batch list endpoint (viewer+)
# ---------------------------------------------------------------------------


class BatchListItem(BaseModel):
    """Single item returned by ``GET /apply-prices/batches``."""

    id: str
    mode: str
    status: str
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    user_id: Optional[str] = None


class BatchListResponse(BaseModel):
    """Paginated list returned by ``GET /apply-prices/batches``."""

    total: int
    items: list[BatchListItem]


def _history_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="History requires auth — set SBOPTIMA_AUTH_REQUIRED=true.")


def _parse_iso(value: str | None):
    """Parse an ISO datetime string, returning *None* on failure."""
    if not value:
        return None
    try:
        from datetime import datetime as _dt
        return _dt.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@router.get("/apply-prices/batches", response_model=BatchListResponse, dependencies=[Depends(require_role("viewer"))])
def list_batches_endpoint(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> BatchListResponse:
    """Return a paginated, tenant-scoped list of apply batches.

    Only available when ``SBOPTIMA_AUTH_REQUIRED=true``.
    """
    from backend.config import get_settings as _get_settings

    settings = _get_settings()
    if not settings.sboptima_auth_required:
        raise _history_unavailable()

    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    since_dt = _parse_iso(since)
    until_dt = _parse_iso(until)

    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise _history_unavailable()

    from backend.db import get_db
    from backend.repositories import batches_repo

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        total, items = batches_repo.list_batches(
            db,
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            status=status,
            mode=mode,
            since=since_dt,
            until=until_dt,
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass

    return BatchListResponse(
        total=total,
        items=[
            BatchListItem(
                id=str(b.id),
                mode=b.mode,
                status=b.status,
                created_at=b.created_at.isoformat() if b.created_at else None,
                finished_at=b.finished_at.isoformat() if b.finished_at else None,
                user_id=str(b.user_id) if b.user_id else None,
            )
            for b in items
        ],
    )


@router.post("/apply-prices/dry-run", response_model=DryRunResponse, dependencies=[Depends(require_role("operator"))])
def dry_run_apply(payload: DryRunRequest, request: Request) -> DryRunResponse:
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

    # Persist to DB when auth is enabled
    _persist_batch_to_db(request, batch_id, "dry_run", manifest, summary.model_dump())

    return DryRunResponse(
        batch_id=batch_id,
        changes=changes,
        summary=summary,
    )


@router.get("/apply-prices/batch/{batch_id}", dependencies=[Depends(require_role("viewer"))])
def get_batch(batch_id: str, request: Request) -> dict:
    """Return a previously persisted dry-run manifest.

    ``batch_id`` is strictly validated as a UUID-4 string to prevent
    path-traversal attacks.

    When auth is enabled, reads from DB (tenant-scoped) first.
    """
    if not UUID_RE.match(batch_id):
        raise HTTPException(status_code=422, detail="Invalid batch_id format.")

    # When auth is enabled, try reading from DB first (tenant-scoped)
    from backend.config import get_settings as _get_settings
    settings = _get_settings()
    if settings.sboptima_auth_required:
        db_result = _get_batch_from_db(request, batch_id)
        if db_result is not None:
            return db_result

    # Legacy: read from disk
    manifest_path = BATCH_DIR / f"{batch_id}.json"
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Batch not found.")

    return json.loads(manifest_path.read_text(encoding="utf-8"))


@router.post("/apply-prices/create-manifest", response_model=CreateManifestResponse, dependencies=[Depends(require_role("operator"))])
def create_manifest(payload: CreateManifestRequest, request: Request) -> CreateManifestResponse:
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

    # Persist to DB when auth is enabled
    _persist_batch_to_db(request, batch_id, "create_manifest", manifest, summary.model_dump())

    return CreateManifestResponse(
        batch_id=batch_id,
        changes=changes,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# DB persistence helpers
# ---------------------------------------------------------------------------


def _persist_batch_to_db(
    request: Request,
    batch_id: str,
    mode: str,
    manifest: dict,
    summary: dict,
) -> None:
    """Write an ApplyBatch row when auth is enabled (best-effort)."""
    try:
        from backend.config import get_settings as _get_settings
        settings = _get_settings()
        if not settings.sboptima_auth_required:
            return

        from backend.db import get_db
        from backend.repositories import batches_repo

        user = getattr(request.state, "user", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return

        get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
        db_gen = get_db_fn()
        db = next(db_gen)
        try:
            # Sanitize: strip credentials from stored manifest
            safe_manifest = {k: v for k, v in manifest.items()
                            if k not in ("api_password", "api_username")}

            batch = batches_repo.create_batch(
                db,
                batch_id=uuid.UUID(batch_id),
                tenant_id=tenant_id,
                user_id=user.id if user else None,
                mode=mode,
                manifest_meta=safe_manifest,
            )
            batches_repo.update_batch_status(
                db,
                batch_id=uuid.UUID(batch_id),
                tenant_id=tenant_id,
                status="completed",
                finished_at=datetime.now(timezone.utc),
                summary=summary,
            )
            batches_repo.emit_batch_audit(
                db,
                tenant_id=tenant_id,
                user_id=user.id if user else None,
                event_type=f"apply.{mode}.completed",
                meta={"batch_id": batch_id},
            )
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except Exception:
        logger.debug("Failed to persist batch to DB (non-fatal)", exc_info=True)


def _get_batch_from_db(request: Request, batch_id: str) -> dict | None:
    """Read batch from DB, scoped to the current tenant. Returns None on miss."""
    try:
        from backend.db import get_db
        from backend.repositories import batches_repo

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return None

        get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
        db_gen = get_db_fn()
        db = next(db_gen)
        try:
            batch = batches_repo.get_batch(
                db, batch_id=uuid.UUID(batch_id), tenant_id=tenant_id
            )
            if batch is None:
                raise HTTPException(status_code=404, detail="Batch not found.")
            result: dict = {}
            if batch.manifest_json:
                result = json.loads(batch.manifest_json)
            result["batch_id"] = str(batch.id)
            result["status"] = batch.status
            result["mode"] = batch.mode
            if batch.summary_json:
                result["summary"] = json.loads(batch.summary_json)
            return result
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except HTTPException:
        raise
    except Exception:
        logger.debug("Failed to read batch from DB (non-fatal)", exc_info=True)
        return None
