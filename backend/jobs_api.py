"""Async jobs API — enqueue optimisation jobs and poll for results.

Endpoints
---------
``POST /jobs/optimize``
    Enqueue an optimisation job via Arq; returns ``{job_id}``.

``GET /jobs/{job_id}``
    Poll job status/result from Redis.

When ``REDIS_URL`` is not configured both endpoints return **503**.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import get_settings
from backend.optimizer_api import OptimizeRequest

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
        import redis.asyncio as aioredis  # noqa: WPS433

        return aioredis.from_url(settings.redis_url, decode_responses=True)
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
            database=int(parsed.path.lstrip("/") or 0),
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


@router.post("/optimize", response_model=EnqueueResponse)
async def enqueue_optimize(payload: OptimizeRequest) -> EnqueueResponse:
    """Enqueue an async optimisation job and return its ``job_id``."""
    settings = get_settings()
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
        # Persist initial job record
        record = build_job_record(job_id, JobStatus.queued)
        await r.set(
            _redis_key(job_id),
            json.dumps(record),
            ex=settings.job_result_ttl_s,
        )

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


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Return the current status (and result when completed) of a job."""
    _validate_uuid(job_id)

    settings = get_settings()
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
