"""Coverage Converter module — API import, analysis, and push-to-shop."""

from __future__ import annotations

import time

import streamlit as st
import pandas as pd
import numpy as np

from dandomain_api import DanDomainClient, DanDomainAPIError
from push_safety import build_push_updates

from domain.pricing import (
    VAT_RATE,
    MIN_COVERAGE_RATE,
    REQUIRED_COLUMNS,
    EXPORT_COLUMNS,
    IMPORT_COLUMNS_BASE,
    clean_price,
    format_dk,
    calc_coverage_rate,
    optimize_prices,
    api_products_to_dataframe,
    _build_brand_id_map,
)
from domain.supplier import (
    DEFAULT_CURRENCY_RATES,
    ENCODING_OPTIONS,
    parse_supplier_file,
    detect_supplier_columns,
    match_supplier_to_products,
    detect_discount_lines,
)

# Columns shown in the simplified (default) table view.
_SIMPLE_COLUMNS = [
    'TITLE_DK', 'NUMBER', 'PRODUCER',
    'BUY_PRICE', 'PRICE', 'COVERAGE_RATE_%',
    'NEW_PRICE', 'NEW_COVERAGE_RATE_%',
]


def render(
    api_username: str,
    api_password: str,
    api_ready: bool,
    site_id: int,
    dry_run: bool,
) -> None:
    """Render the full Coverage Converter page."""

    st.markdown(
        '<h1 class="hero-header">Coverage Converter</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hero-sub">'
        f"Import products from the DanDomain API, calculate coverage rates, "
        f"and automatically adjust prices to at least a "
        f"<strong>{int(MIN_COVERAGE_RATE * 100)}%</strong> profit margin. "
        f"Variant-aware — handles products with multiple variants correctly."
        f"</p>",
        unsafe_allow_html=True,
    )

    # --- Price Rules (local to Coverage Converter) ---
    with st.expander("Price Rules", expanded=False):
        pr_col1, pr_col2 = st.columns(2)
        with pr_col1:
            price_pct = st.number_input(
                "Adjust Sales Price (%)",
                min_value=-50.0,
                max_value=200.0,
                value=0.0,
                step=0.5,
                help=(
                    "Increase or decrease all sales prices by this percentage "
                    "before recalculating coverage rates."
                ),
                key="_cc_price_pct",
            )
        with pr_col2:
            include_buy_price = st.checkbox(
                "Include BUY_PRICE in import file",
                value=False,
                help="When checked, the import-ready CSV will contain the BUY_PRICE column.",
                key="_cc_include_bp",
            )

    parsed_df = None
    _import_error = False

    if not api_ready:
        st.markdown(
            '<div class="info-card">'
            "<h4>API Not Connected</h4>"
            "<p>Configure your DanDomain API credentials in the sidebar "
            "to import products directly from your webshop.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        parsed_df, _import_error = _render_api_import(
            api_username, api_password,
        )

    if parsed_df is not None and not _import_error:
        _render_analysis(
            parsed_df,
            api_username,
            api_password,
            api_ready,
            site_id,
            dry_run,
            price_pct,
            include_buy_price,
        )


# ---------------------------------------------------------------------------
# API Import section
# ---------------------------------------------------------------------------

