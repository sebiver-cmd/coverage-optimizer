"""Tests for the ``_render_result_summary`` helper in Price Optimizer.

Validates the computation and rendering logic of the result summary
panel added to the Price Optimizer page.  Streamlit calls are mocked
so that tests run without a live Streamlit session.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from domain.pricing import format_dk
from ui.pages.price_optimizer import _render_result_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_final_df(rows: list[dict], *, include_title: bool = False) -> pd.DataFrame:
    """Build a minimal ``final_df`` with Danish-formatted prices."""
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


def _setup_mocks(mock_st):
    """Wire up the Streamlit mock so context managers and columns work."""
    container_ctx = MagicMock()
    mock_st.container.return_value.__enter__ = MagicMock(return_value=container_ctx)
    mock_st.container.return_value.__exit__ = MagicMock(return_value=False)

    m1, m2, m3, m4 = MagicMock(), MagicMock(), MagicMock(), MagicMock()

    col_left, col_right = MagicMock(), MagicMock()
    col_left.__enter__ = MagicMock(return_value=col_left)
    col_left.__exit__ = MagicMock(return_value=False)
    col_right.__enter__ = MagicMock(return_value=col_right)
    col_right.__exit__ = MagicMock(return_value=False)

    mock_st.columns.side_effect = [(m1, m2, m3, m4), (col_left, col_right)]
    return m1, m2, m3, m4


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRenderResultSummary:
    """Unit tests for ``_render_result_summary``."""

    @patch("ui.pages.price_optimizer.st")
    def test_empty_df_renders_nothing(self, mock_st):
        """An empty DataFrame should cause an early return with no output."""
        _render_result_summary(pd.DataFrame(columns=["NUMBER", "PRICE", "NEW_PRICE"]))
        mock_st.container.assert_not_called()

    @patch("ui.pages.price_optimizer.st")
    def test_metrics_all_unchanged(self, mock_st):
        """When all prices are unchanged, changed count is 0 and avg metrics show N/A."""
        df = _make_final_df([
            {"number": "A1", "old_price": 100.0, "new_price": 100.0},
            {"number": "A2", "old_price": 200.0, "new_price": 200.0},
        ])

        m1, m2, m3, m4 = _setup_mocks(mock_st)

        _render_result_summary(df)

        m1.metric.assert_called_once_with("Total Products", "2")
        m2.metric.assert_called_once_with("Changed Products", "0")
        m3.metric.assert_called_once_with("Avg Increase", "N/A")
        m4.metric.assert_called_once_with("Avg Decrease", "N/A")

    @patch("ui.pages.price_optimizer.st")
    def test_metrics_mixed_changes(self, mock_st):
        """Mixed increases and decreases should compute correct averages."""
        df = _make_final_df([
            {"number": "X1", "old_price": 100.0, "new_price": 120.0},   # +20%
            {"number": "X2", "old_price": 200.0, "new_price": 240.0},   # +20%
            {"number": "X3", "old_price": 100.0, "new_price": 80.0},    # -20%
            {"number": "X4", "old_price": 100.0, "new_price": 100.0},   # unchanged
        ])

        m1, m2, m3, m4 = _setup_mocks(mock_st)

        _render_result_summary(df)

        m1.metric.assert_called_once_with("Total Products", "4")
        m2.metric.assert_called_once_with("Changed Products", "3")
        m3.metric.assert_called_once_with("Avg Increase", "+20.00%")
        m4.metric.assert_called_once_with("Avg Decrease", "-20.00%")

    @patch("ui.pages.price_optimizer.st")
    def test_top5_increases_fewer_than_5(self, mock_st):
        """When fewer than 5 increases exist, the table should have that many rows."""
        df = _make_final_df([
            {"number": "P1", "old_price": 100.0, "new_price": 150.0},   # +50%
            {"number": "P2", "old_price": 100.0, "new_price": 130.0},   # +30%
        ])

        _setup_mocks(mock_st)
        _render_result_summary(df)

        # st.dataframe is called inside "with col:" context --
        # when mocked, calls land on the top-level mock_st.
        dataframe_calls = mock_st.dataframe.call_args_list
        assert len(dataframe_calls) == 1  # only increases, no decreases
        shown_df = dataframe_calls[0][0][0]
        assert len(shown_df) == 2
        assert shown_df.iloc[0]["NUMBER"] == "P1"  # 50% > 30%

    @patch("ui.pages.price_optimizer.st")
    def test_top5_decreases_sorted(self, mock_st):
        """Top 5 decreases should be sorted by largest decrease first."""
        df = _make_final_df([
            {"number": "D1", "old_price": 100.0, "new_price": 95.0},    # -5%
            {"number": "D2", "old_price": 100.0, "new_price": 70.0},    # -30%
            {"number": "D3", "old_price": 100.0, "new_price": 85.0},    # -15%
        ])

        _setup_mocks(mock_st)
        _render_result_summary(df)

        dataframe_calls = mock_st.dataframe.call_args_list
        assert len(dataframe_calls) == 1  # only decreases, no increases
        shown_df = dataframe_calls[0][0][0]
        assert len(shown_df) == 3
        # Biggest decrease first (D2 at -30%)
        assert shown_df.iloc[0]["NUMBER"] == "D2"
        assert shown_df.iloc[1]["NUMBER"] == "D3"
        assert shown_df.iloc[2]["NUMBER"] == "D1"

    @patch("ui.pages.price_optimizer.st")
    def test_no_increases_shows_caption(self, mock_st):
        """When there are no increases, a caption should be shown."""
        df = _make_final_df([
            {"number": "X1", "old_price": 100.0, "new_price": 80.0},
            {"number": "X2", "old_price": 100.0, "new_price": 100.0},
        ])

        _setup_mocks(mock_st)
        _render_result_summary(df)

        # Caption for "no increases" is called on mock_st
        caption_calls = [
            c for c in mock_st.caption.call_args_list
            if c[0][0] == "No price increases detected."
        ]
        assert len(caption_calls) == 1

    @patch("ui.pages.price_optimizer.st")
    def test_no_decreases_shows_caption(self, mock_st):
        """When there are no decreases, a caption should be shown."""
        df = _make_final_df([
            {"number": "X1", "old_price": 100.0, "new_price": 120.0},
        ])

        _setup_mocks(mock_st)
        _render_result_summary(df)

        caption_calls = [
            c for c in mock_st.caption.call_args_list
            if c[0][0] == "No price decreases detected."
        ]
        assert len(caption_calls) == 1

    @patch("ui.pages.price_optimizer.st")
    def test_zero_old_price_no_division_error(self, mock_st):
        """A product with zero old price should not cause a division error.

        When old price is 0 the percentage change is treated as 0%.
        The product still counts as *changed* (old != new) and as an
        *increase* (new > old) but contributes 0% to the average.
        """
        df = _make_final_df([
            {"number": "Z1", "old_price": 0.0, "new_price": 100.0},
            {"number": "Z2", "old_price": 100.0, "new_price": 150.0},
        ])

        m1, m2, m3, m4 = _setup_mocks(mock_st)

        # Should not raise
        _render_result_summary(df)

        # Both products differ from old price -> 2 changed
        m2.metric.assert_called_once_with("Changed Products", "2")
        # Z1 counted as increase (0% change), Z2 as increase (50%)
        # avg = (0 + 50) / 2 = 25%
        m3.metric.assert_called_once_with("Avg Increase", "+25.00%")

    @patch("ui.pages.price_optimizer.st")
    def test_top5_includes_title_dk_when_present(self, mock_st):
        """Top 5 tables should include TITLE_DK when the column exists."""
        df = _make_final_df(
            [
                {"number": "T1", "old_price": 100.0, "new_price": 150.0, "title": "Widget A"},
                {"number": "T2", "old_price": 100.0, "new_price": 130.0, "title": "Widget B"},
            ],
            include_title=True,
        )

        _setup_mocks(mock_st)
        _render_result_summary(df)

        dataframe_calls = mock_st.dataframe.call_args_list
        assert len(dataframe_calls) == 1  # only increases
        shown_df = dataframe_calls[0][0][0]
        assert "TITLE_DK" in shown_df.columns
        assert shown_df.iloc[0]["TITLE_DK"] == "Widget A"

    @patch("ui.pages.price_optimizer.st")
    def test_top5_omits_title_dk_when_absent(self, mock_st):
        """Top 5 tables should not contain TITLE_DK when the column is missing."""
        df = _make_final_df([
            {"number": "N1", "old_price": 100.0, "new_price": 150.0},
        ])

        _setup_mocks(mock_st)
        _render_result_summary(df)

        dataframe_calls = mock_st.dataframe.call_args_list
        assert len(dataframe_calls) == 1
        shown_df = dataframe_calls[0][0][0]
        assert "TITLE_DK" not in shown_df.columns
