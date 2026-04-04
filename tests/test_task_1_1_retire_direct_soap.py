"""Tests for SaaS Task 1.1 — Retire Streamlit direct SOAP writes.

Validates:
- ``POST /apply-prices/create-manifest`` endpoint structure and persistence.
- ``POST /apply-prices/apply`` now forwards ``variant_id``, ``product_id``,
  and ``buy_price`` to ``DanDomainClient.update_prices_batch``.
- ``ui/pages/price_optimizer.py`` does not import ``DanDomainClient`` at
  module level when ``SB_OPTIMA_ALLOW_UI_DIRECT_PUSH`` is unset.
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

_BATCH_DIR = Path("data/apply_batches")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest_with_variant(batch_id: str) -> dict:
    """Persist a manifest with a mix of base and variant rows."""
    changes = [
        {
            "NUMBER": "BASE-001",
            "TITLE_DK": "Base Product",
            "product_id": "pid-101",
            "variant_id": "",
            "variant_types": "",
            "old_price": 200.0,
            "new_price": 249.0,
            "change_pct": 24.5,
            "buy_price": 100.0,
            "old_buy_price": 100.0,
        },
        {
            "NUMBER": "VAR-002",
            "TITLE_DK": "Variant Product (Red / XL)",
            "product_id": "pid-102",
            "variant_id": "vid-55",
            "variant_types": "Color/Size",
            "old_price": 400.0,
            "new_price": 449.0,
            "change_pct": 12.25,
            "buy_price": 200.0,
            "old_buy_price": 200.0,
        },
    ]
    manifest = {
        "batch_id": batch_id,
        "created_at": "2024-01-01T00:00:00+00:00",
        "source": "ui-create-manifest",
        "changes": changes,
        "summary": {
            "total": 2,
            "increases": 2,
            "decreases": 0,
            "unchanged": 0,
        },
    }
    _BATCH_DIR.mkdir(parents=True, exist_ok=True)
    (_BATCH_DIR / f"{batch_id}.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


@pytest.fixture(autouse=True)
def _enable_apply(monkeypatch):
    monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", "true")


@pytest.fixture(autouse=True)
def _clean_dirs():
    yield
    if _BATCH_DIR.exists():
        shutil.rmtree(_BATCH_DIR)
    audit = Path("data/apply_audit.log")
    if audit.exists():
        audit.unlink()


# ---------------------------------------------------------------------------
# Tests – create-manifest endpoint
# ---------------------------------------------------------------------------


class TestCreateManifest:
    """``POST /apply-prices/create-manifest`` — persist pre-computed changes."""

    def _minimal_changes(self) -> list[dict]:
        return [
            {
                "NUMBER": "SKU-A",
                "TITLE_DK": "Product A",
                "product_id": "p1",
                "variant_id": "",
                "variant_types": "",
                "old_price": 100.0,
                "new_price": 120.0,
                "change_pct": 20.0,
                "buy_price": 50.0,
                "old_buy_price": 50.0,
            },
            {
                "NUMBER": "SKU-B-V",
                "TITLE_DK": "Product B (variant)",
                "product_id": "p2",
                "variant_id": "v99",
                "variant_types": "Color",
                "old_price": 200.0,
                "new_price": 220.0,
                "change_pct": 10.0,
                "buy_price": 80.0,
                "old_buy_price": 80.0,
            },
        ]

    def test_returns_batch_id(self):
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": self._minimal_changes()},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "batch_id" in data
        # Must be a valid UUID
        uuid.UUID(data["batch_id"])

    def test_returns_changes(self):
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": self._minimal_changes()},
        )
        data = resp.json()
        assert "changes" in data
        assert len(data["changes"]) == 2

    def test_returns_summary(self):
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": self._minimal_changes()},
        )
        data = resp.json()
        summary = data["summary"]
        assert summary["total"] == 2
        assert summary["increases"] == 2
        assert summary["decreases"] == 0

    def test_manifest_file_persisted(self):
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": self._minimal_changes()},
        )
        batch_id = resp.json()["batch_id"]
        manifest_path = _BATCH_DIR / f"{batch_id}.json"
        assert manifest_path.is_file()

    def test_manifest_file_contains_variant_id(self):
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": self._minimal_changes()},
        )
        batch_id = resp.json()["batch_id"]
        manifest = json.loads((_BATCH_DIR / f"{batch_id}.json").read_text())
        variant_row = next(
            c for c in manifest["changes"] if c["variant_id"] == "v99"
        )
        assert variant_row["NUMBER"] == "SKU-B-V"
        assert variant_row["product_id"] == "p2"

    def test_manifest_retrievable_via_get_batch(self):
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": self._minimal_changes()},
        )
        batch_id = resp.json()["batch_id"]
        get_resp = client.get(f"/apply-prices/batch/{batch_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["batch_id"] == batch_id
        assert len(data["changes"]) == 2

    def test_empty_changes_accepted(self):
        """Empty change list should still produce a valid (empty) manifest."""
        resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": []},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total"] == 0


# ---------------------------------------------------------------------------
# Tests – apply endpoint now forwards variant_id and product_id
# ---------------------------------------------------------------------------


class TestApplyVariantRouting:
    """apply_real_api must forward variant_id / product_id to update_prices_batch."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_variant_id_forwarded_to_update_batch(self, mock_cls):
        """variant_id from the manifest must be included in the update dict."""
        bid = str(uuid.uuid4())
        _make_manifest_with_variant(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        resp = client.post(
            "/apply-prices/apply",
            json={
                "batch_id": bid,
                "confirm": True,
                "api_username": "user",
                "api_password": "pass",
                "site_id": 1,
            },
        )
        assert resp.status_code == 200

        call_args = mock_instance.update_prices_batch.call_args
        updates = call_args[0][0]
        assert len(updates) == 2

        by_num = {u["product_number"]: u for u in updates}

        # Base product: variant_id should be empty string
        base = by_num["BASE-001"]
        assert base.get("variant_id") == ""

        # Variant product: variant_id must be forwarded
        var = by_num["VAR-002"]
        assert var.get("variant_id") == "vid-55"

    @patch("backend.apply_real_api.DanDomainClient")
    def test_product_id_forwarded_to_update_batch(self, mock_cls):
        """product_id from the manifest must be forwarded."""
        bid = str(uuid.uuid4())
        _make_manifest_with_variant(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        client.post(
            "/apply-prices/apply",
            json={
                "batch_id": bid,
                "confirm": True,
                "api_username": "user",
                "api_password": "pass",
                "site_id": 1,
            },
        )

        updates = mock_instance.update_prices_batch.call_args[0][0]
        by_num = {u["product_number"]: u for u in updates}
        assert by_num["BASE-001"].get("product_id") == "pid-101"
        assert by_num["VAR-002"].get("product_id") == "pid-102"

    @patch("backend.apply_real_api.DanDomainClient")
    def test_buy_price_forwarded_when_present(self, mock_cls):
        """buy_price should be included in the update dict when non-zero."""
        bid = str(uuid.uuid4())
        _make_manifest_with_variant(bid)

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 2,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        client.post(
            "/apply-prices/apply",
            json={
                "batch_id": bid,
                "confirm": True,
                "api_username": "user",
                "api_password": "pass",
                "site_id": 1,
            },
        )

        updates = mock_instance.update_prices_batch.call_args[0][0]
        by_num = {u["product_number"]: u for u in updates}
        # Both rows have non-zero buy_price → must be forwarded
        assert by_num["BASE-001"].get("buy_price") == 100.0
        assert by_num["VAR-002"].get("buy_price") == 200.0


# ---------------------------------------------------------------------------
# Tests – UI module does not import DanDomainClient at module level
# ---------------------------------------------------------------------------


class TestUINoDirectSoap:
    """UI must not directly import DanDomainClient at module level."""

    def test_no_module_level_dandomain_import(self):
        """price_optimizer.py must not unconditionally import DanDomainClient."""
        import ui.pages.price_optimizer as mod
        assert "DanDomainClient" not in mod.__dict__, (
            "DanDomainClient must not be present in module namespace."
        )

    def test_no_dandomain_api_in_source(self):
        """The UI module must not reference dandomain_api at all."""
        import ui.pages.price_optimizer as mod
        source = inspect.getsource(mod)
        assert "dandomain_api" not in source, (
            "dandomain_api must not appear in price_optimizer.py source."
        )

    def test_backend_client_module_imported(self):
        """The UI should import from ui.backend_client for apply operations."""
        import ui.pages.price_optimizer as mod
        source = inspect.getsource(mod)
        assert "backend_client" in source, (
            "UI must use ui.backend_client for backend HTTP calls."
        )


# ---------------------------------------------------------------------------
# Tests – create-manifest + apply end-to-end with variant
# ---------------------------------------------------------------------------


class TestCreateManifestThenApply:
    """Integration: create a manifest via the new endpoint, then apply it."""

    @patch("backend.apply_real_api.DanDomainClient")
    def test_create_then_apply_with_variant(self, mock_cls):
        """Create a manifest with a variant row, then apply — verify variant_id forwarded."""
        changes = [
            {
                "NUMBER": "CRAFT-001-XL",
                "TITLE_DK": "Craft Jacket (XL)",
                "product_id": "craft-p1",
                "variant_id": "craft-vid-7",
                "variant_types": "Size",
                "old_price": 500.0,
                "new_price": 549.0,
                "change_pct": 9.8,
                "buy_price": 200.0,
                "old_buy_price": 200.0,
            },
        ]
        create_resp = client.post(
            "/apply-prices/create-manifest",
            json={"changes": changes},
        )
        assert create_resp.status_code == 200
        batch_id = create_resp.json()["batch_id"]

        mock_instance = MagicMock()
        mock_instance.update_prices_batch.return_value = {
            "success": 1,
            "failed": 0,
            "errors": [],
        }
        mock_cls.return_value = mock_instance

        apply_resp = client.post(
            "/apply-prices/apply",
            json={
                "batch_id": batch_id,
                "confirm": True,
                "api_username": "u",
                "api_password": "p",
                "site_id": 1,
            },
        )
        assert apply_resp.status_code == 200
        assert apply_resp.json()["applied_count"] == 1

        updates = mock_instance.update_prices_batch.call_args[0][0]
        assert updates[0]["variant_id"] == "craft-vid-7"
        assert updates[0]["product_number"] == "CRAFT-001-XL"
        assert updates[0]["new_price"] == 549.0
