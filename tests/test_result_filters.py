"""Tests for client-side result filtering helpers in Price Optimizer.

Covers ``_prepare_pricing_columns`` and ``apply_result_filters`` without
touching Streamlit widgets — pure DataFrame logic only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from domain.pricing import format_dk
from ui.pages.price_optimizer import _prepare_pricing_columns, apply_result_filters


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal DataFrame with Danish-formatted prices."""
    records = []
    for r in rows:
        rec = {
            "NUMBER": r["number"],
            "PRICE": format_dk(r["old_price"]),
            "NEW_PRICE": format_dk(r["new_price"]),
        }
        if "name" in r:
            rec["TITLE_DK"] = r["name"]
        records.append(rec)
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# _prepare_pricing_columns
# ---------------------------------------------------------------------------

class TestPreparePricingColumns:
    """Unit tests for ``_prepare_pricing_columns``."""

    def test_correct_old_and_new_price(self):
        df = _make_df([
            {"number": "A1", "old_price": 100.0, "new_price": 110.0},
            {"number": "A2", "old_price": 200.0, "new_price": 180.0},
        ])
        out = _prepare_pricing_columns(df)
        assert list(out["old_price"]) == [100.0, 200.0]
        assert list(out["new_price"]) == [110.0, 180.0]

    def test_change_pct_increase(self):
        """100 → 110 is +10 %."""
        df = _make_df([{"number": "A1", "old_price": 100.0, "new_price": 110.0}])
        out = _prepare_pricing_columns(df)
        assert out["change_pct"].iloc[0] == pytest.approx(10.0)

    def test_change_pct_decrease(self):
        """100 → 90 is −10 %."""
        df = _make_df([{"number": "A1", "old_price": 100.0, "new_price": 90.0}])
        out = _prepare_pricing_columns(df)
        assert out["change_pct"].iloc[0] == pytest.approx(-10.0)

    def test_change_pct_unchanged(self):
        df = _make_df([{"number": "A1", "old_price": 100.0, "new_price": 100.0}])
        out = _prepare_pricing_columns(df)
        assert out["change_pct"].iloc[0] == pytest.approx(0.0)

    def test_zero_old_price_no_crash(self):
        """Division by zero must not raise; change_pct should be 0.0."""
        df = _make_df([{"number": "Z1", "old_price": 0.0, "new_price": 50.0}])
        out = _prepare_pricing_columns(df)
        assert out["change_pct"].iloc[0] == pytest.approx(0.0)

    def test_does_not_mutate_original(self):
        df = _make_df([{"number": "A1", "old_price": 100.0, "new_price": 110.0}])
        _ = _prepare_pricing_columns(df)
        assert "old_price" not in df.columns


# ---------------------------------------------------------------------------
# apply_result_filters — direction
# ---------------------------------------------------------------------------

class TestDirectionFilter:
    """Direction filter tests."""

    _ROWS = [
        {"number": "I1", "old_price": 100.0, "new_price": 120.0, "name": "Inc"},
        {"number": "D1", "old_price": 100.0, "new_price": 80.0, "name": "Dec"},
        {"number": "U1", "old_price": 100.0, "new_price": 100.0, "name": "Same"},
    ]

    def _prepared(self):
        return _prepare_pricing_columns(_make_df(self._ROWS))

    def test_all_returns_everything(self):
        out = apply_result_filters(self._prepared(), direction="All")
        assert len(out) == 3

    def test_only_increases(self):
        out = apply_result_filters(self._prepared(), direction="Only increases")
        assert list(out["NUMBER"]) == ["I1"]

    def test_only_decreases(self):
        out = apply_result_filters(self._prepared(), direction="Only decreases")
        assert list(out["NUMBER"]) == ["D1"]

    def test_only_unchanged(self):
        out = apply_result_filters(self._prepared(), direction="Only unchanged")
        assert list(out["NUMBER"]) == ["U1"]


# ---------------------------------------------------------------------------
# apply_result_filters — percentage range
# ---------------------------------------------------------------------------

class TestPctRangeFilter:
    """Percentage-range filter tests."""

    _ROWS = [
        {"number": "A", "old_price": 100.0, "new_price": 105.0},  # +5 %
        {"number": "B", "old_price": 100.0, "new_price": 120.0},  # +20 %
        {"number": "C", "old_price": 100.0, "new_price": 90.0},   # -10 %
        {"number": "D", "old_price": 100.0, "new_price": 100.0},  # 0 %
    ]

    def _prepared(self):
        return _prepare_pricing_columns(_make_df(self._ROWS))

    def test_range_0_to_15(self):
        """0–15 % absolute keeps A (+5 %), C (|-10 %|=10 %), D (0 %)."""
        out = apply_result_filters(self._prepared(), pct_min=0.0, pct_max=15.0)
        assert set(out["NUMBER"]) == {"A", "C", "D"}

    def test_range_10_to_25(self):
        """10–25 % absolute keeps B (+20 %) and C (|-10 %|=10 %)."""
        out = apply_result_filters(self._prepared(), pct_min=10.0, pct_max=25.0)
        assert set(out["NUMBER"]) == {"B", "C"}


# ---------------------------------------------------------------------------
# apply_result_filters — text search
# ---------------------------------------------------------------------------

class TestTextSearch:
    """Text search filter tests."""

    _ROWS = [
        {"number": "SKU-100", "old_price": 100.0, "new_price": 110.0, "name": "Widget Alpha"},
        {"number": "SKU-200", "old_price": 200.0, "new_price": 200.0, "name": "Gadget Beta"},
        {"number": "SKU-300", "old_price": 50.0, "new_price": 60.0, "name": "Widget Gamma"},
    ]

    def _prepared(self):
        return _prepare_pricing_columns(_make_df(self._ROWS))

    def test_search_by_sku(self):
        out = apply_result_filters(self._prepared(), search_text="SKU-200")
        assert list(out["NUMBER"]) == ["SKU-200"]

    def test_search_by_name_case_insensitive(self):
        out = apply_result_filters(self._prepared(), search_text="widget")
        assert set(out["NUMBER"]) == {"SKU-100", "SKU-300"}

    def test_search_no_match(self):
        out = apply_result_filters(self._prepared(), search_text="nonexistent")
        assert out.empty

    def test_empty_search_returns_all(self):
        out = apply_result_filters(self._prepared(), search_text="")
        assert len(out) == 3


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------

class TestCombinedFilters:
    """Combining direction + pct range + search."""

    _ROWS = [
        {"number": "P1", "old_price": 100.0, "new_price": 115.0, "name": "Foo"},
        {"number": "P2", "old_price": 100.0, "new_price": 130.0, "name": "Bar"},
        {"number": "P3", "old_price": 100.0, "new_price": 90.0, "name": "Foo Baz"},
        {"number": "P4", "old_price": 100.0, "new_price": 100.0, "name": "Qux"},
    ]

    def _prepared(self):
        return _prepare_pricing_columns(_make_df(self._ROWS))

    def test_increases_with_search(self):
        out = apply_result_filters(
            self._prepared(),
            direction="Only increases",
            search_text="Foo",
        )
        assert list(out["NUMBER"]) == ["P1"]

    def test_increases_with_pct_range(self):
        out = apply_result_filters(
            self._prepared(),
            direction="Only increases",
            pct_min=0.0,
            pct_max=20.0,
        )
        # P1 is +15 % (within 0–20 %), P2 is +30 % (outside)
        assert list(out["NUMBER"]) == ["P1"]
