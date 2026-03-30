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

from typing import Callable, Optional

import pandas as pd

from dandomain_api import DanDomainClient
from domain.pricing import api_products_to_dataframe, _build_brand_id_map


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

    # Build brand_id_map from both raw product data and API-resolved brands
    brand_id_map = _build_brand_id_map(raw_products)
    if brands_map:
        brand_id_map = {**brand_id_map, **brands_map}

    return df, brand_id_map


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
