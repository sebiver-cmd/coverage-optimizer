"""Tests for backend.soap_limiter — SOAP rate limiting (Task 3.3).

All tests run without network, without Redis, and are deterministic.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from backend.soap_limiter import soap_limit, _reset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_limiter():
    """Reset global limiter state before/after every test."""
    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# 1. Concurrency cap
# ---------------------------------------------------------------------------

class TestConcurrencyCap:
    """Verify that at most SOAP_MAX_CONCURRENT calls run in parallel."""

    def test_max_concurrent_respected(self, monkeypatch):
        """Launch more threads than the cap and observe that the cap holds."""
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "2")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0")  # disable delay for this test

        peak = {"value": 0}
        active = {"value": 0}
        lock = threading.Lock()
        barrier = threading.Event()

        def work():
            with soap_limit("caller_a"):
                with lock:
                    active["value"] += 1
                    if active["value"] > peak["value"]:
                        peak["value"] = active["value"]
                # Hold the semaphore slot until the barrier is released
                barrier.wait(timeout=5)
                with lock:
                    active["value"] -= 1

        threads = [threading.Thread(target=work) for _ in range(5)]
        for t in threads:
            t.start()

        # Give threads time to start and block on the semaphore
        time.sleep(0.3)

        # At this point, exactly 2 threads should be inside the CM
        with lock:
            assert active["value"] == 2
            assert peak["value"] == 2

        # Release the barrier so all threads finish
        barrier.set()
        for t in threads:
            t.join(timeout=5)

    def test_concurrency_cap_of_one(self, monkeypatch):
        """With max_concurrent=1, calls are fully serialised."""
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "1")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0")

        peak = {"value": 0}
        active = {"value": 0}
        lock = threading.Lock()
        done = threading.Event()

        def work():
            with soap_limit("caller_a"):
                with lock:
                    active["value"] += 1
                    if active["value"] > peak["value"]:
                        peak["value"] = active["value"]
                done.wait(timeout=5)
                with lock:
                    active["value"] -= 1

        t1 = threading.Thread(target=work)
        t2 = threading.Thread(target=work)
        t1.start()
        time.sleep(0.1)
        t2.start()
        time.sleep(0.1)

        with lock:
            # Only 1 should be active
            assert active["value"] == 1
            assert peak["value"] == 1

        done.set()
        t1.join(timeout=5)
        t2.join(timeout=5)


# ---------------------------------------------------------------------------
# 2. Delay enforcement
# ---------------------------------------------------------------------------

class TestDelayEnforcement:
    """Verify minimum delay between successive SOAP calls."""

    def test_delay_between_calls(self, monkeypatch):
        """Two consecutive calls should be spaced at least SOAP_CALL_DELAY_S apart."""
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "5")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0.15")

        timestamps: list[float] = []

        for _ in range(3):
            with soap_limit("caller_delay"):
                timestamps.append(time.monotonic())

        assert len(timestamps) == 3
        for i in range(1, len(timestamps)):
            gap = timestamps[i] - timestamps[i - 1]
            # Allow 10ms tolerance for timer granularity
            assert gap >= 0.14, f"Gap {gap:.4f}s < 0.14s expected"

    def test_zero_delay_skips_sleep(self, monkeypatch):
        """When SOAP_CALL_DELAY_S=0, no inter-call delay is added."""
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "5")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0")

        start = time.monotonic()
        for _ in range(5):
            with soap_limit("caller_fast"):
                pass
        elapsed = time.monotonic() - start

        # 5 calls with zero delay should complete in well under 0.5s
        assert elapsed < 0.5


# ---------------------------------------------------------------------------
# 3. Per-caller isolation
# ---------------------------------------------------------------------------

class TestPerCallerIsolation:
    """Caller A and caller B must get independent semaphores."""

    def test_separate_callers_independent(self, monkeypatch):
        """Each caller can use up to N concurrency independently."""
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "1")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0")

        active_a = {"value": 0}
        active_b = {"value": 0}
        lock = threading.Lock()
        barrier = threading.Event()

        def work_a():
            with soap_limit("caller_a"):
                with lock:
                    active_a["value"] += 1
                barrier.wait(timeout=5)
                with lock:
                    active_a["value"] -= 1

        def work_b():
            with soap_limit("caller_b"):
                with lock:
                    active_b["value"] += 1
                barrier.wait(timeout=5)
                with lock:
                    active_b["value"] -= 1

        ta = threading.Thread(target=work_a)
        tb = threading.Thread(target=work_b)
        ta.start()
        tb.start()
        time.sleep(0.2)

        # Both should be active simultaneously (separate semaphores)
        with lock:
            assert active_a["value"] == 1
            assert active_b["value"] == 1

        barrier.set()
        ta.join(timeout=5)
        tb.join(timeout=5)

    def test_none_key_uses_default(self, monkeypatch):
        """Passing None as caller_key uses a shared default bucket."""
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "2")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0")

        active = {"value": 0}
        peak = {"value": 0}
        lock = threading.Lock()
        barrier = threading.Event()

        def work():
            with soap_limit(None):
                with lock:
                    active["value"] += 1
                    if active["value"] > peak["value"]:
                        peak["value"] = active["value"]
                barrier.wait(timeout=5)
                with lock:
                    active["value"] -= 1

        threads = [threading.Thread(target=work) for _ in range(4)]
        for t in threads:
            t.start()
        time.sleep(0.3)

        with lock:
            assert peak["value"] == 2

        barrier.set()
        for t in threads:
            t.join(timeout=5)


# ---------------------------------------------------------------------------
# 4. Semaphore release on exception
# ---------------------------------------------------------------------------

class TestExceptionSafety:
    """The semaphore must be released even when the body raises."""

    def test_semaphore_released_on_error(self, monkeypatch):
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "1")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0")

        with pytest.raises(RuntimeError):
            with soap_limit("caller_err"):
                raise RuntimeError("boom")

        # Should not deadlock — semaphore was released
        with soap_limit("caller_err"):
            pass  # succeeds


# ---------------------------------------------------------------------------
# 5. Config integration
# ---------------------------------------------------------------------------

class TestConfigIntegration:
    """Settings are read from environment via get_settings()."""

    def test_default_settings(self, monkeypatch):
        """Without env vars, defaults apply."""
        for var in ("SOAP_MAX_CONCURRENT", "SOAP_CALL_DELAY_S", "SOAP_RATE_LIMIT_PER_S"):
            monkeypatch.delenv(var, raising=False)

        from backend.config import get_settings

        s = get_settings()
        assert s.soap_max_concurrent == 3
        assert s.soap_call_delay_s == 0.2
        assert s.soap_rate_limit_per_s == 5.0

    def test_custom_settings(self, monkeypatch):
        monkeypatch.setenv("SOAP_MAX_CONCURRENT", "10")
        monkeypatch.setenv("SOAP_CALL_DELAY_S", "0.5")
        monkeypatch.setenv("SOAP_RATE_LIMIT_PER_S", "20")

        from backend.config import get_settings

        s = get_settings()
        assert s.soap_max_concurrent == 10
        assert s.soap_call_delay_s == 0.5
        assert s.soap_rate_limit_per_s == 20.0