def _render_api_import(
    api_username: str,
    api_password: str,
) -> tuple[pd.DataFrame | None, bool]:
    """Render the API fetch controls and return (parsed_df, error_flag)."""
    _import_error = False
    parsed_df = None

    _api_brands_available = st.session_state.get("_api_brands", [])
    _api_brand_id_map = st.session_state.get("_api_brand_id_map", {})

    def _brand_label(pid: int) -> str:
        return _api_brand_id_map.get(pid, f"Unknown ({pid})")

    api_filter_col1, api_filter_col2 = st.columns(2)
    with api_filter_col1:
        selected_brands = st.multiselect(
            "Filter by brand / producer",
            options=_api_brands_available,
            format_func=_brand_label,
            default=[],
            key="_brand_filter",
            placeholder=(
                "All brands (no filter)"
                if _api_brands_available
                else "Fetch products first"
            ),
            disabled=not _api_brands_available,
            help=(
                "Select one or more brands to include. "
                "Leave empty to include all brands."
            ),
        )
    with api_filter_col2:
        only_online = st.checkbox(
            "Only active (online) products",
            value=True,
            help="When checked, only products marked as 'online' are imported.",
        )

    if st.button("Fetch Products from API", type="primary"):
        try:
            with st.spinner("Fetching products from the API…"):
                progress_text = st.empty()

                def _api_progress(count):
                    progress_text.text(f"Fetched {count} products…")

                _use_brand_fetch = bool(
                    selected_brands and _api_brand_id_map
                )

                with DanDomainClient(api_username, api_password) as client:
                    if _use_brand_fetch:
                        raw_products = []
                        for bid in selected_brands:
                            raw_products.extend(
                                client.get_products_by_brand(
                                    brand_id=bid,
                                    progress_callback=_api_progress,
                                )
                            )
                    else:
                        raw_products = client.get_products_batch(
                            progress_callback=_api_progress,
                        )

                    _producer_ids = []
                    for p in raw_products:
                        _pid = p.get("ProducerId")
                        if _pid is not None:
                            try:
                                _producer_ids.append(int(_pid))
                            except (ValueError, TypeError):
                                pass
                    _brands_map = client.get_all_brands(
                        producer_ids=_producer_ids,
                    )

                progress_text.empty()

            # Hydrate Producer on each product using the brands map
            if _brands_map:
                for p in raw_products:
                    pid = p.get("ProducerId")
                    if pid is not None:
                        try:
                            _bname = _brands_map.get(int(pid))
                            if _bname:
                                p["Producer"] = _bname
                        except (ValueError, TypeError):
                            pass

            if only_online:
                raw_products = [
                    p for p in raw_products
                    if p.get('Status') is not False
                    and str(p.get('Status', '')).lower()
                        not in ('false', '0', 'no')
                ]

            if not raw_products:
                st.warning("No products matched the selected filters.")
            else:
                raw_df = api_products_to_dataframe(raw_products)
                raw_df = raw_df.sort_values(
                    "PRODUCER", key=lambda s: s.str.lower(),
                ).reset_index(drop=True)

                brand_id_map = _build_brand_id_map(raw_products)

                if _use_brand_fetch:
                    existing = st.session_state.get("_api_raw_df")
                    if existing is not None:
                        other = existing[
                            ~existing["PRODUCER_ID"].isin(
                                selected_brands
                            )
                        ]
                        raw_df = pd.concat(
                            [other, raw_df], ignore_index=True,
                        ).sort_values(
                            "PRODUCER",
                            key=lambda s: s.str.lower(),
                        ).reset_index(drop=True)
                    prev_map = st.session_state.get(
                        "_api_brand_id_map", {},
                    )
                    brand_id_map = {**prev_map, **brand_id_map}

                st.session_state["_api_raw_df"] = raw_df
                if _brands_map:
                    brand_id_map = {**brand_id_map, **_brands_map}
                st.session_state["_api_brand_id_map"] = brand_id_map
                st.session_state["_api_brands"] = sorted(
                    brand_id_map.keys(),
                    key=lambda pid: brand_id_map[pid].lower(),
                )

                st.success(
                    f"Loaded **{len(raw_df)}** product rows "
                    f"({len(raw_products)} base products)."
                )
        except (DanDomainAPIError, ValueError, AttributeError) as exc:
            st.error(f"API import failed: {exc}")
            _import_error = True

    # Derive parsed_df from cached data, applying brand filter
    if "_api_raw_df" in st.session_state:
        _raw_df = st.session_state["_api_raw_df"]
        if selected_brands:
            parsed_df = _raw_df[
                _raw_df["PRODUCER_ID"].isin(selected_brands)
            ].reset_index(drop=True)
        else:
            parsed_df = _raw_df

    return parsed_df, _import_error


# ---------------------------------------------------------------------------
# Analysis, Data Tabs, Downloads, Push
# ---------------------------------------------------------------------------

