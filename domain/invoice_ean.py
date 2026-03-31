"""Invoice-to-EAN barcode matching — no Streamlit dependency.

Parses an invoice (CSV / PDF), matches its SKUs to the product catalogue
using fuzzy matching, and builds an export DataFrame with:
  SKU · Product Number · Title · Variant Name · Amount · EAN Barcode

The export document is **read-only** — it is never used for API imports.
"""

from __future__ import annotations

import re

import pandas as pd

from domain.supplier import (
    match_supplier_to_products,
    parse_supplier_file,
    detect_supplier_columns,
)


# --- Quantity / amount column auto-detection ---

_QTY_NAMES = [
    'quantity', 'qty', 'antal', 'anzahl', 'amount', 'count',
    'mængde', 'stk', 'pcs', 'units', 'beløb', 'menge',
]


def detect_invoice_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Auto-detect SKU, quantity, and description columns in an invoice.

    Returns ``{'sku': ..., 'qty': ..., 'description': ...}`` where each
    value is the original column name or ``None``.
    """
    base = detect_supplier_columns(df)
    lower_map = {c.lower().strip(): c for c in df.columns}

    qty_col = None
    for pat in _QTY_NAMES:
        if pat in lower_map:
            qty_col = lower_map[pat]
            break
    if qty_col is None:
        for pat in _QTY_NAMES:
            for lc, orig in lower_map.items():
                if pat in lc:
                    qty_col = orig
                    break
            if qty_col is not None:
                break

    return {
        'sku': base['sku'],
        'qty': qty_col,
        'description': base['description'],
    }


def build_ean_export(
    products_df: pd.DataFrame,
    invoice_df: pd.DataFrame,
    invoice_sku_col: str,
    invoice_qty_col: str | None,
    threshold: int = 70,
    invoice_desc_col: str | None = None,
) -> pd.DataFrame:
    """Match invoice lines to products and build a scannable EAN export.

    Parameters
    ----------
    products_df:
        Product catalogue DataFrame (must contain ``NUMBER``, ``TITLE_DK``,
        ``VARIANT_ID``, ``VARIANT_TYPES``, and ``EAN`` columns).
    invoice_df:
        Parsed invoice DataFrame.
    invoice_sku_col:
        Column in *invoice_df* holding the SKU / article number.
    invoice_qty_col:
        Column in *invoice_df* holding the quantity / amount, or ``None``
        if not available (defaults to 1).
    threshold:
        Minimum fuzzy-match score (0–100).
    invoice_desc_col:
        Optional column in *invoice_df* holding a description or product
        name.  Used together with the SKU text to narrow variant matches
        (e.g. ``"190 cm"`` or ``"Red / Large"``).

    Returns
    -------
    pd.DataFrame
        Export document with columns:
        ``SKU``, ``Product Number``, ``Title``, ``Variant Name``,
        ``Amount``, ``EAN``, ``Match %``.
    """
    invoice_skus = (
        invoice_df[invoice_sku_col]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s != '']
    )

    # Pre-compute a per-row SKU → quantity mapping so that we look up
    # the amount by the exact same cleaned key used for matching, avoiding
    # any type/whitespace mismatch with a second boolean-mask lookup later.
    qty_map: dict[str, float] = {}
    if invoice_qty_col and invoice_qty_col in invoice_df.columns:
        for idx in invoice_df.index:
            raw_sku = invoice_df.at[idx, invoice_sku_col]
            if pd.isna(raw_sku):
                continue
            clean_sku = str(raw_sku).strip()
            if not clean_sku or clean_sku in qty_map:
                continue
            raw_qty = invoice_df.at[idx, invoice_qty_col]
            qty_map[clean_sku] = _parse_qty(raw_qty)

    # Pre-compute a per-row SKU → description mapping.
    desc_map: dict[str, str] = {}
    if invoice_desc_col and invoice_desc_col in invoice_df.columns:
        for idx in invoice_df.index:
            raw_sku = invoice_df.at[idx, invoice_sku_col]
            if pd.isna(raw_sku):
                continue
            clean_sku = str(raw_sku).strip()
            if not clean_sku or clean_sku in desc_map:
                continue
            raw_desc = invoice_df.at[idx, invoice_desc_col]
            desc_map[clean_sku] = str(raw_desc or '').strip()

    # Build an augmented product-SKU pool that includes composite keys
    # (NUMBER + VARIANT_TYPES) so that invoice SKUs like "TBL-01 190 cm"
    # can match the specific "190 cm" variant directly.
    # Also include NUMBER + TITLE_DK composites so that product names
    # strengthen variant disambiguation.
    composite_lookup: dict[str, tuple[str, str]] = {}
    augmented_skus: list[str] = []
    title_lookup: dict[str, str] = {}

    for _, row in products_df.iterrows():
        num = str(row.get('NUMBER', '') or '').strip()
        vtype = str(row.get('VARIANT_TYPES', '') or '').strip()
        title = str(row.get('TITLE_DK', '') or '').strip()
        if not num:
            continue
        if num not in composite_lookup:
            composite_lookup[num] = (num, '')
            augmented_skus.append(num)
            if title:
                title_lookup[num] = title
        if vtype:
            composite = f"{num} {vtype}"
            if composite not in composite_lookup:
                composite_lookup[composite] = (num, vtype)
                augmented_skus.append(composite)

    matches = match_supplier_to_products(
        invoice_skus.tolist(),
        augmented_skus,
        threshold=threshold,
    )

    rows: list[dict] = []
    for inv_sku, (matched_key, score) in matches.items():
        qty_val = qty_map.get(inv_sku, 1.0)

        # Resolve the matched key to a product NUMBER and optional variant
        number, vtype = composite_lookup.get(matched_key, (matched_key, ''))

        prod_mask = (
            products_df['NUMBER'].astype(str).str.strip() == number
        )
        matched_products = products_df.loc[prod_mask]

        if matched_products.empty:
            continue

        # If the match resolved to a specific variant, narrow to that
        if vtype:
            vt_mask = (
                matched_products['VARIANT_TYPES']
                .fillna('').astype(str).str.strip() == vtype
            )
            specific = matched_products.loc[vt_mask]
            if not specific.empty:
                matched_products = specific
        elif len(matched_products) > 1:
            # Fall back to description-based narrowing using SKU text,
            # the optional description column, and product titles.
            inv_desc = desc_map.get(inv_sku, '')
            matched_products = _narrow_variants(
                matched_products, inv_sku, inv_desc,
            )

        for _, prod_row in matched_products.iterrows():
            rows.append({
                'SKU': inv_sku,
                'Product Number': prod_row.get('NUMBER', ''),
                'Title': prod_row.get('TITLE_DK', ''),
                'Variant Name': prod_row.get('VARIANT_TYPES', ''),
                'Amount': qty_val,
                'EAN': prod_row.get('EAN', ''),
                'Match %': score,
            })

    if not rows:
        return pd.DataFrame(
            columns=[
                'SKU', 'Product Number', 'Title', 'Variant Name',
                'Amount', 'EAN', 'Match %',
            ]
        )

    return pd.DataFrame(rows)


def _variant_in_context(vt: str, context: str) -> bool:
    """Check whether a variant name appears in the context string.

    Uses word-boundary matching so that short names like ``"S"`` or ``"L"``
    do not accidentally match inside unrelated words (e.g. ``"PSW"``).
    """
    vt = vt.strip()
    if not vt:
        return False
    pattern = r'(?<![A-Za-z0-9])' + re.escape(vt.lower()) + r'(?![A-Za-z0-9])'
    return bool(re.search(pattern, context.lower()))


def _parse_qty(raw: object) -> float:
    """Extract a numeric quantity from a raw cell value.

    Handles Danish/European decimal comma (``2,5`` → ``2.5``), thousand
    separators, and trailing unit suffixes (``5 stk``, ``10 pcs``).
    Returns ``1.0`` when the value cannot be interpreted.
    """
    text = str(raw).strip()
    if not text:
        return 1.0
    # Replace comma decimal, collapse spaces
    text = text.replace(',', '.').replace(' ', '')
    try:
        val = float(text)
        # Guard against NaN / Inf that float() silently accepts
        if val != val or val == float('inf') or val == float('-inf'):
            return 1.0
        return val
    except (ValueError, TypeError):
        pass
    # Try extracting the leading number from mixed strings like "5stk"
    m = re.match(r'^[+-]?\d+(?:\.\d+)?', text)
    if m:
        try:
            return float(m.group())
        except (ValueError, TypeError):
            pass
    return 1.0


def _narrow_variants(
    matched_products: pd.DataFrame,
    inv_sku: str,
    inv_desc: str,
) -> pd.DataFrame:
    """Try to narrow multiple variant rows to the specific variant.

    Builds a context string from the invoice SKU text and the description,
    then checks which variant names appear in it.  If exactly one (or a
    smaller subset) matches, returns only those rows.  Otherwise returns
    all rows unchanged (safe fallback).
    """
    # Build a combined context string from the invoice line
    context = inv_sku.lower()
    if inv_desc:
        context = f"{context} {inv_desc.lower()}"

    # Check each variant name against the context
    variant_types = matched_products['VARIANT_TYPES'].fillna('').astype(str)
    has_variant = variant_types.str.strip().ne('')
    if not has_variant.any():
        return matched_products

    hit_mask = variant_types.apply(
        lambda vt: _variant_in_context(vt, context)
    )
    if hit_mask.any() and hit_mask.sum() < len(matched_products):
        return matched_products.loc[hit_mask]
    return matched_products
