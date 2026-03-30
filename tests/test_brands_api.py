"""Tests for GET /brands endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_client_factory(brands: dict[int, str]):
    """Return a mock DanDomainClient that yields *brands* from get_all_brands."""
    mock = MagicMock()
    mock.get_all_brands.return_value = brands
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@patch("backend.brands_api.DanDomainClient")
def test_brands_returns_sorted_list(mock_cls):
    """Brands are returned sorted by name (ascending) by default."""
    mock_cls.return_value = _mock_client_factory(
        {42: "Brand A", 99: "Brand B", 7: "Zebra Co"}
    )

    resp = client.get(
        "/brands",
        params={"api_username": "u", "api_password": "p"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    assert data[0] == {"id": 42, "name": "Brand A"}
    assert data[1] == {"id": 99, "name": "Brand B"}
    assert data[2] == {"id": 7, "name": "Zebra Co"}


@patch("backend.brands_api.DanDomainClient")
def test_brands_sort_desc(mock_cls):
    """Sorting by name descending works."""
    mock_cls.return_value = _mock_client_factory(
        {1: "Alpha", 2: "Charlie", 3: "Bravo"}
    )

    resp = client.get(
        "/brands",
        params={"api_username": "u", "api_password": "p", "sort": "desc"},
    )

    assert resp.status_code == 200
    data = resp.json()
    names = [b["name"] for b in data]
    assert names == ["Charlie", "Bravo", "Alpha"]


@patch("backend.brands_api.DanDomainClient")
def test_brands_empty(mock_cls):
    """An empty brand map returns an empty JSON list (not 404)."""
    mock_cls.return_value = _mock_client_factory({})

    resp = client.get(
        "/brands",
        params={"api_username": "u", "api_password": "p"},
    )

    assert resp.status_code == 200
    assert resp.json() == []


def test_brands_missing_credentials():
    """Missing credentials return 422 (FastAPI validation error)."""
    resp = client.get("/brands")
    assert resp.status_code == 422


@patch("backend.brands_api.DanDomainClient")
def test_brands_api_error(mock_cls):
    """DanDomainAPIError is surfaced as 502."""
    from dandomain_api import DanDomainAPIError

    mock_cls.return_value.__enter__ = MagicMock(
        side_effect=DanDomainAPIError("SOAP failure"),
    )

    resp = client.get(
        "/brands",
        params={"api_username": "u", "api_password": "p"},
    )

    assert resp.status_code == 502
    assert "SOAP failure" in resp.json()["detail"]


@patch("backend.brands_api.DanDomainClient")
def test_brands_ids_match_producer_id(mock_cls):
    """Returned IDs must be the same integers as PRODUCER_ID values."""
    mock_cls.return_value = _mock_client_factory({100: "Foo", 200: "Bar"})

    resp = client.get(
        "/brands",
        params={"api_username": "u", "api_password": "p"},
    )

    ids = {b["id"] for b in resp.json()}
    assert ids == {100, 200}
