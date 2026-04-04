"""Shared product-loading pipeline for the Streamlit UI and REST API.

Centralises the logic to fetch products from the DanDomain SOAP API,
resolve brand names, expand variants into individual rows, and apply
inclusion filters (online/offline, brand selection).

The three public functions partition the pipeline so that each consumer
can call the exact subset it needs:

*  ``fetch_products``  — API fetch, brand resolution, variant expansion.
*  ``filter_products`` — online/offline and brand inclusion filters.
*  ``load_products_for_optimization`` — full pipeline (fetch + filter).

Brand filtering
---------------
Brand IDs are **integer** ``ProducerId`` values stored in the
``PRODUCER_ID`` column of the product DataFrame.  The Streamlit
multiselect stores these integers (with a ``format_func`` that
displays the brand name), and the ``/optimize`` endpoint accepts
them via ``brand_ids``.  Filtering is a simple ``isin`` check::

    df[df["PRODUCER_ID"].isin(brand_ids)]

When ``brand_ids`` is ``None`` or empty, all brands are included —
matching the Streamlit default of *"All brands (no filter)"*.
"""

from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import pandas as pd

from dandomain_api import DanDomainClient, DanDomainAPIError, BATCH_DELAY
from domain.pricing import api_products_to_dataframe, _build_brand_id_map

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Debug helper — VARIANT_ITEMNUMBER proof
# ---------------------------------------------------------------------------

