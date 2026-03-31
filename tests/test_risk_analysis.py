"""Tests for the ``domain.risk_analysis`` module.

Covers:
- ``compute_largest_decreases``
- ``compute_near_cost_warnings``
- ``compute_change_histogram``
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from domain.pricing import format_dk
from domain.risk_analysis import (
    compute_largest_decreases,
    compute_near_cost_warnings,
    compute_change_histogram,
    NEAR_COST_MARGIN_THRESHOLD,
    TOP_DECREASES_COUNT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(rows: list[dict], *, include_title: bool = False) -> pd.DataFrame:
    """Build a minimal display DataFrame with Danish-formatted prices."""
    records = []
    for r in rows:
        rec: dict = {
            "NUMBER": r["number"],
            "PRICE": format_dk(r["old_price"]),
            "NEW_PRICE": format_dk(r["new_price"]),
        }
        if include_title:
            rec["TITLE_DK"] = r.get("title", "")
        records.append(rec)
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# compute_largest_decreases
# ---------------------------------------------------------------------------

class TestLargestDecreases:

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["NUMBER", "PRICE", "NEW_PRICE"])
        result = compute_largest_decreases(df)
        assert result.empty

    def test_no_decreases_returns_empty(self):
        df = _make_df([
            {"number": "A1", "old_price": 100, "new_price": 120},
            {"number": "A2", "old_price": 100, "new_price": 100},
        ])
        result = compute_largest_decreases(df)
        assert result.empty

    def test_sorted_by_largest_decrease(self):
        df = _make_df([
            {"number": "D1", "old_price": 100, "new_price": 95},   # -5%
            {"number": "D2", "old_price": 100, "new_price": 70},   # -30%
            {"number": "D3", "old_price": 100, "new_price": 85},   # -15%
        ])
        result = compute_largest_decreases(df, top_n=10)
        assert list(result["NUMBER"]) == ["D2", "D3", "D1"]

    def test_top_n_limits_rows(self):
        rows = [
            {"number": f"P{i}", "old_price": 100, "new_price": 100 - i}
            for i in range(1, 20)
        ]
        df = _make_df(rows)
        result = compute_largest_decreases(df, top_n=5)
        assert len(result) == 5

    def test_includes_title_when_present(self):
        df = _make_df(
            [{"number": "T1", "old_price": 200, "new_price": 150, "title": "Widget"}],
            include_title=True,
        )
        result = compute_largest_decreases(df)
        assert "TITLE_DK" in result.columns
        assert result.iloc[0]["TITLE_DK"] == "Widget"

    def test_omits_title_when_absent(self):
        df = _make_df(
            [{"number": "T1", "old_price": 200, "new_price": 150}],
        )
        result = compute_largest_decreases(df)
        assert "TITLE_DK" not in result.columns

    def test_change_pct_is_correct(self):
        df = _make_df([
            {"number": "C1", "old_price": 200, "new_price": 150},  # -25%
        ])
        result = compute_largest_decreases(df)
        assert result.iloc[0]["Change %"] == pytest.approx(-25.0)

    def test_zero_old_price_treated_as_zero_pct(self):
        """Products with zero old price have 0% change and are not decreases."""
        df = _make_df([
            {"number": "Z1", "old_price": 0, "new_price": 100},
        ])
        result = compute_largest_decreases(df)
        assert result.empty


# ---------------------------------------------------------------------------
# compute_near_cost_warnings
# ---------------------------------------------------------------------------

class TestNearCostWarnings:

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["NUMBER", "NEW_PRICE"])
        bp = pd.Series([], dtype=float)
        result = compute_near_cost_warnings(df, bp)
        assert result.empty

    def test_no_warnings_when_margins_healthy(self):
        # New price 200 incl VAT = 160 ex VAT, buy = 50 → margin ~69%
        df = _make_df([
            {"number": "H1", "old_price": 180, "new_price": 200},
        ])
        bp = pd.Series([50.0])
        result = compute_near_cost_warnings(df, bp)
        assert result.empty

    def test_flags_near_cost_product(self):
        # New price 125 incl VAT = 100 ex VAT, buy = 95 → margin 5%
        df = _make_df([
            {"number": "NC1", "old_price": 120, "new_price": 125},
        ])
        bp = pd.Series([95.0])
        result = compute_near_cost_warnings(df, bp)
        assert len(result) == 1
        assert result.iloc[0]["NUMBER"] == "NC1"
        assert result.iloc[0]["Margin %"] < 10.0

    def test_below_cost_flagged(self):
        # New price 100 incl VAT = 80 ex VAT, buy = 90 → margin negative
        df = _make_df([
            {"number": "BC1", "old_price": 120, "new_price": 100},
        ])
        bp = pd.Series([90.0])
        result = compute_near_cost_warnings(df, bp)
        assert len(result) == 1
        assert result.iloc[0]["Margin %"] < 0

    def test_zero_buy_price_not_flagged(self):
        """Products with zero buy price should not be flagged."""
        df = _make_df([
            {"number": "Z1", "old_price": 100, "new_price": 50},
        ])
        bp = pd.Series([0.0])
        result = compute_near_cost_warnings(df, bp)
        assert result.empty

    def test_sorted_by_margin_ascending(self):
        # P1: price 125 → ex-VAT 100, buy 95 → margin 5%
        # P2: price 125 → ex-VAT 100, buy 92 → margin 8%
        df = _make_df([
            {"number": "P1", "old_price": 120, "new_price": 125},
            {"number": "P2", "old_price": 120, "new_price": 125},
        ])
        bp = pd.Series([95.0, 92.0])
        result = compute_near_cost_warnings(df, bp)
        assert len(result) == 2
        assert result.iloc[0]["NUMBER"] == "P1"  # lower margin first

    def test_custom_threshold(self):
        # New price 200 incl VAT = 160 ex VAT, buy = 140 → margin 12.5%
        df = _make_df([
            {"number": "CT1", "old_price": 180, "new_price": 200},
        ])
        bp = pd.Series([140.0])
        # Default 10% threshold: 12.5% margin → not flagged
        result_default = compute_near_cost_warnings(df, bp)
        assert result_default.empty
        # 15% threshold: 12.5% margin → flagged
        result_custom = compute_near_cost_warnings(df, bp, margin_threshold=0.15)
        assert len(result_custom) == 1


# ---------------------------------------------------------------------------
# compute_change_histogram
# ---------------------------------------------------------------------------

class TestChangeHistogram:

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame(columns=["PRICE", "NEW_PRICE"])
        labels, counts = compute_change_histogram(df)
        assert labels == []
        assert counts == []

    def test_returns_labels_and_counts(self):
        df = _make_df([
            {"number": "H1", "old_price": 100, "new_price": 110},  # +10%
            {"number": "H2", "old_price": 100, "new_price": 105},  # +5%
            {"number": "H3", "old_price": 100, "new_price": 90},   # -10%
        ])
        labels, counts = compute_change_histogram(df)
        assert len(labels) > 0
        assert len(labels) == len(counts)
        assert sum(counts) == 3  # All products accounted for

    def test_all_unchanged_in_zero_bin(self):
        df = _make_df([
            {"number": "U1", "old_price": 100, "new_price": 100},
            {"number": "U2", "old_price": 200, "new_price": 200},
        ])
        labels, counts = compute_change_histogram(df)
        # 0% change should land in the "0 to 5" bin
        zero_bin_idx = labels.index("0 to 5")
        assert counts[zero_bin_idx] == 2

    def test_overflow_bins(self):
        df = _make_df([
            {"number": "O1", "old_price": 100, "new_price": 200},  # +100%
            {"number": "O2", "old_price": 100, "new_price": 30},   # -70%
        ])
        labels, counts = compute_change_histogram(df)
        assert labels[0] == "< -30"
        assert labels[-1] == "> +30"
        # -70% should be in the "< -30" bin
        assert counts[0] == 1
        # +100% should be in the "> +30" bin
        assert counts[-1] == 1

    def test_custom_bin_edges(self):
        df = _make_df([
            {"number": "B1", "old_price": 100, "new_price": 110},  # +10%
        ])
        labels, counts = compute_change_histogram(df, bin_edges=[-10, 0, 10, 20])
        assert len(labels) == 5  # 4 edges → 3 bins + 2 overflow = 5
        assert sum(counts) == 1