def _render_analysis(
    parsed_df: pd.DataFrame,
    api_username: str,
    api_password: str,
    api_ready: bool,
    site_id: int,
    dry_run: bool,
    price_pct: float,
    include_buy_price: bool,
) -> None:
    """Render analysis results, data tabs, downloads, and push-to-shop."""
    # --- Apply persisted BUY_PRICE edits from data-editor state ---
    work_df = parsed_df.copy()
    for key in ("_ed_all", "_ed_adj", "_ed_imp"):
        for row_str, changes in (
            st.session_state.get(key, {}).get("edited_rows", {}).items()
        ):
            if "BUY_PRICE" not in changes:
                continue
            row_idx = int(row_str)
            if key == "_ed_adj" and "_adj_index_map" in st.session_state:
                idx_map = st.session_state["_adj_index_map"]
                if row_idx < len(idx_map):
                    row_idx = idx_map[row_idx]
                else:
                    continue
            if key == "_ed_imp" and "_imp_index_map" in st.session_state:
                idx_map = st.session_state["_imp_index_map"]
                if row_idx < len(idx_map):
                    row_idx = idx_map[row_idx]
                else:
                    continue
            if row_idx in work_df.index:
                work_df.at[row_idx, 'BUY_PRICE_NUM'] = changes["BUY_PRICE"]

    final_df, adjusted_count, adjusted_mask, import_df = optimize_prices(
        work_df, price_pct, original_buy_prices=parsed_df['BUY_PRICE_NUM'],
    )

    # Include PRODUCER (brand) column when available (API import).
    if 'PRODUCER' in work_df.columns:
        _pos = final_df.columns.get_loc('NUMBER') + 1
        final_df.insert(_pos, 'PRODUCER', work_df['PRODUCER'].values)

    # --- Summary Metrics ---
    total = len(final_df)
    unchanged = total - adjusted_count
    st.markdown("")
    mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
    mcol1.metric("Total Products", f"{total:,}")
    mcol2.metric("Prices Adjusted", f"{adjusted_count:,}")
    mcol3.metric("Unchanged", f"{unchanged:,}")
    adj_pct = (adjusted_count / total * 100) if total else 0
    mcol4.metric("Adjusted %", f"{adj_pct:.1f}%" if total else "—")

    if total:
        base_ex_vat = parsed_df['PRICE_NUM'] / (1 + VAT_RATE)
        base_coverage = calc_coverage_rate(
            parsed_df.assign(PRICE_EX_VAT_NUM=base_ex_vat),
            'PRICE_EX_VAT_NUM', 'BUY_PRICE_NUM',
        )
        base_avg = (base_coverage * 100).mean()
        cov_vals = (
            final_df['COVERAGE_RATE_%']
            .str.replace('%', '', regex=False)
            .str.replace(',', '.', regex=False)
            .astype(float)
        )
        adj_avg = float(cov_vals.mean())
        delta = adj_avg - base_avg
        mcol5.metric(
            "Avg Coverage",
            f"{adj_avg:.1f}%",
            delta=f"{delta:+.1f}%" if price_pct != 0 else None,
        )
    st.markdown("")

    # --- Data Tabs ---
    _buy_price_col = "BUY_PRICE"
    _display_all = final_df.copy()
    _display_all[_buy_price_col] = work_df['BUY_PRICE_NUM']

    _disabled_cols = [
        c for c in _display_all.columns if c != _buy_price_col
    ]
    _col_config = {
        _buy_price_col: st.column_config.NumberColumn(
            "BUY_PRICE",
            help="Cost price -- edit to match current supplier price",
            format="%.2f",
            min_value=0.0,
        ),
    }

    # --- Advanced columns toggle ---
    show_advanced = st.checkbox(
        "Advanced columns",
        value=False,
        help="Show all columns including IDs, ex-VAT prices, and variant details.",
        key="_show_advanced_cols",
    )

    # Determine which columns to display
    if show_advanced:
        _visible_cols = list(_display_all.columns)
    else:
        _visible_cols = [
            c for c in _SIMPLE_COLUMNS if c in _display_all.columns
        ]
        # Always include BUY_PRICE even if not in the simple list
        if _buy_price_col not in _visible_cols:
            _visible_cols.insert(0, _buy_price_col)

    tab_all, tab_adjusted, tab_import, tab_supplier = st.tabs([
        "All Products",
        "Adjusted Only",
        "Import Preview",
        "Supplier Match",
    ])

    with tab_all:
        st.data_editor(
            _display_all[_visible_cols],
            disabled=[c for c in _visible_cols if c != _buy_price_col],
            column_config=_col_config,
            use_container_width=True,
            hide_index=True,
            key="_ed_all",
        )

    with tab_adjusted:
        _adj_full = _display_all[adjusted_mask]
        _display_adj = _adj_full[_visible_cols].reset_index(drop=True)
        st.session_state["_adj_index_map"] = list(
            _display_all.index[adjusted_mask]
        )
        if _display_adj.empty:
            st.info("All products already meet the minimum margin -- no adjustments needed.")
        else:
            st.data_editor(
                _display_adj,
                disabled=[c for c in _visible_cols if c != _buy_price_col],
                column_config=_col_config,
                use_container_width=True,
                hide_index=True,
                key="_ed_adj",
            )

    with tab_import:
        if import_df.empty:
            st.info("No products needed adjustment -- nothing to import.")
        else:
            adjusted_full = _display_all[adjusted_mask]
            st.session_state["_imp_index_map"] = list(
                _display_all.index[adjusted_mask]
            )
            st.markdown(
                f"**{adjusted_count}** product"
                f"{'s' if adjusted_count != 1 else ''} "
                "will be included in the import file. "
                "Variant ID and Variant Types are included to ensure "
                "the correct product/variant is targeted."
            )
            preview_df = pd.DataFrame({
                'Product ID': adjusted_full['PRODUCT_ID'].values,
                'Title': adjusted_full['TITLE_DK'].values,
                'Number': adjusted_full['NUMBER'].values,
                'BUY_PRICE': adjusted_full[_buy_price_col].values,
                'Variant ID': adjusted_full['VARIANT_ID'].values,
                'Variant Types': adjusted_full['VARIANT_TYPES'].values,
                'Old Price': adjusted_full['PRICE'].values,
                'New Price': adjusted_full['NEW_PRICE'].values,
                'Old Coverage': adjusted_full['COVERAGE_RATE_%'].values,
                'New Coverage': adjusted_full['NEW_COVERAGE_RATE_%'].values,
            })
            st.data_editor(
                preview_df,
                disabled=[
                    c for c in preview_df.columns if c != _buy_price_col
                ],
                use_container_width=True,
                hide_index=True,
                key="_ed_imp",
                column_config={
                    'Product ID': st.column_config.TextColumn(width='small'),
                    'Title': st.column_config.TextColumn(width='medium'),
                    'Number': st.column_config.TextColumn(width='small'),
                    _buy_price_col: st.column_config.NumberColumn(
                        "BUY_PRICE",
                        help="Cost price -- edit to match current supplier price",
                        format="%.2f",
                        min_value=0.0,
                    ),
                    'Variant ID': st.column_config.TextColumn(width='small'),
                    'Variant Types': st.column_config.TextColumn(width='small'),
                    'Old Price': st.column_config.TextColumn(
                        'Old Price', width='small',
                    ),
                    'New Price': st.column_config.TextColumn(
                        'New Price', width='small',
                    ),
                    'Old Coverage': st.column_config.TextColumn(
                        'Old Coverage', width='small',
                    ),
                    'New Coverage': st.column_config.TextColumn(
                        'New Coverage', width='small',
                    ),
                },
            )

    # --- Supplier Match Tab ---
    with tab_supplier:
        _render_supplier_match(work_df)

    # --- Downloads ---
    _render_downloads(final_df, import_df, adjusted_count, include_buy_price)

    # --- Push to Shop ---
    if not import_df.empty:
        _render_push_to_shop(
            final_df,
            adjusted_mask,
            parsed_df,
            work_df,
            api_username,
            api_password,
            api_ready,
            site_id,
            dry_run,
        )


