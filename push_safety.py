"""Push-to-shop safety gates.

Enforces two hard constraints before any data is sent to the
HostedShop API:

1. **Explicit selection** — only products the user has ticked in the
   UI are included.
2. **Computed diff** — only products with an *actual* field change
   (sales price or buying price) are included; unchanged products
   are always blocked.

The module is intentionally free of Streamlit imports so that its
logic can be unit-tested without mocking the UI framework.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

# Tolerance for floating-point price comparisons
PRICE_EPSILON = 0.001


def _parse_price(val: Any) -> float:
    """Parse a Danish-formatted price value to a float.

    Handles:
    - ``None`` / NaN / Inf → ``0.0``
    - Numeric types → ``float(val)``
    - Strings like ``"1.234,56"`` (Danish thousands-separator + comma
      decimal)
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        v = float(val)
        return 0.0 if (math.isnan(v) or math.isinf(v)) else v
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return 0.0
    # Danish format: dot as thousands separator, comma as decimal
    s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return 0.0 if (math.isnan(v) or math.isinf(v)) else v
    except (ValueError, TypeError):
        return 0.0


def build_push_updates(
    final_df: pd.DataFrame,
    adjusted_mask: "np.ndarray",
    parsed_buy_prices: "pd.Series",
    work_buy_prices: "pd.Series",
    selected_indices: "set[int] | None" = None,
) -> list[dict]:
    """Build update dicts for push-to-shop from adjusted products.

    Safety gates
    ------------
    1. Only products whose DataFrame index is in *selected_indices* are
       included (when the set is provided).  This enforces explicit user
       selection.
    2. Products with no actual change (same sales price **and** same buy
       price compared to the current shop state) are excluded even when
       they appear in the adjusted set.

    Each returned dict contains only the fields that actually changed
    (``new_price`` and/or ``buy_price``), plus identifiers.  Extra keys
    (``title``, ``old_price``, ``old_buy_price``, ``endpoint``) are
    included for display / audit purposes and are ignored by the API
    client.

    Parameters
    ----------
    final_df : pd.DataFrame
        Output of :func:`optimize_prices` — contains both original
        and new formatted price columns.
    adjusted_mask : np.ndarray
        Boolean mask of the same length as *final_df* indicating
        which rows were marked for adjustment.
    parsed_buy_prices : pd.Series
        Original buying prices (numeric) *before* any user edits.
    work_buy_prices : pd.Series
        Current buying prices (numeric) *after* user edits.
    selected_indices : set[int] | None
        DataFrame indices of products explicitly selected by the
        user.  When ``None``, **all** adjusted products pass gate 1.

    Returns
    -------
    list[dict]
        Update dicts compatible with
        :meth:`DanDomainClient.update_prices_batch`.
    """
    bp_changed = parsed_buy_prices != work_buy_prices
    adjusted_full = final_df[adjusted_mask]
    updates: list[dict] = []

    for idx, row in adjusted_full.iterrows():
        # Gate 1: explicit user selection
        if selected_indices is not None and idx not in selected_indices:
            continue

        pid_raw = row.get("PRODUCT_ID", "")
        pid = "" if pd.isna(pid_raw) else str(pid_raw).strip()
        pnum = str(row.get("NUMBER", "")).strip()
        title = str(row.get("TITLE_DK", "")).strip()

        new_price_val = _parse_price(row.get("NEW_PRICE", "0"))
        orig_price_val = _parse_price(row.get("PRICE", "0"))

        vid_raw = row.get("VARIANT_ID", "")
        if pd.isna(vid_raw) or vid_raw == "":
            vid = ""
        else:
            vid = str(vid_raw).strip()
            try:
                n = float(vid)
                vid = "" if n == 0 else str(int(n))
            except (ValueError, OverflowError):
                pass

        vtypes_raw = row.get("VARIANT_TYPES", "")
        vtypes = "" if pd.isna(vtypes_raw) else str(vtypes_raw).strip()

        # Gate 2: actual change detection
        sales_price_changed = abs(new_price_val - orig_price_val) > PRICE_EPSILON
        has_buy_change = (
            bool(bp_changed.loc[idx]) if idx in bp_changed.index else False
        )

        if not pnum or not (sales_price_changed or has_buy_change):
            continue

        entry: dict[str, Any] = {
            "product_id": pid,
            "product_number": pnum,
            "title": title,
            "variant_id": vid,
            "variant_types": vtypes,
        }

        # Only include fields that actually changed
        if sales_price_changed and new_price_val > 0:
            entry["new_price"] = new_price_val
            entry["old_price"] = orig_price_val

        if has_buy_change:
            try:
                entry["buy_price"] = round(float(work_buy_prices.loc[idx]), 2)
            except (KeyError, ValueError, TypeError):
                entry["buy_price"] = _parse_price(row.get("BUY_PRICE", ""))
            try:
                entry["old_buy_price"] = round(
                    float(parsed_buy_prices.loc[idx]), 2,
                )
            except (KeyError, ValueError, TypeError):
                pass

        # Audit: which endpoint will be called
        entry["endpoint"] = (
            "Product_UpdateVariant" if vid else "Product_Update"
        )

        updates.append(entry)

    return updates
