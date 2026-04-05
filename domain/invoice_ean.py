"""Invoice-to-EAN barcode matching — no UI dependency.

Parses an invoice (CSV / PDF), matches its SKUs to the product catalogue
using fuzzy matching, and builds an export DataFrame with:
  SKU · Product Number · Title · Variant Name · Amount · EAN Barcode

This module is the **single source of truth** for all SKU matching logic.
Both Supplier price-list matching and Invoice/EAN matching use the same
functions defined here.

The unified ``matches_df`` schema (produced by :func:`build_matches_df`)
uses ``src_*`` column names (``src_sku``, ``src_qty``, ``src_type``, etc.)
to be source-agnostic.  ``export_from_matches_df`` also accepts the
legacy ``inv_*`` column names for backward compatibility.

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
    _guess_candidates,
)


# --- Quantity / amount column auto-detection ---

# NOTE: _QTY_NAMES is kept here for backward-compatibility but the
# canonical pattern list now lives in domain.supplier._QTY_NAMES and is
# used by _guess_candidates().
_QTY_NAMES = [
    'quantity', 'qty', 'antal', 'anzahl', 'amount', 'count',
    'mængde', 'stk', 'pcs', 'units', 'beløb', 'menge',
]


def _heuristic_detect_invoice_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Pure heuristic/regex column detection for invoice files.

    Internal fallback used when the LLM path is unavailable or fails.
    """
    candidates = _guess_candidates(list(df.columns))
    return {
        'sku': candidates['sku'][0] if candidates['sku'] else None,
        'qty': candidates['qty'][0] if candidates['qty'] else None,
        'description': (candidates['description'][0]
                        if candidates['description'] else None),
    }


def detect_invoice_columns(
    df: pd.DataFrame,
    *,
    api_key: str | None = None,
    model: str = 'gpt-4o-mini',
    llm_call: 'LLMCallFn | None' = None,
    tenant_id: str | None = None,
) -> dict[str, str | None]:
    """Auto-detect SKU, quantity, and description columns in an invoice.

    Uses :func:`suggest_column_mapping` (LLM-based) as the primary
    detection mechanism.  Falls back to pure heuristic matching when the
    LLM is unavailable, fails, or returns an unusable mapping.

    Returns ``{'sku': ..., 'qty': ..., 'description': ...}`` where each
    value is the original column name or ``None``.
    """
    # --- LLM-first path ---
    try:
        mapping = suggest_column_mapping(
            df, api_key=api_key, model=model, llm_call=llm_call,
            tenant_id=tenant_id,
        )
    except Exception:
        mapping = None

    if mapping and any(mapping.get(f) for f in ('sku', 'qty')):
        # Fill in gaps from heuristics for unambiguous candidates
        candidates = _guess_candidates(list(df.columns))
        used_cols = set(mapping.values())
        for field in ('sku', 'qty', 'description'):
            if not mapping.get(field) and candidates[field]:
                free = [c for c in candidates[field] if c not in used_cols]
                if len(free) == 1:
                    mapping[field] = free[0]
                    used_cols.add(free[0])
        return {
            'sku': mapping.get('sku'),
            'qty': mapping.get('qty'),
            'description': mapping.get('description'),
        }

    # --- Heuristic fallback ---
    return _heuristic_detect_invoice_columns(df)


# ---------------------------------------------------------------------------
# SKU normalisation & fuzzy matching (single source of truth)
# ---------------------------------------------------------------------------

# Craft / Danish-style size-index → size-label mapping.
# Invoice SKUs use a trailing numeric index (e.g. ``-7`` for XL),
# while the catalogue uses the label (e.g. ``-XL``).
_CRAFT_SIZE_INDEX_TO_LABEL: dict[str, str] = {
    "2": "XXS",
    "3": "XS",
    "4": "S",
    "5": "M",
    "6": "L",
    "7": "XL",
    "8": "2XL",
    "9": "3XL",
    "10": "4XL",
}

# Craft women EU-size variant: index → ``<alpha>/<EU>`` label.
# Used to produce variant itemnumber candidates like ``1910155-430000-L/40``.
_CRAFT_WOMEN_SIZE_INDEX_TO_LABEL: dict[str, str] = {
    "2": "XXS/32",
    "3": "XS/34",
    "4": "S/36",
    "5": "M/38",
    "6": "L/40",
    "7": "XL/42",
    "8": "2XL/44",
    "9": "3XL/46",
    "10": "4XL/48",
}

# Pattern: <base>-<color>-<size_idx>  where base and color are digit groups.
_CRAFT_SKU_RE = re.compile(
    r'^(\d{5,})-(\d{3,})-(\d{1,2})$'
)


def _normalize_craft_sku(s: str) -> str | None:
    """Translate a Craft-style numeric size-index SKU to size-label form.

    Example::

        >>> _normalize_craft_sku('1910163-999000-7')
        '1910163-999000-XL'

    Returns ``None`` when *s* does not match the expected pattern or the
    trailing index is not in :data:`_CRAFT_SIZE_INDEX_TO_LABEL`.
    """
    if not s:
        return None
    m = _CRAFT_SKU_RE.match(s.strip())
    if not m:
        return None
    base, color, idx = m.group(1), m.group(2), m.group(3)
    label = _CRAFT_SIZE_INDEX_TO_LABEL.get(idx)
    if label is None:
        return None
    return f"{base}-{color}-{label}"


def _craft_sku_candidates(s: str) -> list[str]:
    """Return all possible Craft-normalized forms for SKU *s*.

    Produces both the unisex form (e.g. ``1910163-999000-XL``) and the
    women/EU form (e.g. ``1910163-999000-XL/42``) so that the matching
    pipeline can try each against the variant itemnumber lookup.

    Returns an empty list when *s* does not match the Craft SKU pattern.
    """
    if not s:
        return []
    m = _CRAFT_SKU_RE.match(s.strip())
    if not m:
        return []
    base, color, idx = m.group(1), m.group(2), m.group(3)
    candidates: list[str] = []
    label = _CRAFT_SIZE_INDEX_TO_LABEL.get(idx)
    if label is not None:
        candidates.append(f"{base}-{color}-{label}")
    women_label = _CRAFT_WOMEN_SIZE_INDEX_TO_LABEL.get(idx)
    if women_label is not None:
        candidates.append(f"{base}-{color}-{women_label}")
    return candidates