def _debug_variant_itemnumber(df: pd.DataFrame) -> None:
    """Log diagnostic info about variant columns in the product DataFrame.

    Logs a health-metric summary (behind INFO level):
    - total rows
    - rows with non-empty VARIANT_ID
    - rows with non-empty VARIANT_ITEMNUMBER
    - rows with non-empty EAN

    **Removal criteria**: remove this function (and its call site in
    ``fetch_products``) once runtime output has confirmed that
    VARIANT_ITEMNUMBER is correctly populated for live shop data.
    Alternatively, keep it as a permanent DEBUG-level diagnostic by
    changing the ``logger.info`` calls to ``logger.debug``.
    """
    total = len(df)
    logger.info("[DEBUG-VARIANT] DataFrame columns: %s", list(df.columns))

    if 'VARIANT_ITEMNUMBER' not in df.columns:
        logger.warning(
            "[DEBUG-VARIANT] VARIANT_ITEMNUMBER column is MISSING from DataFrame"
        )
        return

    def _non_empty(col_name: str) -> int:
        if col_name not in df.columns:
            return 0
        return int(df[col_name].astype(str).str.strip().ne('').sum())

    vid_count = _non_empty('VARIANT_ID')
    vi_count = _non_empty('VARIANT_ITEMNUMBER')
    ean_count = _non_empty('EAN')
    logger.info(
        "[DEBUG-VARIANT] Health: total=%d, VARIANT_ID non-empty=%d, "
        "VARIANT_ITEMNUMBER non-empty=%d, EAN non-empty=%d",
        total, vid_count, vi_count, ean_count,
    )

    sample_cols = [
        c for c in ('NUMBER', 'VARIANT_ID', 'VARIANT_TYPES',
                     'VARIANT_ITEMNUMBER', 'EAN', 'TITLE_DK')
        if c in df.columns
    ]
    sample = df[sample_cols].head(10)
    logger.info("[DEBUG-VARIANT] Sample rows:\n%s", sample.to_string(index=False))


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_products(
    client: DanDomainClient,
    include_variants: bool = True,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> tuple[pd.DataFrame, dict[int, str]]:
    """Fetch products from the DanDomain API and convert to a DataFrame.

    This function mirrors the product-loading logic originally inlined in
    the Dashboard (``ui/pages/home.py``) and the backend
    (``backend/optimizer_api.py``):

    1. ``get_products_batch`` to retrieve all products.
    2. Extract unique ``ProducerId`` values.
    3. ``get_all_brands`` to resolve brand names.
    4. Hydrate the ``Producer`` field on each raw product dict.
    5. Optionally strip variant data (when *include_variants* is False).
    6. ``api_products_to_dataframe`` to expand variants into rows.
    7. Sort by ``PRODUCER`` (case-insensitive).
    8. Build a ``{ProducerId: brand_name}`` mapping.

    Parameters
    ----------
    client : DanDomainClient
        An open (connected) DanDomain SOAP client.
    include_variants : bool
        When True (default), each product variant is expanded into its
        own row.  When False, only one row per base product is returned.
    progress_callback : callable, optional
        Called with the current product count during fetching.

    Returns
    -------
    df : pd.DataFrame
        Product DataFrame with variant expansion, sorted by PRODUCER.
        Empty DataFrame when no products are found.
    brand_id_map : dict[int, str]
        Mapping of ``ProducerId`` → brand name.
    """
    raw_products = client.get_products_batch(
        progress_callback=progress_callback,
    )

    # Extract ProducerIds for brand resolution
    producer_ids: list[int] = []
    for p in raw_products:
        pid = p.get("ProducerId")
        if pid is not None:
            try:
                producer_ids.append(int(pid))
            except (ValueError, TypeError):
                pass

    brands_map = client.get_all_brands(producer_ids=producer_ids)

    # Hydrate Producer on each product using the brands map
    if brands_map:
        for p in raw_products:
            pid = p.get("ProducerId")
            if pid is not None:
                try:
                    brand_name = brands_map.get(int(pid))
                    if brand_name:
                        p["Producer"] = brand_name
                except (ValueError, TypeError):
                    pass

    if not raw_products:
        return pd.DataFrame(), {}

    # Strip variants when requested
    if not include_variants:
        raw_products = [{**p, "Variants": []} for p in raw_products]

    df = api_products_to_dataframe(raw_products)
    df = df.sort_values(
        "PRODUCER", key=lambda s: s.str.lower(),
    ).reset_index(drop=True)

    # --- DEBUG: VARIANT_ITEMNUMBER availability proof ---
    _debug_variant_itemnumber(df)

    # Build brand_id_map from both raw product data and API-resolved brands
    brand_id_map = _build_brand_id_map(raw_products)
    if brands_map:
        brand_id_map = {**brand_id_map, **brands_map}

    return df, brand_id_map


# ---------------------------------------------------------------------------
# Variant enrichment
# ---------------------------------------------------------------------------

def enrich_variants(
    df: pd.DataFrame,
    client: DanDomainClient,
) -> pd.DataFrame:
    """Enrich product DataFrame with variant-level data from the API.

    For each unique base product ``NUMBER`` in *df*, calls
    ``Product_GetVariantsByItemNumber`` (read-only) and populates:

    - ``VARIANT_ITEMNUMBER`` — the variant's own item number / SKU.
    - ``VARIANT_TITLE`` — the variant's title (VariantTypeValues
      concatenated with `` // `` per the HostedShop docs ``$Title``
      field).
    - ``VARIANT_EAN`` — the variant's EAN if available per variant.

    When the initial product fetch already populated
    ``VARIANT_ITEMNUMBER`` (from the ``Variants`` array included in
    ``Product_GetAll``), rows that already have a non-empty value are
    left untouched.

    **Safety**: read-only, cached by base item number, respects
    ``BATCH_DELAY`` between calls, catches all errors gracefully.
    On any failure the DataFrame is returned unchanged.
    """
    if df.empty:
        return df

    # Ensure target columns exist
    for col in ('VARIANT_ITEMNUMBER', 'VARIANT_TITLE', 'VARIANT_EAN'):
        if col not in df.columns:
            df[col] = ''

    # Identify rows that still need enrichment: have a VARIANT_ID but
    # no VARIANT_ITEMNUMBER yet.
    needs_enrichment = (
        df['VARIANT_ID'].astype(str).str.strip().ne('')
        & df['VARIANT_ITEMNUMBER'].astype(str).str.strip().eq('')
    )
    if not needs_enrichment.any():
        return df

    # Collect unique base item numbers that have un-enriched variants.
    base_numbers = (
        df.loc[needs_enrichment, 'NUMBER']
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .tolist()
    )

    # Cache: base_item_number → {variant_id_str: {itemnumber, title, ean}}
    variant_cache: dict[str, dict[str, dict[str, str]]] = {}

    for base_num in base_numbers:
        if not base_num:
            continue
        try:
            raw_variants = client.get_variants_by_item_number(base_num)
        except Exception:
            logger.debug(
                "Variant enrichment failed for base %s — skipping",
                base_num,
            )
            continue

        by_id: dict[str, dict[str, str]] = {}
        for rv in raw_variants:
            vid = str(rv.get('Id', '') or '').strip()
            if not vid:
                continue
            by_id[vid] = {
                'itemnumber': str(rv.get('ItemNumber', '') or '').strip(),
                'title': str(rv.get('Title', '') or '').strip(),
                'ean': str(rv.get('Ean', '') or '').strip(),
            }
        variant_cache[base_num] = by_id

        # Delay between SOAP calls.  When the client has a caller_key
        # the delay is enforced by the SOAP limiter (Task 3.3);
        # otherwise fall back to the legacy BATCH_DELAY sleep.
        if not getattr(client, "caller_key", None):
            time.sleep(BATCH_DELAY)

    # Merge enrichment data into the DataFrame
    df = df.copy()
    for idx in df.index:
        if not needs_enrichment.at[idx]:
            continue
        base_num = str(df.at[idx, 'NUMBER'] or '').strip()
        vid = str(df.at[idx, 'VARIANT_ID'] or '').strip()
        by_id = variant_cache.get(base_num, {})
        vdata = by_id.get(vid, {})
        if vdata:
            if vdata.get('itemnumber'):
                df.at[idx, 'VARIANT_ITEMNUMBER'] = vdata['itemnumber']
            if vdata.get('title'):
                df.at[idx, 'VARIANT_TITLE'] = vdata['title']
            if vdata.get('ean'):
                df.at[idx, 'VARIANT_EAN'] = vdata['ean']

    return df


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

def filter_products(
    df: pd.DataFrame,
    include_offline: bool = False,
    brand_ids: Optional[list[int]] = None,
) -> pd.DataFrame:
    """Apply online/offline and brand filters to a product DataFrame.

    The filter semantics exactly match the Streamlit Price Optimizer
    (``ui/pages/price_optimizer.py``):

    *  **Online filter** — when *include_offline* is ``False`` (the
       default), only rows with ``ONLINE == True`` are kept.  This
       corresponds to the *"Only active (online) products"* checkbox
       in the Streamlit UI, which defaults to checked.
    *  **Brand filter** — when *brand_ids* is a non-empty list of
       integer ``ProducerId`` values, only rows whose ``PRODUCER_ID``
       is in the list are kept.  This corresponds to the brand
       multiselect in the Streamlit UI.  When ``None`` or empty, all
       brands are included.

    Parameters
    ----------
    df : pd.DataFrame
        Product DataFrame (as returned by ``fetch_products``).
    include_offline : bool
        When False (default), only online products are kept.
    brand_ids : list[int], optional
        Filter to specific brand / producer IDs.

    Returns
    -------
    pd.DataFrame
        Filtered copy of the input DataFrame.
    """
    result = df.copy()
    if not include_offline and "ONLINE" in result.columns:
        result = result[result["ONLINE"]].reset_index(drop=True)
    if brand_ids:
        result = result[
            result["PRODUCER_ID"].isin(brand_ids)
        ].reset_index(drop=True)
    return result


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def load_products_for_optimization(
    client: DanDomainClient,
    site_id: int,
    include_offline: bool = False,
    include_variants: bool = True,
    brand_ids: Optional[list[int]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> pd.DataFrame:
    """Fetch, convert, and filter products — the complete pipeline.

    Returns the same DataFrame that the Streamlit Price Optimizer uses:
    same variant expansion, same online/offline behaviour, and same
    brand filtering.

    Parameters
    ----------
    client : DanDomainClient
        An open (connected) DanDomain SOAP client.
    site_id : int
        Site / shop ID (reserved for future per-site filtering).
    include_offline : bool
        When False (default), only online products are included.
    include_variants : bool
        When True (default), variants are expanded into separate rows.
    brand_ids : list[int], optional
        Filter to specific brand / producer IDs.
    progress_callback : callable, optional
        Called with the current product count during fetching.

    Returns
    -------
    pd.DataFrame
        Filtered product DataFrame ready for the pricing pipeline.
    """
    df, _brand_id_map = fetch_products(
        client,
        include_variants=include_variants,
        progress_callback=progress_callback,
    )
    return filter_products(
        df,
        include_offline=include_offline,
        brand_ids=brand_ids,
    )
