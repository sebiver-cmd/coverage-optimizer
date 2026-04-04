"""Shared fixtures for the test suite."""

from __future__ import annotations

import pytest

from backend.config import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """Ensure every test gets a fresh Settings object.

    Pydantic Settings reads from env vars at construction time, so the
    ``@lru_cache`` on ``get_settings()`` must be invalidated whenever a
    test mutates environment variables via ``monkeypatch``.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