# ---------------------------------------------------------------------------
# Supplier Match
# ---------------------------------------------------------------------------

def _render_supplier_match(work_df: pd.DataFrame) -> None:
    """Render the supplier file match tab."""
    st.markdown(
        "Upload a **supplier price list** (CSV or PDF) to automatically "
        "match SKUs and update cost prices. Supports fuzzy SKU matching, "
        "multiple currencies, and discount detection."
    )

    sup_file = st.file_uploader(
        "Upload Supplier Price List",
        type=['csv', 'pdf'],
        key="_supplier_file",
        label_visibility="collapsed",
    )

    # Encoding selection under Advanced (auto-detect by default)
    with st.expander("Advanced: File Encoding", expanded=False):
        encoding_label = st.selectbox(
            "CSV file encoding",
            options=list(ENCODING_OPTIONS.keys()),
            index=0,
            help=(
                "Choose 'Auto-detect' to let the app guess the encoding, "
                "or pick a specific one if Danish characters look wrong."
            ),
            key="_supplier_encoding",
        )
    selected_encoding = ENCODING_OPTIONS[encoding_label]

    if sup_file is not None:
        try:
            sup_bytes = sup_file.getvalue()
            sup_df = parse_supplier_file(
                sup_bytes, sup_file.name, selected_encoding,
            )
        except Exception as exc:
            st.error(f"Failed to parse supplier file: {exc}")
            sup_df = None

        if sup_df is not None and not sup_df.empty:
            detected = detect_supplier_columns(sup_df)

            col_names = ['(none)'] + list(sup_df.columns)
            scol1, scol2, scol3 = st.columns(3)
            with scol1:
                sku_idx = (
                    col_names.index(detected['sku'])
                    if detected['sku'] in col_names else 0
                )
                sup_sku_col = st.selectbox(
                    "SKU column", col_names, index=sku_idx,
                    help="Column containing the product SKU / article number.",
                )
            with scol2:
                price_idx = (
                    col_names.index(detected['price'])
                    if detected['price'] in col_names else 0
                )
                sup_price_col = st.selectbox(
                    "Price column", col_names, index=price_idx,
                    help="Column containing the unit cost price.",
                )
            with scol3:
                disc_idx = (
                    col_names.index(detected['discount'])
                    if detected['discount'] in col_names else 0
                )
                sup_disc_col = st.selectbox(
                    "Discount column", col_names, index=disc_idx,
                    help="Column containing discount percentages (optional).",
                )

            cur_col1, cur_col2, cur_col3 = st.columns(3)
            with cur_col1:
                det_currency = 'EUR'
                if detected['currency'] and detected['currency'] in sup_df.columns:
                    cur_vals = sup_df[detected['currency']].dropna()
                    if not cur_vals.empty:
                        first_val = str(cur_vals.iloc[0]).upper().strip()
                        if first_val in DEFAULT_CURRENCY_RATES:
                            det_currency = first_val
                currency_list = list(DEFAULT_CURRENCY_RATES.keys())
                sup_currency = st.selectbox(
                    "Source currency",
                    currency_list,
                    index=currency_list.index(det_currency),
                    help="Currency of the prices in the supplier file.",
                )
            with cur_col2:
                default_rate = DEFAULT_CURRENCY_RATES.get(sup_currency, 1.0)
                exchange_rate = st.number_input(
                    f"Rate → DKK",
                    min_value=0.001,
                    max_value=9999.0,
                    value=default_rate,
                    step=0.01,
                    format="%.4f",
                    help=(
                        f"Exchange rate from {sup_currency} to DKK. "
                        "Adjust if the default rate is outdated."
                    ),
                )
            with cur_col3:
                match_threshold = st.slider(
                    "Match threshold",
                    min_value=50,
                    max_value=100,
                    value=70,
                    help=(
                        "Minimum similarity score (%) for fuzzy SKU "
                        "matching. Lower = more matches but less precise."
                    ),
                )

            if sup_sku_col != '(none)' and sup_price_col != '(none)':
                supplier_skus = (
                    sup_df[sup_sku_col].dropna()
                    .astype(str).str.strip()
                    .loc[lambda s: s != '']
                )
                product_skus = work_df['NUMBER'].dropna().astype(str).str.strip()

                matches = match_supplier_to_products(
                    supplier_skus.tolist(),
                    product_skus.tolist(),
                    threshold=match_threshold,
                )

                disc_col_name = (
                    sup_disc_col if sup_disc_col != '(none)' else None
                )
                disc_lines = detect_discount_lines(sup_df, disc_col_name)

                if disc_lines:
                    with st.expander(
                        f"{len(disc_lines)} discount line(s) detected",
                        expanded=False,
                    ):
                        disc_df = pd.DataFrame(disc_lines)
                        st.dataframe(
                            disc_df, use_container_width=True,
                            hide_index=True,
                        )

                if not matches:
                    st.warning(
                        "No SKU matches found. Try lowering the "
                        "match threshold or checking the SKU column."
                    )
                else:
                    match_rows = _build_match_rows(
                        matches, sup_df, sup_sku_col, sup_price_col,
                        disc_lines, exchange_rate, sup_currency, work_df,
                    )

                    if match_rows:
                        match_result_df = pd.DataFrame(match_rows)
                        display_cols = [
                            c for c in match_result_df.columns
                            if not c.startswith('_')
                        ]
                        st.markdown(
                            f"**{len(match_rows)}** SKU match"
                            f"{'es' if len(match_rows) != 1 else ''} "
                            f"found"
                        )
                        st.dataframe(
                            match_result_df[display_cols],
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                'Score': st.column_config.ProgressColumn(
                                    'Match %',
                                    min_value=0,
                                    max_value=100,
                                    format="%d%%",
                                ),
                                'Diff': st.column_config.NumberColumn(
                                    'Diff (DKK)',
                                    format="%.2f",
                                ),
                            },
                        )

                        if st.button(
                            "Update Cost Prices from Supplier",
                            type="primary",
                            use_container_width=True,
                            key="_apply_supplier",
                        ):
                            _apply_supplier_prices(match_rows, work_df)
            else:
                st.info(
                    "Select the **SKU** and **Price** columns above "
                    "to start matching."
                )
    else:
        st.info(
            "Upload a supplier price list (CSV or PDF) to match "
            "SKUs and update cost prices automatically."
        )


