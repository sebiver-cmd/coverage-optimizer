"""Per-caller SOAP rate limiter (Task 3.3).

Provides :func:`soap_limit`, a **synchronous** context manager that
enforces:

1. **Bounded concurrency** — at most ``SOAP_MAX_CONCURRENT`` SOAP calls
   may be in-flight simultaneously for the same *caller_key*.
2. **Minimum inter-call delay** — consecutive SOAP calls for the same
   *caller_key* are spaced at least ``SOAP_CALL_DELAY_S`` seconds apart.

The limiter is **process-global** (shared across threads / requests)
and does **not** require Redis.  Separate *caller_key* values get
independent semaphores and delay trackers so that one tenant cannot
starve another.

Usage::

    from backend.soap_limiter import soap_limit

    with soap_limit("caller_abc"):
        result = client._call("Product_GetAll")
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator

from backend.config import get_settings

# ---------------------------------------------------------------------------
# Module-level state (process-global, thread-safe)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_semaphores: dict[str, threading.Semaphore] = {}
_delay_locks: dict[str, threading.Lock] = {}
_last_call_ts: dict[str, float] = {}

_DEFAULT_KEY = "__default__"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_key(caller_key: str | None) -> str:
    """Return *caller_key* or a fallback default."""
    return caller_key if caller_key else _DEFAULT_KEY


def _get_semaphore(key: str) -> threading.Semaphore:
    """Return (or lazily create) the semaphore for *key*."""
    with _lock:
        if key not in _semaphores:
            settings = get_settings()
            _semaphores[key] = threading.Semaphore(settings.soap_max_concurrent)
        return _semaphores[key]


def _get_delay_lock(key: str) -> threading.Lock:
    """Return (or lazily create) the delay-serialisation lock for *key*."""
    with _lock:
        if key not in _delay_locks:
            _delay_locks[key] = threading.Lock()
        return _delay_locks[key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@contextmanager
def soap_limit(caller_key: str | None = None) -> Iterator[None]:
    """Synchronous context manager that throttles SOAP calls.

    Parameters
    ----------
    caller_key : str, optional
        Identifies the calling tenant / user.  ``None`` maps to a shared
        default bucket.

    Guarantees (per *caller_key*):

    * At most ``settings.soap_max_concurrent`` calls are active at the
      same time (semaphore).
    * Successive calls are spaced at least ``settings.soap_call_delay_s``
      seconds apart (delay lock + monotonic timestamp).
    """
    key = _resolve_key(caller_key)
    sem = _get_semaphore(key)

    sem.acquire()
    try:
        # --- enforce minimum delay between calls for this key ---
        settings = get_settings()
        delay = settings.soap_call_delay_s
        if delay > 0:
            dlock = _get_delay_lock(key)
            with dlock:
                now = time.monotonic()
                last = _last_call_ts.get(key, 0.0)
                gap = now - last
                if gap < delay:
                    time.sleep(delay - gap)
                _last_call_ts[key] = time.monotonic()
        yield
    finally:
        sem.release()


# ---------------------------------------------------------------------------
# Test / reset helper
# ---------------------------------------------------------------------------

def _reset() -> None:
    """Clear all limiter state.  **Test-only.**"""
    with _lock:
        _semaphores.clear()
        _delay_locks.clear()
        _last_call_ts.clear()
