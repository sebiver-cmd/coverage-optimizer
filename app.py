import os
import time

import streamlit as st
import pandas as pd
import numpy as np
import math

from dandomain_api import DanDomainClient, DanDomainAPIError

# --- Page Configuration ---
st.set_page_config(
    page_title="Coverage Optimizer",
    page_icon="📊",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Custom CSS — modern, professional theme
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* ---- Global ---- */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1a1a2e 0%, #16213e 100%);
}
section[data-testid="stSidebar"] * {
    color: #e0e0e0 !important;
}
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stTextInput label {
    font-weight: 600;
    font-size: 0.85rem;
    letter-spacing: 0.02em;
}

/* ---- Metrics ---- */
div[data-testid="stMetric"] {
    background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
    border: 1px solid #e9ecef;
    border-radius: 12px;
    padding: 1rem 1.25rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04);
}
div[data-testid="stMetric"] label {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: #6c757d !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-weight: 700;
    font-size: 1.8rem;
    color: #212529 !important;
}

/* ---- Buttons ---- */
.stButton > button {
    border-radius: 8px;
    font-weight: 600;
    letter-spacing: 0.02em;
    transition: all 0.15s ease;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.10);
}

/* ---- Download buttons ---- */
.stDownloadButton > button {
    border-radius: 8px;
    font-weight: 600;
}

/* ---- Expanders ---- */
.streamlit-expanderHeader {
    font-weight: 600;
    font-size: 0.95rem;
}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.5rem;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px 8px 0 0;
    font-weight: 600;
    padding: 0.5rem 1.25rem;
}

/* ---- Dataframes ---- */
.stDataFrame {
    border-radius: 8px;
    overflow: hidden;
}

/* ---- Dividers ---- */
hr {
    border: none;
    border-top: 1px solid #e9ecef;
    margin: 1.5rem 0;
}

/* ---- File uploader ---- */
section[data-testid="stFileUploader"] {
    border: 2px dashed #dee2e6;
    border-radius: 12px;
    padding: 0.5rem;
    transition: border-color 0.15s ease;
}
section[data-testid="stFileUploader"]:hover {
    border-color: #4361ee;
}

