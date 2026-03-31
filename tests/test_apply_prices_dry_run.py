"""Tests for the dry-run apply-prices endpoint.

Validates:
- Correct response structure and counts.
- Filtering by ``product_numbers``.
- No-write guarantee (no write/push imports in the module).
- Batch persistence (batch_id, manifest file, GET endpoint).
"""

from __future__ import annotations

import inspect
import json
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from backend.main import app
from backend.optimizer_api import OptimizeResponse, OptimizeSummary, ProductRow

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_ROWS = [
    ProductRow(
        product_id="101",
        title="Widget Pro",
        item_number="WID-001",
        producer="Supplier Inc",
        buy_price=100.0,
        current_price=200.0,
        current_price_ex_vat=160.0,
        current_coverage_pct=37.5,
        suggested_price=249.0,
        suggested_price_ex_vat=199.2,
        suggested_coverage_pct=49.8,
        needs_adjustment=True,
        variant_id="",
        variant_types="",
    ),
    ProductRow(
        product_id="102",
        title="Gadget Lite",
        item_number="GAD-002",
        producer="Other Co",
        buy_price=50.0,
        current_price=150.0,
        current_price_ex_vat=120.0,
        current_coverage_pct=58.33,
        suggested_price=150.0,
        suggested_price_ex_vat=120.0,
        suggested_coverage_pct=58.33,
        needs_adjustment=False,
        variant_id="V1",
        variant_types="Color: Red",
    ),
    ProductRow(
        product_id="103",
        title="Gizmo Max",
        item_number="GIZ-003",
        producer="Supplier Inc",
        buy_price=80.0,
        current_price=180.0,
        current_price_ex_vat=144.0,
        current_coverage_pct=44.44,
        suggested_price=159.0,
        suggested_price_ex_vat=127.2,
        suggested_coverage_pct=49.81,
        needs_adjustment=True,
        variant_id="",
        variant_types="",
    ),
]

_SAMPLE_SUMMARY = OptimizeSummary(
    total_products=3,
    base_products=3,
    total_rows=3,
    adjusted_count=2,
    unchanged_count=1,
    adjusted_pct=66.67,
    avg_current_coverage_pct=46.76,
    avg_suggested_coverage_pct=52.54,
)

_SAMPLE_OPT_RESPONSE = OptimizeResponse(
    summary=_SAMPLE_SUMMARY,
    rows=_SAMPLE_ROWS,
)


def _dry_run_payload(product_numbers=None):
    """Build a valid dry-run request body."""
    body = {
        "optimize_payload": {
            "api_username": "test@example.com",
            "api_password": "secret",
            "site_id": 1,
            "price_pct": 0.0,
            "beautify_digit": 9,
        },
    }
    if product_numbers is not None:
        body["product_numbers"] = product_numbers
    return body


# ---------------------------------------------------------------------------
# Tests – response structure
# ---------------------------------------------------------------------------


