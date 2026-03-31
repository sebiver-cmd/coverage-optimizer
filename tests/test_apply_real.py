"""Tests for the real apply-prices endpoint (POST /apply-prices/apply).

Validates:
- confirm=false is rejected.
- >100 rows is rejected.
- out-of-range change_pct is rejected.
- non-positive / non-finite new_price is rejected.
- valid manifest triggers the write function (mocked).
- double-apply is blocked (409).
- invalid / missing batch_id handled correctly.
- audit log is written.
"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BATCH_DIR = Path("data/apply_batches")
_AUDIT_LOG = Path("data/apply_audit.log")


def _make_manifest(
    batch_id: str,
    changes: list[dict] | None = None,
) -> dict:
    """Build and persist a minimal batch manifest."""
    if changes is None:
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget Pro",
                "old_price": 200.0,
                "new_price": 249.0,
                "change_pct": 24.5,
            },
            {
                "NUMBER": "SKU-002",
                "TITLE_DK": "Gadget Lite",
                "old_price": 150.0,
                "new_price": 159.0,
                "change_pct": 6.0,
            },
        ]
    manifest = {
        "batch_id": batch_id,
        "created_at": "2024-01-01T00:00:00+00:00",
        "optimize_payload": {
            "api_username": "test@example.com",
            "api_password": "secret",
        },
        "product_numbers": [c["NUMBER"] for c in changes],
        "changes": changes,
        "summary": {
            "total": len(changes),
            "increases": sum(1 for c in changes if c["new_price"] > c["old_price"]),
            "decreases": sum(1 for c in changes if c["new_price"] < c["old_price"]),
            "unchanged": sum(1 for c in changes if c["new_price"] == c["old_price"]),
        },
    }
    _BATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = _BATCH_DIR / f"{batch_id}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _apply_payload(batch_id: str, confirm: bool = True) -> dict:
    return {
        "batch_id": batch_id,
        "confirm": confirm,
        "api_username": "test@example.com",
        "api_password": "secret",
        "site_id": 1,
    }


@pytest.fixture(autouse=True)
def _clean_dirs():
    """Clean up batch dir and audit log after every test."""
    yield
    if _BATCH_DIR.exists():
        shutil.rmtree(_BATCH_DIR)
    if _AUDIT_LOG.exists():
        _AUDIT_LOG.unlink()


# ---------------------------------------------------------------------------
# Tests – confirm gate
# ---------------------------------------------------------------------------


class TestConfirmGate:
    """confirm=false must be rejected."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_confirm_false_rejected(self, mock_cls):
        bid = str(uuid.uuid4())
        _make_manifest(bid)

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid, confirm=False),
        )
        assert resp.status_code == 400
        assert "confirm" in resp.json()["detail"].lower()
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# Tests – row count guardrail
# ---------------------------------------------------------------------------


class TestRowCountGuardrail:
    """Batches with >100 rows must be rejected."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_over_100_rows_rejected(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": f"SKU-{i:04d}",
                "TITLE_DK": f"Product {i}",
                "old_price": 100.0,
                "new_price": 109.0,
                "change_pct": 9.0,
            }
            for i in range(101)
        ]
        _make_manifest(bid, changes=changes)

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 400
        assert "101" in resp.json()["detail"]
        assert "100" in resp.json()["detail"]
        mock_cls.assert_not_called()

    @patch("backend.apply_real_api.DanDomainClient")
    def test_exactly_100_rows_accepted(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": f"SKU-{i:04d}",
                "TITLE_DK": f"Product {i}",
                "old_price": 100.0,
                "new_price": 109.0,
                "change_pct": 9.0,
            }
            for i in range(100)
        ]
        _make_manifest(bid, changes=changes)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 100,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 200
        assert resp.json()["applied_count"] == 100


# ---------------------------------------------------------------------------
# Tests – change_pct guardrail
# ---------------------------------------------------------------------------


class TestChangePctGuardrail:
    """abs(change_pct) > 30 must be rejected."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_positive_change_pct_over_30_rejected(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget",
                "old_price": 100.0,
                "new_price": 131.0,
                "change_pct": 31.0,
            },
        ]
        _make_manifest(bid, changes=changes)

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 400
        assert "change_pct" in resp.json()["detail"]
        mock_cls.assert_not_called()

    @patch("backend.apply_real_api.DanDomainClient")
    def test_negative_change_pct_over_30_rejected(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget",
                "old_price": 200.0,
                "new_price": 139.0,
                "change_pct": -30.5,
            },
        ]
        _make_manifest(bid, changes=changes)

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 400
        assert "change_pct" in resp.json()["detail"]
        mock_cls.assert_not_called()

    @patch("backend.apply_real_api.DanDomainClient")
    def test_change_pct_exactly_30_accepted(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget",
                "old_price": 100.0,
                "new_price": 130.0,
                "change_pct": 30.0,
            },
        ]
        _make_manifest(bid, changes=changes)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 1,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests – new_price guardrail
