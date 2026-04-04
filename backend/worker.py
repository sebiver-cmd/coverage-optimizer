"""Arq worker for SB-Optima background jobs.

Run with::

    arq backend.worker.WorkerSettings

The worker connects to the Redis instance specified by ``REDIS_URL`` and
processes ``optimize_job`` tasks enqueued via the ``/jobs/optimize`` API.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from arq.connections import RedisSettings

from backend.config import get_settings
from backend.jobs_api import (
    JOB_KEY_PREFIX,
    JobStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Redis key helper (mirrors jobs_api)
# ---------------------------------------------------------------------------


def _redis_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}{job_id}"


async def _update_job(redis, job_id: str, status: JobStatus, *, result=None, error=None):
    """Persist an updated job record in Redis."""
    settings = get_settings()
    key = _redis_key(job_id)
    raw = await redis.get(key)
    record: dict = json.loads(raw) if raw else {"job_id": job_id}
    record["status"] = status.value
    record["updated_at"] = datetime.now(timezone.utc).isoformat()
    if result is not None:
        record["result"] = result
    if error is not None:
        record["error"] = error
    await redis.set(key, json.dumps(record), ex=settings.job_result_ttl_s)


# ---------------------------------------------------------------------------
# Job function
# ---------------------------------------------------------------------------


async def optimize_job(ctx: dict, job_id: str, payload: dict):
    """Run the optimisation pipeline in the background.

    *payload* is the dict representation of an ``OptimizeRequest``.
    """
    redis = ctx.get("redis") or ctx.get("arq")
    if redis is None:
        logger.error("optimize_job: no redis handle in ctx")
        return

    await _update_job(redis, job_id, JobStatus.running)

    try:
        # Import synchronous handler and run it in a thread so the async
        # event loop is not blocked by heavy pandas/SOAP work.
        import asyncio

        from backend.optimizer_api import OptimizeRequest, run_optimization

        request = OptimizeRequest(**payload)
        response = await asyncio.to_thread(run_optimization, request)
        result_data = response.model_dump()

        await _update_job(redis, job_id, JobStatus.completed, result=result_data)
        logger.info("Job %s completed successfully", job_id)

    except Exception as exc:
        error_msg = f"{type(exc).__name__}: {exc}"
        await _update_job(redis, job_id, JobStatus.failed, error=error_msg)
        logger.exception("Job %s failed: %s", job_id, error_msg)


# ---------------------------------------------------------------------------
# Worker settings
# ---------------------------------------------------------------------------


def _redis_settings() -> RedisSettings:
    settings = get_settings()
    url = settings.redis_url or "redis://localhost:6379/0"
    parsed = urlparse(url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or 0),
        password=parsed.password,
    )


class WorkerSettings:
    """Arq worker configuration — discovered by ``arq backend.worker.WorkerSettings``."""

    functions = [optimize_job]
    redis_settings = _redis_settings()
    max_jobs = 4
    job_timeout = 600  # 10 min
