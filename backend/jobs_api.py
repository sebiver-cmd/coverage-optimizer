"""Async jobs API — enqueue optimisation jobs and poll for results.

Endpoints
---------
``POST /jobs/optimize``
    Enqueue an optimisation job via Arq; returns ``{job_id}``.

``GET /jobs/{job_id}``
    Poll job status/result from Redis.

When ``REDIS_URL`` is not configured both endpoints return **503**.

When ``SBOPTIMA_AUTH_REQUIRED=true``, job records are persisted to the
database (durable, tenant-scoped) in addition to Redis (fast polling).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.config import get_settings
from backend.billing_gate import check_billing_gate
from backend.optimizer_api import OptimizeRequest
from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Redis key prefix for job records.
JOB_KEY_PREFIX = "sboptima:job:"


class JobStatus(str, Enum):
    """Lifecycle states for an async job."""

    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class EnqueueResponse(BaseModel):
    """Returned by ``POST /jobs/optimize``."""

    job_id: str


class JobStatusResponse(BaseModel):
    """Returned by ``GET /jobs/{job_id}``."""

    job_id: str
    status: JobStatus
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redis_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


def _validate_uuid(value: str) -> str:
    """Validate that *value* is a well-formed UUID4 string."""
    try:
        uuid.UUID(value, version=4)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid job_id format (expected UUID): {value!r}",
        )
    return value


async def _get_redis():
    """Return an ``redis.asyncio.Redis`` connection or *None*."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as redis_async  # noqa: WPS433

        return redis_async.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        logger.exception("Failed to connect to Redis")
        return None


async def _get_arq_pool():
    """Return an Arq Redis pool or *None*."""
    settings = get_settings()
    if not settings.redis_url:
        return None
    try:
        from arq import create_pool
        from arq.connections import RedisSettings

        # Parse redis_url into RedisSettings
        from urllib.parse import urlparse

        parsed = urlparse(settings.redis_url)
        rs = RedisSettings(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            database=int(parsed.path.lstrip("/") or 0) if parsed.path.lstrip("/").isdigit() else 0,
            password=parsed.password,
        )
        return await create_pool(rs)
    except Exception:
        logger.exception("Failed to create Arq pool")
        return None


def _service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Jobs service not configured — REDIS_URL is not set.",
    )


# ---------------------------------------------------------------------------
# Job record helpers (used by both API and worker)
# ---------------------------------------------------------------------------


def build_job_record(
    job_id: str,
    status: JobStatus,
    *,
    result: Any = None,
    error: str | None = None,
) -> dict:
    """Create a serialisable job-record dict."""
    now = datetime.now(timezone.utc).isoformat()
    return {
        "job_id": job_id,
        "status": status.value,
        "created_at": now,
        "updated_at": now,
        "result": result,
        "error": error,
    }


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# List endpoint (viewer+ — lower role than the router default)
# ---------------------------------------------------------------------------


class JobListItem(BaseModel):
    """Single item returned by ``GET /jobs``."""

    id: str
    status: str
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    user_id: Optional[str] = None
    error: Optional[str] = None


class JobListResponse(BaseModel):
    """Paginated list returned by ``GET /jobs``."""

    total: int
    items: list[JobListItem]


def _history_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="History requires auth — set SBOPTIMA_AUTH_REQUIRED=true.")


