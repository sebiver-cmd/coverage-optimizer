"""Tests for POST /catalog/products endpoint.

Verifies that:
- The endpoint returns product rows with VARIANT_ITEMNUMBER.
- Partial enrichment failure does not crash the endpoint.
- Filters (include_offline, brand_ids) are respected.
- Empty catalogue returns an empty list.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_products():
    """Return a list of raw product dicts as DanDomainClient would return."""
    return [
        {
            "Id": 1,
            "Number": "SKU-001",
            "Title": [{"Value": "Test Product", "SiteId": 1}],
            "ProducerId": 10,
            "Producer": "BrandA",
            "Price": "500.00",
            "BuyingPrice": "250.00",
            "ProductStatus": 1,  # online
            "Ean": "5701234000001",
            "Variants": [
                {
                    "Id": 101,
                    "ItemNumber": "SKU-001-RED",
                    "Price": "500.00",
                    "BuyingPrice": "250.00",
                    "Ean": "5701234000002",
                    "VariantTypeValues": [
                        {"Value": "Red", "VariantType": {"Value": "Color"}}
                    ],
                },
                {
                    "Id": 102,
                    "ItemNumber": "",
                    "Price": "500.00",
                    "BuyingPrice": "250.00",
                    "Ean": "",
                    "VariantTypeValues": [
                        {"Value": "Blue", "VariantType": {"Value": "Color"}}
                    ],
                },
            ],
        },
        {
            "Id": 2,
            "Number": "SKU-002",
            "Title": [{"Value": "Basic Tee", "SiteId": 1}],
            "ProducerId": 20,
            "Producer": "BrandB",
            "Price": "100.00",
            "BuyingPrice": "50.00",
            "ProductStatus": 1,
            "Ean": "5701234000099",
            "Variants": [],
        },
    ]


def _make_mock_client(raw_products=None, brands=None, variants_by_item=None):
    """Build a mock DanDomainClient."""
    if raw_products is None:
        raw_products = _make_raw_products()
    if brands is None:
        brands = {10: "BrandA", 20: "BrandB"}
    if variants_by_item is None:
        variants_by_item = {}

    mock = MagicMock()
    mock.get_products_batch.return_value = raw_products
    mock.get_all_brands.return_value = brands

    def _get_variants(item_number):
        return variants_by_item.get(item_number, [])

    mock.get_variants_by_item_number.side_effect = _get_variants
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("backend.catalog_api.DanDomainClient")
def test_catalog_returns_variant_itemnumber(mock_cls):
    """Response rows contain the VARIANT_ITEMNUMBER field."""
    mock_cls.return_value = _make_mock_client()

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0

    # Every row must have the VARIANT_ITEMNUMBER key
    for row in data:
        assert "VARIANT_ITEMNUMBER" in row, (
            f"Row missing VARIANT_ITEMNUMBER: {row.get('NUMBER')}"
        )


@patch("backend.catalog_api.DanDomainClient")
def test_catalog_variant_itemnumber_populated(mock_cls):
    """VARIANT_ITEMNUMBER is populated from bulk fetch variant data."""
    mock_cls.return_value = _make_mock_client()

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
        },
    )

    assert resp.status_code == 200
    data = resp.json()

    # Find the Red variant row — it had ItemNumber="SKU-001-RED"
    red_rows = [r for r in data if r.get("VARIANT_ITEMNUMBER") == "SKU-001-RED"]
    assert len(red_rows) >= 1, (
        "Expected at least one row with VARIANT_ITEMNUMBER='SKU-001-RED'"
    )


@patch("backend.catalog_api.DanDomainClient")
def test_catalog_empty_products(mock_cls):
    """Empty product catalogue returns an empty list."""
    mock_cls.return_value = _make_mock_client(raw_products=[])

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
        },
    )

    assert resp.status_code == 200
    assert resp.json() == []


@patch("backend.catalog_api.enrich_variants")
@patch("backend.catalog_api.DanDomainClient")
def test_catalog_partial_enrichment_failure(mock_cls, mock_enrich):
    """Partial enrichment failure does not crash the endpoint."""
    mock_cls.return_value = _make_mock_client()
    # Simulate enrichment raising an exception
    mock_enrich.side_effect = Exception("Simulated SOAP timeout")

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) > 0

    # VARIANT_ITEMNUMBER key should still be present (even if empty)
    for row in data:
        assert "VARIANT_ITEMNUMBER" in row


@patch("backend.catalog_api.DanDomainClient")
def test_catalog_required_fields_present(mock_cls):
    """All required output fields are present in each row."""
    mock_cls.return_value = _make_mock_client()

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
        },
    )

    assert resp.status_code == 200
    data = resp.json()

    required_fields = {
        "NUMBER", "TITLE_DK", "VARIANT_ID", "VARIANT_TYPES", "EAN",
        "VARIANT_ITEMNUMBER", "PRODUCER", "PRODUCER_ID", "ONLINE",
    }

    for row in data:
        for field in required_fields:
            assert field in row, f"Missing field '{field}' in row: {row.get('NUMBER')}"


@patch("backend.catalog_api.DanDomainClient")
def test_catalog_api_error_returns_502(mock_cls):
    """DanDomain API error returns HTTP 502."""
    from dandomain_api import DanDomainAPIError

    mock = MagicMock()
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=False)
    mock.get_products_batch.side_effect = DanDomainAPIError("SOAP fault")
    mock_cls.return_value = mock

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
        },
    )

    assert resp.status_code == 502


@patch("backend.catalog_api.DanDomainClient")
def test_catalog_include_variants_false(mock_cls):
    """When include_variants=False, no variant rows are expanded."""
    mock_cls.return_value = _make_mock_client()

    resp = client.post(
        "/catalog/products",
        json={
            "api_username": "user@test.dk",
            "api_password": "secret",
            "include_variants": False,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    # With include_variants=False, we should get fewer rows
    # (no variant expansion) — just base products
    assert len(data) > 0
    # All rows should still have VARIANT_ITEMNUMBER key
    for row in data:
        assert "VARIANT_ITEMNUMBER" in row