def _build_match_rows(
    matches, sup_df, sup_sku_col, sup_price_col,
    disc_lines, exchange_rate, sup_currency, work_df,
):
    """Build the match result rows for supplier matching."""
    match_rows = []
    for sup_sku, (prod_sku, score) in matches.items():
        sup_row = sup_df.loc[
            sup_df[sup_sku_col].astype(str).str.strip() == sup_sku
        ]
        if sup_row.empty:
            continue
        raw_price = str(sup_row.iloc[0][sup_price_col])
        price_val = clean_price(raw_price)

        row_idx = sup_row.index[0]
        disc_pct = 0.0
        for d in disc_lines:
            if d['row'] == row_idx:
                disc_pct = d['discount_pct']
                break
        if disc_pct > 0:
            price_val = price_val * (1 - disc_pct / 100)

        price_dkk = price_val * exchange_rate

        prod_mask = (
            work_df['NUMBER'].astype(str).str.strip() == prod_sku
        )
        current_cost = 0.0
        if prod_mask.any():
            current_cost = work_df.loc[
                prod_mask, 'BUY_PRICE_NUM'
            ].iloc[0]

        prod_price_row = work_df.loc[prod_mask]
        if not prod_price_row.empty:
            sell_ex_vat = (
                prod_price_row['PRICE_NUM'].iloc[0] / (1 + VAT_RATE)
            )
            old_cov = (
                ((sell_ex_vat - current_cost) / sell_ex_vat * 100)
                if sell_ex_vat > 0 else 0.0
            )
            new_cov = (
                ((sell_ex_vat - price_dkk) / sell_ex_vat * 100)
                if sell_ex_vat > 0 else 0.0
            )
        else:
            old_cov = 0.0
            new_cov = 0.0

        match_rows.append({
            'Supplier SKU': sup_sku,
            'Product SKU': prod_sku,
            'Score': score,
            f'Price ({sup_currency})': round(price_val, 2),
            'Discount %': disc_pct if disc_pct > 0 else '',
            'Price (DKK)': round(price_dkk, 2),
            'Current Cost': round(current_cost, 2),
            'Diff': round(price_dkk - current_cost, 2),
            'Old Coverage %': round(old_cov, 1),
            'New Coverage %': round(new_cov, 1),
            '_prod_sku': prod_sku,
            '_new_cost': price_dkk,
        })

    return match_rows


