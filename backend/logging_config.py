"""Structured JSON logging configuration for SB-Optima (Task 9.1).

Provides:
- :func:`setup_logging` — configure Python logging to emit JSON lines.
- :func:`redact_dict` — scrub sensitive keys from a dict before logging.
- :class:`JSONFormatter` — stdlib-compatible formatter that outputs JSON.

Usage::

    from backend.logging_config import setup_logging
    setup_logging()
"""

from __future__ import annotations

import copy
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Sensitive key names (case-insensitive matching)
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "api_password",
        "password",
        "jwt_secret",
        "encryption_key",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "cookie",
        "x-api-key",
    }
)

_SENSITIVE_RE = re.compile(
    "|".join(re.escape(k) for k in _SENSITIVE_KEYS),
    re.IGNORECASE,
)

_REDACTED = "***REDACTED***"


# ---------------------------------------------------------------------------
# Redaction utility
# ---------------------------------------------------------------------------


def redact_dict(d: dict[str, Any], *, extra_keys: frozenset[str] | None = None) -> dict[str, Any]:
    """Return a shallow copy of *d* with sensitive values replaced.

    Keys are matched **case-insensitively** against :data:`_SENSITIVE_KEYS`
    (and *extra_keys* when provided).

    The original dict is never mutated.
    """
    sensitive = _SENSITIVE_KEYS | extra_keys if extra_keys else _SENSITIVE_KEYS
    out: dict[str, Any] = {}
    for key, value in d.items():
        if key.lower() in sensitive:
            out[key] = _REDACTED
        elif isinstance(value, dict):
            out[key] = redact_dict(value, extra_keys=extra_keys)
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        obj: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Contextual extras injected by middleware
        for field in (
            "request_id",
            "tenant_id",
            "user_id",
            "path",
            "method",
            "status_code",
            "duration_ms",
        ):
            val = getattr(record, field, None)
            if val is not None:
                obj[field] = val

        if record.exc_info and record.exc_info[1] is not None:
            obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(obj, default=str)


# ---------------------------------------------------------------------------
# Setup helper
# ---------------------------------------------------------------------------


def setup_logging(*, level: int = logging.INFO) -> None:
    """Configure the root logger to use :class:`JSONFormatter`.

    Safe to call multiple times — removes existing handlers first.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any pre-existing handlers to avoid duplicate output
    for h in root.handlers[:]:
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