# ---------------------------------------------------------------------------


class TestNewPriceGuardrail:
    """new_price must be positive and finite."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_zero_price_rejected(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget",
                "old_price": 100.0,
                "new_price": 0.0,
                "change_pct": -100.0,
            },
        ]
        _make_manifest(bid, changes=changes)

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 400
        assert "new_price" in resp.json()["detail"]

    @patch("backend.apply_real_api.DanDomainClient")
    def test_negative_price_rejected(self, mock_cls):
        bid = str(uuid.uuid4())
        changes = [
            {
                "NUMBER": "SKU-001",
                "TITLE_DK": "Widget",
                "old_price": 100.0,
                "new_price": -50.0,
                "change_pct": -150.0,
            },
        ]
        _make_manifest(bid, changes=changes)

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests – valid manifest triggers write
# ---------------------------------------------------------------------------


class TestValidManifestApply:
    """A valid manifest should trigger DanDomainClient.update_prices_batch."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_write_called_with_correct_updates(self, mock_cls):
        bid = str(uuid.uuid4())
        _make_manifest(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["batch_id"] == bid
        assert data["applied_count"] == 2
        assert data["failed"] == []
        assert "started_at" in data
        assert "finished_at" in data

        # Verify the write was called
        mock_instance.update_prices_batch.assert_called_once()
        call_args = mock_instance.update_prices_batch.call_args
        updates = call_args[0][0]
        assert len(updates) == 2
        assert updates[0]["product_number"] == "SKU-001"
        assert updates[0]["new_price"] == 249.0
        assert updates[1]["product_number"] == "SKU-002"
        assert updates[1]["new_price"] == 159.0

    @patch("backend.apply_real_api.DanDomainClient")
    def test_partial_failure_reported(self, mock_cls):
        bid = str(uuid.uuid4())
        _make_manifest(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 1,
            "failed": 1,
            "errors": [
                {
                    "product_id": "",
                    "product_number": "SKU-002",
                    "variant_id": "",
                    "error": "SOAP fault",
                },
            ],
        }
        mock_cls.return_value = mock_instance

        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["applied_count"] == 1
        assert len(data["failed"]) == 1
        assert data["failed"][0]["NUMBER"] == "SKU-002"
        assert "SOAP" in data["failed"][0]["reason"]

    @patch("backend.apply_real_api.DanDomainClient")
    def test_audit_log_written(self, mock_cls):
        bid = str(uuid.uuid4())
        _make_manifest(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )

        assert _AUDIT_LOG.is_file()
        lines = _AUDIT_LOG.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["batch_id"] == bid
        assert entry["api_username"] == "test@example.com"
        assert entry["total_rows"] == 2
        assert entry["applied_count"] == 2
        assert entry["failed_count"] == 0


# ---------------------------------------------------------------------------
# Tests – double-apply prevention
# ---------------------------------------------------------------------------


class TestDoubleApply:
    """A batch that has already been applied must be blocked with 409."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_double_apply_blocked(self, mock_cls):
        bid = str(uuid.uuid4())
        _make_manifest(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        # First apply succeeds
        resp1 = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp1.status_code == 200

        # Second apply is blocked
        resp2 = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp2.status_code == 409
        assert "already" in resp2.json()["detail"].lower()

    @patch("backend.apply_real_api.DanDomainClient")
    def test_applied_marker_exists_after_success(self, mock_cls):
        bid = str(uuid.uuid4())
        _make_manifest(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )

        marker = _BATCH_DIR / f"{bid}.applied"
        assert marker.is_file()


# ---------------------------------------------------------------------------
# Tests – batch_id validation
# ---------------------------------------------------------------------------


class TestBatchIdValidation:
    """Invalid or missing batch_id must be rejected."""

    def test_invalid_uuid_format(self):
        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload("not-a-uuid"),
        )
        assert resp.status_code == 422

    def test_missing_batch(self):
        bid = str(uuid.uuid4())
        resp = client.post(
            "/apply-prices/apply",
            json=_apply_payload(bid),
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
