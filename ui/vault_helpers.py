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

from typing import Any

import requests

from ui.backend_url import normalize_base_url

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
            headers=get_auth_headers(token),
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
            headers=get_auth_headers(token),
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
            headers=get_auth_headers(token),
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


def decode_token_role(token: str | None) -> str | None:
    """Extract the ``role`` claim from a JWT **without** verifying the signature.

    This is safe for UI display gating — the backend always re-verifies
    the token on every request.  Returns ``None`` when the token is
    missing or cannot be decoded.
    """
    if not token:
        return None
    import base64
    import json as _json

    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        # JWT payload is base64url-encoded; add padding if needed
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = _json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("role")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# History / dashboard helpers (Task 6.2)
# ---------------------------------------------------------------------------


def list_jobs(
    backend_url: str,
    token: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    since: str | None = None,
    until: str | None = None,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Fetch paginated job list via ``GET /jobs``."""
    base = normalize_base_url(backend_url)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    try:
        resp = requests.get(
            f"{base}/jobs",
            params=params,
            headers=get_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        return None, f"Error {exc.response.status_code}: {_extract_detail(exc)}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


def list_batches(
    backend_url: str,
    token: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    mode: str | None = None,
    since: str | None = None,
    until: str | None = None,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Fetch paginated batch list via ``GET /apply-prices/batches``."""
    base = normalize_base_url(backend_url)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        params["status"] = status
    if mode:
        params["mode"] = mode
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    try:
        resp = requests.get(
            f"{base}/apply-prices/batches",
            params=params,
            headers=get_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        return None, f"Error {exc.response.status_code}: {_extract_detail(exc)}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


def list_audit(
    backend_url: str,
    token: str,
    *,
    limit: int = 50,
    offset: int = 0,
    event_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Fetch paginated audit event list via ``GET /audit``."""
    base = normalize_base_url(backend_url)
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if event_type:
        params["event_type"] = event_type
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    try:
        resp = requests.get(
            f"{base}/audit",
            params=params,
            headers=get_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        return None, f"Error {exc.response.status_code}: {_extract_detail(exc)}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


# ---------------------------------------------------------------------------
# Billing helpers (Task 8.2)
# ---------------------------------------------------------------------------


def get_billing_status(
    backend_url: str,
    token: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Fetch billing status via ``GET /billing/status``.

    Returns ``(status_dict, None)`` on success, ``(None, error_message)``
    on failure.  A **503** is returned as a specific message so the UI
    can show "Billing not enabled".
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/billing/status"
    try:
        resp = requests.get(
            url,
            headers=get_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        status_code = exc.response.status_code
        if status_code == 503:
            return None, "billing_not_enabled"
        if status_code in (401, 403):
            return None, "Authentication required or insufficient permissions."
        detail = _extract_detail(exc)
        return None, f"Error {status_code}: {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


def get_tenant_plan(
    backend_url: str,
    token: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Fetch tenant plan info via ``GET /tenant/plan``.

    Returns ``(plan_dict, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/tenant/plan"
    try:
        resp = requests.get(
            url,
            headers=get_auth_headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        status_code = exc.response.status_code
        if status_code == 503:
            return None, "Plan info unavailable (auth disabled)."
        detail = _extract_detail(exc)
        return None, f"Error {status_code}: {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Request failed: {exc}"


def build_checkout_payload(
    plan: str,
    success_url: str,
    cancel_url: str,
) -> dict[str, str]:
    """Build the JSON payload for ``POST /billing/checkout``.

    This is a pure function (no I/O) that is easy to unit-test.
    It intentionally includes **only** the three required fields and
    never any secret keys.
    """
    return {
        "plan": plan,
        "success_url": success_url,
        "cancel_url": cancel_url,
    }


def create_checkout(
    backend_url: str,
    token: str,
    plan: str,
    success_url: str,
    cancel_url: str,
    timeout: float = _AUTH_TIMEOUT,
) -> tuple[str | None, str | None]:
    """Start a Stripe checkout via ``POST /billing/checkout``.

    Returns ``(checkout_url, None)`` on success or
    ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/billing/checkout"
    payload = build_checkout_payload(plan, success_url, cancel_url)
    try:
        resp = requests.post(
            url,
            headers=get_auth_headers(token),
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("checkout_url"), None
    except requests.HTTPError as exc:
        status_code = exc.response.status_code
        if status_code == 503:
            return None, "Billing is not enabled on this server."
        if status_code in (401, 403):
            return None, "Only admin/owner users can start a checkout."
        detail = _extract_detail(exc)
        return None, f"Checkout failed ({status_code}): {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable."
    except requests.RequestException as exc:
        return None, f"Checkout request failed: {exc}"


def can_manage_billing(role: str | None) -> bool:
    """Return ``True`` if *role* is allowed to start a Stripe checkout.

    Only ``admin`` and ``owner`` roles can manage billing.
    This is a pure helper used by the UI to gate checkout controls.
    """
    return role in ("admin", "owner")
