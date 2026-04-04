"""Integration tests for the async jobs API — **require a running Redis**.

These tests are skipped by default.  To run them::

    RUN_INTEGRATION_TESTS=1 python -m pytest tests/test_jobs_integration.py -v

They exercise the real Redis read/write path without mocks.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

# Skip the entire module unless the flag is set.
pytestmark = pytest.mark.integration

_SKIP_REASON = "Set RUN_INTEGRATION_TESTS=1 and ensure Redis is running"


def _should_skip() -> bool:
    return os.environ.get("RUN_INTEGRATION_TESTS", "0") != "1"


if _should_skip():
    pytest.skip(_SKIP_REASON, allow_module_level=True)


from backend.config import get_settings
from backend.jobs_api import JOB_KEY_PREFIX, JobStatus, build_job_record


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def redis_url(monkeypatch):
    """Ensure REDIS_URL is set (use real or CI Redis)."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("REDIS_URL", url)
    return url


@pytest.fixture()
def client():
    from backend.main import app
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRedisRoundTrip:
    """Write / read a job record via the real Redis instance."""

    @pytest.mark.anyio
    async def test_write_and_read_record(self, redis_url, monkeypatch):
        import redis.asyncio as aioredis

        r = aioredis.from_url(redis_url, decode_responses=True)
        jid = str(uuid.uuid4())
        key = f"{JOB_KEY_PREFIX}{jid}"
        record = build_job_record(jid, JobStatus.queued)

        try:
            await r.set(key, json.dumps(record), ex=60)
            raw = await r.get(key)
            assert raw is not None
            loaded = json.loads(raw)
            assert loaded["job_id"] == jid
            assert loaded["status"] == "queued"
        finally:
            await r.delete(key)
            await r.aclose()

    @pytest.mark.anyio
    async def test_ttl_is_applied(self, redis_url, monkeypatch):
        import redis.asyncio as aioredis

        r = aioredis.from_url(redis_url, decode_responses=True)
        jid = str(uuid.uuid4())
        key = f"{JOB_KEY_PREFIX}{jid}"
        record = build_job_record(jid, JobStatus.completed, result={"ok": True})

        try:
            await r.set(key, json.dumps(record), ex=120)
            ttl = await r.ttl(key)
            assert 0 < ttl <= 120
        finally:
            await r.delete(key)
            await r.aclose()