/* ---- Section headers ---- */
.section-header {
    font-size: 1.1rem;
    font-weight: 700;
    color: #212529;
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

/* ---- Confirmation banner ---- */
.confirm-banner {
    background: #fff3cd;
    border: 1px solid #ffc107;
    border-radius: 10px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
}
.confirm-banner strong { color: #856404; }
</style>
""", unsafe_allow_html=True)

# --- Constants ---
VAT_RATE = 0.25  # 25% Danish VAT
MIN_COVERAGE_RATE = 0.50  # Minimum acceptable profit margin (50%)
BEAUTIFY_LAST_DIGIT = 9  # Prices are rounded up to end in this digit
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
ENCODING_OPTIONS = {
    'Auto-detect': 'auto',
    'UTF-8': 'utf-8',
    'Latin-1 (ISO-8859-1)': 'latin-1',
    'Windows-1252': 'cp1252',
}


def detect_encoding(raw_bytes: bytes) -> str:
    """Detect the most likely encoding of *raw_bytes*.

    Checks for a UTF-8 BOM first, then tries strict UTF-8 decoding.
    Falls back to Windows-1252 which is a superset of Latin-1 and
    covers all Danish characters (Æ, Ø, Å, æ, ø, å).
    """
    if raw_bytes.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    try:
        raw_bytes.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        return 'cp1252'


def clean_price(price_str):
    """Convert a Danish-formatted price string (e.g. '1.234,56') to a float."""
    if pd.isna(price_str):
        return 0.0
    if isinstance(price_str, str):
        price_str = price_str.replace('.', '').replace(',', '.')
    try:
        return float(price_str)
    except (ValueError, TypeError):
        return 0.0


def beautify_price(price):
    """Round *price* up to the nearest integer ending in BEAUTIFY_LAST_DIGIT."""
    if price == 0:
        return 0.0
    target = math.ceil(price)
    remainder = target % 10
    return float(target + (BEAUTIFY_LAST_DIGIT - remainder))


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


@st.cache_data
def parse_csv(raw_bytes: bytes, encoding: str = 'auto') -> pd.DataFrame:
    """Parse uploaded CSV bytes and return a validated DataFrame.

    Numeric helper columns ``BUY_PRICE_NUM`` and ``PRICE_NUM`` are added
    so that downstream code can work with floats directly.
    """
    from io import BytesIO

    if encoding == 'auto':
        encoding = detect_encoding(raw_bytes)

    df = pd.read_csv(BytesIO(raw_bytes), sep=';', skiprows=1, encoding=encoding)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

    # Clean the prices
    df['BUY_PRICE_NUM'] = df['BUY_PRICE'].apply(clean_price)
    df['PRICE_NUM'] = df['PRICE'].apply(clean_price)

    # Preserve integer formatting for columns like VARIANT_TYPES
    for col in ('VARIANT_TYPES', 'VARIANT_ID', 'PRODUCT_ID'):
        if col in df.columns:
            df[col] = df[col].apply(format_int_col)

    return df


def optimize_prices(df: pd.DataFrame, price_pct: float = 0.0) -> tuple:
    """Apply optional PRICE adjustment, calculate coverage and optimise.

    Parameters
    ----------
    price_pct : float
        Percentage adjustment to apply to all PRICE values before
        recalculating coverage (e.g. 10.0 = +10 %).
    """
    df = df.copy()

    # Reformat BUY_PRICE from (possibly per-line edited) numeric values
    df['BUY_PRICE'] = df['BUY_PRICE_NUM'].apply(format_dk)

    # Apply optional PRICE adjustment
    if price_pct != 0:
        df['PRICE_NUM'] = df['PRICE_NUM'] * (1 + price_pct / 100)
        df['PRICE'] = df['PRICE_NUM'].apply(format_dk)

    df['PRICE_EX_VAT_NUM'] = df['PRICE_NUM'] / (1 + VAT_RATE)

    # Calculate current coverage rate
    df['COVERAGE_RATE'] = calc_coverage_rate(df, 'PRICE_EX_VAT_NUM', 'BUY_PRICE_NUM')

    # Identify items needing adjustment
    needs_adjustment = (df['COVERAGE_RATE'] < MIN_COVERAGE_RATE) & (df['BUY_PRICE_NUM'] > 0)

    # Calculate target EX VAT price for the minimum margin.
    # beautify_price() rounds up afterwards, so the final margin will always
    # be slightly above MIN_COVERAGE_RATE — this is intentional.
    df['NEW_PRICE_EX_VAT_NUM'] = df['PRICE_EX_VAT_NUM']
    df.loc[needs_adjustment, 'NEW_PRICE_EX_VAT_NUM'] = (
        df.loc[needs_adjustment, 'BUY_PRICE_NUM'] / MIN_COVERAGE_RATE
    )

    # Calculate inc VAT and beautify
    df['NEW_PRICE_NUM'] = df['NEW_PRICE_EX_VAT_NUM'] * (1 + VAT_RATE)
    df['FINAL_PRICE_NUM'] = df['PRICE_NUM']
    df.loc[needs_adjustment, 'FINAL_PRICE_NUM'] = (
        df.loc[needs_adjustment, 'NEW_PRICE_NUM'].apply(beautify_price)
    )

    # Recalculate final metrics
    df['FINAL_PRICE_EX_VAT'] = df['FINAL_PRICE_NUM'] / (1 + VAT_RATE)
    df['FINAL_COVERAGE_RATE'] = calc_coverage_rate(df, 'FINAL_PRICE_EX_VAT', 'BUY_PRICE_NUM')

    # Format original price columns (PRICE may reflect the global % adjustment)
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

    # Build import-ready DataFrame (adjusted rows only, original columns)
    import_df = df.loc[needs_adjustment, REQUIRED_COLUMNS].copy()
    import_df['PRICE'] = df.loc[needs_adjustment, 'FINAL_PRICE_NUM'].apply(format_dk)

    return df[EXPORT_COLUMNS], int(needs_adjustment.sum()), needs_adjustment.values, import_df


# --- Sidebar ---
with st.sidebar:
    st.markdown("### ⚙️ Settings")

    st.caption("FILE IMPORT")
    encoding_label = st.selectbox(
        "CSV file encoding",
        options=list(ENCODING_OPTIONS.keys()),
        index=0,
        help=(
            "Choose 'Auto-detect' to let the app guess the encoding, "
            "or pick a specific one if Danish characters (Æ, Ø, Å) look wrong."
        ),
    )
    selected_encoding = ENCODING_OPTIONS[encoding_label]

    st.divider()
    st.caption("PRICE RULES")
    price_pct = st.number_input(
        "Adjust PRICE (%)",
        min_value=-50.0,
        max_value=200.0,
        value=0.0,
        step=0.5,
        help=(
            "Increase or decrease all sales prices by this percentage "
            "before recalculating coverage rates. For example, enter 10 "
            "to simulate a 10 % price increase across all products."
        ),
    )
    include_buy_price = st.checkbox(
        "Include BUY_PRICE in import file",
        value=False,
        help=(
            "When checked, the import-ready CSV will contain the "
            "BUY_PRICE column so the buy price is also updated on "
            "re-import."
        ),
    )

    # --- DanDomain API Settings ---
    st.divider()
    st.caption("API CONNECTION")

    # Credential loading priority: secrets.toml > env var > sidebar input
    try:
        _dd_secrets = st.secrets.get("dandomain", {})
    except Exception:
        _dd_secrets = {}

    api_method = st.selectbox(
        "API method",
        options=["rest"],
        index=0,
        help=(
            "**REST (v1)** — uses the ProductDataService PATCH endpoint.\n\n"
            "GraphQL is not yet available for product data "
            "([DanDomain roadmap]"
            "(https://webshop-help.dandomain.dk/roadmap/))."
        ),
    )
    shop_url = st.text_input(
        "Shop URL",
        value=_dd_secrets.get("shop_url", os.environ.get("DANDOMAIN_SHOP_URL", "")),
        placeholder="https://your-shop.webshop.dandomain.dk",
        help="Must start with https://",
    )
    api_key = st.text_input(
        "API key / secret",
        value=_dd_secrets.get("api_key", os.environ.get("DANDOMAIN_API_KEY", "")),
        type="password",
        help=(
            "Your DanDomain API key. Found in admin under "
            "Settings → Integration → API. "
            "Stored in memory only — never written to disk or logs."
        ),
    )
    site_id = st.number_input(
        "Site ID",
        min_value=1,
        max_value=100,
        value=_dd_secrets.get("site_id", 1),
        help="Language / site ID in your webshop (default: 1).",
    )
    dry_run = st.checkbox(
        "🧪 Dry-run (simulate only)",
        value=True,
        help=(
            "When checked, the push-to-shop button shows what would "
            "be sent but makes **no** API calls."
        ),
    )

    api_ready = bool(shop_url and api_key)

    st.divider()
    st.markdown(
        "<div style='text-align:center;opacity:0.6;font-size:0.78rem;'>"
        "Coverage Optimizer v1.2<br>"
        f"Min margin {int(MIN_COVERAGE_RATE * 100)}% · "
        f"Prices end in {BEAUTIFY_LAST_DIGIT}"
        "</div>",
        unsafe_allow_html=True,
    )

# --- Main Content ---
st.markdown(
    "<h1 style='margin-bottom:0.2rem;'>📊 Product Coverage Optimizer</h1>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<p style='color:#6c757d;margin-top:0;font-size:1.05rem;'>"
    f"Upload your product CSV to calculate coverage rates and adjust prices "
    f"to at least a <strong>{int(MIN_COVERAGE_RATE * 100)}%</strong> margin."
    f"</p>",
    unsafe_allow_html=True,
)

uploaded_file = st.file_uploader("Upload Product CSV", type=['csv'], label_visibility="collapsed")

if uploaded_file is not None:
    try:
        raw_bytes = uploaded_file.getvalue()
        parsed_df = parse_csv(raw_bytes, selected_encoding)
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Failed to process CSV: {exc}")
    else:
        # --- Per-line BUY_PRICE editing ---
        with st.expander("✏️ BUY_PRICE – Per-Line Adjustment", expanded=False):
            st.caption(
                "Edit individual BUY_PRICE values to cross-check with "
                "current supplier cost prices. Changes are reflected in "
                "the coverage calculation below."
            )
            edit_cols = ['PRODUCT_ID', 'TITLE_DK', 'NUMBER',
                         'BUY_PRICE_NUM', 'PRICE_NUM']
            edit_df = parsed_df[edit_cols].copy()
            edit_df = edit_df.rename(columns={
                'BUY_PRICE_NUM': 'BUY_PRICE',
                'PRICE_NUM': 'PRICE (ref)',
            })
            edited = st.data_editor(
                edit_df,
                disabled=['PRODUCT_ID', 'TITLE_DK', 'NUMBER', 'PRICE (ref)'],
                column_config={
                    "BUY_PRICE": st.column_config.NumberColumn(
                        "BUY_PRICE",
                        help="Cost price – edit to match current supplier price",
                        format="%.2f",
                        min_value=0.0,
                    ),
                    "PRICE (ref)": st.column_config.NumberColumn(
                        "PRICE (ref)",
                        help="Current sales price (read-only reference)",
                        format="%.2f",
                    ),
                },
                use_container_width=True,
                hide_index=True,
            )

        # Apply edited buy prices (use index-based assignment to keep alignment)
        work_df = parsed_df.copy()
        work_df['BUY_PRICE_NUM'] = edited['BUY_PRICE']

        final_df, adjusted_count, adjusted_mask, import_df = optimize_prices(
            work_df, price_pct,
        )

        # --- Summary Metrics ---
        total = len(final_df)
        unchanged = total - adjusted_count
        st.markdown("")  # spacing
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Products", f"{total:,}")
        col2.metric("Prices Adjusted", f"{adjusted_count:,}")
        col3.metric("Unchanged", f"{unchanged:,}")
        st.markdown("")  # spacing

        # --- Data Tabs ---
        tab_all, tab_adjusted = st.tabs(["All Products", "Adjusted Only"])

        with tab_all:
            st.dataframe(final_df, use_container_width=True)

        with tab_adjusted:
            adjusted_df = final_df[adjusted_mask]
            if adjusted_df.empty:
                st.info("No products needed price adjustment.")
            else:
                st.dataframe(adjusted_df, use_container_width=True)

        # --- Downloads ---
        st.divider()
        st.markdown(
            '<div class="section-header">📥 Downloads</div>',
            unsafe_allow_html=True,
        )
        dl_col1, dl_col2 = st.columns(2)

        # Preview – full report with all analysis columns
        csv_preview = "\ufeff" + "PRODUCTS\n" + final_df.to_csv(sep=';', index=False)
        with dl_col1:
            st.download_button(
                label="📥 Preview – Full Report CSV",
                data=csv_preview.encode('utf-8'),
                file_name="preview_products.csv",
                mime="text/csv; charset=utf-8",
            )

        # Import-ready – only adjusted rows, importable columns
        import_cols = IMPORT_COLUMNS_BASE.copy()
        if include_buy_price:
            import_cols.insert(3, 'BUY_PRICE')

        if import_df.empty:
            with dl_col2:
                st.download_button(
                    label="📥 Import-Ready CSV",
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
                    label="📥 Import-Ready CSV",
                    data=csv_import.encode('utf-8'),
                    file_name="import_products.csv",
                    mime="text/csv; charset=utf-8",
                )

        # --- Import Preview ---
        if not import_df.empty:
            adjusted_full = final_df[adjusted_mask]
            with st.expander(
                f"🔍 Preview Import Changes ({adjusted_count} product"
                f"{'s' if adjusted_count != 1 else ''})",
                expanded=False,
            ):
                preview_df = pd.DataFrame({
                    'Product ID': adjusted_full['PRODUCT_ID'].values,
                    'Title': adjusted_full['TITLE_DK'].values,
                    'Number': adjusted_full['NUMBER'].values,
                    'Old Price': adjusted_full['PRICE'].values,
                    'New Price': adjusted_full['NEW_PRICE'].values,
                    'Old Coverage': adjusted_full['COVERAGE_RATE_%'].values,
                    'New Coverage': adjusted_full['NEW_COVERAGE_RATE_%'].values,
                })

                st.dataframe(
                    preview_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        'Product ID': st.column_config.TextColumn(width='small'),
                        'Title': st.column_config.TextColumn(width='medium'),
                        'Number': st.column_config.TextColumn(width='small'),
                        'Old Price': st.column_config.TextColumn(
                            'Old Price 💰', width='small',
                        ),
                        'New Price': st.column_config.TextColumn(
                            'New Price ✅', width='small',
                        ),
                        'Old Coverage': st.column_config.TextColumn(
                            'Old Coverage', width='small',
                        ),
                        'New Coverage': st.column_config.TextColumn(
                            'New Coverage ✅', width='small',
                        ),
                    },
                )

        # --- Push to Shop via API ---
        if not import_df.empty:
            st.divider()
            st.markdown(
                '<div class="section-header">🚀 Push to Shop</div>',
                unsafe_allow_html=True,
            )

            if not api_ready:
                st.info(
                    "Configure your DanDomain API credentials in the "
                    "sidebar to enable direct price updates."
                )
            else:
                mode_label = "dry-run" if dry_run else "**LIVE**"
                st.markdown(
                    f"**{adjusted_count}** product"
                    f"{'s' if adjusted_count != 1 else ''} "
                    f"will be updated via **{api_method.upper()}** API "
                    f"({mode_label})."
                )

                # Connection test
                test_col, push_col = st.columns(2)
                with test_col:
                    if st.button("🔍 Test Connection", use_container_width=True):
                        try:
                            with DanDomainClient(shop_url, api_key, api_method) as client:
                                info = client.test_connection()
                            st.success(
                                f"✅ Connected! Product count: "
                                f"{info.get('product_count', 'N/A')}"
                            )
                        except (DanDomainAPIError, ValueError) as exc:
                            st.error(f"❌ Connection failed: {exc}")

                # Push / simulate button
                with push_col:
                    push_clicked = st.button(
                        "🧪 Simulate Push" if dry_run else "⚡ Push Prices Now",
                        type="primary" if not dry_run else "secondary",
                        use_container_width=True,
                        disabled=st.session_state.get("_push_running", False),
                    )

                if push_clicked:
                    # Build the update list from adjusted rows
                    adjusted_full = final_df[adjusted_mask]
                    updates = []
                    for _, row in adjusted_full.iterrows():
                        pnum = str(row['NUMBER']).strip()
                        new_price_str = str(row['NEW_PRICE'])
                        new_price_val = clean_price(new_price_str)
                        if pnum and new_price_val > 0:
                            updates.append({
                                "product_number": pnum,
                                "new_price": new_price_val,
                            })

                    if not updates:
                        st.warning("No valid products to update.")
                    elif dry_run:
                        st.info(
                            f"🧪 **Dry-run**: {len(updates)} product(s) "
                            "would be updated. Disable dry-run in the "
                            "sidebar to push for real."
                        )
                        dry_df = pd.DataFrame(updates)
                        dry_df.columns = ['Product Number', 'New Price']
                        st.dataframe(dry_df, use_container_width=True, hide_index=True)
                    else:
                        # --- Two-step confirmation for live push --------
                        st.session_state["_push_pending"] = True
                        st.session_state["_push_updates"] = updates

                # Handle pending live-push confirmation
                if (
                    st.session_state.get("_push_pending")
                    and not dry_run
                    and not st.session_state.get("_push_running")
                ):
                    pending_updates = st.session_state.get("_push_updates", [])
                    st.markdown(
                        '<div class="confirm-banner">'
                        "<strong>⚠️ Confirm Live Push</strong><br>"
                        f"You are about to update <strong>{len(pending_updates)}</strong> "
                        "product price(s) on your <strong>live</strong> webshop. "
                        "This action cannot be undone automatically."
                        "</div>",
                        unsafe_allow_html=True,
                    )
                    confirm_col, cancel_col = st.columns(2)
                    with confirm_col:
                        confirmed = st.button(
                            "✅ Confirm & Push",
                            type="primary",
                            use_container_width=True,
                        )
                    with cancel_col:
                        cancelled = st.button(
                            "❌ Cancel",
                            use_container_width=True,
                        )

                    if cancelled:
                        st.session_state.pop("_push_pending", None)
                        st.session_state.pop("_push_updates", None)
                        st.info("Push cancelled.")

                    if confirmed:
                        st.session_state["_push_running"] = True
                        st.session_state.pop("_push_pending", None)

                        progress_bar = st.progress(0, text="Pushing prices…")
                        log_entries: list[dict] = []

                        def on_progress(idx, total, pnum, ok, err):
                            progress_bar.progress(
                                idx / total,
                                text=f"Updating {idx}/{total}: {pnum}",
                            )
                            log_entries.append({
                                "product_number": pnum,
                                "status": "✅" if ok else "❌",
                                "error": err,
                                "timestamp": time.strftime("%H:%M:%S"),
                            })

                        try:
                            with DanDomainClient(shop_url, api_key, api_method) as client:
                                results = client.update_prices_batch(
                                    pending_updates,
                                    site_id=site_id,
                                    progress_callback=on_progress,
                                )

                            progress_bar.progress(1.0, text="Done!")

                            res_c1, res_c2 = st.columns(2)
                            res_c1.metric("✅ Succeeded", results["success"])
                            res_c2.metric("❌ Failed", results["failed"])

                            if results["errors"]:
                                with st.expander("⚠️ Errors", expanded=True):
                                    err_df = pd.DataFrame(results["errors"])
                                    st.dataframe(
                                        err_df,
                                        use_container_width=True,
                                        hide_index=True,
                                    )

                        except (DanDomainAPIError, ValueError) as exc:
                            st.error(f"❌ Push failed: {exc}")
                        finally:
                            st.session_state.pop("_push_running", None)
                            st.session_state.pop("_push_updates", None)

                        # Audit log download
                        if log_entries:
                            log_df = pd.DataFrame(log_entries)
                            log_csv = log_df.to_csv(index=False)
                            st.download_button(
                                label="📋 Download Audit Log",
                                data=log_csv.encode("utf-8"),
                                file_name="api_push_log.csv",
                                mime="text/csv",
                            )