"""Small HTTP client helper for Streamlit to call backend apply endpoints.

Provides consistent error handling so callers do not need to handle
:mod:`requests` exceptions directly.  All public functions return a
``(result_dict_or_None, error_message_or_None)`` tuple.

Credentials are forwarded to the backend as JSON body fields and are
**never** logged.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from ui.backend_url import normalize_base_url

logger = logging.getLogger(__name__)

# Default HTTP timeout for apply-related backend calls (seconds).
_APPLY_TIMEOUT = 120


def create_manifest(
    backend_url: str,
    changes: list[dict],
    timeout: float = _APPLY_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Call ``POST /apply-prices/create-manifest`` to persist a change set.

    Parameters
    ----------
    backend_url:
        Base URL of the FastAPI backend (e.g. ``http://127.0.0.1:8000``).
    changes:
        List of change dicts produced by
        :func:`push_safety.build_push_updates`.  Each dict must contain
        at least ``product_number`` and either ``new_price`` or
        ``buy_price``.
    timeout:
        Seconds before the HTTP request times out.

    Returns
    -------
    tuple[dict | None, str | None]
        ``(response_dict, None)`` on success or
        ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/apply-prices/create-manifest"

    # Translate push_safety dict keys to the backend model field names.
    entries: list[dict] = []
    for u in changes:
        entry: dict[str, Any] = {
            "NUMBER": u.get("product_number", ""),
            "TITLE_DK": u.get("title", ""),
            "product_id": u.get("product_id", ""),
            "variant_id": u.get("variant_id", ""),
            "variant_types": u.get("variant_types", ""),
            "old_price": u.get("old_price", 0.0),
            "new_price": u.get("new_price", 0.0),
            "buy_price": u.get("buy_price", 0.0),
            "old_buy_price": u.get("old_buy_price", 0.0),
        }
        if entry["old_price"] != 0.0 and entry["new_price"] != 0.0:
            old = entry["old_price"]
            new = entry["new_price"]
            entry["change_pct"] = round((new - old) / old * 100, 2) if old else 0.0
        else:
            entry["change_pct"] = 0.0
        entries.append(entry)

    try:
        resp = requests.post(url, json={"changes": entries}, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        detail = _extract_detail(exc)
        return None, f"Backend error {exc.response.status_code}: {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable — is the FastAPI server running?"
    except requests.Timeout:
        return None, f"Backend request timed out after {timeout}s."
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected error calling create_manifest")
        return None, f"Unexpected error: {exc}"


def apply_batch(
    backend_url: str,
    batch_id: str,
    api_username: str,
    api_password: str,
    site_id: int = 1,
    timeout: float = _APPLY_TIMEOUT,
) -> tuple[dict | None, str | None]:
    """Call ``POST /apply-prices/apply`` to apply a previously created batch.

    Parameters
    ----------
    backend_url:
        Base URL of the FastAPI backend.
    batch_id:
        UUID returned by :func:`create_manifest` (or the dry-run endpoint).
    api_username:
        DanDomain API username (forwarded to backend, never logged here).
    api_password:
        DanDomain API password (forwarded to backend, never logged here).
    site_id:
        DanDomain site/language ID (default ``1``).
    timeout:
        Seconds before the HTTP request times out.

    Returns
    -------
    tuple[dict | None, str | None]
        ``(response_dict, None)`` on success or
        ``(None, error_message)`` on failure.
    """
    base = normalize_base_url(backend_url)
    url = f"{base}/apply-prices/apply"

    payload = {
        "batch_id": batch_id,
        "confirm": True,
        "api_username": api_username,
        "api_password": api_password,
        "site_id": site_id,
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json(), None
    except requests.HTTPError as exc:
        detail = _extract_detail(exc)
        status = exc.response.status_code
        if status == 403:
            return None, (
                "Apply is disabled on the backend. "
                "Set SB_OPTIMA_ENABLE_APPLY=true on the server to enable writes."
            )
        if status == 409:
            return None, "This batch has already been applied (idempotency check)."
        return None, f"Backend error {status}: {detail}"
    except requests.ConnectionError:
        return None, "Backend unreachable — is the FastAPI server running?"
    except requests.Timeout:
        return None, f"Backend request timed out after {timeout}s."
    except Exception as exc:  # pragma: no cover
        logger.exception("Unexpected error calling apply_batch")
        return None, f"Unexpected error: {exc}"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_detail(exc: requests.HTTPError) -> str:
    """Extract a human-readable detail string from an HTTP error response."""
    try:
        body = exc.response.json()
        return str(body.get("detail", exc.response.text))
    except Exception:
        return exc.response.text or str(exc)