def _apply_supplier_prices(match_rows, work_df):
    """Apply supplier prices to the work DataFrame."""
    updated = 0
    for row in match_rows:
        prod_sku = row['_prod_sku']
        new_cost = row['_new_cost']
        mask = (
            work_df['NUMBER'].astype(str).str.strip() == prod_sku
        )
        if mask.any():
            work_df.loc[mask, 'BUY_PRICE_NUM'] = new_cost
            updated += mask.sum()

    if updated:
        if "_ed_all" not in st.session_state:
            st.session_state["_ed_all"] = {"edited_rows": {}}
        elif "edited_rows" not in st.session_state["_ed_all"]:
            st.session_state["_ed_all"]["edited_rows"] = {}
        edits = st.session_state["_ed_all"]["edited_rows"]
        for row in match_rows:
            prod_sku = row['_prod_sku']
            mask = (
                work_df['NUMBER'].astype(str).str.strip() == prod_sku
            )
            for idx in work_df.index[mask]:
                edits[str(idx)] = {"BUY_PRICE": row['_new_cost']}
        st.success(
            f"Updated cost prices for {updated} product row(s). "
            f"Changes are reflected in all tabs."
        )
        st.rerun()
    else:
        st.warning("No products were updated.")


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def _render_downloads(final_df, import_df, adjusted_count, include_buy_price):
    """Render the download section."""
    st.markdown(
        '<div class="section-header" style="margin-top:1rem;">'
        'Download Reports</div>',
        unsafe_allow_html=True,
    )
    dl_col1, dl_col2 = st.columns(2, gap="small")

    csv_preview = "\ufeff" + "PRODUCTS\n" + final_df.to_csv(sep=';', index=False)
    with dl_col1:
        st.download_button(
            label="Preview -- Full Report CSV",
            data=csv_preview.encode('utf-8'),
            file_name="preview_products.csv",
            mime="text/csv; charset=utf-8",
        )

    import_cols = IMPORT_COLUMNS_BASE.copy()
    if include_buy_price:
        import_cols.insert(3, 'BUY_PRICE')

    if import_df.empty:
        with dl_col2:
            st.download_button(
                label="Import-Ready CSV",
                data="",
                file_name="import_products.csv",
                mime="text/csv; charset=utf-8",
                disabled=True,
            )
            st.caption("No products needed adjustment.")
    else:
        csv_import = (
            "\ufeff" + "PRODUCTS\n"
            + import_df[import_cols].to_csv(sep=';', index=False)
        )
        with dl_col2:
            st.download_button(
                label="Import-Ready CSV",
                data=csv_import.encode('utf-8'),
                file_name="import_products.csv",
                mime="text/csv; charset=utf-8",
            )


# ---------------------------------------------------------------------------
# Push to Shop
# ---------------------------------------------------------------------------

