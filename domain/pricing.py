"""Pure pricing computation — no UI dependency.

Contains coverage-rate calculation, price beautification, Danish
number formatting, and the main ``optimize_prices`` pipeline.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# --- Constants ---
VAT_RATE = 0.25  # 25% Danish VAT
MIN_COVERAGE_RATE = 0.50  # Minimum acceptable profit margin (50%)
BEAUTIFY_LAST_DIGIT = 9  # Prices are rounded up to end in this digit
PRICE_EPSILON = 0.001  # Tolerance for floating-point price comparisons
REQUIRED_COLUMNS = [
    'PRODUCT_ID', 'TITLE_DK', 'NUMBER',
    'BUY_PRICE', 'PRICE',
    'VARIANT_ID', 'VARIANT_TYPES',
]
EXPORT_COLUMNS = [
    'PRODUCT_ID', 'TITLE_DK', 'NUMBER',
    'BUY_PRICE', 'PRICE_EX_VAT', 'PRICE', 'COVERAGE_RATE_%',
    'VARIANT_ID', 'VARIANT_TYPES',
    'NEW_PRICE_EX_VAT', 'NEW_PRICE', 'NEW_COVERAGE_RATE_%',
]
IMPORT_COLUMNS_BASE = [
    'PRODUCT_ID', 'TITLE_DK', 'NUMBER',
    'PRICE', 'VARIANT_ID', 'VARIANT_TYPES',
]


def clean_price(price_str):
    """Convert a Danish-formatted price string (e.g. '1.234,56') to a float."""
    if pd.isna(price_str):
        return 0.0
    if isinstance(price_str, str):
        # Guard against "nan"/"NaN" strings that float() would accept
        if price_str.strip().lower() == 'nan':
            return 0.0
        price_str = price_str.replace('.', '').replace(',', '.')
    try:
        val = float(price_str)
        # Reject NaN / Inf that slipped through
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return val
    except (ValueError, TypeError):
        return 0.0


def beautify_price(price, last_digit=BEAUTIFY_LAST_DIGIT):
    """Round *price* up to the nearest integer ending in *last_digit*."""
    if price == 0:
        return 0.0
    target = math.ceil(price)
    remainder = target % 10
    offset = (last_digit - remainder) % 10
    return float(target + offset)


def format_dk(num):
    """Format a number into Danish locale style (e.g. 1.234,56)."""
    return f"{num:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def format_int_col(value):
    """Format a value as an integer string, handling NaN and float-cast integers."""
    if pd.isna(value):
        return ''
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def calc_coverage_rate(df, price_col, buy_col):
    """Return a Series of coverage rates, handling zero-price and zero-cost rows."""
    rate = np.where(
        df[price_col] > 0,
        (df[price_col] - df[buy_col]) / df[price_col],
        0.0,
    )
    rate = pd.Series(rate, index=df.index)
    rate.loc[(df[buy_col] == 0) & (df[price_col] > 0)] = 1.0
    return rate


def api_products_to_dataframe(products: list[dict]) -> pd.DataFrame:
    """Convert a list of API product dicts to a DataFrame matching the CSV format.

    Each product may have variants — those are expanded into separate rows
    (one per variant), mirroring the structure of a CSV export.
    """
    rows: list[dict] = []
    for p in products:
        pid = p.get('Id', '')
        title = p.get('Title', '')
        item_number = p.get('ItemNumber', '')
        price = p.get('Price', 0)
        buy_price = p.get('BuyingPrice', 0)
        ean = str(p.get('Ean') or '').strip()
        # Online status for post-fetch filtering
        _status_raw = p.get('Status')
        _is_online = (
            _status_raw is not False
            and str(_status_raw or '').lower() not in ('false', '0', 'no')
        )
        # Producer is a User object in the API; extract the brand name from it.
        # After serialize_object() it becomes a dict with Company, Firstname, etc.
        _producer_raw = p.get('Producer')
        if isinstance(_producer_raw, dict):
            producer = str(_producer_raw.get('Company', '') or '').strip()
            if not producer:
                _fname = str(_producer_raw.get('Firstname', '') or '').strip()
                _lname = str(_producer_raw.get('Lastname', '') or '').strip()
                producer = ' '.join(filter(None, [_fname, _lname])).strip()
        else:
            producer = str(_producer_raw or '').strip()

        # Preserve ProducerId (int) for reliable brand matching.
        _producer_id_raw = p.get('ProducerId')
        try:
            producer_id = int(_producer_id_raw) if _producer_id_raw is not None else None
        except (ValueError, TypeError):
            producer_id = None
        variants = p.get('Variants') or []
        if isinstance(variants, dict):
            items = variants.get('item', [])
            if not isinstance(items, list):
                items = [items]
        elif isinstance(variants, list):
            items = variants
        else:
            items = []

        variant_types = p.get('VariantTypes') or ''
        if isinstance(variant_types, dict):
            vt_items = variant_types.get('item', [])
            if isinstance(vt_items, list):
                variant_types = ', '.join(
                    str(getattr(vt, 'Title', vt) if hasattr(vt, 'Title') else vt)
                    for vt in vt_items
                )
            else:
                variant_types = str(vt_items)
        elif not isinstance(variant_types, str):
            variant_types = str(variant_types)

        if items:
            for v in items:
                vid = v.get('Id', '') if isinstance(v, dict) else getattr(v, 'Id', '')
                vprice = v.get('Price', price) if isinstance(v, dict) else getattr(v, 'Price', price)
                vbuy = v.get('BuyingPrice', buy_price) if isinstance(v, dict) else getattr(v, 'BuyingPrice', buy_price)
                vean_raw = v.get('Ean', '') if isinstance(v, dict) else getattr(v, 'Ean', '')
                vean = str(vean_raw).strip() if vean_raw else ean
                # Prefer the variant's own Title (e.g. "190 cm", "Red / L")
                # over the product-level VariantTypes (e.g. "Size, Color").
                vtitle_raw = v.get('Title', '') if isinstance(v, dict) else getattr(v, 'Title', '')
                vtitle = str(vtitle_raw or '').strip()
                # Extract the variant's own ItemNumber (variant SKU).
                vitemnumber_raw = v.get('ItemNumber', '') if isinstance(v, dict) else getattr(v, 'ItemNumber', '')
                vitemnumber = str(vitemnumber_raw or '').strip()
                rows.append({
                    'PRODUCT_ID': format_int_col(pid),
                    'TITLE_DK': title,
                    'NUMBER': item_number,
                    'BUY_PRICE': format_dk(float(vbuy or 0)),
                    'PRICE': format_dk(float(vprice or 0)),
                    'VARIANT_ID': format_int_col(vid),
                    'VARIANT_TYPES': vtitle if vtitle else variant_types,
                    'VARIANT_ITEMNUMBER': vitemnumber,
                    'PRODUCER': producer,
                    'PRODUCER_ID': producer_id,
                    'ONLINE': _is_online,
                    'EAN': vean,
                })
        else:
            rows.append({
                'PRODUCT_ID': format_int_col(pid),
                'TITLE_DK': title,
                'NUMBER': item_number,
                'BUY_PRICE': format_dk(float(buy_price or 0)),
                'PRICE': format_dk(float(price or 0)),
                'VARIANT_ID': '',
                'VARIANT_TYPES': '',
                'VARIANT_ITEMNUMBER': '',
                'PRODUCER': producer,
                'PRODUCER_ID': producer_id,
                'ONLINE': _is_online,
                'EAN': ean,
            })

    if not rows:
        raise ValueError("No products found in the API response.")

    df = pd.DataFrame(rows)

    # Create numeric helper columns
    df['BUY_PRICE_NUM'] = df['BUY_PRICE'].apply(clean_price)
    df['PRICE_NUM'] = df['PRICE'].apply(clean_price)

    for col in ('VARIANT_ID', 'PRODUCT_ID'):
        if col in df.columns:
            df[col] = df[col].apply(format_int_col)

    return df


def _build_brand_id_map(raw_products: list[dict]) -> dict[int, str]:
    """Build a ProducerId → brand-name mapping from raw API product dicts.

    Used to enable targeted ``Product_GetByBrand`` calls on subsequent
    fetches and to populate the brand filter dropdown with integer IDs.
    """
    brand_id_map: dict[int, str] = {}
    for p in raw_products:
        _pr = p.get("Producer")
        if isinstance(_pr, dict):
            _name = str(_pr.get("Company", "") or "").strip()
        else:
            _name = str(_pr or "").strip()
        _pid = p.get("ProducerId")
        if _name and _pid:
            try:
                brand_id_map[int(_pid)] = _name
            except (ValueError, TypeError):
                pass
    return brand_id_map


def optimize_prices(
    df: pd.DataFrame,
    price_pct: float = 0.0,
    original_buy_prices: "pd.Series | None" = None,
    beautify_digit: int = BEAUTIFY_LAST_DIGIT,
) -> tuple:
    """Compute optimised prices following the pipeline:

    Current → Proposed → Beautified → Rules-adjusted → Final New Price.

    Parameters
    ----------
    price_pct : float
        Percentage adjustment applied to the **New Sales Price**
        (after coverage-based adjustment and beautification),
        not the current price.  E.g. 10.0 = +10 %.
    original_buy_prices : pd.Series, optional
        Original ``BUY_PRICE_NUM`` values before per-line editing.
        When supplied, products whose BUY_PRICE was changed are
        included in the import / push set even when their coverage
        rate already meets the minimum threshold.
    beautify_digit : int
        Desired ending digit for beautified prices (9, 0, or 5).
    """
    df = df.copy()

    # Detect manual BUY_PRICE edits
    if original_buy_prices is not None:
        buy_price_changed = df['BUY_PRICE_NUM'] != original_buy_prices
    else:
        buy_price_changed = pd.Series(False, index=df.index)

    # Reformat BUY_PRICE from (possibly per-line edited) numeric values
    df['BUY_PRICE'] = df['BUY_PRICE_NUM'].apply(format_dk)

    # Step 1 — Current prices (no price_pct applied here)
    df['PRICE_EX_VAT_NUM'] = df['PRICE_NUM'] / (1 + VAT_RATE)

    # Step 2 — Current coverage on original prices
    df['COVERAGE_RATE'] = calc_coverage_rate(df, 'PRICE_EX_VAT_NUM', 'BUY_PRICE_NUM')

    # Identify items needing adjustment
    needs_adjustment = (df['COVERAGE_RATE'] < MIN_COVERAGE_RATE) & (df['BUY_PRICE_NUM'] > 0)

    # Step 3 — Proposed new ex-VAT price (minimum-margin based)
    df['NEW_PRICE_EX_VAT_NUM'] = df['PRICE_EX_VAT_NUM']
    df.loc[needs_adjustment, 'NEW_PRICE_EX_VAT_NUM'] = (
        df.loc[needs_adjustment, 'BUY_PRICE_NUM'] / MIN_COVERAGE_RATE
    )

    # Calculate inc-VAT proposed price
    df['NEW_PRICE_NUM'] = df['NEW_PRICE_EX_VAT_NUM'] * (1 + VAT_RATE)

    # Step 4 — Beautify (only adjusted products)
    df['FINAL_PRICE_NUM'] = df['PRICE_NUM']
    df.loc[needs_adjustment, 'FINAL_PRICE_NUM'] = (
        df.loc[needs_adjustment, 'NEW_PRICE_NUM'].apply(
            lambda p: beautify_price(p, beautify_digit)
        )
    )

    # Step 5 — Apply Price Rules (price_pct) to all new prices
    if price_pct != 0:
        df['FINAL_PRICE_NUM'] = df['FINAL_PRICE_NUM'] * (1 + price_pct / 100)

    # Recalculate final metrics
    df['FINAL_PRICE_EX_VAT'] = df['FINAL_PRICE_NUM'] / (1 + VAT_RATE)
    df['FINAL_COVERAGE_RATE'] = calc_coverage_rate(df, 'FINAL_PRICE_EX_VAT', 'BUY_PRICE_NUM')

    # Format original price columns
    df['PRICE_EX_VAT'] = df['PRICE_EX_VAT_NUM'].apply(format_dk)
    df['COVERAGE_RATE_%'] = (
        (df['COVERAGE_RATE'] * 100).round(2).astype(str).str.replace('.', ',', regex=False) + '%'
    )

    # Format new/adjusted price columns (placed at the end)
    df['NEW_PRICE'] = df['FINAL_PRICE_NUM'].apply(format_dk)
    df['NEW_PRICE_EX_VAT'] = df['FINAL_PRICE_EX_VAT'].apply(format_dk)
    df['NEW_COVERAGE_RATE_%'] = (
        (df['FINAL_COVERAGE_RATE'] * 100).round(2).astype(str).str.replace('.', ',', regex=False) + '%'
    )

    # Determine the full set of products for import / push:
    # products needing a price adjustment + products with manually changed BUY_PRICE
    # + products whose final price differs from current (e.g. due to price_pct)
    in_import_set = needs_adjustment.copy()
    in_import_set = in_import_set | buy_price_changed
    if price_pct != 0:
        price_changed_by_rules = (
            abs(df['FINAL_PRICE_NUM'] - df['PRICE_NUM']) > PRICE_EPSILON
        )
        in_import_set = in_import_set | price_changed_by_rules

    # Build import-ready DataFrame
    import_df = df.loc[in_import_set, REQUIRED_COLUMNS].copy()
    import_df['PRICE'] = df.loc[in_import_set, 'FINAL_PRICE_NUM'].apply(format_dk)

    return df[EXPORT_COLUMNS], int(in_import_set.sum()), in_import_set.values, import_df