# ---------------------------------------------------------------------------
# Craft women EU numeric sizes (used as authoritative disambiguator)
# ---------------------------------------------------------------------------
_EU_WOMEN_SIZES = frozenset({'34', '36', '38', '40', '42', '44', '46', '48'})

_EU_SIZE_RE = re.compile(
    r'(?<![0-9])'          # not preceded by a digit
    r'(34|36|38|40|42|44|46|48)'
    r'(?![0-9])'           # not followed by a digit
)


def _extract_eu_size(text: str) -> str | None:
    """Extract a women EU numeric size from *text* if present.

    Looks for whole-token occurrences of ``34, 36, 38, 40, 42, 44, 46, 48``
    — typical EU women sizes seen on Craft invoices (e.g. ``"/ 36"``,
    ``"Size 36"``).

    Returns the matched size string (e.g. ``"36"``) or ``None``.
    """
    if not text:
        return None
    m = _EU_SIZE_RE.search(text)
    return m.group(1) if m else None


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
    'score': int, 'alternatives': [(product_sku, score), …],
    'method': str}}``.
    ``sku`` is ``None`` when no match exceeds *threshold*.
    The ``method`` field indicates how the match was made:
    ``"sku-exact"``, ``"craft-exact"``, ``"fuzzy"``, or ``""``.
    """
    norm_to_orig: dict[str, str] = {}
    for sku in product_skus:
        norm = normalize_sku(sku)
        if norm and norm not in norm_to_orig:
            norm_to_orig[norm] = sku

    # Parallel raw-key map: uppercased, trimmed originals preserving
    # hyphens so that Craft-normalised keys like "1910163-999000-XL"
    # can be matched case-insensitively without relying on
    # normalize_sku (which strips separators and may cause fuzzy drift).
    raw_upper_to_orig: dict[str, str] = {}
    for sku in product_skus:
        key = str(sku).upper().strip()
        if key and key not in raw_upper_to_orig:
            raw_upper_to_orig[key] = sku

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
                'method': 'sku-exact',
            }
            continue

        # Craft-style size-index → size-label normalisation.
        # E.g. invoice "1910163-999000-7" → "1910163-999000-XL" which
        # should match the catalogue entry directly.
        # Tries both unisex (e.g. "-XL") and women/EU (e.g. "-XL/42")
        # forms so that variant itemnumber keys are matched first.
        # Try raw case-insensitive match first (preserves hyphens),
        # then fall back to normalize_sku comparison.
        craft_candidates = _craft_sku_candidates(sup_sku)
        craft_matched = False
        for craft_norm in craft_candidates:
            craft_upper = craft_norm.upper().strip()
            if craft_upper in raw_upper_to_orig:
                matches[sup_sku] = {
                    'sku': raw_upper_to_orig[craft_upper],
                    'score': 100,
                    'alternatives': [],
                    'method': 'craft-exact',
                }
                craft_matched = True
                break
            craft_key = normalize_sku(craft_norm)
            if craft_key and craft_key in norm_to_orig:
                matches[sup_sku] = {
                    'sku': norm_to_orig[craft_key],
                    'score': 100,
                    'alternatives': [],
                    'method': 'craft-exact',
                }
                craft_matched = True
                break
        if craft_matched:
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
                'method': 'fuzzy',
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
            'method': '',
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

    # Also build a variant-item-number → (NUMBER, VARIANT_TYPES) lookup
    # so that invoice SKUs matching a variant's own item number can be
    # resolved deterministically (priority 2 after EAN exact match).
    variant_itemnumber_lookup: dict[str, tuple[str, str]] = {}

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

        # Register variant item numbers as additional exact-match keys.
        # When a variant has its own ItemNumber (e.g. "1910163-999000-XL"),
        # an invoice SKU equal to that string should match directly.
        vitemnumber = str(row.get('VARIANT_ITEMNUMBER', '') or '').strip()
        if vitemnumber and vitemnumber != num:
            if vitemnumber not in composite_lookup:
                composite_lookup[vitemnumber] = (num, vtype)
                augmented_skus.append(vitemnumber)
                if title:
                    title_lookup[vitemnumber] = title
            # Keep a separate lookup for the "variant-itemnumber-exact"
            # method label (used after the fuzzy pass).
            if vitemnumber not in variant_itemnumber_lookup:
                variant_itemnumber_lookup[vitemnumber] = (num, vtype)

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

    # --- Variant-item-number promotion ---
    # When a match resolved to a variant item number key (e.g.
    # "1910163-999000-XL"), relabel it with a specific method so the
    # UI / export can distinguish deterministic variant-level matches
    # from base-number or fuzzy matches.
    # Because normalize_sku strips separators, a composite key like
    # "1910163 999000 // XL" normalizes identically to "1910163-999000-XL".
    # We therefore compare normalized invoice SKUs against normalized
    # variant item numbers (rather than the raw matched_key).
    # We also check if the *matched key* itself is a variant item number
    # (covers Craft-normalised SKUs whose invoice form differs from the
    # catalogue variant item number).
    if variant_itemnumber_lookup:
        norm_variant_keys: dict[str, tuple[str, str]] = {}
        for _vinum in variant_itemnumber_lookup:
            norm_vkey = normalize_sku(_vinum)
            if norm_vkey:
                norm_variant_keys[norm_vkey] = variant_itemnumber_lookup[_vinum]
        for inv_sku, mentry in matches.items():
            if mentry.get('sku') is None:
                continue
            if mentry.get('method') not in ('sku-exact', 'craft-exact'):
                continue
            orig_method = mentry['method']
            norm_inv_sku = normalize_sku(inv_sku)
            norm_matched = normalize_sku(mentry['sku'])
            if (norm_inv_sku and norm_inv_sku in norm_variant_keys) or \
               (norm_matched and norm_matched in norm_variant_keys):
                # Distinguish Craft-normalised itemnumber matches from
                # direct itemnumber matches for diagnostic clarity.
                if orig_method == 'craft-exact':
                    mentry['method'] = 'craft-variant-itemnumber-exact'
                else:
                    mentry['method'] = 'variant-itemnumber-exact'

    # --- EAN cross-check: use EAN as a strong signal ---
    # If the product catalogue has EAN values, build a reverse lookup
    # from EAN → (NUMBER, VARIANT_TYPES).  For any invoice SKU that is
    # a bare EAN (pure digits, 8/12/13 chars) or whose description
    # contains a valid EAN, override the fuzzy match with a 100% EAN match.
    # This ensures EAN-based and SKU-based matching stay consistent.
    # Valid lengths: EAN-8 (8 digits), UPC-A (12 digits), EAN-13 (13 digits).
    _VALID_EAN_LENGTHS = (8, 12, 13)
    ean_to_product: dict[str, tuple[str, str]] = {}
    if 'EAN' in products_df.columns:
        for _, row in products_df.iterrows():
            ean = re.sub(r'\D', '', str(row.get('EAN', '') or '').strip())
            num = str(row.get('NUMBER', '') or '').strip()
            vtype = str(row.get('VARIANT_TYPES', '') or '').strip()
            if ean and len(ean) in _VALID_EAN_LENGTHS and num:
                if ean not in ean_to_product:
                    ean_to_product[ean] = (num, vtype)

    if ean_to_product:
        for inv_sku in invoice_skus.tolist():
            # Check if the invoice SKU itself is a bare EAN
            digits = re.sub(r'\D', '', inv_sku)
            ean_hit = None
            if digits and len(digits) in _VALID_EAN_LENGTHS:
                ean_hit = ean_to_product.get(digits)

            # Also check if description contains an EAN
            if ean_hit is None and inv_sku in desc_map:
                desc_digits = re.findall(r'\b\d{8,13}\b', desc_map[inv_sku])
                for dd in desc_digits:
                    if dd in ean_to_product:
                        ean_hit = ean_to_product[dd]
                        break

            if ean_hit is not None:
                num, vtype = ean_hit
                # Build a composite key that resolves to the specific variant
                if vtype:
                    composite = f"{num} {vtype}"
                    if composite in composite_lookup:
                        matched_key = composite
                    else:
                        matched_key = num
                else:
                    matched_key = num
                # Only override if the EAN match is stronger than existing
                existing = matches.get(inv_sku)
                if existing is None or existing['sku'] is None or existing['score'] < 100:
                    matches[inv_sku] = {
                        'sku': matched_key,
                        'score': 100,
                        'alternatives': [],
                        'method': 'ean-exact',
                    }

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
                    emb_entry = emb_matches[emb_sku].copy()
                    emb_entry['method'] = 'embedded-sku'
                    matches[inv_sku] = emb_entry

    return {
        'matches': matches,
        'composite_lookup': composite_lookup,
        'title_lookup': title_lookup,
        'qty_map': qty_map,
        'desc_map': desc_map,
    }


# Canonical output column names *and* order for export DataFrames.
_EXPORT_COLUMNS = [
    'SKU', 'Product Number', 'Title', 'Variant Name',
    'Amount', 'EAN', 'Match %',
]

# Canonical column names for the matches DataFrame.
_MATCHES_COLUMNS = [
    'src_row_id', 'src_type', 'src_sku', 'src_sku_craft_normalized',
    'src_description', 'src_qty',
    'matched_number', 'matched_variant', 'matched_title',
    'matched_ean', 'match_score', 'match_source',
    'match_method_detail', 'status',
]

# Legacy aliases — ``inv_*`` names that older callers may rely on.
# ``build_matches_df`` always produces the ``src_*`` canonical names,
# and ``export_from_matches_df`` accepts both ``src_*`` and ``inv_*``.
_LEGACY_ALIASES = {
    'inv_row_id': 'src_row_id',
    'inv_sku': 'src_sku',
    'inv_description': 'src_description',
    'inv_qty': 'src_qty',
}


def build_matches_df(
    products_df: pd.DataFrame,
    match_data: dict,
    *,
    manual_overrides: dict[str, str] | None = None,
    src_type: str = 'invoice',
) -> pd.DataFrame:
    """Build a unified matches DataFrame for UI display and export.

    Produces a single DataFrame where each row represents one source line
    (invoice or supplier) and its match state.  Columns:

    - ``src_row_id`` — unique row identifier (sequential).
    - ``src_type`` — ``"invoice"`` or ``"supplier"``.
    - ``src_sku`` — source SKU text.
    - ``src_description`` — source description text.
    - ``src_qty`` — parsed source quantity.
    - ``matched_number`` — product NUMBER of best match (or empty).
    - ``matched_variant`` — VARIANT_TYPES of narrowed match.
    - ``matched_title`` — product title.
    - ``matched_ean`` — EAN barcode of matched variant.
    - ``match_score`` — fuzzy match score (0–100).
    - ``match_source`` — ``"auto-sku"``, ``"auto-ean"``, ``"manual"``,
      or ``"unmatched"``.
    - ``status`` — ``"ok"``, ``"ambiguous"``, or ``"needs-manual"``.

    This DataFrame serves as the **single source of truth** for the
    matching step.  The frontend renders it directly, and
    :func:`export_from_matches_df` converts it to the final export.

    Parameters
    ----------
    products_df:
        Product catalogue DataFrame.
    match_data:
        Dict returned by :func:`match_invoice_to_products`.
    manual_overrides:
        Optional dict mapping source SKUs to augmented product SKUs
        for manual corrections.
    src_type:
        Label for the ``src_type`` column (``"invoice"`` or ``"supplier"``).
    """
    matches = dict(match_data['matches'])  # shallow copy
    composite_lookup = match_data['composite_lookup']
    title_lookup = match_data.get('title_lookup', {})
    qty_map = match_data['qty_map']
    desc_map = match_data['desc_map']

    overrides = manual_overrides or {}

    # Apply manual overrides on a copy so we don't mutate the original
    for src_sku, prod_key in overrides.items():
        matches[src_sku] = {
            'sku': prod_key,
            'score': 100,
            'alternatives': [],
            'method': 'manual',
        }

    # Build a lookup from product NUMBER → best (title, variant, ean)
    # by doing the same expansion + narrowing as build_export_from_matches
    # but collecting per-SKU results.
    prods_cols = ['NUMBER', 'VARIANT_TYPES', 'EAN', 'TITLE_DK']
    if 'VARIANT_ITEMNUMBER' in products_df.columns:
        prods_cols.append('VARIANT_ITEMNUMBER')
    prods = products_df[prods_cols].copy()
    prods['_num_key'] = prods['NUMBER'].astype(str).str.strip()

    records: list[dict] = []
    row_id = 0

    for src_sku, mentry in matches.items():
        matched_key = mentry['sku']
        score = mentry['score']
        method_detail = mentry.get('method', '')
        src_qty = qty_map.get(src_sku, 1.0)
        src_desc = desc_map.get(src_sku, '')
        craft_norm = _normalize_craft_sku(src_sku) or ''

        if matched_key is None:
            # Unmatched
            records.append({
                'src_row_id': row_id,
                'src_type': src_type,
                'src_sku': src_sku,
                'src_sku_craft_normalized': craft_norm,
                'src_description': src_desc,
                'src_qty': src_qty,
                'matched_number': '',
                'matched_variant': '',
                'matched_title': '',
                'matched_ean': '',
                'match_score': 0,
                'match_source': 'unmatched',
                'match_method_detail': method_detail,
                'status': 'needs-manual',
            })
            row_id += 1
            continue

        number, vtype = composite_lookup.get(matched_key, (matched_key, ''))
        source = (
            'manual' if src_sku in overrides
            else 'auto-sku'
        )
        if src_sku in overrides:
            method_detail = 'manual'

        # Find matching products to narrow variant
        prod_rows = prods.loc[prods['_num_key'] == number]
        if prod_rows.empty:
            records.append({
                'src_row_id': row_id,
                'src_type': src_type,
                'src_sku': src_sku,
                'src_sku_craft_normalized': craft_norm,
                'src_description': src_desc,
                'src_qty': src_qty,
                'matched_number': number,
                'matched_variant': vtype,
                'matched_title': title_lookup.get(number, ''),
                'matched_ean': '',
                'match_score': score,
                'match_source': source,
                'match_method_detail': method_detail,
                'status': 'ok',
            })
            row_id += 1
            continue

        # Build a mini-group for narrowing
        group = prod_rows.copy()
        group['_inv_sku'] = src_sku
        group['_inv_desc'] = src_desc
        group['_matched_number'] = number
        group['_matched_vtype'] = vtype
        group['_inv_qty'] = src_qty
        group['_score'] = score

        narrowed = _narrow_group_variants(group)
        title = title_lookup.get(number, '')

        if len(narrowed) == 1:
            r = narrowed.iloc[0]
            var_name = str(r.get('VARIANT_TYPES', '') or '').strip()
            ean = str(r.get('EAN', '') or '').strip()
            status = 'ok'
        elif len(narrowed) > 1 and vtype:
            # Had a hint but couldn't resolve → ambiguous, show first
            r = narrowed.iloc[0]
            var_name = str(r.get('VARIANT_TYPES', '') or '').strip()
            ean = str(r.get('EAN', '') or '').strip()
            status = 'ambiguous'
        elif len(narrowed) > 1:
            # Multiple variants, no hint — expand all
            for _, r in narrowed.iterrows():
                var_name = str(r.get('VARIANT_TYPES', '') or '').strip()
                ean = str(r.get('EAN', '') or '').strip()
                records.append({
                    'src_row_id': row_id,
                    'src_type': src_type,
                    'src_sku': src_sku,
                    'src_sku_craft_normalized': craft_norm,
                    'src_description': src_desc,
                    'src_qty': src_qty,
                    'matched_number': number,
                    'matched_variant': var_name,
                    'matched_title': title,
                    'matched_ean': ean,
                    'match_score': score,
                    'match_source': source,
                    'match_method_detail': method_detail,
                    'status': 'ok',
                })
                row_id += 1
            continue
        else:
            var_name = ''
            ean = ''
            status = 'ok'

        records.append({
            'src_row_id': row_id,
            'src_type': src_type,
            'src_sku': src_sku,
            'src_sku_craft_normalized': craft_norm,
            'src_description': src_desc,
            'src_qty': src_qty,
            'matched_number': number,
            'matched_variant': var_name,
            'matched_title': title,
            'matched_ean': ean,
            'match_score': score,
            'match_source': source,
            'match_method_detail': method_detail,
            'status': status,
        })
        row_id += 1

    if not records:
        return pd.DataFrame(columns=_MATCHES_COLUMNS)

    return pd.DataFrame(records, columns=_MATCHES_COLUMNS)


def export_from_matches_df(matches_df: pd.DataFrame) -> pd.DataFrame:
    """Convert a matches DataFrame to the canonical export format.

    Filters to rows with ``match_source != 'unmatched'`` (i.e. matched rows)
    and renames columns to the standard export column names.

    Accepts both the canonical ``src_*`` column names and the legacy
    ``inv_*`` names for backward compatibility.

    This replaces the need to re-run ``build_export_from_matches`` after
    manual overrides — the user edits ``matches_df`` directly and then
    calls this function to produce the export.
    """
    if matches_df.empty:
        return pd.DataFrame(columns=_EXPORT_COLUMNS)

    # Normalise legacy column names → canonical names
    df = matches_df.copy()
    for old, new in _LEGACY_ALIASES.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})

    # Only export rows that have a match (auto or manual)
    exportable = df.loc[
        df['match_source'] != 'unmatched'
    ].copy()

    if exportable.empty:
        return pd.DataFrame(columns=_EXPORT_COLUMNS)

    export = pd.DataFrame({
        'SKU': exportable['src_sku'],
        'Product Number': exportable['matched_number'],
        'Title': exportable['matched_title'],
        'Variant Name': exportable['matched_variant'],
        'Amount': exportable['src_qty'],
        'EAN': exportable['matched_ean'],
        'Match %': exportable['match_score'],
    })

    return export.reset_index(drop=True)


def _dedupe_product_rows(matched_products: pd.DataFrame) -> pd.DataFrame:
    """Safety-net dedup for catalogue rows sharing the same (NUMBER, VARIANT_TYPES).

    Keeps one representative row per unique ``(NUMBER, VARIANT_TYPES)``
    pair — preferring the row with a non-empty EAN.  This handles the
    edge-case where the product catalogue itself contains duplicate rows
    for the same variant (e.g. different VARIANT_IDs both labelled
    ``"XXS"``).

    .. note::

        This is a *safety net*, not the primary deduplication mechanism.
        The main pipeline in :func:`build_export_from_matches` prevents
        spurious duplication at the structural level by narrowing variant
        groups and limiting row cardinality per invoice line.
    """
    if len(matched_products) <= 1:
        return matched_products

    num_col = matched_products['NUMBER'].fillna('').astype(str).str.strip()
    vt_col = (
        matched_products['VARIANT_TYPES'].fillna('').astype(str).str.strip()
    )
    group_key = num_col + '|||' + vt_col

    if group_key.nunique() == len(matched_products):
        return matched_products  # already unique

    ean_col = matched_products['EAN'].fillna('').astype(str).str.strip()
    has_ean = ean_col.ne('')

    keep_idxs: list[int] = []
    for _, grp in matched_products.groupby(group_key, sort=False):
        if len(grp) == 1:
            keep_idxs.append(grp.index[0])
        else:
            with_ean = grp.loc[has_ean.reindex(grp.index, fill_value=False)]
            if not with_ean.empty:
                keep_idxs.append(with_ean.index[0])
            else:
                keep_idxs.append(grp.index[0])

    return matched_products.loc[keep_idxs]


# Regex for extracting standalone numbers from text.  Uses Unicode range
# \u00c0–\u024f in lookbehind/lookahead to cover Latin Extended characters
# (Danish ø, å, æ, German ü, ö, ä, etc.) so that numbers embedded in
# European words are not accidentally extracted.
_NUMERIC_HINT_RE = re.compile(
    r'(?<![A-Za-z0-9\u00c0-\u024f])'
    r'(\d+(?:[.,]\d+)?)'
    r'(?![A-Za-z0-9\u00c0-\u024f])'
)


def _extract_numeric_hints(text: str) -> set[float]:
    """Extract decimal/integer numbers from *text* that look like size hints.

    Handles European decimal commas (``3,0`` → ``3.0``) as well as
    standard decimal points.  Only returns numbers that are plausible
    size/length values (> 0 and finite).

    Used to match invoice lines like ``"GTBL 3,0"`` against product
    variants with numeric lengths such as ``"265 cm / 3.5"``.
    """
    # Match numbers with optional decimal (comma or dot)
    # Patterns like "3,0", "3.5", "265", "4.0"
    nums: set[float] = set()
    for m in _NUMERIC_HINT_RE.finditer(text):
        raw = m.group(1).replace(',', '.')
        try:
            val = float(raw)
            if val > 0 and math.isfinite(val):
                nums.add(val)
        except (ValueError, TypeError):
            pass
    return nums


def _numeric_match_variants(
    group: pd.DataFrame,
    context_nums: set[float],
) -> pd.DataFrame:
    """Narrow variant rows using numeric hints from the invoice line.

    For each variant name (e.g. ``"265 cm / 3.5"``), extract its numeric
    values and check if *any* match one of the *context_nums* from the
    invoice.  Returns only the matching rows, or the full group if no
    rows (or all rows) match.
    """
    if not context_nums or group.empty:
        return group

    vt_col = group['VARIANT_TYPES'].fillna('').astype(str)

    def _has_matching_number(vt: str) -> bool:
        vt_nums = _extract_numeric_hints(vt)
        return bool(vt_nums & context_nums)

    hit_mask = vt_col.apply(_has_matching_number)
    if hit_mask.any() and hit_mask.sum() < len(group):
        return group.loc[hit_mask]
    return group


def _has_variant_hint(text: str, product_number: str) -> bool:
    """Detect whether *text* contains a size/numeric token absent from *product_number*.

    Used as a defensive check after variant narrowing fails: if the invoice
    SKU text contains a recognised size abbreviation (e.g. ``"XXS"``,
    ``"XL"``, ``"M"``) or a numeric value (e.g. ``"3,0"``, ``"4.5"``)
    that is **not** part of the product NUMBER itself, we treat the SKU
    as carrying an unresolved variant hint and limit the export to a
    single row instead of emitting all catalogue variants with
    duplicated quantity.

    This prevents:
    - The "FB 400 XXS" duplication bug where the invoice intends a
      specific size but the narrowing logic cannot resolve it.
    - The "GTBL 3,0" duplication bug where a numeric hint maps to a
      belt length but cannot be resolved to a specific variant.
    """
    text_lower = text.lower().strip()
    num_lower = product_number.lower().strip()
    # Check size-alias tokens
    for key in _SIZE_ALIAS_MAP:
        if len(key) < 1:
            continue
        if _boundary_pattern(key).search(text_lower):
            if not _boundary_pattern(key).search(num_lower):
                return True
    # Check numeric hints not present in the product number
    text_nums = _extract_numeric_hints(text_lower)
    num_nums = _extract_numeric_hints(num_lower)
    extra_nums = text_nums - num_nums
    if extra_nums:
        return True
    return False


def _narrow_group_variants(group: pd.DataFrame) -> pd.DataFrame:
    """Narrow product variant rows for one invoice-SKU match group.

    Given the merged rows (match info joined with product catalogue) for a
    single invoice SKU, apply variant narrowing to select the correct
    variant(s).

    .. rubric:: Root-cause fix for the "FB 400 XXS" duplication bug

    Previously, ``build_export_from_matches`` iterated every catalogue
    variant row for a matched NUMBER and appended one export row per
    variant, each carrying the full invoice quantity.  This meant an
    invoice line ``FB 400 XXS  qty=2`` produced::

        FB 400 XXS  2X-Small   2
        FB 400 XXS  X-Small    2
        FB 400 XXS  Small      2
        FB 400 XXS  Medium     2

    The duplication arose because:

    1. **Composite key resolution** — the fuzzy matcher returned the plain
       product NUMBER (e.g. ``"TO-FB400"``), so ``vtype`` was empty.
    2. **Variant narrowing failure** — ``_narrow_variants`` could not
       match ``"XXS"`` to any catalogue variant name because ``"2X-Small"``
       was missing from the size-alias table.
    3. **Blind row expansion** — with narrowing returning all rows
       unchanged, every catalogue variant was emitted with the same
       ``Amount``.

    The fix in this function:

    * When ``vtype`` is specified (from a composite match) but cannot be
      resolved via exact or alias lookup, emit **one** row only.
    * When ``vtype`` is empty but the invoice SKU text contains a
      recognised size token not present in the product number (detected
      by :func:`_has_variant_hint`), emit **one** row only.
    * Multiple variant rows are only returned when the invoice line
      genuinely has no variant specificity (e.g. a base SKU like
      ``"PSW 003"`` matching all S / M / L / XL variants).
    """
    if len(group) <= 1:
        return group

    vtype = str(group.iloc[0].get('_matched_vtype', '') or '')
    inv_sku = str(group.iloc[0]['_inv_sku'])
    inv_desc = str(group.iloc[0].get('_inv_desc', '') or '')
    number = str(group.iloc[0]['_matched_number'])

    vt_col = group['VARIANT_TYPES'].fillna('').astype(str).str.strip()
    original_count = len(group)

    # ---- VARIANT_ITEMNUMBER-based narrowing (highest priority) ----
    # When candidate rows have non-empty VARIANT_ITEMNUMBER, try matching
    # the invoice SKU (and its Craft-normalised candidates) directly.
    # This is authoritative and prevents VARIANT_TITLE / VARIANT_TYPES
    # from overriding a correct itemnumber match.
    if 'VARIANT_ITEMNUMBER' in group.columns:
        vi_col = group['VARIANT_ITEMNUMBER'].fillna('').astype(str).str.strip()
        has_vi = vi_col.ne('')
        if has_vi.any():
            candidates_upper: set[str] = set()
            raw_sku = inv_sku.strip().upper()
            if raw_sku:
                candidates_upper.add(raw_sku)
            for craft_form in _craft_sku_candidates(inv_sku):
                candidates_upper.add(craft_form.upper())
            vi_upper = vi_col.str.upper()
            vi_match = vi_upper.isin(candidates_upper)
            if vi_match.sum() == 1:
                return _dedupe_product_rows(group.loc[vi_match])

    # Pre-compute numeric hints from the invoice line for numeric
    # variant matching (e.g. "GTBL 3,0" → {3.0}).
    context_text = inv_sku
    if inv_desc:
        context_text = f"{context_text} {inv_desc}"
    context_nums = _extract_numeric_hints(context_text)
    number_nums = _extract_numeric_hints(number)
    # Only consider hints NOT already in the product number
    extra_nums = context_nums - number_nums

    # ---- Craft-only: EU women numeric size as authoritative filter ----
    # For Craft composite SKUs (digits-digits-digits), check whether the
    # invoice context contains an EU numeric size (34-48) and use it to
    # disambiguate among catalogue variant rows.
    is_craft = bool(_CRAFT_SKU_RE.match(inv_sku.strip()))
    if is_craft:
        eu = _extract_eu_size(context_text)
        if eu is not None:
            eu_pat = re.compile(r'(?<![0-9])' + re.escape(eu) + r'(?![0-9])')
            eu_mask = vt_col.apply(lambda v: bool(eu_pat.search(v)))
            vi_col = group.get('VARIANT_ITEMNUMBER')
            if vi_col is not None:
                vi_str = vi_col.fillna('').astype(str).str.strip()
                eu_mask = eu_mask | vi_str.apply(lambda v: bool(eu_pat.search(v)))
            eu_hits = group.loc[eu_mask]
            if len(eu_hits) == 1:
                return _dedupe_product_rows(eu_hits)
            # >1 or 0 hits: fall through to existing logic

    if vtype:
        # ---- Composite key specified a variant type ----
        # Try exact match on VARIANT_TYPES
        specific = group.loc[vt_col == vtype]
        if specific.empty:
            # Try size-alias match (e.g. "L" ↔ "Large")
            aliases = _size_aliases(vtype)
            if aliases:
                specific = group.loc[vt_col.str.lower().isin(aliases)]
        if not specific.empty:
            return _dedupe_product_rows(specific)

        # Exact/alias match failed — try description-based narrowing
        narrowed = _narrow_variants(group, inv_sku, inv_desc)
        if len(narrowed) < original_count:
            return _dedupe_product_rows(narrowed)

        # Try numeric matching (e.g. vtype "3,0" → 3.0 against "3.5")
        if extra_nums:
            num_narrowed = _numeric_match_variants(group, extra_nums)
            if len(num_narrowed) < original_count:
                return _dedupe_product_rows(num_narrowed)

        # Variant hint existed (vtype was set) but unresolvable.
        # Emit ONE row to prevent duplicated quantity across variants.
        return _dedupe_product_rows(group).head(1)

    # ---- No composite variant type — try description-based narrowing ----
    narrowed = _narrow_variants(group, inv_sku, inv_desc)
    if len(narrowed) < original_count:
        return _dedupe_product_rows(narrowed)

    # Try numeric matching for cases like "GTBL 3,0" where the number
    # 3.0 matches a variant like "285 cm / 3.0"
    if extra_nums:
        num_narrowed = _numeric_match_variants(group, extra_nums)
        if len(num_narrowed) < original_count:
            return _dedupe_product_rows(num_narrowed)

    # Check if the invoice SKU text carries an unresolved variant hint
    # (e.g. "FB 400 XXS" has size token "XXS" not in product number
    # "TO-FB400", or "GTBL 3,0" has numeric "3.0" not in "GTBL")
    # — limit to 1 row if so.
    if _has_variant_hint(inv_sku, number):
        return _dedupe_product_rows(group).head(1)

    # Genuinely no variant info — return all catalogue variants
    return _dedupe_product_rows(group)


def build_export_from_matches(
    products_df: pd.DataFrame,
    match_data: dict,
    *,
    manual_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Build the EAN export DataFrame from pre-computed match results.

    .. rubric:: DataFrame-native pipeline

    The export is constructed in five phases, each expressed as a
    DataFrame operation rather than a parallel Python list:

    1. **Match-level DataFrame** — one row per successfully matched
       invoice SKU, carrying the resolved product NUMBER, composite
       variant type, invoice quantity, description, and match score.
    2. **Catalogue join** — an inner merge with the product catalogue
       on NUMBER expands each match row to include all catalogue
       variants for that product.
    3. **Per-group variant narrowing** — a ``groupby('_inv_sku')``
       with :func:`_narrow_group_variants` selects the correct
       variant(s) for each invoice line:

       - If a composite variant type (``vtype``) was resolved, narrow
         to that variant via exact match or size aliases.
       - Otherwise, use ``_narrow_variants`` (description-based).
       - If narrowing fails but the invoice SKU contains a variant
         hint (detected by :func:`_has_variant_hint`), emit **one**
         row only — preventing spurious duplication.
       - If the invoice line genuinely has no variant specificity,
         keep all variants (business requirement).
    4. **Final deduplication** — a safety-net groupby on
       ``(SKU, Product Number, Variant Name)`` ensures no exact
       duplicate rows remain (handles rare catalogue-level duplication).
    5. **Column formatting** — select and rename to the canonical
       export column order.

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

    # ---- Phase 1: Build match-level intermediate DataFrame ----
    # One row per matched invoice SKU with resolved product NUMBER,
    # composite variant type, invoice quantity/description, and score.
    match_records: list[dict] = []
    for inv_sku, mentry in matches.items():
        matched_key = mentry['sku']
        if matched_key is None:
            continue
        number, vtype = composite_lookup.get(matched_key, (matched_key, ''))
        match_records.append({
            '_inv_sku': inv_sku,
            '_inv_qty': qty_map.get(inv_sku, 1.0),
            '_inv_desc': desc_map.get(inv_sku, ''),
            '_matched_number': number,
            '_matched_vtype': vtype,
            '_score': mentry['score'],
        })

    if not match_records:
        return pd.DataFrame(columns=_EXPORT_COLUMNS)

    match_df = pd.DataFrame(match_records)

    # ---- Phase 2: Join with product catalogue on NUMBER ----
    prods_cols = ['NUMBER', 'VARIANT_TYPES', 'EAN', 'TITLE_DK']
    if 'VARIANT_ITEMNUMBER' in products_df.columns:
        prods_cols.append('VARIANT_ITEMNUMBER')
    prods = products_df[prods_cols].copy()
    prods['_num_key'] = prods['NUMBER'].astype(str).str.strip()

    merged = match_df.merge(
        prods,
        left_on='_matched_number',
        right_on='_num_key',
        how='inner',
    )

    if merged.empty:
        return pd.DataFrame(columns=_EXPORT_COLUMNS)

    # ---- Phase 3: Narrow variants per invoice SKU group ----
    narrowed_parts: list[pd.DataFrame] = []
    for _inv_sku, group in merged.groupby('_inv_sku', sort=False):
        narrowed_parts.append(_narrow_group_variants(group))

    result = pd.concat(narrowed_parts, ignore_index=True)

    # ---- Phase 4: Final safety-net deduplication ----
    result = _normalize_export_df(result)

    # ---- Phase 5: Format output columns ----
    export = pd.DataFrame({
        'SKU': result['_inv_sku'],
        'Product Number': result['NUMBER'].fillna(''),
        'Title': result['TITLE_DK'].fillna(''),
        'Variant Name': result['VARIANT_TYPES'].fillna(''),
        'Amount': result['_inv_qty'],
        'EAN': result['EAN'].fillna(''),
        'Match %': result['_score'],
    })

    return export


def _normalize_export_df(df: pd.DataFrame) -> pd.DataFrame:
    """Safety-net dedup on the merged result before final column formatting.

    Ensures one row per unique ``(_inv_sku, NUMBER, VARIANT_TYPES)``
    combination.  When duplicates exist (e.g. from catalogue-level duplication
    that survived earlier phases), keeps the row with a non-empty EAN.

    .. note::

        This is a lightweight safety net.  The primary correctness guarantee
        comes from the DataFrame-native pipeline in
        :func:`build_export_from_matches` and per-group narrowing in
        :func:`_narrow_group_variants`.
    """
    if df.empty or len(df) <= 1:
        return df

    key_cols = ['_inv_sku', 'NUMBER', 'VARIANT_TYPES']
    if not df.duplicated(subset=key_cols, keep=False).any():
        return df

    deduped_parts: list[pd.DataFrame] = []
    for _, grp in df.groupby(key_cols, sort=False):
        if len(grp) == 1:
            deduped_parts.append(grp)
        else:
            ean_vals = grp['EAN'].fillna('').astype(str).str.strip()
            with_ean = grp.loc[ean_vals.ne('')]
            deduped_parts.append(
                with_ean.head(1) if not with_ean.empty else grp.head(1)
            )

    return pd.concat(deduped_parts, ignore_index=True)


_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}

# --- Size-abbreviation aliases ---
# Each tuple is a group of equivalent size names (all lower-cased).
# Any member can match any other member in the same group.
# Compound sizes include both hyphenated and concatenated forms
# (e.g. "x-large" and "xlarge") because both appear in the wild.
# Simple sizes like "small" or "large" need no concatenated form.

_SIZE_GROUPS: list[tuple[str, ...]] = [
    ('xxs', 'xx-small', 'xxsmall', '2x-small', '2xs'),
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
LLMCallFn = Callable[[str, str, str], "str | None"]


def _build_mapping_prompt(df: pd.DataFrame) -> str:
    """Build a compact prompt for column-mapping from sample rows.

    Encodes the first 5 rows as a Markdown table together with the list
    of target internal fields.  Includes heuristic candidate suggestions
    from :func:`~domain.supplier._guess_candidates` so the LLM has
    additional context about likely matches.
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

    # Include heuristic candidates as hints
    candidates = _guess_candidates(cols)
    hints_lines = []
    for field in ('sku', 'qty', 'price', 'description', 'discount',
                  'currency'):
        if candidates.get(field):
            hints_lines.append(
                f"  {field}: suggested candidates = {candidates[field]}"
            )
    if hints_lines:
        hints_section = (
            "\nHeuristic suggestions (use as hints, not as final answer):\n"
            + '\n'.join(hints_lines) + '\n'
        )
    else:
        hints_section = ''

    return (
        "You are a data-mapping assistant for supplier/invoice files.\n"
        "Given the columns and sample rows below, map each column to one of "
        "the internal fields or null if not mappable.\n\n"
        "Internal fields:\n"
        "- sku: The main product SKU, article number, or product code used to "
        "identify the item. This is NOT the variant/size/colour descriptor. "
        "If the file has a combined column like 'FB 400 XXS' where 'FB 400' "
        "is the base product and 'XXS' is a size variant, this column is still "
        "the best 'sku' candidate.\n"
        "- qty: Quantity or count of units sold/ordered. This is a numeric "
        "count (e.g. 5, 10, 100), NOT a price, monetary amount, or weight. "
        "Typical column names: Quantity, Qty, Antal, Anzahl, Count, Stk, Pcs.\n"
        "- price: Unit cost price or purchase price per item. This is a "
        "monetary value per unit (e.g. 29.95, 150.00). NOT the total line "
        "amount (qty × price). Typical names: Price, Unit Price, Pris, "
        "Enhedspris, Cost, Kostpris.\n"
        "- description: Product name, title, or human-readable designation. "
        "May contain variant info like size or colour as part of the text.\n"
        "- discount: Discount percentage (e.g. 10, 15.5). A column with "
        "'%' or 'rabat' / 'discount' in its name.\n"
        "- currency: Currency code (e.g. EUR, DKK, USD).\n\n"
        "Rules:\n"
        "- Map at most ONE column per internal field.\n"
        "- If a column clearly contains a line-item total (qty × unit price), "
        "do NOT map it to 'price' or 'qty'; map it to null.\n"
        "- If unsure between 'qty' and 'price', look at the typical values: "
        "small integers (1–999) are usually qty; values with decimals or "
        "larger numbers are usually prices.\n\n"
        f"Columns: {list(df.columns)}\n\n"
        f"Sample data:\n{md_table}\n"
        f"{hints_section}\n"
        "Return ONLY a JSON object mapping each column name to its internal "
        "field (or null). "
        "Example: {\"Col1\": \"sku\", \"Col2\": \"price\", \"Col3\": null}\n"
        "Do not include any text outside the JSON object."
    )


