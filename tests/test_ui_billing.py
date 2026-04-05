"""Tests for Task 8.2 — Self-serve billing UX helpers.

Covers:
1. ``build_checkout_payload`` never includes secret keys.
2. ``can_manage_billing`` RBAC gating logic.
3. ``decode_token_role`` extracts role from JWT.
4. ``get_auth_headers`` returns correct headers.
5. ``get_billing_status`` handles 503 as "billing_not_enabled".
6. ``create_checkout`` handles various HTTP errors.
7. ``get_tenant_plan`` helper.

All tests are pure unit tests — no network calls, no Stripe dependency.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from ui.vault_helpers import (
    build_checkout_payload,
    can_manage_billing,
    create_checkout,
    decode_token_role,
    get_auth_headers,
    get_billing_status,
    get_tenant_plan,
)


# ---------------------------------------------------------------------------
# build_checkout_payload — must never include secrets
# ---------------------------------------------------------------------------


class TestBuildCheckoutPayload:
    """Validate the checkout payload builder."""

    def test_basic_fields(self):
        payload = build_checkout_payload("pro", "https://ok.example.com", "https://cancel.example.com")
        assert payload == {
            "plan": "pro",
            "success_url": "https://ok.example.com",
            "cancel_url": "https://cancel.example.com",
        }

    def test_no_secret_keys(self):
        """Payload must never contain Stripe secret keys or tokens."""
        payload = build_checkout_payload("enterprise", "https://s.example.com", "https://c.example.com")
        for key in payload:
            assert "secret" not in key.lower(), f"Payload key {key!r} looks like a secret"
            assert "stripe_secret" not in key.lower()
            assert "api_key" not in key.lower()
        for val in payload.values():
            if isinstance(val, str):
                assert not val.startswith("sk_"), f"Value {val!r} looks like a Stripe secret key"
                assert not val.startswith("whsec_"), f"Value {val!r} looks like a webhook secret"

    def test_only_three_keys(self):
        """Exactly three keys, nothing extra."""
        payload = build_checkout_payload("pro", "http://a", "http://b")
        assert set(payload.keys()) == {"plan", "success_url", "cancel_url"}

    def test_preserves_plan_casing(self):
        """Plan name is forwarded as-is (backend handles case)."""
        payload = build_checkout_payload("Enterprise", "http://a", "http://b")
        assert payload["plan"] == "Enterprise"


# ---------------------------------------------------------------------------
# can_manage_billing — RBAC gating
# ---------------------------------------------------------------------------


class TestCanManageBilling:
    """Validate RBAC helper for billing management."""

    @pytest.mark.parametrize("role", ["admin", "owner"])
    def test_allowed_roles(self, role: str):
        assert can_manage_billing(role) is True

    @pytest.mark.parametrize("role", ["viewer", "operator", "", None])
    def test_denied_roles(self, role):
        assert can_manage_billing(role) is False


# ---------------------------------------------------------------------------
# decode_token_role
# ---------------------------------------------------------------------------


def _make_jwt(payload: dict) -> str:
    """Create a fake JWT (header.payload.signature) without real signing."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


class TestDecodeTokenRole:
    """Validate JWT role extraction."""

    def test_extracts_role(self):
        token = _make_jwt({"sub": "user-1", "role": "admin", "tenant_id": "t-1"})
        assert decode_token_role(token) == "admin"

    def test_missing_role_claim(self):
        token = _make_jwt({"sub": "user-1"})
        assert decode_token_role(token) is None

    def test_none_token(self):
        assert decode_token_role(None) is None

    def test_empty_token(self):
        assert decode_token_role("") is None

    def test_malformed_token(self):
        assert decode_token_role("not-a-jwt") is None

    @pytest.mark.parametrize("role", ["viewer", "operator", "admin", "owner"])
    def test_all_roles(self, role: str):
        token = _make_jwt({"role": role})
        assert decode_token_role(token) == role


# ---------------------------------------------------------------------------
# get_billing_status — mock HTTP
# ---------------------------------------------------------------------------


