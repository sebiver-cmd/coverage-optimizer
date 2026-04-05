"""Auth, credential-profile, and vault-mode payload helpers for Streamlit.

This module centralises all logic related to:

- Logging in / signing up against the backend ``/auth/*`` endpoints.
- Managing credential profiles via ``/credentials``.
- Building request payloads that prefer ``credential_id`` when a JWT
  token is present (vault mode) and fall back to raw credentials when
  no token is available (legacy mode).

Security invariants
-------------------
- Plaintext HostedShop credentials are **never** included in request
  payloads when vault mode is active (``token`` is present and a
  ``credential_id`` is selected).
- Plaintext credentials are **never** logged.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from ui.backend_url import normalize_base_url

logger = logging.getLogger(__name__)

# Default HTTP timeout for auth / credential calls (seconds).
_AUTH_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def login(
    backend_url: str,
    email: str,
    password: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[str | None, str | None]:
    """Authenticate against ``POST /auth/login``.

    Returns ``(access_token, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/auth/login"
    try:
        resp = requests.post(
            url,
            json={"email": email, "password": password},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("access_token"), None
    except requests.HTTPError as exc:
        detail = _extract_detail(exc)
        return None, f"Login failed ({exc.response.status_code}): {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable — is the FastAPI server running?"
    except requests.RequestException as exc:
        return None, f"Login request failed: {exc}"


def signup(
    backend_url: str,
    tenant_name: str,
    email: str,
    password: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[str | None, str | None]:
    """Create a new tenant via ``POST /auth/signup``.

    Returns ``(access_token, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/auth/signup"
    try:
        resp = requests.post(
            url,
            json={
                "tenant_name": tenant_name,
                "email": email,
                "password": password,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("access_token"), None
    except requests.HTTPError as exc:
        detail = _extract_detail(exc)
        return None, f"Signup failed ({exc.response.status_code}): {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable — is the FastAPI server running?"
    except requests.RequestException as exc:
        return None, f"Signup request failed: {exc}"


# ---------------------------------------------------------------------------
# Credential profile helpers
# ---------------------------------------------------------------------------


def list_credentials(
    backend_url: str,
    token: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[list[dict] | None, str | None]:
    """Fetch credential profiles via ``GET /credentials``.

    Returns ``(list_of_creds, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/credentials/"
    try:
        resp = requests.get(
            url,
            headers=_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        status_code = exc.response.status_code
        if status_code in (401, 403):
            return None, "Authentication required or insufficient permissions."
        if status_code == 503:
            return None, "Vault unavailable (auth disabled on backend)."
        detail = _extract_detail(exc)
        return None, f"Error {status_code}: {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


def create_credential(
    backend_url: str,
    token: str,
    name: str,
    site_id: str,
    api_username: str,
    api_password: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Create a credential profile via ``POST /credentials/``.

    Returns ``(credential_dict, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/credentials/"
    try:
        resp = requests.post(
            url,
            headers=_auth_headers(token),
            json={
                "name": name,
                "site_id": site_id,
                "api_username": api_username,
                "api_password": api_password,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        detail = _extract_detail(exc)
        return None, f"Error {exc.response.status_code}: {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


def delete_credential(
    backend_url: str,
    token: str,
    credential_id: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[bool, str | None]:
    """Delete a credential profile via ``DELETE /credentials/{id}``.

    Returns ``(True, None)`` on success or ``(False, error_message)``
    on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/credentials/{credential_id}"
    try:
        resp = requests.delete(
            url,
            headers=_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return True, None
    except requests.HTTPError as exc:
        detail = _extract_detail(exc)
        return False, f"Error {exc.response.status_code}: {detail}"
    except requests.ConnectionError:
        return False, "Backend unreachable."
    except requests.RequestException as exc:
        return False, f"Request failed: {exc}"


# ---------------------------------------------------------------------------
# Payload builders — vault mode vs legacy mode
# ---------------------------------------------------------------------------


def build_optimize_payload(
    *,
    site_id: int,
    price_pct: float,
    beautify_digit: int,
    include_offline: bool,
    include_variants: bool,
    brand_ids: list[int] | None = None,
    credential_id: str | None = None,
    token_present: bool = False,
    api_username: str = "",
    api_password: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for ``POST /optimize/``.

    When *token_present* is ``True`` and *credential_id* is given,
    the payload uses vault mode (no plaintext credentials).
    Otherwise it falls back to legacy mode with raw credentials.
    """
    payload: dict[str, Any] = {
        "site_id": site_id,
        "price_pct": price_pct,
        "beautify_digit": beautify_digit,
        "include_offline": include_offline,
        "include_variants": include_variants,
    }
    if brand_ids:
        payload["brand_ids"] = brand_ids

    _inject_credentials(
        payload,
        credential_id=credential_id,
        token_present=token_present,
        api_username=api_username,
        api_password=api_password,
    )
    return payload


def build_catalog_payload(
    *,
    site_id: int,
    include_offline: bool,
    include_variants: bool,
    brand_ids: list[int] | None = None,
    credential_id: str | None = None,
    token_present: bool = False,
    api_username: str = "",
    api_password: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for ``POST /catalog/products``."""
    payload: dict[str, Any] = {
        "site_id": site_id,
        "include_offline": include_offline,
        "include_variants": include_variants,
    }
    if brand_ids:
        payload["brand_ids"] = brand_ids

    _inject_credentials(
        payload,
        credential_id=credential_id,
        token_present=token_present,
        api_username=api_username,
        api_password=api_password,
    )
    return payload


def build_apply_payload(
    *,
    batch_id: str,
    site_id: int,
    credential_id: str | None = None,
    token_present: bool = False,
    api_username: str = "",
    api_password: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for ``POST /apply-prices/apply``."""
    payload: dict[str, Any] = {
        "batch_id": batch_id,
        "confirm": True,
        "site_id": site_id,
    }
    _inject_credentials(
        payload,
        credential_id=credential_id,
        token_present=token_present,
        api_username=api_username,
        api_password=api_password,
    )
    return payload


def build_brands_params(
    *,
    credential_id: str | None = None,
    token_present: bool = False,
    api_username: str = "",
    api_password: str = "",
) -> dict[str, Any]:
    """Build query params for ``GET /brands``."""
    params: dict[str, Any] = {}
    _inject_credentials(
        params,
        credential_id=credential_id,
        token_present=token_present,
        api_username=api_username,
        api_password=api_password,
    )
    return params


def build_test_connection_payload(
    *,
    site_id: int,
    credential_id: str | None = None,
    token_present: bool = False,
    api_username: str = "",
    api_password: str = "",
) -> dict[str, Any]:
    """Build the JSON payload for ``POST /test-connection``."""
    payload: dict[str, Any] = {"site_id": site_id}
    _inject_credentials(
        payload,
        credential_id=credential_id,
        token_present=token_present,
        api_username=api_username,
        api_password=api_password,
    )
    return payload


# ---------------------------------------------------------------------------
# Auth header helper
# ---------------------------------------------------------------------------


def get_auth_headers(token: str | None) -> dict[str, str]:
    """Return ``Authorization`` header dict when *token* is set."""
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    """Return auth header dict for requests."""
    return {"Authorization": f"Bearer {token}"}


def _inject_credentials(
    payload: dict[str, Any],
    *,
    credential_id: str | None,
    token_present: bool,
    api_username: str,
    api_password: str,
) -> None:
    """Mutate *payload* to include credential fields.

    Vault mode (token_present + credential_id): only ``credential_id``
    is added — no plaintext credentials.

    Legacy mode: ``api_username`` and ``api_password`` are added.
    """
    if token_present and credential_id:
        payload["credential_id"] = credential_id
    else:
        payload["api_username"] = api_username
        payload["api_password"] = api_password


def _extract_detail(exc: requests.HTTPError) -> str:
    """Extract a human-readable detail string from an HTTP error response."""
    try:
        body = exc.response.json()
        return str(body.get("detail", exc.response.text))
    except Exception:
        return exc.response.text or str(exc)