class TestDryRunStructure:
    """Verify the response shape and field types."""

    @patch("backend.apply_prices_api.run_optimization")
    def test_returns_changes_and_summary(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        assert resp.status_code == 200

        data = resp.json()
        assert "batch_id" in data
        assert "changes" in data
        assert "summary" in data

    @patch("backend.apply_prices_api.run_optimization")
    def test_change_row_fields(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        row = resp.json()["changes"][0]

        assert "NUMBER" in row
        assert "TITLE_DK" in row
        assert "old_price" in row
        assert "new_price" in row
        assert "change_pct" in row

    @patch("backend.apply_prices_api.run_optimization")
    def test_numeric_types(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        row = resp.json()["changes"][0]

        assert isinstance(row["old_price"], (int, float))
        assert isinstance(row["new_price"], (int, float))
        assert isinstance(row["change_pct"], (int, float))

    @patch("backend.apply_prices_api.run_optimization")
    def test_summary_counts(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        summary = resp.json()["summary"]

        assert summary["total"] == 3
        # WID-001: 200 → 249 (increase), GAD-002: 150 → 150 (unchanged),
        # GIZ-003: 180 → 159 (decrease)
        assert summary["increases"] == 1
        assert summary["decreases"] == 1
        assert summary["unchanged"] == 1

    @patch("backend.apply_prices_api.run_optimization")
    def test_change_pct_computation(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        changes = resp.json()["changes"]

        # WID-001: ((249 - 200) / 200) * 100 = 24.5
        wid = next(c for c in changes if c["NUMBER"] == "WID-001")
        assert wid["change_pct"] == 24.5

        # GAD-002: unchanged → 0.0
        gad = next(c for c in changes if c["NUMBER"] == "GAD-002")
        assert gad["change_pct"] == 0.0

        # GIZ-003: ((159 - 180) / 180) * 100 ≈ -11.67
        giz = next(c for c in changes if c["NUMBER"] == "GIZ-003")
        assert giz["change_pct"] == pytest.approx(-11.67, abs=0.01)

    @patch("backend.apply_prices_api.run_optimization")
    def test_zero_old_price_change_pct(self, mock_opt):
        """When old_price is 0, change_pct should be 0.0 (no division error)."""
        zero_row = ProductRow(
            product_id="200",
            title="Free Item",
            item_number="FREE-001",
            producer="",
            buy_price=0.0,
            current_price=0.0,
            current_price_ex_vat=0.0,
            current_coverage_pct=0.0,
            suggested_price=99.0,
            suggested_price_ex_vat=79.2,
            suggested_coverage_pct=100.0,
            needs_adjustment=True,
        )
        mock_opt.return_value = OptimizeResponse(
            summary=OptimizeSummary(
                total_products=1,
                base_products=1,
                total_rows=1,
                adjusted_count=1,
                unchanged_count=0,
                adjusted_pct=100.0,
                avg_current_coverage_pct=0.0,
                avg_suggested_coverage_pct=100.0,
            ),
            rows=[zero_row],
        )

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        assert resp.status_code == 200
        assert resp.json()["changes"][0]["change_pct"] == 0.0


# ---------------------------------------------------------------------------
# Tests – product_numbers filtering
# ---------------------------------------------------------------------------


class TestDryRunFiltering:
    """Verify product_numbers filtering."""

    @patch("backend.apply_prices_api.run_optimization")
    def test_filter_single_product(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=["WID-001"]),
        )
        data = resp.json()

        assert data["summary"]["total"] == 1
        assert data["changes"][0]["NUMBER"] == "WID-001"

    @patch("backend.apply_prices_api.run_optimization")
    def test_filter_multiple_products(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=["WID-001", "GIZ-003"]),
        )
        data = resp.json()

        assert data["summary"]["total"] == 2
        numbers = {c["NUMBER"] for c in data["changes"]}
        assert numbers == {"WID-001", "GIZ-003"}

    @patch("backend.apply_prices_api.run_optimization")
    def test_filter_nonexistent_product(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=["DOES-NOT-EXIST"]),
        )
        data = resp.json()

        assert data["summary"]["total"] == 0
        assert data["changes"] == []

    @patch("backend.apply_prices_api.run_optimization")
    def test_no_filter_returns_all(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=None),
        )
        data = resp.json()

        assert data["summary"]["total"] == 3

    @patch("backend.apply_prices_api.run_optimization")
    def test_empty_filter_returns_all(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=[]),
        )
        data = resp.json()

        # Empty list is falsy → no filter applied → all products returned
        assert data["summary"]["total"] == 3

    @patch("backend.apply_prices_api.run_optimization")
    def test_filter_summary_counts_are_correct(self, mock_opt):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        # Filter to only the increase (WID-001) and unchanged (GAD-002)
        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=["WID-001", "GAD-002"]),
        )
        summary = resp.json()["summary"]

        assert summary["total"] == 2
        assert summary["increases"] == 1
        assert summary["decreases"] == 0
        assert summary["unchanged"] == 1


# ---------------------------------------------------------------------------
# Tests – no-write guarantee
# ---------------------------------------------------------------------------


class TestNoWriteGuarantee:
    """Ensure the apply_prices_api module does not import write functions."""

    def test_no_dandomain_write_imports(self):
        """The module must not import DanDomainClient or any write helpers."""
        import backend.apply_prices_api as mod
        source = inspect.getsource(mod)

        # Should not directly import DanDomainClient (writes go through it)
        assert "DanDomainClient" not in source, (
            "apply_prices_api must not import DanDomainClient"
        )

    def test_no_push_safety_imports(self):
        """The module must not import push_safety (push-to-shop logic)."""
        import backend.apply_prices_api as mod
        source = inspect.getsource(mod)

        assert "push_safety" not in source, (
            "apply_prices_api must not import push_safety"
        )
        assert "build_push_updates" not in source, (
            "apply_prices_api must not import build_push_updates"
        )

    def test_no_update_or_set_calls(self):
        """The module must not contain calls to update/set methods."""
        import backend.apply_prices_api as mod
        source = inspect.getsource(mod)

        for forbidden in ("update_product", "set_product", "Product_Set",
                          "push_to_shop", "apply_prices"):
            assert forbidden not in source, (
                f"apply_prices_api must not contain '{forbidden}'"
            )


# ---------------------------------------------------------------------------
# Tests – error propagation
# ---------------------------------------------------------------------------


class TestDryRunErrors:
    """Verify that errors from the optimisation pipeline propagate correctly."""

    @patch("backend.apply_prices_api.run_optimization")
    def test_404_when_no_products(self, mock_opt):
        mock_opt.side_effect = HTTPException(
            status_code=404, detail="No products match the specified filters."
        )

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        assert resp.status_code == 404

    @patch("backend.apply_prices_api.run_optimization")
    def test_502_on_api_error(self, mock_opt):
        mock_opt.side_effect = HTTPException(
            status_code=502, detail="SOAP failure"
        )

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        assert resp.status_code == 502

    def test_422_on_invalid_payload(self):
        resp = client.post("/apply-prices/dry-run", json={})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests – batch persistence
