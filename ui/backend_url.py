"""Centralised backend-URL helpers for the SB-Optima Streamlit app.

Every module that needs to talk to the FastAPI backend should import
from here instead of duplicating URL normalisation or connectivity
logic.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

import requests


def normalize_backend_url(url: str) -> str:
    """Normalize a backend URL so ``localhost`` becomes ``127.0.0.1``.

    This avoids IPv6/IPv4 issues on Windows where ``localhost`` may
    resolve to ``::1`` rather than ``127.0.0.1``.  Non-local hosts are
    returned unchanged.
    """
    url = url.strip()
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname == "localhost":
        # Replace only the hostname portion, preserving port / path / etc.
        netloc = "127.0.0.1"
        if parsed.port:
            netloc = f"127.0.0.1:{parsed.port}"
        parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


def normalize_base_url(url: str) -> str:
    """Normalize the backend base URL for safe joining with endpoint paths.

    - Strips leading/trailing whitespace.
    - Replaces ``localhost`` with ``127.0.0.1`` (IPv4 stability on Windows).
    - Strips trailing slashes.
    - Leaves scheme/host/port otherwise unchanged.
    """
    return normalize_backend_url(url).rstrip("/")


def check_backend_connected(
    base_url: str,
    api_username: str,
    api_password: str,
    timeout_s: float = 5.0,
) -> tuple[bool, str]:
    """Lightweight connectivity check against the FastAPI backend.

    Sends ``GET /brands`` (the lightest existing endpoint) and returns a
    ``(ok, message)`` tuple so the caller can display status without
    having to know HTTP details.
    """
    url = f"{normalize_base_url(base_url)}/brands"
    try:
        resp = requests.get(
            url,
            params={
                "api_username": api_username,
                "api_password": api_password,
            },
            timeout=timeout_s,
        )
        resp.raise_for_status()
        return True, "Backend connected"
    except requests.RequestException as exc:
        return False, str(exc)
