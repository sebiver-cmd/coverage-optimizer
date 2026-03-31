"""Invoice-to-EAN barcode matching — no Streamlit dependency.

Parses an invoice (CSV / PDF), matches its SKUs to the product catalogue
using fuzzy matching, and builds an export DataFrame with:
  SKU · Product Number · Title · Variant Name · Amount · EAN Barcode

The export document is **read-only** — it is never used for API imports.
"""

from __future__ import annotations

import io
import math
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
    manual_overrides: dict[str, str] | None = None,
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
    manual_overrides:
        Optional dict mapping invoice SKUs to product SKUs for manual
        corrections.  Entries here override automatic matches.

    Returns
    -------
    pd.DataFrame
        Export document with columns:
        ``SKU``, ``Product Number``, ``Title``, ``Variant Name``,
        ``Amount``, ``EAN``, ``Match %``.
    """
    mdata = match_invoice_to_products(
        products_df, invoice_df, invoice_sku_col,
        invoice_qty_col, threshold, invoice_desc_col,
    )
    return build_export_from_matches(
        products_df, mdata, manual_overrides=manual_overrides,
    )


def match_invoice_to_products(
    products_df: pd.DataFrame,
    invoice_df: pd.DataFrame,
    invoice_sku_col: str,
    invoice_qty_col: str | None,
    threshold: int = 70,
    invoice_desc_col: str | None = None,
) -> dict:
    """Run invoice-to-product matching and return raw results.

    Returns a dict with keys:
    ``matches`` – the dict from :func:`match_supplier_to_products`,
    ``composite_lookup`` – maps augmented SKU → (number, variant_type),
    ``title_lookup`` – maps product NUMBER → title,
    ``qty_map`` – maps invoice SKU → parsed quantity,
    ``desc_map`` – maps invoice SKU → description text.
    """
    invoice_skus = (
        invoice_df[invoice_sku_col]
        .dropna()
        .astype(str)
        .str.strip()
        .loc[lambda s: s != '']
        .drop_duplicates()
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

    # Build name lookups so match_supplier_to_products can use product
    # name / designation similarity as a secondary reranking signal.
    inv_names: dict[str, str] | None = desc_map if desc_map else None
    prod_names: dict[str, str] | None = title_lookup if title_lookup else None

    matches = match_supplier_to_products(
        invoice_skus.tolist(),
        augmented_skus,
        threshold=threshold,
        supplier_names=inv_names,
        product_names=prod_names,
    )

    return {
        'matches': matches,
        'composite_lookup': composite_lookup,
        'title_lookup': title_lookup,
        'qty_map': qty_map,
        'desc_map': desc_map,
    }


def build_export_from_matches(
    products_df: pd.DataFrame,
    match_data: dict,
    *,
    manual_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Build the EAN export DataFrame from pre-computed match results.

    Parameters
    ----------
    products_df:
        Product catalogue DataFrame.
    match_data:
        Dict returned by :func:`match_invoice_to_products`.
    manual_overrides:
        Optional dict mapping invoice SKUs to augmented product SKUs
        for manual corrections.  Entries here add or override automatic
        matches.

    Returns
    -------
    pd.DataFrame
        Export document with columns:
        ``SKU``, ``Product Number``, ``Title``, ``Variant Name``,
        ``Amount``, ``EAN``, ``Match %``.
    """
    matches = match_data['matches']
    composite_lookup = match_data['composite_lookup']
    qty_map = match_data['qty_map']
    desc_map = match_data['desc_map']

    # Apply manual overrides — they add or replace automatic matches.
    if manual_overrides:
        for inv_sku, prod_key in manual_overrides.items():
            matches[inv_sku] = {
                'sku': prod_key,
                'score': 100,
                'alternatives': [],
            }

    rows: list[dict] = []
    for inv_sku, mentry in matches.items():
        matched_key = mentry['sku']
        score = mentry['score']
        if matched_key is None:
            continue
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
            vt_col = (
                matched_products['VARIANT_TYPES']
                .fillna('').astype(str).str.strip()
            )
            # Exact match
            specific = matched_products.loc[vt_col == vtype]
            if specific.empty:
                # Size-alias match (e.g. "L" ↔ "Large")
                aliases = _size_aliases(vtype)
                if aliases:
                    specific = matched_products.loc[
                        vt_col.str.lower().isin(aliases)
                    ]
            if not specific.empty:
                matched_products = specific
            elif len(matched_products) > 1:
                # vtype didn't match exactly; fall back to narrowing
                inv_desc = desc_map.get(inv_sku, '')
                matched_products = _narrow_variants(
                    matched_products, inv_sku, inv_desc,
                )
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