@router.get("", response_model=JobListResponse, dependencies=[Depends(require_role("viewer"))])
def list_jobs_endpoint(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> JobListResponse:
    """Return a paginated, tenant-scoped list of optimisation jobs.

    Only available when ``SBOPTIMA_AUTH_REQUIRED=true``.
    """
    settings = get_settings()
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
    from backend.repositories import jobs_repo

    get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
    db_gen = get_db_fn()
    db = next(db_gen)
    try:
        total, items = jobs_repo.list_jobs(
            db,
            tenant_id=tenant_id,
            limit=limit,
            offset=offset,
            status=status,
            since=since_dt,
            until=until_dt,
        )
    finally:
        try:
            next(db_gen, None)
        except StopIteration:
            pass

    return JobListResponse(
        total=total,
        items=[
            JobListItem(
                id=str(j.id),
                status=j.status,
                created_at=j.created_at.isoformat() if j.created_at else None,
                started_at=j.started_at.isoformat() if j.started_at else None,
                finished_at=j.finished_at.isoformat() if j.finished_at else None,
                user_id=str(j.user_id) if j.user_id else None,
                error=j.error,
            )
            for j in items
        ],
    )


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO datetime string, returning *None* on failure or ``None`` input."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@router.post("/optimize", response_model=EnqueueResponse, dependencies=[Depends(require_role("operator")), Depends(check_billing_gate)])
async def enqueue_optimize(payload: OptimizeRequest, request: Request) -> EnqueueResponse:
    """Enqueue an async optimisation job and return its ``job_id``."""
    settings = get_settings()

    # Quota enforcement (Task 7.1) — only when auth is enabled
    if settings.sboptima_auth_required:
        _check_optimize_quota(request)

    if not settings.redis_url:
        raise _service_unavailable()

    r = await _get_redis()
    if r is None:
        raise _service_unavailable()

    pool = await _get_arq_pool()
    if pool is None:
        await r.aclose()
        raise _service_unavailable()

    job_id = str(uuid.uuid4())

    try:
        # Persist initial job record in Redis (fast polling)
        record = build_job_record(job_id, JobStatus.queued)
        await r.set(
            _redis_key(job_id),
            json.dumps(record),
            ex=settings.job_result_ttl_s,
        )

        # When auth is enabled, persist to DB as durable record
        if settings.sboptima_auth_required:
            _persist_job_to_db(request, job_id, payload)

        # Enqueue via Arq
        await pool.enqueue_job(
            "optimize_job",
            job_id,
            payload.model_dump(),
            _job_id=f"opt-{job_id}",
        )
    finally:
        await r.aclose()
        await pool.aclose()

    return EnqueueResponse(job_id=job_id)


def _check_optimize_quota(request: Request) -> None:
    """Enforce daily optimize-job quota (best-effort, non-fatal on DB error)."""
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
            check_quota(db, tenant, "optimize_job")
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except HTTPException:
        raise
    except Exception:
        logger.debug("Quota check failed (non-fatal)", exc_info=True)


def _persist_job_to_db(request: Request, job_id: str, payload: OptimizeRequest) -> None:
    """Write an OptimizationJob row when auth is enabled."""
    try:
        from backend.db import get_db
        from backend.repositories import jobs_repo

        user = getattr(request.state, "user", None)
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return

        get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
        db_gen = get_db_fn()
        db = next(db_gen)
        try:
            # Sanitize: strip credentials from stored request
            meta = payload.model_dump()
            for secret_field in ("api_password", "api_username"):
                meta.pop(secret_field, None)

            jobs_repo.create_job(
                db,
                job_id=uuid.UUID(job_id),
                tenant_id=tenant_id,
                user_id=user.id if user else None,
                request_meta=meta,
            )
            jobs_repo.emit_job_audit(
                db,
                tenant_id=tenant_id,
                user_id=user.id if user else None,
                event_type="job.enqueued",
                meta={"job_id": job_id},
            )
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except Exception:
        logger.debug("Failed to persist job to DB (non-fatal)", exc_info=True)


@router.get("/{job_id}", response_model=JobStatusResponse, dependencies=[Depends(require_role("operator"))])
async def get_job_status(job_id: str, request: Request) -> JobStatusResponse:
    """Return the current status (and result when completed) of a job."""
    _validate_uuid(job_id)

    settings = get_settings()

    # When auth is enabled, try DB first (durable, tenant-scoped)
    if settings.sboptima_auth_required:
        db_response = _get_job_from_db(request, job_id)
        if db_response is not None:
            return db_response

    # Fall back to Redis (legacy or fast-polling)
    if not settings.redis_url:
        raise _service_unavailable()

    r = await _get_redis()
    if r is None:
        raise _service_unavailable()

    try:
        raw = await r.get(_redis_key(job_id))
    finally:
        await r.aclose()

    if raw is None:
        raise HTTPException(status_code=404, detail="Job not found or expired.")

    data: dict = json.loads(raw)
    return JobStatusResponse(**data)


def _get_job_from_db(request: Request, job_id: str) -> JobStatusResponse | None:
    """Read job from DB, scoped to the current tenant. Returns None on miss."""
    try:
        from backend.db import get_db
        from backend.repositories import jobs_repo

        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id is None:
            return None

        get_db_fn = request.app.dependency_overrides.get(get_db, get_db)
        db_gen = get_db_fn()
        db = next(db_gen)
        try:
            job = jobs_repo.get_job(db, job_id=uuid.UUID(job_id), tenant_id=tenant_id)
            if job is None:
                raise HTTPException(status_code=404, detail="Job not found.")
            return JobStatusResponse(
                job_id=str(job.id),
                status=JobStatus(job.status),
                created_at=job.created_at.isoformat() if job.created_at else None,
                updated_at=job.finished_at.isoformat() if job.finished_at else (
                    job.started_at.isoformat() if job.started_at else (
                        job.created_at.isoformat() if job.created_at else None
                    )
                ),
                result=json.loads(job.result_json) if job.result_json else None,
                error=job.error,
            )
        finally:
            try:
                next(db_gen, None)
            except StopIteration:
                pass
    except HTTPException:
        raise
    except Exception:
        logger.debug("Failed to read job from DB (non-fatal)", exc_info=True)
        return None