def _render_push_to_shop(
    final_df,
    adjusted_mask,
    parsed_df,
    work_df,
    api_username,
    api_password,
    api_ready,
    site_id,
    dry_run,
):
    """Render the push-to-shop section with safety gates."""
    st.divider()
    st.markdown(
        '<div class="section-header">Push to Shop</div>',
        unsafe_allow_html=True,
    )

    if not api_ready:
        st.markdown(
            '<div class="info-card">'
            "<h4>API Not Connected</h4>"
            "<p>Configure your DanDomain API credentials in the sidebar "
            "to enable direct price updates to your live webshop.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    all_potential_updates = build_push_updates(
        final_df,
        adjusted_mask,
        parsed_df['BUY_PRICE_NUM'],
        work_df['BUY_PRICE_NUM'],
        selected_indices=None,
    )

    if not all_potential_updates:
        st.info(
            "No products have actual price or cost "
            "changes to push."
        )
        return

    mode_pill = (
        '<span class="status-pill dry">DRY-RUN</span>'
        if dry_run
        else '<span class="status-pill live">LIVE</span>'
    )

    # --- Product selection table ---
    sel_rows: list[dict] = []
    for u in all_potential_updates:
        changes: list[str] = []
        if "new_price" in u:
            changes.append(
                f"Price: {format_dk(u['old_price'])} → "
                f"{format_dk(u['new_price'])}"
            )
        if "buy_price" in u:
            old_bp = u.get("old_buy_price")
            if old_bp is not None:
                changes.append(
                    f"BuyPrice: {format_dk(old_bp)} → "
                    f"{format_dk(u['buy_price'])}"
                )
            else:
                changes.append(
                    f"BuyPrice: → {format_dk(u['buy_price'])}"
                )
        sel_rows.append({
            "Push": True,
            "Product ID": u["product_id"],
            "Number": u["product_number"],
            "Title": u.get("title", ""),
            "Variant ID": u.get("variant_id", ""),
            "Changes": " · ".join(changes),
            "Endpoint": u.get("endpoint", ""),
        })

    sel_df = pd.DataFrame(sel_rows)
    edited_sel = st.data_editor(
        sel_df,
        disabled=[c for c in sel_df.columns if c != "Push"],
        column_config={
            "Push": st.column_config.CheckboxColumn(
                "Push", default=True,
            ),
            "Product ID": st.column_config.TextColumn(width="small"),
            "Number": st.column_config.TextColumn(width="small"),
            "Title": st.column_config.TextColumn(width="medium"),
            "Variant ID": st.column_config.TextColumn(width="small"),
            "Changes": st.column_config.TextColumn(width="large"),
            "Endpoint": st.column_config.TextColumn(width="medium"),
        },
        use_container_width=True,
        hide_index=True,
        key="_push_sel",
    )

    push_flags = edited_sel["Push"].tolist()
    if len(push_flags) != len(all_potential_updates):
        st.error("Selection state mismatch — please re-run the page.")
        selected_updates = []
    else:
        selected_updates = [
            u for u, sel in zip(all_potential_updates, push_flags) if sel
        ]

    n_selected = len(selected_updates)
    n_total = len(all_potential_updates)
    n_variants = sum(1 for u in selected_updates if u.get("variant_id"))
    n_base = n_selected - n_variants
    endpoints_summary: list[str] = []
    if n_base > 0:
        endpoints_summary.append(f"Product_Update × {n_base}")
    if n_variants > 0:
        endpoints_summary.append(f"Product_UpdateVariant × {n_variants}")

    st.markdown(
        f"{mode_pill} &nbsp; "
        f"**{n_selected}** / {n_total} products selected "
        f"&nbsp;·&nbsp; Endpoints: "
        f"{', '.join(endpoints_summary) if endpoints_summary else '—'}",
        unsafe_allow_html=True,
    )

    if n_selected == 0:
        st.info("Select at least one product to push.")
    else:
        test_col, push_col = st.columns(2)
        with test_col:
            if st.button(
                "Test Connection",
                use_container_width=True,
            ):
                try:
                    with DanDomainClient(
                        api_username, api_password,
                    ) as client:
                        info = client.test_connection()
                    st.success(
                        f"Connected! Product count: "
                        f"{info.get('product_count', 'N/A')}"
                    )
                except (
                    DanDomainAPIError, ValueError, AttributeError,
                ) as exc:
                    st.error(f"Connection failed: {exc}")

        with push_col:
            push_clicked = st.button(
                "Simulate Push" if dry_run else "Push Prices Now",
                type="primary" if not dry_run else "secondary",
                use_container_width=True,
                disabled=st.session_state.get("_push_running", False),
            )

        if push_clicked:
            if dry_run:
                _handle_dry_run(selected_updates, n_selected)
            else:
                st.session_state["_push_pending"] = True
                st.session_state["_push_updates"] = selected_updates

        # Handle pending live-push confirmation
        if (
            st.session_state.get("_push_pending")
            and not dry_run
            and not st.session_state.get("_push_running")
        ):
            _handle_live_push_confirmation(
                api_username, api_password, site_id,
            )


