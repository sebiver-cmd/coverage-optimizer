"""Tests for the Price Optimizer backend integration helpers.

Validates the three helper functions that wire the Streamlit UI
to the FastAPI backend:
- ``_fetch_brands_from_backend``
- ``_run_backend_optimization``
- ``_build_dataframes_from_response``
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

# Import the private helpers directly from the module.
from ui.pages.price_optimizer import (
    _fetch_brands_from_backend,
    _run_backend_optimization,
    _build_dataframes_from_response,
)


# ---------------------------------------------------------------------------
# Sample API responses
# ---------------------------------------------------------------------------

_BRANDS_RESPONSE = [
    {"id": 42, "name": "Brand A"},
    {"id": 99, "name": "Brand B"},
]

_OPTIMIZE_RESPONSE = {
    "summary": {
        "total_products": 2,
        "base_products": 2,
        "total_rows": 2,
        "adjusted_count": 1,
        "unchanged_count": 1,
        "adjusted_pct": 50.0,
        "avg_current_coverage_pct": 40.0,
        "avg_suggested_coverage_pct": 52.5,
    },
    "rows": [
        {
            "product_id": "101",
            "title": "Widget Pro",
            "item_number": "WID-001",
            "producer": "Supplier Inc",
            "buy_price": 100.0,
            "current_price": 200.0,
            "current_price_ex_vat": 160.0,
            "current_coverage_pct": 37.5,
            "suggested_price": 249.0,
            "suggested_price_ex_vat": 199.2,
            "suggested_coverage_pct": 49.8,
            "needs_adjustment": True,
            "variant_id": "",
            "variant_types": "",
        },
        {
            "product_id": "102",
            "title": "Gadget Lite",
            "item_number": "GAD-002",
            "producer": "Other Co",
            "buy_price": 50.0,
            "current_price": 150.0,
            "current_price_ex_vat": 120.0,
            "current_coverage_pct": 58.33,
            "suggested_price": 150.0,
            "suggested_price_ex_vat": 120.0,
            "suggested_coverage_pct": 58.33,
            "needs_adjustment": False,
            "variant_id": "V1",
            "variant_types": "Color: Red",
        },
    ],
}


# ---------------------------------------------------------------------------
# _fetch_brands_from_backend
# ---------------------------------------------------------------------------

class TestFetchBrands:
    """Tests for ``_fetch_brands_from_backend``."""

    @patch("ui.pages.price_optimizer.requests.get")
    def test_returns_brand_list(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _BRANDS_RESPONSE
        mock_get.return_value = mock_resp

        result = _fetch_brands_from_backend(
            "http://localhost:8000", "user", "pass",
        )

        assert result == _BRANDS_RESPONSE
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert "api_username" in call_kwargs.kwargs.get("params", {}) or \
               "api_username" in (call_kwargs[1].get("params", {}) if len(call_kwargs) > 1 else call_kwargs.kwargs.get("params", {}))

    @patch("ui.pages.price_optimizer.requests.get")
    def test_returns_empty_on_error(self, mock_get):
        import requests as _req
        mock_get.side_effect = _req.ConnectionError("down")

        result = _fetch_brands_from_backend(
            "http://localhost:8000", "user", "pass",
        )

        assert result == []

    @patch("ui.pages.price_optimizer.requests.get")
    def test_strips_trailing_slash_from_url(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []
        mock_get.return_value = mock_resp

        _fetch_brands_from_backend(
            "http://localhost:8000/", "user", "pass",
        )

        url_called = mock_get.call_args[0][0]
        assert url_called == "http://localhost:8000/brands"


# ---------------------------------------------------------------------------
# _run_backend_optimization
# ---------------------------------------------------------------------------

class TestRunOptimization:
    """Tests for ``_run_backend_optimization``."""

    @patch("ui.pages.price_optimizer.st")
    @patch("ui.pages.price_optimizer.requests.post")
    def test_returns_response_on_success(self, mock_post, _mock_st):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _OPTIMIZE_RESPONSE
        mock_post.return_value = mock_resp

        result = _run_backend_optimization(
            "http://localhost:8000", "user", "pass",
            site_id=1, brand_ids=None, include_offline=False,
            include_variants=True, price_pct=0.0, beautify_digit=9,
        )

        assert result is not None
        assert result["summary"]["total_products"] == 2

    @patch("ui.pages.price_optimizer.st")
    @patch("ui.pages.price_optimizer.requests.post")
    def test_returns_none_on_connection_error(self, mock_post, mock_st):
        import requests as _req
        mock_post.side_effect = _req.ConnectionError("down")

        result = _run_backend_optimization(
            "http://localhost:8000", "user", "pass",
            site_id=1, brand_ids=None, include_offline=False,
            include_variants=True, price_pct=0.0, beautify_digit=9,
        )

        assert result is None
        mock_st.error.assert_called_once()

    @patch("ui.pages.price_optimizer.st")
    @patch("ui.pages.price_optimizer.requests.post")
    def test_returns_none_on_http_error(self, mock_post, mock_st):
        import requests as _req

        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.json.return_value = {"detail": "SOAP failure"}
        http_err = _req.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = http_err
        mock_post.return_value = mock_resp

        result = _run_backend_optimization(
            "http://localhost:8000", "user", "pass",
            site_id=1, brand_ids=None, include_offline=False,
            include_variants=True, price_pct=0.0, beautify_digit=9,
        )

        assert result is None
        mock_st.error.assert_called_once()

    @patch("ui.pages.price_optimizer.st")
    @patch("ui.pages.price_optimizer.requests.post")
    def test_sends_brand_ids_when_provided(self, mock_post, _mock_st):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _OPTIMIZE_RESPONSE
        mock_post.return_value = mock_resp

        _run_backend_optimization(
            "http://localhost:8000", "user", "pass",
            site_id=1, brand_ids=[42, 99], include_offline=False,
            include_variants=True, price_pct=5.0, beautify_digit=9,
        )

        payload = mock_post.call_args.kwargs["json"]
        assert payload["brand_ids"] == [42, 99]
        assert payload["price_pct"] == 5.0

    @patch("ui.pages.price_optimizer.st")
    @patch("ui.pages.price_optimizer.requests.post")
    def test_omits_brand_ids_when_none(self, mock_post, _mock_st):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _OPTIMIZE_RESPONSE
        mock_post.return_value = mock_resp

        _run_backend_optimization(
            "http://localhost:8000", "user", "pass",
            site_id=1, brand_ids=None, include_offline=False,
            include_variants=True, price_pct=0.0, beautify_digit=9,
        )

        payload = mock_post.call_args.kwargs["json"]
        assert "brand_ids" not in payload


# ---------------------------------------------------------------------------
# _build_dataframes_from_response
# ---------------------------------------------------------------------------

class TestBuildDataframes:
    """Tests for ``_build_dataframes_from_response``."""

    def test_returns_five_tuple(self):
        result = _build_dataframes_from_response(_OPTIMIZE_RESPONSE)
        assert len(result) == 5

    def test_final_df_shape(self):
        final_df, _, _, _, _ = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert len(final_df) == 2
        expected_cols = {
            "PRODUCT_ID", "TITLE_DK", "NUMBER", "PRODUCER",
            "BUY_PRICE", "PRICE_EX_VAT", "PRICE", "COVERAGE_RATE_%",
            "VARIANT_ID", "VARIANT_TYPES",
            "NEW_PRICE_EX_VAT", "NEW_PRICE", "NEW_COVERAGE_RATE_%",
        }
        assert set(final_df.columns) == expected_cols

    def test_adjusted_count(self):
        _, adjusted_count, _, _, _ = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert adjusted_count == 1

    def test_adjusted_mask(self):
        _, _, adjusted_mask, _, _ = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert isinstance(adjusted_mask, np.ndarray)
        assert list(adjusted_mask) == [True, False]

    def test_import_df_contains_adjusted_only(self):
        _, _, _, import_df, _ = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert len(import_df) == 1
        assert import_df.iloc[0]["PRODUCT_ID"] == "101"

    def test_raw_df_has_numeric_columns(self):
        _, _, _, _, raw_df = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert "BUY_PRICE_NUM" in raw_df.columns
        assert "PRICE_NUM" in raw_df.columns
        assert raw_df.iloc[0]["BUY_PRICE_NUM"] == 100.0
        assert raw_df.iloc[1]["PRICE_NUM"] == 150.0

    def test_raw_df_has_producer(self):
        _, _, _, _, raw_df = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert "PRODUCER" in raw_df.columns
        assert raw_df.iloc[0]["PRODUCER"] == "Supplier Inc"

    def test_coverage_rate_format(self):
        """Coverage rate should use Danish comma format with % sign."""
        final_df, _, _, _, _ = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        # First row: 37.5% → "37,5%"
        assert final_df.iloc[0]["COVERAGE_RATE_%"] == "37,5%"
        assert final_df.iloc[0]["NEW_COVERAGE_RATE_%"] == "49,8%"

    def test_empty_response(self):
        """Handle response with no rows gracefully."""
        empty_resp = {
            "summary": {
                "total_products": 0,
                "base_products": 0,
                "total_rows": 0,
                "adjusted_count": 0,
                "unchanged_count": 0,
                "adjusted_pct": 0.0,
                "avg_current_coverage_pct": 0.0,
                "avg_suggested_coverage_pct": 0.0,
            },
            "rows": [],
        }
        final_df, adj_count, adj_mask, import_df, raw_df = (
            _build_dataframes_from_response(empty_resp)
        )
        assert len(final_df) == 0
        assert adj_count == 0
        assert len(adj_mask) == 0
        assert len(import_df) == 0
        assert len(raw_df) == 0

    def test_variant_data_preserved(self):
        _, _, _, _, raw_df = _build_dataframes_from_response(
            _OPTIMIZE_RESPONSE,
        )
        assert raw_df.iloc[1]["VARIANT_ID"] == "V1"
        assert raw_df.iloc[1]["VARIANT_TYPES"] == "Color: Red"