_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}

# --- Size-abbreviation aliases ---
# Each tuple is a group of equivalent size names (all lower-cased).
# Any member can match any other member in the same group.
# Compound sizes include both hyphenated and concatenated forms
# (e.g. "x-large" and "xlarge") because both appear in the wild.
# Simple sizes like "small" or "large" need no concatenated form.

_SIZE_GROUPS: list[tuple[str, ...]] = [
    ('xxs', 'xx-small', 'xxsmall'),
    ('xs', 'x-small', 'xsmall'),
    ('s', 'small'),
    ('m', 'medium', 'med'),
    ('l', 'large'),
    ('xl', 'x-large', 'xlarge'),
    ('xxl', 'xx-large', 'xxlarge', '2xl'),
    ('xxxl', 'xxx-large', 'xxxlarge', '3xl'),
]

_SIZE_ALIAS_MAP: dict[str, frozenset[str]] = {}
for _grp in _SIZE_GROUPS:
    _fs = frozenset(_grp)
    for _member in _grp:
        _SIZE_ALIAS_MAP[_member] = _fs


def _size_aliases(name: str) -> frozenset[str]:
    """Return the set of equivalent size names for *name*, or empty."""
    return _SIZE_ALIAS_MAP.get(name.lower().strip(), frozenset())


def _boundary_pattern(key: str) -> re.Pattern[str]:
    """Get or compile a word-boundary regex for *key*.

    The key is lower-cased internally — callers need not pre-normalise.
    """
    key = key.lower()
    pat = _BOUNDARY_CACHE.get(key)
    if pat is None:
        pat = re.compile(
            r'(?<![A-Za-z0-9])' + re.escape(key) + r'(?![A-Za-z0-9])'
        )
        _BOUNDARY_CACHE[key] = pat
    return pat


def _variant_in_context(vt: str, context: str) -> bool:
    """Check whether a variant name appears in the context string.

    Uses word-boundary matching so that short names like ``"S"`` or ``"L"``
    do not accidentally match inside unrelated words (e.g. ``"PSW"``).

    Composite variants separated by ``/`` or ``//`` (e.g. ``"rød//Large"``)
    are split and each part is checked independently — returns ``True`` if
    **any** part matches (allowing partial info such as only size or only
    colour to narrow the candidate set).

    Also recognises standard size abbreviations: ``XS`` ↔ ``X-Small``,
    ``S`` ↔ ``Small``, ``M`` ↔ ``Medium``, ``L`` ↔ ``Large``,
    ``XL`` ↔ ``X-Large``, and so on.
    """
    vt = vt.strip()
    if not vt:
        return False

    # Split composite variants on "/" (handles both "/" and "//")
    if '/' in vt:
        parts = [p.strip() for p in vt.split('/') if p.strip()]
        if len(parts) > 1:
            return any(_variant_in_context(part, context) for part in parts)

    key = vt.lower()
    context_lower = context.lower()

    # Direct word-boundary match
    if _boundary_pattern(key).search(context_lower):
        return True

    # Size-alias match: check if any equivalent size name appears
    for alias in _size_aliases(key):
        if alias != key and _boundary_pattern(alias).search(context_lower):
            return True

    return False


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
        if math.isnan(val) or math.isinf(val):
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


# ---------------------------------------------------------------------------
# Barcode PDF generation
# ---------------------------------------------------------------------------

def _render_barcode_image(ean_value: str) -> io.BytesIO | None:
    """Render an EAN barcode as a PNG image in a BytesIO buffer.

    Supports EAN-13 (12–13 digits) and EAN-8 (7–8 digits).
    Returns ``None`` when the value is empty or not a valid EAN.
    """
    try:
        import barcode as barcode_lib
        from barcode.writer import ImageWriter
    except ImportError:
        return None

    digits = re.sub(r'\D', '', str(ean_value).strip())
    if not digits:
        return None

    try:
        if len(digits) <= 8:
            code = barcode_lib.get('ean8', digits, writer=ImageWriter())
        else:
            code = barcode_lib.get('ean13', digits, writer=ImageWriter())
    except Exception:
        return None

    buf = io.BytesIO()
    try:
        code.write(buf, options={
            'write_text': True,
            'module_height': 15.0,
            'module_width': 0.33,
            'font_size': 10,
            'text_distance': 3.0,
            'quiet_zone': 2.0,
        })
    except Exception:
        return None
    buf.seek(0)
    # fpdf2 uses the .name attribute to infer the image format (PNG).
    buf.name = 'barcode.png'
    return buf