def _handle_dry_run(selected_updates, n_selected):
    """Display dry-run results."""
    st.info(
        f"**Dry-run**: {n_selected} product(s) would be updated. "
        "Disable dry-run in the sidebar to push for real."
    )
    dry_data: list[dict] = []
    for u in selected_updates:
        r: dict = {
            "Product ID": u["product_id"],
            "Number": u["product_number"],
            "Title": u.get("title", ""),
            "Variant ID": u.get("variant_id", ""),
        }
        if "new_price" in u:
            r["Old Price"] = u.get("old_price", "")
            r["New Price"] = u["new_price"]
        if "buy_price" in u:
            r["Old Buy Price"] = u.get("old_buy_price", "")
            r["New Buy Price"] = u["buy_price"]
        r["Endpoint"] = u.get("endpoint", "")
        dry_data.append(r)
    st.dataframe(
        pd.DataFrame(dry_data),
        use_container_width=True,
        hide_index=True,
    )


def _handle_live_push_confirmation(api_username, api_password, site_id):
    """Handle the two-step live push confirmation."""
    pending_updates = st.session_state.get("_push_updates", [])
    n_pend = len(pending_updates)
    n_pend_var = sum(1 for u in pending_updates if u.get("variant_id"))
    n_pend_base = n_pend - n_pend_var
    ep_list: list[str] = []
    if n_pend_base > 0:
        ep_list.append(f"Product_Update × {n_pend_base}")
    if n_pend_var > 0:
        ep_list.append(f"Product_UpdateVariant × {n_pend_var}")

    st.markdown(
        '<div class="confirm-banner">'
        "<strong>Confirm Live Push</strong><br>"
        f"<strong>{n_pend}</strong> selected product(s) "
        "with verified changes<br>"
        f"Endpoints: <strong>{', '.join(ep_list)}</strong><br>"
        "This action cannot be undone automatically."
        "</div>",
        unsafe_allow_html=True,
    )

    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        confirmed = st.button(
            "Confirm and Push",
            type="primary",
            use_container_width=True,
        )
    with cancel_col:
        cancelled = st.button(
            "Cancel",
            use_container_width=True,
        )

    if cancelled:
        st.session_state.pop("_push_pending", None)
        st.session_state.pop("_push_updates", None)
        st.info("Push cancelled.")

    if confirmed:
        _execute_live_push(
            pending_updates, api_username, api_password, site_id,
        )


def _execute_live_push(pending_updates, api_username, api_password, site_id):
    """Execute the actual live push to the shop."""
    st.session_state["_push_running"] = True
    st.session_state.pop("_push_pending", None)

    progress_bar = st.progress(0, text="Pushing prices…")
    log_entries: list[dict] = []

    def on_progress(idx, total, pnum, ok, err):
        progress_bar.progress(
            idx / total,
            text=f"Updating {idx}/{total}: {pnum}",
        )
        entry = {
            "product_number": pnum,
            "status": "OK" if ok else "FAILED",
            "error": err,
            "timestamp": time.strftime("%H:%M:%S"),
        }
        if idx - 1 < len(pending_updates):
            u = pending_updates[idx - 1]
            entry["product_id"] = u.get("product_id", "")
            entry["variant_id"] = u.get("variant_id", "")
            entry["variant_types"] = u.get("variant_types", "")
        log_entries.append(entry)

    try:
        with DanDomainClient(api_username, api_password) as client:
            results = client.update_prices_batch(
                pending_updates,
                site_id=site_id,
                progress_callback=on_progress,
            )

        progress_bar.progress(1.0, text="Done!")

        res_c1, res_c2 = st.columns(2)
        res_c1.metric("Succeeded", results["success"])
        res_c2.metric("Failed", results["failed"])

        if results["errors"]:
            with st.expander("Errors", expanded=True):
                st.dataframe(
                    pd.DataFrame(results["errors"]),
                    use_container_width=True,
                    hide_index=True,
                )

    except (DanDomainAPIError, ValueError, AttributeError) as exc:
        st.error(f"Push failed: {exc}")
    finally:
        st.session_state.pop("_push_running", None)
        st.session_state.pop("_push_updates", None)

    if log_entries:
        log_df = pd.DataFrame(log_entries)
        log_csv = log_df.to_csv(index=False)
        st.download_button(
            label="Download Audit Log",
            data=log_csv.encode("utf-8"),
            file_name="api_push_log.csv",
            mime="text/csv",
        )
