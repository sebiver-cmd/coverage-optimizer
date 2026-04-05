"""Tests for Task 5.2 — vault mode payload safety.

Asserts that when vault mode is active (token present + credential_id),
request payloads do **not** contain plaintext ``api_username`` /
``api_password`` fields.  Conversely, in legacy mode (no token),
payloads include the raw credentials.

All tests are deterministic, require no network, and import only from
the ``ui.vault_helpers`` module.
"""

from __future__ import annotations

import pytest

from ui.vault_helpers import (
    build_optimize_payload,
    build_catalog_payload,
    build_apply_payload,
    build_brands_params,
    build_test_connection_payload,
    get_auth_headers,
    _inject_credentials,
)


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

_CREDENTIAL_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_API_USER = "shop@example.com"
_API_PASS = "s3cret"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_vault_mode(payload: dict) -> None:
    """Assert payload uses vault mode: credential_id present, no plaintext creds."""
    assert "credential_id" in payload, "Vault mode must include credential_id"
    assert payload["credential_id"] == _CREDENTIAL_ID
    assert "api_username" not in payload, "Vault mode must NOT include api_username"
    assert "api_password" not in payload, "Vault mode must NOT include api_password"


def _assert_legacy_mode(payload: dict) -> None:
    """Assert payload uses legacy mode: plaintext creds present, no credential_id."""
    assert "api_username" in payload, "Legacy mode must include api_username"
    assert "api_password" in payload, "Legacy mode must include api_password"
    assert payload["api_username"] == _API_USER
    assert payload["api_password"] == _API_PASS
    assert "credential_id" not in payload, "Legacy mode must NOT include credential_id"


# ===================================================================
# build_optimize_payload
# ===================================================================

