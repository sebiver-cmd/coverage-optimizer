"""Tests for push-to-shop safety gates.

Verifies that:
1. Unselected products are never included in push updates.
2. Unchanged products are never included in push updates.
3. Only changed fields are present in update dicts.
"""

import numpy as np
import pandas as pd
import pytest

from push_safety import build_push_updates, PRICE_EPSILON


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_test_df() -> pd.DataFrame:
    """Create a minimal final_df-like DataFrame for testing.

    - Product A (SKU-A): sales price changed 200 → 249 (no variant)
    - Product B (SKU-B): sales price changed 400 → 499 (variant 55)
    - Product C (SKU-C): sales price UNCHANGED (PRICE == NEW_PRICE == 600)
    """
    return pd.DataFrame({
        "PRODUCT_ID": ["101", "102", "103"],
        "TITLE_DK": ["Product A", "Product B", "Product C"],
        "NUMBER": ["SKU-A", "SKU-B", "SKU-C"],
        "BUY_PRICE": ["100,00", "200,00", "300,00"],
        "PRICE_EX_VAT": ["160,00", "320,00", "480,00"],
        "PRICE": ["200,00", "400,00", "600,00"],
        "COVERAGE_RATE_%": ["50,00%", "50,00%", "50,00%"],
        "VARIANT_ID": ["", "55", ""],
        "VARIANT_TYPES": ["", "Color", ""],
        "NEW_PRICE_EX_VAT": ["200,00", "400,00", "480,00"],
        "NEW_PRICE": ["249,00", "499,00", "600,00"],
        "NEW_COVERAGE_RATE_%": ["60,00%", "60,00%", "50,00%"],
    })


# ---------------------------------------------------------------------------
# Test: Unselected products are never pushed
# ---------------------------------------------------------------------------

class TestSelectionGate:
    """Gate 1: only explicitly-selected products appear in updates."""

    def test_unselected_products_excluded(self):
        df = _make_test_df()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        # Only select product at index 0
        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0},
        )
        pnums = [u["product_number"] for u in updates]
        assert "SKU-A" in pnums
        assert "SKU-B" not in pnums
        assert "SKU-C" not in pnums

    def test_empty_selection_produces_no_updates(self):
        df = _make_test_df()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices=set(),
        )
        assert updates == []

    def test_selection_none_includes_all_changed(self):
        """When selected_indices is None, all changed products pass gate 1."""
        df = _make_test_df()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices=None,
        )
        pnums = [u["product_number"] for u in updates]
        # A and B have price changes; C does not
        assert "SKU-A" in pnums
        assert "SKU-B" in pnums
        assert "SKU-C" not in pnums


# ---------------------------------------------------------------------------
# Test: Unchanged products are never pushed
# ---------------------------------------------------------------------------

class TestDiffGate:
    """Gate 2: only products with actual changes appear in updates."""

    def test_unchanged_price_excluded(self):
        """Product C has same PRICE and NEW_PRICE — must be excluded."""
        df = _make_test_df()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0, 1, 2},
        )
        pnums = [u["product_number"] for u in updates]
        assert "SKU-C" not in pnums

    def test_changed_prices_included(self):
        df = _make_test_df()
        mask = np.array([True, True, False])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0, 1},
        )
        pnums = [u["product_number"] for u in updates]
        assert "SKU-A" in pnums
        assert "SKU-B" in pnums

    def test_buy_price_only_change(self):
        """When only buy price changed, product is still included."""
        df = _make_test_df()
        # Only product C in the adjusted set
        mask = np.array([False, False, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 350.0])  # C changed

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={2},
        )
        assert len(updates) == 1
        assert updates[0]["product_number"] == "SKU-C"

    def test_no_changes_at_all(self):
        """When nothing changed, no updates are produced."""
        df = _make_test_df()
        # Override NEW_PRICE to equal PRICE for all rows
        df["NEW_PRICE"] = df["PRICE"].copy()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0, 1, 2},
        )
        assert updates == []


# ---------------------------------------------------------------------------
# Test: Only changed fields are sent
# ---------------------------------------------------------------------------

class TestFieldFiltering:
    """Only changed fields are included in update dicts."""

    def test_price_change_only(self):
        """When only sales price changed, buy_price is absent."""
        df = _make_test_df()
        mask = np.array([True, True, False])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0, 1},
        )
        for u in updates:
            assert "new_price" in u
            assert "buy_price" not in u

    def test_buy_price_change_only(self):
        """When only buy price changed, new_price is absent."""
        df = _make_test_df()
        mask = np.array([False, False, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 350.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={2},
        )
        assert len(updates) == 1
        u = updates[0]
        assert "buy_price" in u
        assert u["buy_price"] == 350.0
        assert "new_price" not in u

    def test_both_prices_changed(self):
        """When both prices changed, both fields are present."""
        df = _make_test_df()
        mask = np.array([True, False, False])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([150.0, 200.0, 300.0])  # A's buy price changed

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0},
        )
        assert len(updates) == 1
        u = updates[0]
        assert "new_price" in u  # sales price changed (200 → 249)
        assert "buy_price" in u  # buy price changed (100 → 150)

    def test_old_values_included_for_audit(self):
        """Old price values are included for diff display."""
        df = _make_test_df()
        mask = np.array([True, False, False])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([150.0, 200.0, 300.0])

        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0},
        )
        u = updates[0]
        assert "old_price" in u
        assert "old_buy_price" in u


# ---------------------------------------------------------------------------
# Test: Variant endpoint detection
# ---------------------------------------------------------------------------

class TestEndpointRouting:
    """Products with variant_id use Product_UpdateVariant."""

    def test_base_product_endpoint(self):
        df = _make_test_df()
        mask = np.array([True, False, False])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(df, mask, parsed_bp, work_bp)
        assert updates[0]["endpoint"] == "Product_Update"
        assert updates[0]["variant_id"] == ""

    def test_variant_product_endpoint(self):
        df = _make_test_df()
        mask = np.array([False, True, False])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        updates = build_push_updates(df, mask, parsed_bp, work_bp)
        assert updates[0]["endpoint"] == "Product_UpdateVariant"
        assert updates[0]["variant_id"] == "55"


# ---------------------------------------------------------------------------
# Test: Combined gates (both must pass)
# ---------------------------------------------------------------------------

class TestCombinedGates:
    """Both selection AND diff gates must pass for a product to be pushed."""

    def test_selected_but_unchanged_excluded(self):
        """Selected product with no actual change is still excluded."""
        df = _make_test_df()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        # Select all three, but C has no price change
        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={0, 1, 2},
        )
        pnums = [u["product_number"] for u in updates]
        assert "SKU-C" not in pnums

    def test_changed_but_unselected_excluded(self):
        """Changed product that is not selected is excluded."""
        df = _make_test_df()
        mask = np.array([True, True, True])
        parsed_bp = pd.Series([100.0, 200.0, 300.0])
        work_bp = pd.Series([100.0, 200.0, 300.0])

        # Only select C (which has no change) — A and B are changed but
        # not selected
        updates = build_push_updates(
            df, mask, parsed_bp, work_bp, selected_indices={2},
        )
        assert updates == []