# ---------------------------------------------------------------------------

# Use a temporary directory to avoid polluting the real data dir.
_TEST_BATCH_DIR = Path("data/apply_batches")


@pytest.fixture(autouse=False)
def _clean_batch_dir():
    """Remove test-created batch files after each test that uses this fixture."""
    yield
    if _TEST_BATCH_DIR.exists():
        shutil.rmtree(_TEST_BATCH_DIR)


class TestBatchPersistence:
    """Verify that dry-run creates a batch manifest file."""

    @patch("backend.apply_prices_api.run_optimization")
    def test_dry_run_returns_batch_id(self, mock_opt, _clean_batch_dir):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        assert resp.status_code == 200
        data = resp.json()

        batch_id = data["batch_id"]
        # Must be a valid UUID-4
        parsed = uuid.UUID(batch_id, version=4)
        assert str(parsed) == batch_id

    @patch("backend.apply_prices_api.run_optimization")
    def test_manifest_file_created(self, mock_opt, _clean_batch_dir):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        batch_id = resp.json()["batch_id"]

        manifest_path = _TEST_BATCH_DIR / f"{batch_id}.json"
        assert manifest_path.is_file()

    @patch("backend.apply_prices_api.run_optimization")
    def test_manifest_has_required_fields(self, mock_opt, _clean_batch_dir):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=["WID-001"]),
        )
        batch_id = resp.json()["batch_id"]

        manifest_path = _TEST_BATCH_DIR / f"{batch_id}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["batch_id"] == batch_id
        assert "created_at" in manifest
        assert "optimize_payload" in manifest
        assert "product_numbers" in manifest
        assert "changes" in manifest
        assert "summary" in manifest

    @patch("backend.apply_prices_api.run_optimization")
    def test_manifest_product_numbers_null_when_omitted(
        self, mock_opt, _clean_batch_dir
    ):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        resp = client.post(
            "/apply-prices/dry-run",
            json=_dry_run_payload(product_numbers=None),
        )
        batch_id = resp.json()["batch_id"]

        manifest_path = _TEST_BATCH_DIR / f"{batch_id}.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["product_numbers"] is None


# ---------------------------------------------------------------------------
# Tests – GET /apply-prices/batch/{batch_id}
# ---------------------------------------------------------------------------


class TestGetBatch:
    """Verify the GET endpoint for retrieving a batch manifest."""

    @patch("backend.apply_prices_api.run_optimization")
    def test_get_returns_manifest(self, mock_opt, _clean_batch_dir):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        # Create a batch via dry-run
        post_resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        batch_id = post_resp.json()["batch_id"]

        # Retrieve via GET
        get_resp = client.get(f"/apply-prices/batch/{batch_id}")
        assert get_resp.status_code == 200

        data = get_resp.json()
        assert data["batch_id"] == batch_id
        assert data["summary"]["total"] == 3
        assert len(data["changes"]) == 3

    @patch("backend.apply_prices_api.run_optimization")
    def test_get_returns_same_content_as_dry_run(self, mock_opt, _clean_batch_dir):
        mock_opt.return_value = _SAMPLE_OPT_RESPONSE

        post_resp = client.post("/apply-prices/dry-run", json=_dry_run_payload())
        post_data = post_resp.json()
        batch_id = post_data["batch_id"]

        get_resp = client.get(f"/apply-prices/batch/{batch_id}")
        get_data = get_resp.json()

        # changes and summary must match
        assert get_data["changes"] == post_data["changes"]
        assert get_data["summary"] == post_data["summary"]

    def test_404_missing_batch(self, _clean_batch_dir):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/apply-prices/batch/{fake_id}")
        assert resp.status_code == 404

    def test_422_invalid_batch_id_format(self):
        resp = client.get("/apply-prices/batch/not-a-uuid")
        assert resp.status_code == 422

    def test_422_path_traversal_attempt(self):
        # Dots and slashes are not valid UUID chars; our regex rejects them.
        resp = client.get("/apply-prices/batch/../../../etc/passwd")
        # FastAPI may return 404 (route not found) or our 422; either way
        # it must never return 200 with file contents.
        assert resp.status_code in (404, 422)
        if resp.status_code == 200:
            pytest.fail("Path traversal must not succeed")

    def test_422_path_traversal_uuid_like(self):
        # A UUID-shaped string with invalid hex char 'g' is rejected.
        resp = client.get("/apply-prices/batch/00000000-0000-0000-0000-00000000000g")
        assert resp.status_code == 422

    def test_422_empty_batch_id(self):
        resp = client.get("/apply-prices/batch/ ")
        assert resp.status_code == 422