class TestBuildOptimizePayload:
    """Vault vs legacy mode for /optimize/ payloads."""

    def test_vault_mode(self):
        payload = build_optimize_payload(
            site_id=1,
            price_pct=0.0,
            beautify_digit=9,
            include_offline=False,
            include_variants=True,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_vault_mode(payload)
        assert payload["site_id"] == 1
        assert payload["price_pct"] == 0.0

    def test_legacy_mode(self):
        payload = build_optimize_payload(
            site_id=2,
            price_pct=5.0,
            beautify_digit=0,
            include_offline=True,
            include_variants=False,
            credential_id=None,
            token_present=False,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_legacy_mode(payload)
        assert payload["site_id"] == 2

    def test_token_without_credential_id_falls_back_to_legacy(self):
        """Token present but no credential_id → send raw creds."""
        payload = build_optimize_payload(
            site_id=1,
            price_pct=0.0,
            beautify_digit=9,
            include_offline=False,
            include_variants=True,
            credential_id=None,
            token_present=True,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_legacy_mode(payload)

    def test_brand_ids_included(self):
        payload = build_optimize_payload(
            site_id=1,
            price_pct=0.0,
            beautify_digit=9,
            include_offline=False,
            include_variants=True,
            brand_ids=[10, 20],
            credential_id=_CREDENTIAL_ID,
            token_present=True,
        )
        assert payload["brand_ids"] == [10, 20]

    def test_no_brand_ids_omitted(self):
        payload = build_optimize_payload(
            site_id=1,
            price_pct=0.0,
            beautify_digit=9,
            include_offline=False,
            include_variants=True,
            brand_ids=None,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
        )
        assert "brand_ids" not in payload


# ===================================================================
# build_catalog_payload
# ===================================================================

class TestBuildCatalogPayload:
    """Vault vs legacy mode for /catalog/products payloads."""

    def test_vault_mode(self):
        payload = build_catalog_payload(
            site_id=1,
            include_offline=False,
            include_variants=True,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
        )
        _assert_vault_mode(payload)

    def test_legacy_mode(self):
        payload = build_catalog_payload(
            site_id=1,
            include_offline=False,
            include_variants=True,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_legacy_mode(payload)


# ===================================================================
# build_apply_payload
# ===================================================================

class TestBuildApplyPayload:
    """Vault vs legacy mode for /apply-prices/apply payloads."""

    def test_vault_mode(self):
        payload = build_apply_payload(
            batch_id="some-uuid",
            site_id=1,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
        )
        _assert_vault_mode(payload)
        assert payload["batch_id"] == "some-uuid"
        assert payload["confirm"] is True

    def test_legacy_mode(self):
        payload = build_apply_payload(
            batch_id="some-uuid",
            site_id=1,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_legacy_mode(payload)

    def test_vault_mode_no_raw_creds_even_if_provided(self):
        """When vault mode is active, raw creds must be stripped."""
        payload = build_apply_payload(
            batch_id="test",
            site_id=1,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_vault_mode(payload)


# ===================================================================
# build_brands_params
# ===================================================================

class TestBuildBrandsParams:
    """Vault vs legacy mode for /brands query params."""

    def test_vault_mode(self):
        params = build_brands_params(
            credential_id=_CREDENTIAL_ID,
            token_present=True,
        )
        _assert_vault_mode(params)

    def test_legacy_mode(self):
        params = build_brands_params(
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_legacy_mode(params)


# ===================================================================
# build_test_connection_payload
# ===================================================================

class TestBuildTestConnectionPayload:
    """Vault vs legacy mode for /test-connection payloads."""

    def test_vault_mode(self):
        payload = build_test_connection_payload(
            site_id=1,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
        )
        _assert_vault_mode(payload)
        assert payload["site_id"] == 1

    def test_legacy_mode(self):
        payload = build_test_connection_payload(
            site_id=1,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        _assert_legacy_mode(payload)


# ===================================================================
# get_auth_headers
# ===================================================================

class TestGetAuthHeaders:
    """Auth header construction."""

    def test_with_token(self):
        headers = get_auth_headers("my-jwt-token")
        assert headers == {"Authorization": "Bearer my-jwt-token"}

    def test_without_token(self):
        headers = get_auth_headers(None)
        assert headers == {}

    def test_empty_token(self):
        headers = get_auth_headers("")
        assert headers == {}


# ===================================================================
# _inject_credentials (internal helper)
# ===================================================================

class TestInjectCredentials:
    """Low-level credential injection helper."""

    def test_vault_mode_adds_credential_id(self):
        d: dict = {}
        _inject_credentials(
            d,
            credential_id=_CREDENTIAL_ID,
            token_present=True,
            api_username="ignored",
            api_password="ignored",
        )
        assert d["credential_id"] == _CREDENTIAL_ID
        assert "api_username" not in d
        assert "api_password" not in d

    def test_legacy_mode_adds_raw_creds(self):
        d: dict = {}
        _inject_credentials(
            d,
            credential_id=None,
            token_present=False,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        assert d["api_username"] == _API_USER
        assert d["api_password"] == _API_PASS
        assert "credential_id" not in d

    def test_token_true_but_no_credential_id_falls_back(self):
        d: dict = {}
        _inject_credentials(
            d,
            credential_id=None,
            token_present=True,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        assert "credential_id" not in d
        assert d["api_username"] == _API_USER

    def test_empty_credential_id_string_falls_back(self):
        d: dict = {}
        _inject_credentials(
            d,
            credential_id="",
            token_present=True,
            api_username=_API_USER,
            api_password=_API_PASS,
        )
        # Empty string is falsy → falls back to legacy
        assert "credential_id" not in d
        assert d["api_username"] == _API_USER


# ===================================================================
# Cross-cutting: no plaintext creds in vault mode across all builders
# ===================================================================

class TestNoClearTextCredsInVaultMode:
    """Meta-test: all payload builders MUST exclude api_username/api_password
    when token_present=True and credential_id is provided."""

    _BUILDERS = [
        ("optimize", lambda: build_optimize_payload(
            site_id=1, price_pct=0, beautify_digit=9,
            include_offline=False, include_variants=True,
            credential_id=_CREDENTIAL_ID, token_present=True,
            api_username=_API_USER, api_password=_API_PASS,
        )),
        ("catalog", lambda: build_catalog_payload(
            site_id=1, include_offline=False, include_variants=True,
            credential_id=_CREDENTIAL_ID, token_present=True,
            api_username=_API_USER, api_password=_API_PASS,
        )),
        ("apply", lambda: build_apply_payload(
            batch_id="x", site_id=1,
            credential_id=_CREDENTIAL_ID, token_present=True,
            api_username=_API_USER, api_password=_API_PASS,
        )),
        ("brands", lambda: build_brands_params(
            credential_id=_CREDENTIAL_ID, token_present=True,
            api_username=_API_USER, api_password=_API_PASS,
        )),
        ("test_connection", lambda: build_test_connection_payload(
            site_id=1,
            credential_id=_CREDENTIAL_ID, token_present=True,
            api_username=_API_USER, api_password=_API_PASS,
        )),
    ]

    @pytest.mark.parametrize("name,builder", _BUILDERS, ids=[b[0] for b in _BUILDERS])
    def test_no_plaintext_creds(self, name, builder):
        payload = builder()
        assert "api_username" not in payload, f"{name}: api_username leaked in vault mode"
        assert "api_password" not in payload, f"{name}: api_password leaked in vault mode"
        assert payload["credential_id"] == _CREDENTIAL_ID
