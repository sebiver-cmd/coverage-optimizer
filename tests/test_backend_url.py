"""Tests for the centralised backend-URL helpers in ``ui.backend_url``.

Covers:
- ``normalize_backend_url``
- ``normalize_base_url``
- ``check_backend_connected``
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
import requests

from ui.backend_url import (
    normalize_backend_url,
    normalize_base_url,
    check_backend_connected,
)


# ---------------------------------------------------------------------------
# normalize_backend_url
# ---------------------------------------------------------------------------

class TestNormalizeBackendUrl:
    """Mirrors the existing tests but against the canonical public API."""

    def test_localhost_with_port(self):
        assert normalize_backend_url("http://localhost:8000") == "http://127.0.0.1:8000"

    def test_localhost_without_port(self):
        assert normalize_backend_url("http://localhost") == "http://127.0.0.1"

    def test_non_local_host_unchanged(self):
        assert normalize_backend_url("http://myserver.com:8000") == "http://myserver.com:8000"

    def test_127_already(self):
        assert normalize_backend_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"

    def test_whitespace_stripped(self):
        assert normalize_backend_url("  http://localhost:8000  ") == "http://127.0.0.1:8000"

    def test_preserves_path(self):
        assert normalize_backend_url("http://localhost:8000/api/v1") == "http://127.0.0.1:8000/api/v1"

    def test_preserves_scheme(self):
        assert normalize_backend_url("https://localhost:8443") == "https://127.0.0.1:8443"


# ---------------------------------------------------------------------------
# normalize_base_url
# ---------------------------------------------------------------------------

class TestNormalizeBaseUrl:

    def test_strips_trailing_slash(self):
        assert normalize_base_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"

    def test_strips_multiple_trailing_slashes(self):
        assert normalize_base_url("http://127.0.0.1:8000///") == "http://127.0.0.1:8000"

    def test_localhost_and_trailing_slash(self):
        assert normalize_base_url("http://localhost:8000/") == "http://127.0.0.1:8000"

    def test_whitespace_and_trailing_slash(self):
        assert normalize_base_url("  http://127.0.0.1:8000/  ") == "http://127.0.0.1:8000"


# ---------------------------------------------------------------------------
# check_backend_connected
# ---------------------------------------------------------------------------

class TestCheckBackendConnected:

    @patch("ui.backend_url.requests.get")
    def test_returns_true_on_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        ok, msg = check_backend_connected("http://127.0.0.1:8000")
        assert ok is True
        assert msg == "Backend connected"

    @patch("ui.backend_url.requests.get")
    def test_returns_false_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("refused")

        ok, msg = check_backend_connected("http://127.0.0.1:8000")
        assert ok is False
        assert "refused" in msg

    @patch("ui.backend_url.requests.get")
    def test_returns_false_on_timeout(self, mock_get):
        mock_get.side_effect = requests.Timeout("timed out")

        ok, msg = check_backend_connected("http://127.0.0.1:8000")
        assert ok is False
        assert "timed out" in msg

    @patch("ui.backend_url.requests.get")
    def test_returns_false_on_http_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_get.return_value = mock_resp

        ok, msg = check_backend_connected("http://127.0.0.1:8000")
        assert ok is False
        assert "500" in msg

    @patch("ui.backend_url.requests.get")
    def test_normalizes_localhost(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        check_backend_connected("http://localhost:8000")
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://127.0.0.1:8000/health"

    @patch("ui.backend_url.requests.get")
    def test_uses_health_endpoint(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        check_backend_connected("http://127.0.0.1:8000")
        called_url = mock_get.call_args[0][0]
        assert called_url == "http://127.0.0.1:8000/health"

    @patch("ui.backend_url.requests.get")
    def test_no_credentials_sent(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        check_backend_connected("http://127.0.0.1:8000")
        _, kwargs = mock_get.call_args
        assert "params" not in kwargs

    @patch("ui.backend_url.requests.get")
    def test_custom_timeout(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        check_backend_connected("http://127.0.0.1:8000", timeout_s=10.0)
        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 10.0