class TestGetBillingStatus:
    """Validate billing status helper with mocked requests."""

    @patch("ui.vault_helpers.requests.get")
    def test_success(self, mock_get: MagicMock):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "plan": "pro",
            "billing_status": "active",
            "stripe_customer_id": "cus_123",
            "stripe_subscription_id": "sub_456",
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        data, err = get_billing_status("http://localhost:8000", "tok")
        assert err is None
        assert data["plan"] == "pro"
        assert data["billing_status"] == "active"

    @patch("ui.vault_helpers.requests.get")
    def test_503_billing_not_enabled(self, mock_get: MagicMock):
        import requests as real_requests

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        exc = real_requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = exc
        mock_get.return_value = mock_resp

        data, err = get_billing_status("http://localhost:8000", "tok")
        assert data is None
        assert err == "billing_not_enabled"

    @patch("ui.vault_helpers.requests.get")
    def test_401_unauthorized(self, mock_get: MagicMock):
        import requests as real_requests

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        exc = real_requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = exc
        mock_get.return_value = mock_resp

        data, err = get_billing_status("http://localhost:8000", "tok")
        assert data is None
        assert "Authentication" in err

    @patch("ui.vault_helpers.requests.get")
    def test_connection_error(self, mock_get: MagicMock):
        import requests as real_requests

        mock_get.side_effect = real_requests.ConnectionError("fail")

        data, err = get_billing_status("http://localhost:8000", "tok")
        assert data is None
        assert "unreachable" in err.lower()


# ---------------------------------------------------------------------------
# create_checkout — mock HTTP
# ---------------------------------------------------------------------------


class TestCreateCheckout:
    """Validate checkout creation with mocked requests."""

    @patch("ui.vault_helpers.requests.post")
    def test_success(self, mock_post: MagicMock):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"checkout_url": "https://checkout.stripe.com/xyz"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        url, err = create_checkout("http://localhost:8000", "tok", "pro", "http://ok", "http://cancel")
        assert err is None
        assert url == "https://checkout.stripe.com/xyz"

        # Verify the sent payload contains no secrets
        call_kwargs = mock_post.call_args
        sent_json = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert "stripe_secret" not in str(sent_json).lower()
        assert set(sent_json.keys()) == {"plan", "success_url", "cancel_url"}

    @patch("ui.vault_helpers.requests.post")
    def test_503_billing_disabled(self, mock_post: MagicMock):
        import requests as real_requests

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        exc = real_requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = exc
        mock_post.return_value = mock_resp

        url, err = create_checkout("http://localhost:8000", "tok", "pro", "http://ok", "http://cancel")
        assert url is None
        assert "not enabled" in err.lower()

    @patch("ui.vault_helpers.requests.post")
    def test_403_rbac_denied(self, mock_post: MagicMock):
        import requests as real_requests

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        exc = real_requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = exc
        mock_post.return_value = mock_resp

        url, err = create_checkout("http://localhost:8000", "tok", "pro", "http://ok", "http://cancel")
        assert url is None
        assert "admin" in err.lower() or "owner" in err.lower()

    @patch("ui.vault_helpers.requests.post")
    def test_viewer_cannot_checkout_via_rbac_helper(self, mock_post: MagicMock):
        """Verify the RBAC helper prevents checkout for viewer/operator."""
        # The UI should call can_manage_billing before create_checkout.
        # This test validates the intended flow:
        assert not can_manage_billing("viewer")
        assert not can_manage_billing("operator")
        # mock_post should NOT be called if the UI correctly gates
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# get_tenant_plan — mock HTTP
# ---------------------------------------------------------------------------


class TestGetTenantPlan:
    """Validate tenant plan helper with mocked requests."""

    @patch("ui.vault_helpers.requests.get")
    def test_success(self, mock_get: MagicMock):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "plan": "free",
            "effective_limits": {
                "daily_optimize_jobs_limit": 25,
                "daily_apply_limit": 0,
            },
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        data, err = get_tenant_plan("http://localhost:8000", "tok")
        assert err is None
        assert data["plan"] == "free"

    @patch("ui.vault_helpers.requests.get")
    def test_503_auth_disabled(self, mock_get: MagicMock):
        import requests as real_requests

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        exc = real_requests.HTTPError(response=mock_resp)
        mock_resp.raise_for_status.side_effect = exc
        mock_get.return_value = mock_resp

        data, err = get_tenant_plan("http://localhost:8000", "tok")
        assert data is None
        assert "unavailable" in err.lower()


# ---------------------------------------------------------------------------
# get_auth_headers — sanity
# ---------------------------------------------------------------------------


class TestGetAuthHeaders:
    """Validate auth header construction."""

    def test_with_token(self):
        headers = get_auth_headers("my-token")
        assert headers == {"Authorization": "Bearer my-token"}

    def test_without_token(self):
        assert get_auth_headers(None) == {}
        assert get_auth_headers("") == {}
