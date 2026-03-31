"""Invoice-to-EAN barcode matching — no Streamlit dependency.

Parses an invoice (CSV / PDF), matches its SKUs to the product catalogue
using fuzzy matching, and builds an export DataFrame with:
  SKU · Product Number · Title · Variant Name · Amount · EAN Barcode

The export document is **read-only** — it is never used for API imports.
"""

from __future__ import annotations

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

    # Build an augmented product-SKU pool that includes composite keys
    # (NUMBER + VARIANT_TYPES) so that invoice SKUs like "TBL-01 190 cm"
    # can match the specific "190 cm" variant directly.
    composite_lookup: dict[str, tuple[str, str]] = {}
    augmented_skus: list[str] = []

    for _, row in products_df.iterrows():
        num = str(row.get('NUMBER', '') or '').strip()
        vtype = str(row.get('VARIANT_TYPES', '') or '').strip()
        if not num:
            continue
        if num not in composite_lookup:
            composite_lookup[num] = (num, '')
            augmented_skus.append(num)
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
        # Find invoice row for quantity
        inv_mask = (
            invoice_df[invoice_sku_col].astype(str).str.strip() == inv_sku
        )
        if invoice_qty_col and invoice_qty_col in invoice_df.columns:
            qty_raw = invoice_df.loc[inv_mask, invoice_qty_col]
            if not qty_raw.empty:
                try:
                    qty_val = float(
                        str(qty_raw.iloc[0])
                        .replace(',', '.')
                        .replace(' ', '')
                        .strip()
                    )
                except (ValueError, TypeError):
                    qty_val = 1.0
            else:
                qty_val = 1.0
        else:
            qty_val = 1.0

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
            # Fall back to description-based narrowing
            matched_products = _narrow_variants(
                matched_products, inv_sku, inv_mask,
                invoice_df, invoice_desc_col,
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


def _narrow_variants(
    matched_products: pd.DataFrame,
    inv_sku: str,
    inv_mask: pd.Series,
    invoice_df: pd.DataFrame,
    invoice_desc_col: str | None,
) -> pd.DataFrame:
    """Try to narrow multiple variant rows to the specific variant.

    Builds a context string from the invoice SKU text and the optional
    description column, then checks which variant names appear in it.
    If exactly one (or a smaller subset) matches, returns only those rows.
    Otherwise returns all rows unchanged (safe fallback).
    """
    # Build a combined context string from the invoice line
    context = inv_sku.lower()
    if invoice_desc_col and invoice_desc_col in invoice_df.columns:
        desc_vals = invoice_df.loc[inv_mask, invoice_desc_col]
        if not desc_vals.empty:
            desc_text = str(desc_vals.iloc[0] or '').strip()
            if desc_text:
                context = f"{context} {desc_text.lower()}"

    # Check each variant name against the context
    variant_types = matched_products['VARIANT_TYPES'].fillna('').astype(str)
    has_variant = variant_types.str.strip().ne('')
    if not has_variant.any():
        return matched_products

    hit_mask = variant_types.apply(
        lambda vt: bool(vt.strip()) and vt.strip().lower() in context
    )
    if hit_mask.any() and hit_mask.sum() < len(matched_products):
        return matched_products.loc[hit_mask]
    return matched_products
