"""Unit tests for the async jobs API (``backend/jobs_api.py``).

All tests run **without** Redis — they mock or unset ``REDIS_URL``.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.jobs_api import (
    JOB_KEY_PREFIX,
    JobStatus,
    JobStatusResponse,
    build_job_record,
    _redis_key,
    _validate_uuid,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _no_redis(monkeypatch):
    """Ensure REDIS_URL is unset for the test."""
    monkeypatch.delenv("REDIS_URL", raising=False)


@pytest.fixture()
def _with_redis_url(monkeypatch):
    """Set a dummy REDIS_URL so the config gate passes."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture()
def client():
    """Return a TestClient around the FastAPI app."""
    from backend.main import app

    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 503 when REDIS_URL is missing
# ---------------------------------------------------------------------------


class TestNoRedisReturns503:
    """Both endpoints must return 503 when REDIS_URL is not configured."""

    def test_post_optimize_503(self, client, _no_redis):
        resp = client.post(
            "/jobs/optimize",
            json={
                "api_username": "test@example.com",
                "api_password": "pw",
            },
        )
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()

    def test_get_job_status_503(self, client, _no_redis):
        job_id = str(uuid.uuid4())
        resp = client.get(f"/jobs/{job_id}")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# UUID validation on GET /jobs/{job_id}
# ---------------------------------------------------------------------------


class TestJobIdValidation:
    def test_invalid_uuid_returns_422(self, client, _with_redis_url):
        resp = client.get("/jobs/not-a-uuid")
        assert resp.status_code == 422
        assert "Invalid job_id" in resp.json()["detail"]

    def test_valid_uuid_passes_validation(self):
        job_id = str(uuid.uuid4())
        result = _validate_uuid(job_id)
        assert result == job_id


# ---------------------------------------------------------------------------
# Job record serialisation / deserialisation
# ---------------------------------------------------------------------------


class TestJobRecordSerialization:
    def test_build_job_record_queued(self):
        jid = str(uuid.uuid4())
        rec = build_job_record(jid, JobStatus.queued)
        assert rec["job_id"] == jid
        assert rec["status"] == "queued"
        assert rec["result"] is None
        assert rec["error"] is None
        assert "created_at" in rec
        assert "updated_at" in rec

    def test_build_job_record_completed_with_result(self):
        jid = str(uuid.uuid4())
        result_payload = {"summary": {"total": 10}, "rows": []}
        rec = build_job_record(jid, JobStatus.completed, result=result_payload)
        assert rec["status"] == "completed"
        assert rec["result"] == result_payload

    def test_build_job_record_failed_with_error(self):
        jid = str(uuid.uuid4())
        rec = build_job_record(jid, JobStatus.failed, error="Connection refused")
        assert rec["status"] == "failed"
        assert rec["error"] == "Connection refused"

    def test_round_trip_json(self):
        jid = str(uuid.uuid4())
        rec = build_job_record(jid, JobStatus.completed, result={"ok": True})
        serialized = json.dumps(rec)
        deserialized = json.loads(serialized)
        response = JobStatusResponse(**deserialized)
        assert response.job_id == jid
        assert response.status == JobStatus.completed
        assert response.result == {"ok": True}


# ---------------------------------------------------------------------------
# Redis key helper
# ---------------------------------------------------------------------------


class TestRedisKeyHelper:
    def test_key_prefix(self):
        jid = "abc-123"
        assert _redis_key(jid) == f"{JOB_KEY_PREFIX}abc-123"


# ---------------------------------------------------------------------------
# Enqueue with mocked Redis + Arq
# ---------------------------------------------------------------------------


class TestEnqueueWithMockedRedis:
    """POST /jobs/optimize with a fully mocked Redis + Arq layer."""

    @pytest.mark.anyio
    async def test_enqueue_returns_job_id(self, client, _with_redis_url):
        mock_redis = AsyncMock()
        mock_redis.set = AsyncMock()
        mock_redis.aclose = AsyncMock()

        mock_pool = AsyncMock()
        mock_pool.enqueue_job = AsyncMock()
        mock_pool.aclose = AsyncMock()

        with (
            patch("backend.jobs_api._get_redis", return_value=mock_redis),
            patch("backend.jobs_api._get_arq_pool", return_value=mock_pool),
        ):
            resp = client.post(
                "/jobs/optimize",
                json={
                    "api_username": "test@example.com",
                    "api_password": "pw",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        # Verify it's a valid UUID
        uuid.UUID(body["job_id"], version=4)
        # Verify Redis was called to store the record
        mock_redis.set.assert_called_once()
        # Verify Arq pool was used to enqueue
        mock_pool.enqueue_job.assert_called_once()


class TestGetJobWithMockedRedis:
    """GET /jobs/{job_id} with mocked Redis returning stored records."""

    @pytest.mark.anyio
    async def test_completed_job(self, client, _with_redis_url):
        jid = str(uuid.uuid4())
        record = build_job_record(
            jid, JobStatus.completed, result={"summary": {}, "rows": []}
        )

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(record))
        mock_redis.aclose = AsyncMock()

        with patch("backend.jobs_api._get_redis", return_value=mock_redis):
            resp = client.get(f"/jobs/{jid}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["result"] == {"summary": {}, "rows": []}

    @pytest.mark.anyio
    async def test_missing_job_returns_404(self, client, _with_redis_url):
        jid = str(uuid.uuid4())

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.aclose = AsyncMock()

        with patch("backend.jobs_api._get_redis", return_value=mock_redis):
            resp = client.get(f"/jobs/{jid}")

        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_failed_job(self, client, _with_redis_url):
        jid = str(uuid.uuid4())
        record = build_job_record(jid, JobStatus.failed, error="Timeout")

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(record))
        mock_redis.aclose = AsyncMock()

        with patch("backend.jobs_api._get_redis", return_value=mock_redis):
            resp = client.get(f"/jobs/{jid}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error"] == "Timeout"
        assert body["result"] is None


# ---------------------------------------------------------------------------
# Config setting: JOB_RESULT_TTL_S
# ---------------------------------------------------------------------------


class TestJobResultTtlConfig:
    def test_default_ttl(self, _no_redis):
        from backend.config import get_settings

        s = get_settings()
        assert s.job_result_ttl_s == 3600

    def test_custom_ttl(self, monkeypatch):
        monkeypatch.setenv("JOB_RESULT_TTL_S", "7200")
        from backend.config import get_settings

        s = get_settings()
        assert s.job_result_ttl_s == 7200
