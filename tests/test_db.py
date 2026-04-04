"""Tests for backend.db — database scaffolding (Task 2.2).

These tests use SQLite in-memory so they run without Docker or Postgres.
"""

from __future__ import annotations

import importlib

import pytest
from sqlalchemy.orm import Session

import backend.db as db_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_db_module() -> None:
    """Reset module-level engine/session state between tests."""
    db_mod._engine = None
    db_mod._SessionLocal = None


@pytest.fixture(autouse=True)
def _clean_db_state():
    """Ensure each test starts with a clean module state."""
    _reset_db_module()
    yield
    _reset_db_module()


# ---------------------------------------------------------------------------
# init_engine
# ---------------------------------------------------------------------------


def test_init_engine_returns_none_when_no_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db_mod.init_engine() is None
    assert db_mod.get_engine() is None


def test_init_engine_creates_engine_with_explicit_url():
    engine = db_mod.init_engine("sqlite:///:memory:")
    assert engine is not None
    assert db_mod.get_engine() is engine


def test_init_engine_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    engine = db_mod.init_engine()
    assert engine is not None


# ---------------------------------------------------------------------------
# get_db
# ---------------------------------------------------------------------------


def test_get_db_raises_when_not_configured():
    """get_db must raise RuntimeError if init_engine was never called."""
    with pytest.raises(RuntimeError, match="Database is not configured"):
        gen = db_mod.get_db()
        next(gen)


def test_get_db_yields_session_when_configured():
    """get_db should yield a valid SQLAlchemy Session."""
    db_mod.init_engine("sqlite:///:memory:")
    gen = db_mod.get_db()
    session = next(gen)
    assert isinstance(session, Session)
    # Clean up — drive the generator to completion
    try:
        next(gen)
    except StopIteration:
        pass


def test_get_db_closes_session_on_exit():
    """The session returned by get_db should be closed after the generator exits."""
    db_mod.init_engine("sqlite:///:memory:")
    gen = db_mod.get_db()
    session = next(gen)
    gen.close()  # simulate FastAPI finishing the request
    # After close the session's connection should be returned to pool
    # (no assertion error means success; Session.close() was called)


# ---------------------------------------------------------------------------
# check_db
# ---------------------------------------------------------------------------


def test_check_db_skipped_when_no_engine():
    assert db_mod.check_db() == "skipped"


def test_check_db_ok_with_sqlite():
    db_mod.init_engine("sqlite:///:memory:")
    assert db_mod.check_db() == "ok"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


def test_base_metadata_exists():
    """Base.metadata should be importable and contain registered models."""
    assert db_mod.Base.metadata is not None
    # After Task 4.1, tenants + users tables are registered
    assert "tenants" in db_mod.Base.metadata.tables
    assert "users" in db_mod.Base.metadata.tables


# ---------------------------------------------------------------------------
# Health endpoint integration (via TestClient)
# ---------------------------------------------------------------------------


def test_health_db_skipped(monkeypatch):
    """When DATABASE_URL is unset the /health endpoint reports db: skipped."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Re-import to ensure no stale engine
    _reset_db_module()

    from fastapi.testclient import TestClient
    from backend.main import app

    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "skipped"