def _default_llm_call(
    prompt: str, api_key: str, model: str, *, tenant_id: str | None = None,
) -> str | None:
    """OpenAI-compatible ``/v1/chat/completions`` call using ``requests``.

    Reads ``OPENAI_BASE_URL`` from the environment to allow pointing at
    compatible providers.  Defaults to ``https://api.openai.com``.

    Parameters
    ----------
    tenant_id : str or None
        Optional tenant identifier for usage logging (Task 5.3).
        Keyword-only to preserve backward compatibility with existing callers.
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
        content = data['choices'][0]['message']['content']

        # Log LLM usage with tenant_id (Task 5.3)
        # Token estimate: ~4 chars per token; actual counts may differ for non-ASCII.
        try:
            prompt_tokens = len(prompt) // 4
            response_tokens = len(content) // 4 if content else 0
            _log.info(
                "LLM call completed",
                extra={
                    "tenant_id": tenant_id,
                    "tokens_used": prompt_tokens + response_tokens,
                    "model": model,
                },
            )
        except Exception:
            pass

        return content
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
    tenant_id: str | None = None,
) -> dict[str, str | None] | None:
    """Primary column-mapping mechanism for supplier/invoice DataFrames.

    This is the **canonical entry point** for mapping DataFrame columns to
    internal field names (``sku``, ``qty``, ``price``, ``description``,
    ``discount``, ``currency``).

    Strategy
    --------
    1. **LLM first**: Build a prompt with sample rows and heuristic hints,
       then call the LLM to produce a JSON column mapping.
    2. **Heuristic gap-fill**: If the LLM mapping is missing obviously
       required fields, use :func:`~domain.supplier._guess_candidates` to
       fill in safe, unambiguous gaps.
    3. **Fallback**: When no API key is set, the LLM call fails, or the
       parsed mapping is clearly unusable, the function returns ``None``
       and callers should fall back to pure heuristic detection.

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
    tenant_id : str or None
        Optional tenant identifier for LLM usage tracking (Task 5.3).

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
    in requirements.txt).
    """
    key = api_key or os.environ.get('OPENAI_API_KEY')
    if not key:
        return None

    if df.empty or len(df.columns) == 0:
        return None

    prompt = _build_mapping_prompt(df)
    caller = llm_call or _default_llm_call

    # Pass tenant_id as keyword when using the default LLM caller (Task 5.3)
    if caller is _default_llm_call:
        raw_response = caller(prompt, key, model, tenant_id=tenant_id)
    else:
        raw_response = caller(prompt, key, model)
    mapping = _parse_llm_mapping_response(raw_response, list(df.columns))

    if mapping is None:
        return None

    # --- Heuristic gap-fill for missing required fields ---
    candidates = _guess_candidates(list(df.columns))
    used_cols = set(mapping.values())
    for field in INTERNAL_FIELDS:
        if field not in mapping and candidates.get(field):
            free = [c for c in candidates[field] if c not in used_cols]
            if len(free) == 1:
                mapping[field] = free[0]
                used_cols.add(free[0])

    return mapping


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
    """Generate a label-printer PDF (100 mm × 50 mm landscape, one barcode per page).

    Designed for the ZD421 (or similar) label printer.  Each page
    holds exactly one barcode with concise product metadata.
    Text runs along the longer (100 mm) edge of the label.
    """
    from fpdf import FPDF

    page_w = 100  # mm  (landscape width)
    page_h = 50   # mm  (landscape height)

    pdf = FPDF(orientation='L', unit='mm', format=(page_h, page_w))
    pdf.set_auto_page_break(auto=False)

    def _latin1(text: str) -> str:
        return text.encode('latin-1', 'replace').decode('latin-1')

    barcode_w = 70   # mm – fits within 100 mm page with margins
    barcode_h = 22   # mm
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
        title = str(row.get('Title', ''))[:60]
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
