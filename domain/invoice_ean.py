"""Invoice-to-EAN barcode matching — no Streamlit dependency.

Parses an invoice (CSV / PDF), matches its SKUs to the product catalogue
using fuzzy matching, and builds an export DataFrame with:
  SKU · Product Number · Title · Variant Name · Amount · EAN Barcode

This module is the **single source of truth** for all SKU matching logic.
Both Supplier price-list matching and Invoice/EAN matching use the same
functions defined here.

The export document is **read-only** — it is never used for API imports.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import re
from typing import Callable

import pandas as pd
from rapidfuzz import fuzz, process as rfprocess

from domain.supplier import (
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


# ---------------------------------------------------------------------------
# SKU normalisation & fuzzy matching (single source of truth)
# ---------------------------------------------------------------------------

def normalize_sku(sku: str) -> str:
    """Normalise a SKU for fuzzy comparison.

    Strips common prefixes (e.g. ``TO-``, ``DK-``), removes all
    separators (``-``, ``_``, space, ``.``, ``/``, ``,``) and uppercases
    the result so that ``"AFK 110"`` and ``"TO-AFK-110"`` both become
    ``"AFK110"``.  Handles tricky strings like ``" GTBL 4,5"``
    (leading spaces and European decimal commas).
    """
    s = str(sku).upper().strip()
    s = re.sub(r'^[A-Z]{1,3}[-_]', '', s)
    s = re.sub(r'[-_\s./,]', '', s)
    return s


# Regex to extract an embedded SKU/model number from a description string.
# Targets patterns like "adidas Box-Top schwarz/weiß, ADIBTT02" where
# the SKU appears after the last comma.  The SKU must start with one or
# more letters, contain at least one digit, and end with alphanumerics
# — this distinguishes model codes from ordinary trailing words.
_EMBEDDED_SKU_RE = re.compile(
    r',\s*([A-Za-z]+[A-Za-z0-9]*\d[A-Za-z0-9]*)\s*$'
)


def extract_sku_from_description(description: str) -> str | None:
    """Extract an embedded SKU/model number from a description string.

    Targets German-style invoice descriptions where the manufacturer's
    model code appears after the last comma, e.g.::

        "adidas Box-Top schwarz/weiß, ADIBTT02" → "ADIBTT02"
        "adidas World Boxing Boxhandschuhe Leder, adiWOBG1" → "adiWOBG1"

    Returns ``None`` when no embedded SKU is found.
    """
    if not description:
        return None
    m = _EMBEDDED_SKU_RE.search(description)
    return m.group(1) if m else None


def match_supplier_to_products(
    supplier_skus, product_skus, threshold=65, *,
    supplier_names=None, product_names=None, top_n=5,
):
    """Fuzzy-match supplier SKUs to product SKUs.

    When *supplier_names* (supplier SKU → designation / description)
    and *product_names* (product SKU → product title) are provided,
    description similarity is used as a secondary signal to choose the
    absolute closest match from all candidates.

    Returns ``{supplier_sku: {'sku': best_product_sku | None,
    'score': int, 'alternatives': [(product_sku, score), …]}}``.
    ``sku`` is ``None`` when no match exceeds *threshold*.
    """
    norm_to_orig: dict[str, str] = {}
    for sku in product_skus:
        norm = normalize_sku(sku)
        if norm and norm not in norm_to_orig:
            norm_to_orig[norm] = sku

    norm_list = list(norm_to_orig.keys())

    _prod_names: dict[str, str] = {}
    if product_names:
        for psku, pname in product_names.items():
            _prod_names[psku] = str(pname).strip()

    matches: dict[str, dict] = {}

    for sup_sku in supplier_skus:
        norm_sup = normalize_sku(sup_sku)
        if not norm_sup:
            continue

        sup_name = ''
        if supplier_names and sup_sku in supplier_names:
            sup_name = str(supplier_names[sup_sku]).strip()

        # Exact normalised-SKU match
        if norm_sup in norm_to_orig:
            matches[sup_sku] = {
                'sku': norm_to_orig[norm_sup],
                'score': 100,
                'alternatives': [],
            }
            continue

        # Fuzzy SKU match — read through ALL candidates above cutoff
        results = rfprocess.extract(
            norm_sup, norm_list, scorer=fuzz.ratio,
            score_cutoff=threshold, limit=None,
        )

        if results:
            ranked = _rank_candidates(
                results, norm_to_orig, sup_name, _prod_names,
            )
            best_sku, best_score = ranked[0]
            matches[sup_sku] = {
                'sku': best_sku,
                'score': best_score,
                'alternatives': ranked[1:top_n + 1],
            }
            continue

        # No match above threshold — provide suggestions
        score_map: dict[str, int] = {}

        sku_suggestions = rfprocess.extract(
            norm_sup, norm_list, scorer=fuzz.ratio,
            score_cutoff=30, limit=top_n,
        )
        if sku_suggestions:
            for psku, sc in _rank_candidates(
                sku_suggestions, norm_to_orig, sup_name, _prod_names,
            ):
                if psku not in score_map or sc > score_map[psku]:
                    score_map[psku] = sc

        if sup_name and _prod_names:
            for psku, nscore in _name_based_candidates(
                sup_name, _prod_names, top_n,
            ):
                if psku not in score_map or nscore > score_map[psku]:
                    score_map[psku] = nscore

        ranked = sorted(
            score_map.items(), key=lambda x: x[1], reverse=True,
        )[:top_n]

        matches[sup_sku] = {
            'sku': None,
            'score': 0,
            'alternatives': ranked,
        }

    return matches


def _rank_candidates(results, norm_to_orig, sup_name, prod_names):
    """Re-rank fuzzy-match candidates using SKU + name similarity.

    Uses an adaptive weighting strategy: when the SKU score is weak
    (< 80) and a strong name match exists, name similarity gets a
    higher weight (50 %) so that designation / product-title information
    can rescue otherwise borderline SKU matches.
    """
    candidates: list[tuple[str, int]] = []
    for match_norm, sku_score, _ in results:
        orig_sku = norm_to_orig[match_norm]
        combined = int(sku_score)
        if sup_name and orig_sku in prod_names:
            name_score = max(
                fuzz.token_set_ratio(
                    sup_name.upper(), prod_names[orig_sku].upper(),
                ),
                fuzz.token_sort_ratio(
                    sup_name.upper(), prod_names[orig_sku].upper(),
                ),
            )
            # Adaptive weighting: lean on name similarity more when
            # the SKU match is weak.
            if sku_score < 80 and name_score > sku_score:
                combined = int(0.5 * sku_score + 0.5 * name_score)
            else:
                combined = int(0.7 * sku_score + 0.3 * name_score)
        candidates.append((orig_sku, combined))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates


def _name_based_candidates(sup_name, prod_names, top_n=5):
    """Find product candidates based on name / designation similarity.

    Uses the best of ``token_set_ratio`` and ``token_sort_ratio`` to
    handle reordered words and partial overlaps robustly.
    """
    if not sup_name or not prod_names:
        return []

    name_to_skus: dict[str, list[str]] = {}
    for psku, pname in prod_names.items():
        upper = pname.upper().strip()
        if upper:
            name_to_skus.setdefault(upper, []).append(psku)

    if not name_to_skus:
        return []

    name_list = list(name_to_skus.keys())
    sup_upper = sup_name.upper().strip()

    # Use token_set_ratio (good for subset matches) and also try
    # token_sort_ratio (good for reordered words), keep the best.
    results_set = rfprocess.extract(
        sup_upper, name_list,
        scorer=fuzz.token_set_ratio, limit=top_n,
    )
    results_sort = rfprocess.extract(
        sup_upper, name_list,
        scorer=fuzz.token_sort_ratio, limit=top_n,
    )

    # Merge both result sets, keeping the higher score per name.
    best: dict[str, int] = {}
    for matched_name, score, _ in (results_set or []) + (results_sort or []):
        if matched_name not in best or score > best[matched_name]:
            best[matched_name] = int(score)

    candidates: list[tuple[str, int]] = []
    for matched_name, score in best.items():
        for psku in name_to_skus[matched_name]:
            candidates.append((psku, score))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:top_n]


def search_products(
    query: str,
    product_skus: list[str],
    product_names: dict[str, str] | None = None,
    top_n: int = 10,
) -> list[tuple[str, int]]:
    """Search product catalogue by SKU or name for manual matching.

    Returns up to *top_n* results as ``[(product_sku, score), …]``,
    sorted by descending relevance.  Designed for the "search for
    correct product" UI in the suggested-matchings panel.
    """
    if not query or not product_skus:
        return []

    query_upper = query.upper().strip()
    score_map: dict[str, int] = {}

    # SKU-based search
    norm_to_orig: dict[str, str] = {}
    for sku in product_skus:
        norm = normalize_sku(sku)
        if norm and norm not in norm_to_orig:
            norm_to_orig[norm] = sku
    norm_list = list(norm_to_orig.keys())

    sku_results = rfprocess.extract(
        normalize_sku(query), norm_list,
        scorer=fuzz.ratio, limit=top_n,
    )
    for match_norm, score, _ in (sku_results or []):
        orig = norm_to_orig[match_norm]
        if orig not in score_map or score > score_map[orig]:
            score_map[orig] = int(score)

    # Name-based search
    if product_names:
        name_candidates = _name_based_candidates(
            query, product_names, top_n,
        )
        for psku, nscore in name_candidates:
            if psku not in score_map or nscore > score_map[psku]:
                score_map[psku] = nscore

    ranked = sorted(
        score_map.items(), key=lambda x: x[1], reverse=True,
    )[:top_n]
    return ranked


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
                # Map composite keys to the same title so name-based
                # reranking also works for variant-level matches.
                if title:
                    title_lookup[composite] = title

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

    # --- Fallback: extract embedded SKUs from descriptions for unmatched ---
    # German-style invoices often have an internal Prod.-Nr. as the SKU
    # column value (e.g. "702074002") while the *actual* product code
    # (e.g. "ADIBTT02") is embedded in the description text.  When the
    # initial match fails, try the extracted description-SKU instead.
    if desc_map:
        fallback_map: dict[str, str] = {}  # inv_sku → extracted_sku
        for inv_sku, mentry in matches.items():
            if mentry['sku'] is not None:
                continue
            emb = extract_sku_from_description(desc_map.get(inv_sku, ''))
            if emb:
                fallback_map[inv_sku] = emb
        if fallback_map:
            emb_matches = match_supplier_to_products(
                list(set(fallback_map.values())),
                augmented_skus,
                threshold=threshold,
                product_names=prod_names,
            )
            for inv_sku, emb_sku in fallback_map.items():
                if emb_sku in emb_matches and emb_matches[emb_sku]['sku'] is not None:
                    matches[inv_sku] = emb_matches[emb_sku]

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

# --- Colour / material translation aliases (Danish ↔ English) ---
# Each tuple is a group of equivalent names (all lower-cased).
# Matching is bidirectional: "rød" matches "red" and vice versa.

_TRANSLATION_GROUPS: list[tuple[str, ...]] = [
    # Colours – Danish ↔ English ↔ Spanish (abbrev + full) ↔ German
    ('rød', 'red', 'ro', 'rojo', 'rot'),
    ('blå', 'blue', 'az', 'azul', 'blau'),
    ('grøn', 'green', 've', 'verde', 'grün', 'gruen'),
    ('gul', 'yellow', 'amarillo', 'gelb'),
    ('hvid', 'white', 'bl', 'blanco', 'weiß', 'weiss'),
    ('sort', 'black', 'ne', 'negro', 'schwarz'),
    ('grå', 'grey', 'gray', 'grau'),
    ('lilla', 'purple', 'morado', 'lila'),
    ('orange', 'orange', 'naranja'),
    ('rosa', 'lyserød', 'pink'),
    ('brun', 'brown', 'marrón', 'braun'),
    ('turkis', 'turquoise'),
    ('guld', 'gold'),
    ('sølv', 'silver', 'silber'),
    ('beige', 'beige'),
    ('marineblå', 'navy'),
    ('bordeaux', 'burgundy', 'vinrød'),
    # Materials – Danish ↔ English ↔ German
    ('bomuld', 'cotton'),
    ('uld', 'wool'),
    ('læder', 'leather', 'leder'),
    ('silke', 'silk'),
    ('polyester', 'polyester'),
    ('stål', 'steel', 'stahl'),
    ('træ', 'wood', 'holz'),
]

_TRANSLATION_MAP: dict[str, frozenset[str]] = {}
for _grp in _TRANSLATION_GROUPS:
    _fs = frozenset(_grp)
    for _member in _grp:
        _TRANSLATION_MAP[_member] = _fs


def _size_aliases(name: str) -> frozenset[str]:
    """Return the set of equivalent size names for *name*, or empty."""
    return _SIZE_ALIAS_MAP.get(name.lower().strip(), frozenset())


def _translation_aliases(name: str) -> frozenset[str]:
    """Return translations for *name* (e.g. rød ↔ red), or empty."""
    return _TRANSLATION_MAP.get(name.lower().strip(), frozenset())


def _boundary_pattern(key: str) -> re.Pattern[str]:
    """Get or compile a word-boundary regex for *key*.

    The key is lower-cased internally — callers need not pre-normalise.
    The Unicode range ``\\u00c0–\\u024f`` extends word boundaries to
    cover Latin Extended characters (Danish ø, å, æ, etc.) so that
    translated colour names are not split mid-word.
    """
    key = key.lower()
    pat = _BOUNDARY_CACHE.get(key)
    if pat is None:
        pat = re.compile(
            r'(?<![A-Za-z0-9\u00c0-\u024f])'
            + re.escape(key)
            + r'(?![A-Za-z0-9\u00c0-\u024f])'
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

    Also recognises:
    - Standard size abbreviations: ``XS`` ↔ ``X-Small``,
      ``S`` ↔ ``Small``, ``M`` ↔ ``Medium``, ``L`` ↔ ``Large``,
      ``XL`` ↔ ``X-Large``, and so on.
    - Colour / material translations: ``rød`` ↔ ``red``,
      ``blå`` ↔ ``blue``, ``hvid`` ↔ ``white``, etc.
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

    # Translation match: check if any translated equivalent appears
    for alias in _translation_aliases(key):
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
# AI-assisted column mapping
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

#: Allowed internal field names that the LLM may map columns to.
INTERNAL_FIELDS: frozenset[str] = frozenset({
    'sku', 'qty', 'price', 'description', 'discount', 'currency',
})

#: Type alias for the injectable LLM call function.
#: Signature: (prompt, api_key, model) -> response_text or None
LLMCallFn = Callable[[str, str, str], str | None]


def _build_mapping_prompt(df: pd.DataFrame) -> str:
    """Build a compact prompt for column-mapping from sample rows.

    Encodes the first 5 rows as a Markdown table together with the list
    of target internal fields.
    """
    sample = df.head(5)

    # Build markdown table without requiring tabulate
    cols = list(sample.columns)
    header = '| ' + ' | '.join(str(c) for c in cols) + ' |'
    sep = '| ' + ' | '.join('---' for _ in cols) + ' |'
    rows = []
    for _, row in sample.iterrows():
        rows.append('| ' + ' | '.join(str(v) for v in row) + ' |')
    md_table = '\n'.join([header, sep] + rows)

    return (
        "You are a data-mapping assistant. "
        "Given a supplier/invoice data file with the columns and sample rows below, "
        "map each column to one of these internal fields or null if not mappable.\n\n"
        "Internal fields:\n"
        "- sku: Product SKU or article number\n"
        "- qty: Quantity or amount\n"
        "- price: Unit cost price\n"
        "- description: Product name or description\n"
        "- discount: Discount percentage\n"
        "- currency: Currency code\n\n"
        f"Columns: {list(df.columns)}\n\n"
        f"Sample data:\n{md_table}\n\n"
        "Return ONLY a JSON object mapping each column name to its internal field "
        "(or null). Example: {\"Col1\": \"sku\", \"Col2\": \"price\", \"Col3\": null}\n"
        "Do not include any text outside the JSON object."
    )


def _default_llm_call(prompt: str, api_key: str, model: str) -> str | None:
    """OpenAI-compatible ``/v1/chat/completions`` call using ``requests``.

    Reads ``OPENAI_BASE_URL`` from the environment to allow pointing at
    compatible providers.  Defaults to ``https://api.openai.com``.
    """
    import requests as _requests

    base_url = os.environ.get('OPENAI_BASE_URL', 'https://api.openai.com')
    url = f"{base_url.rstrip('/')}/v1/chat/completions"

    try:
        resp = _requests.post(
            url,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 200,
                'temperature': 0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content']
    except Exception:
        _log.exception("LLM call failed")
        return None


def _parse_llm_mapping_response(
    raw_text: str | None,
    df_columns: list[str],
) -> dict[str, str | None] | None:
    """Parse and validate an LLM column-mapping response.

    Returns a dict ``{internal_field: column_name}`` with only valid
    entries, or ``None`` if the response is unusable.
    """
    if not raw_text:
        return None

    # Try to extract JSON from the response (the LLM may wrap it in
    # markdown code fences or add surrounding text).
    text = raw_text.strip()
    # Strip markdown code fences
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

    # Find the first { ... } block
    brace_start = text.find('{')
    brace_end = text.rfind('}')
    if brace_start == -1 or brace_end == -1 or brace_end <= brace_start:
        _log.warning("No JSON object found in LLM response")
        return None

    json_str = text[brace_start:brace_end + 1]

    try:
        mapping_raw: dict = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        _log.warning("Failed to parse JSON from LLM response")
        return None

    if not isinstance(mapping_raw, dict):
        return None

    # Build the validated mapping: internal_field -> column_name
    col_set = set(df_columns)
    result: dict[str, str | None] = {}

    for col_name, field in mapping_raw.items():
        if col_name not in col_set:
            _log.debug("LLM mapped non-existent column %r — skipping", col_name)
            continue
        if field is None:
            continue
        field_lower = str(field).lower().strip()
        if field_lower not in INTERNAL_FIELDS:
            _log.debug("LLM mapped to unknown field %r — skipping", field)
            continue
        # Only keep the first mapping per internal field
        if field_lower not in result:
            result[field_lower] = col_name

    if not result:
        return None

    return result


def suggest_column_mapping(
    df: pd.DataFrame,
    *,
    api_key: str | None = None,
    model: str = 'gpt-4o-mini',
    llm_call: LLMCallFn | None = None,
) -> dict[str, str | None] | None:
    """Suggest column mappings using an LLM when standard rules fail.

    Strategy
    --------
    1. **Trigger**: Call this *only* when :func:`detect_invoice_columns`
       or :func:`~domain.supplier.detect_supplier_columns` returns
       ``None`` for a required field (SKU, quantity, price).
    2. **Prompt construction**: Send the LLM a compact prompt containing:

       - The first 3–5 rows of the DataFrame as a markdown table.
       - A list of target column roles: ``sku``, ``qty``, ``price``,
         ``description``, ``discount``, ``currency``.
       - Instruction to return a JSON object mapping each role to the
         best matching column name (or ``null``).

    3. **API call**: Use an injectable ``llm_call`` function (defaults to
       an OpenAI-compatible ``/v1/chat/completions`` endpoint via
       ``requests``).  Keep ``max_tokens`` ≤ 200 and ``temperature`` = 0
       for deterministic output.
    4. **Validation**: Parse the JSON response and verify each suggested
       column actually exists in ``df.columns``.  Discard any that don't.

    Parameters
    ----------
    df : pd.DataFrame
        The parsed invoice/supplier DataFrame whose columns need mapping.
    api_key : str or None
        OpenAI-compatible API key.  When ``None``, the function reads
        ``OPENAI_API_KEY`` from the environment.
    model : str
        Model name (default ``'gpt-4o-mini'`` — cheapest option suitable
        for structured extraction tasks).
    llm_call : callable or None
        Optional injectable function with signature
        ``(prompt: str, api_key: str, model: str) -> str | None``.
        Defaults to :func:`_default_llm_call` which uses the OpenAI-
        compatible REST API.

    Returns
    -------
    dict or None
        ``{'sku': col_name, 'qty': col_name, ...}`` on success, or
        ``None`` if no API key is configured or the call fails.

    Setup
    -----
    Set the ``OPENAI_API_KEY`` environment variable::

        export OPENAI_API_KEY="sk-..."

    No additional dependencies are required beyond ``requests`` (already
    in requirements.txt via ``streamlit``).
    """
    key = api_key or os.environ.get('OPENAI_API_KEY')
    if not key:
        return None

    if df.empty or len(df.columns) == 0:
        return None

    prompt = _build_mapping_prompt(df)
    caller = llm_call or _default_llm_call
    raw_response = caller(prompt, key, model)
    return _parse_llm_mapping_response(raw_response, list(df.columns))


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


def generate_barcode_pdf(
    export_df: pd.DataFrame,
    export_format: str = "standard",
) -> bytes:
    """Generate a PDF document with scannable EAN barcodes.

    Each row from *export_df* is rendered as a label containing
    the product information and a scannable barcode image.

    Parameters
    ----------
    export_df:
        DataFrame produced by :func:`build_ean_export`.  Expected
        columns: ``SKU``, ``Product Number``, ``Title``,
        ``Variant Name``, ``Amount``, ``EAN``.
    export_format:
        Layout variant — ``"standard"`` (default, A4 2-column grid),
        ``"zd421_label"`` (50 mm × 100 mm single-label pages), or
        ``"fast_scan"`` (compact A4 grid for rapid scanning).

    Returns
    -------
    bytes
        Raw PDF file content.
    """
    if export_format == "zd421_label":
        return _generate_barcode_pdf_zd421(export_df)
    if export_format == "fast_scan":
        return _generate_barcode_pdf_fast_scan(export_df)
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


# ---------------------------------------------------------------------------
# ZD421 Label Printer format (50 mm × 100 mm, one barcode per page)
# ---------------------------------------------------------------------------

def _generate_barcode_pdf_zd421(export_df: pd.DataFrame) -> bytes:
    """Generate a label-printer PDF (50 mm × 100 mm, one barcode per page).

    Designed for the ZD421 (or similar) label printer.  Each page
    holds exactly one barcode with concise product metadata.
    """
    from fpdf import FPDF

    page_w = 50   # mm
    page_h = 100  # mm

    pdf = FPDF(orientation='P', unit='mm', format=(page_w, page_h))
    pdf.set_auto_page_break(auto=False)

    def _latin1(text: str) -> str:
        return text.encode('latin-1', 'replace').decode('latin-1')

    barcode_w = 44   # mm – fits within 50 mm page with margins
    barcode_h = 20   # mm
    margin = 3       # mm

    label_count = 0
    for _, row in export_df.iterrows():
        pdf.add_page()
        label_count += 1

        cx = page_w / 2  # centre x

        # SKU header (centred)
        pdf.set_font('Helvetica', 'B', 7)
        sku = str(row.get('SKU', ''))
        prod_num = str(row.get('Product Number', ''))
        pdf.set_xy(margin, margin)
        pdf.cell(
            page_w - 2 * margin, 4,
            _latin1(f"SKU: {sku}  |  #{prod_num}"),
            align='C',
        )

        # Title / variant
        pdf.set_font('Helvetica', '', 6)
        title = str(row.get('Title', ''))[:40]
        variant = str(row.get('Variant Name', '') or '')
        if variant:
            title = f"{title} - {variant}"
        pdf.set_xy(margin, margin + 5)
        pdf.cell(page_w - 2 * margin, 3.5, _latin1(title), align='C')

        # Quantity
        amount = row.get('Amount', 1)
        try:
            amount = int(float(amount))
        except (ValueError, TypeError):
            amount = 1
        pdf.set_font('Helvetica', 'B', 6)
        pdf.set_xy(margin, margin + 9.5)
        pdf.cell(page_w - 2 * margin, 3.5, f"Qty: {amount}", align='C')

        # Barcode image (centred)
        ean_val = str(row.get('EAN', '') or '')
        barcode_buf = _render_barcode_image(ean_val)
        bc_y = margin + 14
        if barcode_buf is not None:
            bc_x = cx - barcode_w / 2
            pdf.image(barcode_buf, x=bc_x, y=bc_y, w=barcode_w, h=barcode_h)
        else:
            pdf.set_font('Helvetica', '', 8)
            pdf.set_xy(margin, bc_y + 5)
            display = ean_val if ean_val else '(no EAN)'
            pdf.cell(page_w - 2 * margin, 5, display, align='C')

        # EAN footer
        pdf.set_font('Helvetica', '', 5)
        pdf.set_xy(0, page_h - 6)
        pdf.cell(page_w, 4, f"EAN: {ean_val}" if ean_val else '', align='C')

    if label_count == 0:
        pdf.add_page()
        pdf.set_font('Helvetica', '', 8)
        pdf.cell(0, 10, 'No products with EAN barcodes.', align='C')

    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Fast Scan format (compact A4 grid for rapid inventory scanning)
# ---------------------------------------------------------------------------

def _generate_barcode_pdf_fast_scan(export_df: pd.DataFrame) -> bytes:
    """Generate a compact A4 barcode PDF for fast inventory scanning.

    Uses a dense grid layout (3 columns) to fit more barcodes per
    page while keeping them large enough for reliable scanning.
    """
    from fpdf import FPDF

    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(auto=False)

    def _latin1(text: str) -> str:
        return text.encode('latin-1', 'replace').decode('latin-1')

    # Layout constants – 3-column compact grid
    page_h_a4 = 297       # mm – A4 height
    label_w = 60          # mm
    label_h = 38          # mm
    margin_x = 7          # mm
    margin_y = 7          # mm
    gap_x = 3             # mm – small horizontal gap
    gap_y = 2             # mm – small vertical gap
    cols = 3
    barcode_w = 48        # mm
    barcode_h = 16        # mm

    rows_per_page = int((page_h_a4 - 2 * margin_y + gap_y) / (label_h + gap_y))

    label_idx = 0
    for _, row in export_df.iterrows():
        if label_idx % (cols * rows_per_page) == 0:
            pdf.add_page()

        pos_on_page = label_idx % (cols * rows_per_page)
        col = pos_on_page % cols
        grid_row = pos_on_page // cols

        x = margin_x + col * (label_w + gap_x)
        y = margin_y + grid_row * (label_h + gap_y)

        # Thin border
        pdf.set_draw_color(220, 220, 220)
        pdf.rect(x, y, label_w, label_h)

        text_x = x + 2
        text_y = y + 1.5

        # SKU line (compact)
        pdf.set_font('Helvetica', 'B', 6)
        pdf.set_xy(text_x, text_y)
        sku = str(row.get('SKU', ''))
        prod_num = str(row.get('Product Number', ''))
        pdf.cell(
            label_w - 4, 3,
            _latin1(f"{sku} | #{prod_num}"),
            new_x='LMARGIN', new_y='NEXT',
        )

        # Title (truncated)
        pdf.set_font('Helvetica', '', 5)
        pdf.set_xy(text_x, text_y + 3.5)
        title = str(row.get('Title', ''))[:35]
        variant = str(row.get('Variant Name', '') or '')
        if variant:
            title = f"{title} - {variant}"
        pdf.cell(label_w - 4, 2.5, _latin1(title), new_x='LMARGIN', new_y='NEXT')

        # Barcode image (centred in label)
        ean_val = str(row.get('EAN', '') or '')
        barcode_buf = _render_barcode_image(ean_val)
        bc_y = text_y + 7
        if barcode_buf is not None:
            bc_x = x + (label_w - barcode_w) / 2
            pdf.image(barcode_buf, x=bc_x, y=bc_y, w=barcode_w, h=barcode_h)
        else:
            pdf.set_font('Helvetica', '', 7)
            pdf.set_xy(text_x, bc_y + 3)
            display = ean_val if ean_val else '(no EAN)'
            pdf.cell(label_w - 4, 4, display, align='C')

        # EAN footer
        pdf.set_font('Helvetica', '', 5)
        pdf.set_xy(x, y + label_h - 4)
        pdf.cell(label_w, 3, f"EAN: {ean_val}" if ean_val else '', align='C')

        label_idx += 1

    if label_idx == 0:
        pdf.add_page()
        pdf.set_font('Helvetica', '', 12)
        pdf.cell(0, 10, 'No products with EAN barcodes to display.', align='C')

    return bytes(pdf.output())