def generate_barcode_pdf(export_df: pd.DataFrame) -> bytes:
    """Generate a PDF document with scannable EAN barcodes.

    Each row from *export_df* is rendered as a label containing
    the product information and a scannable barcode image.

    Parameters
    ----------
    export_df:
        DataFrame produced by :func:`build_ean_export`.  Expected
        columns: ``SKU``, ``Product Number``, ``Title``,
        ``Variant Name``, ``Amount``, ``EAN``.

    Returns
    -------
    bytes
        Raw PDF file content.
    """
    from fpdf import FPDF

    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=True, margin=15)

    # Layout constants
    label_w = 90          # mm – label width (2 columns)
    label_h = 55          # mm – label height
    margin_x = 10         # mm – left page margin
    margin_y = 10         # mm – top page margin
    gap_x = 10            # mm – horizontal gap between columns
    gap_y = 5             # mm – vertical gap between rows
    cols = 2
    barcode_w = 50        # mm – rendered barcode image width
    barcode_h = 22        # mm – rendered barcode image height

    rows_per_page = int((297 - 2 * margin_y + gap_y) / (label_h + gap_y))

    def _latin1(text: str) -> str:
        """Sanitise text to Latin-1 for the built-in Helvetica font."""
        return text.encode('latin-1', 'replace').decode('latin-1')

    label_idx = 0
    for _, row in export_df.iterrows():
        # Page management
        if label_idx % (cols * rows_per_page) == 0:
            pdf.add_page()

        pos_on_page = label_idx % (cols * rows_per_page)
        col = pos_on_page % cols
        grid_row = pos_on_page // cols

        x = margin_x + col * (label_w + gap_x)
        y = margin_y + grid_row * (label_h + gap_y)

        # Draw label border
        pdf.set_draw_color(200, 200, 200)
        pdf.rect(x, y, label_w, label_h)

        # Product info text
        text_x = x + 3
        text_y = y + 3

        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_xy(text_x, text_y)
        sku = str(row.get('SKU', ''))
        prod_num = str(row.get('Product Number', ''))
        pdf.cell(label_w - 6, 4, _latin1(f"SKU: {sku}  |  #{prod_num}"), new_x='LMARGIN', new_y='NEXT')

        pdf.set_font('Helvetica', '', 7)
        pdf.set_xy(text_x, text_y + 5)
        title = str(row.get('Title', ''))[:50]
        variant = str(row.get('Variant Name', '') or '')
        if variant:
            title = f"{title} - {variant}"
        pdf.cell(label_w - 6, 3.5, _latin1(title), new_x='LMARGIN', new_y='NEXT')

        amount = row.get('Amount', 1)
        try:
            amount = int(float(amount))
        except (ValueError, TypeError):
            amount = 1
        pdf.set_xy(text_x, text_y + 9.5)
        pdf.set_font('Helvetica', 'B', 7)
        pdf.cell(label_w - 6, 3.5, f"Qty: {amount}", new_x='LMARGIN', new_y='NEXT')

        # Barcode image
        ean_val = str(row.get('EAN', '') or '')
        barcode_buf = _render_barcode_image(ean_val)
        if barcode_buf is not None:
            bc_x = x + (label_w - barcode_w) / 2
            bc_y = text_y + 14
            pdf.image(barcode_buf, x=bc_x, y=bc_y, w=barcode_w, h=barcode_h)
        else:
            # No valid EAN — print the raw value as text
            pdf.set_font('Helvetica', '', 9)
            pdf.set_xy(text_x, text_y + 20)
            display = ean_val if ean_val else '(no EAN)'
            pdf.cell(label_w - 6, 5, display, align='C')

        # EAN number text below barcode
        pdf.set_font('Helvetica', '', 6)
        pdf.set_xy(x, y + label_h - 5)
        pdf.cell(label_w, 4, f"EAN: {ean_val}" if ean_val else '', align='C')

        label_idx += 1

    if label_idx == 0:
        pdf.add_page()
        pdf.set_font('Helvetica', '', 12)
        pdf.cell(0, 10, 'No products with EAN barcodes to display.', align='C')

    return bytes(pdf.output())
