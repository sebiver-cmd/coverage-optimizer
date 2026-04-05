"""Risk analysis helpers — pure computation, no UI dependency.

Provides functions that identify pricing risk from an optimisation
result set:

- **Largest price decreases** — products whose price dropped the most.
- **Near-cost warnings** — products whose new price is dangerously
  close to (or below) the buy/cost price.
- **Change-distribution histogram** — bin counts of percentage change
  across all products for a quick visual overview.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from domain.pricing import clean_price, VAT_RATE

# ---------------------------------------------------------------------------
# Configurable thresholds
# ---------------------------------------------------------------------------

#: Default margin threshold for "near-cost" warnings.
#: A product is flagged when ``new_price_ex_vat / buy_price - 1`` is
#: below this value (i.e. less than 10 % margin above cost).
NEAR_COST_MARGIN_THRESHOLD = 0.10

#: Number of top decreases to return.
TOP_DECREASES_COUNT = 10

#: Histogram bin edges (percent change).  The last bin captures
#: everything above +30 %.  The first bin captures everything below -30 %.
HISTOGRAM_BIN_EDGES = list(range(-30, 35, 5))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_largest_decreases(
    final_df: pd.DataFrame,
    *,
    top_n: int = TOP_DECREASES_COUNT,
) -> pd.DataFrame:
    """Return the *top_n* products with the largest price decreases.

    Parameters
    ----------
    final_df
        Display DataFrame with Danish-formatted ``PRICE`` and ``NEW_PRICE``
        columns (and optionally ``NUMBER``, ``TITLE_DK``).
    top_n
        How many rows to return.

    Returns
    -------
    pd.DataFrame
        Columns: ``NUMBER``, ``TITLE_DK`` (if present), ``Old Price``,
        ``New Price``, ``Change %``.  Sorted by ascending ``Change %``
        (most negative first).  Empty if no decreases exist.
    """
    if final_df.empty:
        return pd.DataFrame()

    old_prices = final_df["PRICE"].apply(clean_price)
    new_prices = final_df["NEW_PRICE"].apply(clean_price)

    with np.errstate(divide="ignore", invalid="ignore"):
        pct_change = np.where(
            old_prices != 0,
            (new_prices - old_prices) / old_prices * 100,
            0.0,
        )

    decreased_mask = new_prices < old_prices
    if not decreased_mask.any():
        return pd.DataFrame()

    cols: dict = {"NUMBER": final_df["NUMBER"].values}
    if "TITLE_DK" in final_df.columns:
        cols["TITLE_DK"] = final_df["TITLE_DK"].values
    cols["Old Price"] = old_prices.values
    cols["New Price"] = new_prices.values
    cols["Change %"] = np.round(pct_change, 2)

    detail_df = pd.DataFrame(cols)
    return (
        detail_df[decreased_mask.values]
        .nsmallest(top_n, "Change %")
        .reset_index(drop=True)
    )


def compute_near_cost_warnings(
    final_df: pd.DataFrame,
    buy_prices: pd.Series,
    *,
    margin_threshold: float = NEAR_COST_MARGIN_THRESHOLD,
) -> pd.DataFrame:
    """Return products whose new price is dangerously close to cost.

    A product is flagged when the margin
    ``(new_price_ex_vat - buy_price) / new_price_ex_vat`` is below
    *margin_threshold*, **and** ``buy_price > 0``.

    Parameters
    ----------
    final_df
        Display DataFrame with Danish-formatted ``NEW_PRICE`` and
        ``NUMBER`` (and optionally ``TITLE_DK``).
    buy_prices
        Numeric buy-price series aligned with *final_df* rows.
    margin_threshold
        Fraction (e.g. 0.10 = 10 %).

    Returns
    -------
    pd.DataFrame
        Columns: ``NUMBER``, ``TITLE_DK`` (if present), ``New Price``,
        ``Buy Price``, ``Margin %``.  Sorted by ascending margin.
        Empty if no warnings.
    """
    if final_df.empty:
        return pd.DataFrame()

    vat_rate = VAT_RATE
    new_prices = final_df["NEW_PRICE"].apply(clean_price)
    new_prices_ex_vat = new_prices / (1 + vat_rate)

    # Ensure buy_prices is numeric and aligned
    bp = pd.to_numeric(buy_prices, errors="coerce").fillna(0.0)

    # Margin = (price_ex_vat - cost) / price_ex_vat
    with np.errstate(divide="ignore", invalid="ignore"):
        margin = np.where(
            new_prices_ex_vat > 0,
            (new_prices_ex_vat - bp) / new_prices_ex_vat,
            0.0,
        )

    # Flag: positive buy price AND margin below threshold
    flagged = (bp > 0) & (margin < margin_threshold)
    if not flagged.any():
        return pd.DataFrame()

    cols: dict = {"NUMBER": final_df["NUMBER"].values}
    if "TITLE_DK" in final_df.columns:
        cols["TITLE_DK"] = final_df["TITLE_DK"].values
    cols["New Price"] = new_prices.values
    cols["Buy Price"] = bp.values
    cols["Margin %"] = np.round(np.array(margin) * 100, 2)

    detail_df = pd.DataFrame(cols)
    return (
        detail_df[flagged.values]
        .sort_values("Margin %", ascending=True)
        .reset_index(drop=True)
    )


def compute_change_histogram(
    final_df: pd.DataFrame,
    *,
    bin_edges: list[int] | None = None,
) -> tuple[list[str], list[int]]:
    """Compute a histogram of percentage price changes.

    Parameters
    ----------
    final_df
        Display DataFrame with ``PRICE`` and ``NEW_PRICE``.
    bin_edges
        Custom bin edges.  Defaults to ``HISTOGRAM_BIN_EDGES``.

    Returns
    -------
    labels
        Human-readable bin labels (e.g. ``["-30 to -25", "-25 to -20", …]``).
        Includes a ``"< -30"`` and ``"> +30"`` overflow bin when
        products fall outside the default range.
    counts
        Number of products in each bin (aligned with *labels*).
    """
    if bin_edges is None:
        bin_edges = HISTOGRAM_BIN_EDGES

    if final_df.empty:
        return [], []

    old_prices = final_df["PRICE"].apply(clean_price)
    new_prices = final_df["NEW_PRICE"].apply(clean_price)

    with np.errstate(divide="ignore", invalid="ignore"):
        pct_change = np.where(
            old_prices != 0,
            (new_prices - old_prices) / old_prices * 100,
            0.0,
        )

    # Build bins with -inf/+inf overflow buckets
    edges = [-math.inf] + list(bin_edges) + [math.inf]
    counts_arr, _ = np.histogram(pct_change, bins=edges)

    labels: list[str] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if lo == -math.inf:
            labels.append(f"< {int(hi)}")
        elif hi == math.inf:
            labels.append(f"> +{int(lo)}")
        else:
            labels.append(f"{int(lo)} to {int(hi)}")

    return labels, counts_arr.tolist()
