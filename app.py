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
# Custom CSS — modern, polished UI theme
# ---------------------------------------------------------------------------
st.markdown("""
<style>
/* ---- Global ---- */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 Roboto, sans-serif;
}

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
}
section[data-testid="stSidebar"] * {
    color: #cbd5e1 !important;
}
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stTextInput label {
    font-weight: 600;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: #94a3b8 !important;
}
section[data-testid="stSidebar"] .stCaption {
    color: #64748b !important;
    font-weight: 700;
    letter-spacing: 0.08em;
}

/* ---- Metrics cards ---- */
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 16px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.03);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
div[data-testid="stMetric"] label {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    color: #64748b !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-weight: 700;
    font-size: 2rem;
    color: #0f172a !important;
}

/* ---- Buttons ---- */
.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    letter-spacing: 0.02em;
    padding: 0.6rem 1.25rem;
    transition: all 0.2s cubic-bezier(.4,0,.2,1);
    border: 1px solid transparent;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(67,97,238,0.20);
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #4361ee 0%, #3730a3 100%);
    color: #fff !important;
}

/* ---- Download buttons ---- */
.stDownloadButton > button {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    color: #334155 !important;
    transition: all 0.15s ease;
}
.stDownloadButton > button:hover {
    background: #f1f5f9;
    border-color: #cbd5e1;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

/* ---- Expanders ---- */
.streamlit-expanderHeader {
    font-weight: 600;
    font-size: 0.95rem;
    color: #1e293b;
    border-radius: 12px;
}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.25rem;
    background: #f8fafc;
    border-radius: 12px;
    padding: 0.25rem;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    padding: 0.5rem 1.5rem;
    color: #64748b;
    transition: all 0.15s ease;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: #ffffff !important;
    color: #0f172a !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

/* ---- Dataframes ---- */
.stDataFrame {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #e2e8f0;
}

/* ---- Dividers ---- */
hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 2rem 0;
}

/* ---- File uploader ---- */
section[data-testid="stFileUploader"] {
    border: 2px dashed #cbd5e1;
    border-radius: 16px;
    padding: 1rem;
    background: #f8fafc;
    transition: all 0.2s ease;
}
section[data-testid="stFileUploader"]:hover {
    border-color: #4361ee;
    background: #eff6ff;
}

/* ---- Section headers ---- */
.section-header {
    font-size: 1.15rem;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

/* ---- Confirmation banner ---- */
.confirm-banner {
    background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
    border: 1px solid #f59e0b;
    border-left: 4px solid #f59e0b;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
}
.confirm-banner strong { color: #92400e; }

/* ---- Status pill ---- */
.status-pill {
    display: inline-block;
    padding: 0.2rem 0.75rem;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.03em;
}
.status-pill.live { background: #fee2e2; color: #991b1b; }
.status-pill.dry  { background: #dbeafe; color: #1e40af; }

/* ---- Hero header ---- */
.hero-header {
    margin-bottom: 0.25rem;
    font-size: 2rem;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: -0.02em;
}
.hero-sub {
    color: #64748b;
    margin-top: 0;
    font-size: 1.05rem;
    line-height: 1.5;
    max-width: 700px;
}
.hero-sub strong { color: #4361ee; }

/* ---- Info cards ---- */
.info-card {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 0.75rem;
}
.info-card h4 {
    margin: 0 0 0.4rem 0;
    color: #0f172a;
    font-size: 0.95rem;
}
.info-card p {
    margin: 0;
    color: #64748b;
    font-size: 0.88rem;
    line-height: 1.5;
}

/* ---- Sidebar version badge ---- */
.version-badge {
    text-align: center;
    opacity: 0.7;
    font-size: 0.72rem;
    padding: 0.5rem;
    border-radius: 8px;
    background: rgba(255,255,255,0.05);
}
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


def optimize_prices(
    df: pd.DataFrame,
    price_pct: float = 0.0,
    original_buy_prices: "pd.Series | None" = None,
) -> tuple:
    """Apply optional PRICE adjustment, calculate coverage and optimise.

    Parameters
    ----------
    price_pct : float
        Percentage adjustment to apply to all PRICE values before
        recalculating coverage (e.g. 10.0 = +10 %).
    original_buy_prices : pd.Series, optional
        Original ``BUY_PRICE_NUM`` values before per-line editing.
        When supplied, products whose BUY_PRICE was changed are
        included in the import / push set even when their coverage
        rate already meets the minimum threshold.
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

    # Determine the full set of products for import / push:
    # products needing a price adjustment + products with manually changed BUY_PRICE
    in_import_set = needs_adjustment.copy()
    if original_buy_prices is not None:
        buy_price_changed = df['BUY_PRICE_NUM'] != original_buy_prices
        in_import_set = in_import_set | buy_price_changed

    # Build import-ready DataFrame
    import_df = df.loc[in_import_set, REQUIRED_COLUMNS].copy()
    import_df['PRICE'] = df.loc[in_import_set, 'FINAL_PRICE_NUM'].apply(format_dk)

    return df[EXPORT_COLUMNS], int(in_import_set.sum()), in_import_set.values, import_df


# --- Sidebar ---
with st.sidebar:
    st.markdown("### ⚙️ Settings")

    st.caption("📁 FILE IMPORT")
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
    st.caption("💰 PRICE RULES")
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
    st.caption("🔌 API CONNECTION")

    # Credential loading priority: secrets.toml > env var > sidebar input
    try:
        _dd_secrets = st.secrets.get("dandomain", {})
    except Exception:
        _dd_secrets = {}

    st.markdown(
        "Uses the [HostedShop SOAP API]"
        "(https://webshop-help.dandomain.dk/integration-via-api/). "
        "Create an API employee under **Settings → Employees** and "
        "enable SOAP access under **Settings → API: SOAP**."
    )
    api_username = st.text_input(
        "API username",
        value=_dd_secrets.get("username", os.environ.get("DANDOMAIN_API_USERNAME", "")),
        placeholder="api-user@example.com",
        help=(
            "The email / username of the API employee created in your "
            "DanDomain admin under Settings → Employees."
        ),
    )
    api_password = st.text_input(
        "API password",
        value=_dd_secrets.get("password", os.environ.get("DANDOMAIN_API_PASSWORD", "")),
        type="password",
        help=(
            "Password for the API employee. "
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

    api_ready = bool(api_username and api_password)

    st.divider()
    st.markdown(
        '<div class="version-badge">'
        "Coverage Optimizer v1.3<br>"
        f"Min margin {int(MIN_COVERAGE_RATE * 100)}% · "
        f"Prices end in {BEAUTIFY_LAST_DIGIT}"
        "</div>",
        unsafe_allow_html=True,
    )

# --- Main Content ---
st.markdown(
    '<h1 class="hero-header">📊 Product Coverage Optimizer</h1>',
    unsafe_allow_html=True,
)
st.markdown(
    f'<p class="hero-sub">'
    f"Upload your product CSV to calculate coverage rates and automatically "
    f"adjust prices to at least a <strong>{int(MIN_COVERAGE_RATE * 100)}%</strong> "
    f"profit margin. Variant-aware — handles products with multiple variants correctly."
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
            work_df, price_pct, original_buy_prices=parsed_df['BUY_PRICE_NUM'],
        )

        # --- Summary Metrics ---
        total = len(final_df)
        unchanged = total - adjusted_count
        st.markdown("")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Products", f"{total:,}")
        col2.metric("Prices Adjusted", f"{adjusted_count:,}")
        col3.metric("Unchanged", f"{unchanged:,}")
        adj_pct = (adjusted_count / total * 100) if total else 0
        col4.metric("Adjusted %", f"{adj_pct:.1f}%" if total else "—")
        st.markdown("")

        # --- Data Tabs ---
        tab_all, tab_adjusted, tab_import = st.tabs([
            "📋 All Products",
            "⚡ Adjusted Only",
            "📦 Import Preview",
        ])

        with tab_all:
            st.dataframe(final_df, use_container_width=True, hide_index=True)

        with tab_adjusted:
            adjusted_df = final_df[adjusted_mask]
            if adjusted_df.empty:
                st.info("✅ All products already meet the minimum margin — no adjustments needed.")
            else:
                st.dataframe(adjusted_df, use_container_width=True, hide_index=True)

        with tab_import:
            if import_df.empty:
                st.info("✅ No products needed adjustment — nothing to import.")
            else:
                adjusted_full = final_df[adjusted_mask]
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
                    'Variant ID': adjusted_full['VARIANT_ID'].values,
                    'Variant Types': adjusted_full['VARIANT_TYPES'].values,
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
                        'Variant ID': st.column_config.TextColumn(width='small'),
                        'Variant Types': st.column_config.TextColumn(width='small'),
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

        # --- Downloads ---
        st.divider()
        st.markdown(
            '<div class="section-header">📥 Download Reports</div>',
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

        # --- Push to Shop via API ---
        if not import_df.empty:
            st.divider()
            st.markdown(
                '<div class="section-header">🚀 Push to Shop</div>',
                unsafe_allow_html=True,
            )

            if not api_ready:
                st.markdown(
                    '<div class="info-card">'
                    "<h4>🔌 API Not Connected</h4>"
                    "<p>Configure your DanDomain API credentials in the sidebar "
                    "to enable direct price updates to your live webshop.</p>"
                    "</div>",
                    unsafe_allow_html=True,
                )
            else:
                mode_pill = (
                    '<span class="status-pill dry">🧪 DRY-RUN</span>'
                    if dry_run
                    else '<span class="status-pill live">⚡ LIVE</span>'
                )
                st.markdown(
                    f'<div class="info-card">'
                    f"<h4>{mode_pill} &nbsp; "
                    f"{adjusted_count} product"
                    f"{'s' if adjusted_count != 1 else ''} queued</h4>"
                    f"<p>Prices will be pushed via the <strong>SOAP API</strong>. "
                    f"Variant ID and Variant Types are included to ensure "
                    f"the correct product/variant is updated.</p>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                # Connection test
                test_col, push_col = st.columns(2)
                with test_col:
                    if st.button("🔍 Test Connection", use_container_width=True):
                        try:
                            with DanDomainClient(api_username, api_password) as client:
                                info = client.test_connection()
                            st.success(
                                f"✅ Connected! Product count: "
                                f"{info.get('product_count', 'N/A')}"
                            )
                        except (DanDomainAPIError, ValueError, AttributeError) as exc:
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
                    # Build the update list from adjusted rows.
                    # Include every identifier that a regular CSV import
                    # would carry: PRODUCT_ID, NUMBER, VARIANT_ID,
                    # VARIANT_TYPES — this ensures the correct
                    # product / variant is targeted.
                    #
                    # buy_price is included when the user edited the
                    # BUY_PRICE for a product so that the cost price
                    # is also pushed to the shop.
                    bp_changed = (
                        parsed_df['BUY_PRICE_NUM'] != work_df['BUY_PRICE_NUM']
                    )
                    adjusted_full = final_df[adjusted_mask]
                    updates = []
                    for idx, row in adjusted_full.iterrows():
                        pid = str(row.get('PRODUCT_ID', '')).strip()
                        pnum = str(row['NUMBER']).strip()
                        new_price_str = str(row['NEW_PRICE'])
                        new_price_val = clean_price(new_price_str)
                        vid = str(row.get('VARIANT_ID', '')).strip()
                        vtypes = str(row.get('VARIANT_TYPES', '')).strip()
                        if pnum and new_price_val > 0:
                            entry = {
                                "product_id": pid,
                                "product_number": pnum,
                                "new_price": new_price_val,
                                "variant_id": vid,
                                "variant_types": vtypes,
                            }
                            if bp_changed.loc[idx]:
                                entry["buy_price"] = clean_price(
                                    str(row.get('BUY_PRICE', ''))
                                )
                            updates.append(entry)

                    if not updates:
                        st.warning("No valid products to update.")
                    elif dry_run:
                        st.info(
                            f"🧪 **Dry-run**: {len(updates)} product(s) "
                            "would be updated. Disable dry-run in the "
                            "sidebar to push for real."
                        )
                        dry_df = pd.DataFrame(updates)
                        dry_cols = [
                            'product_id', 'product_number', 'new_price',
                            'variant_id', 'variant_types',
                        ]
                        dry_labels = [
                            'Product ID', 'Product Number', 'New Price',
                            'Variant ID', 'Variant Types',
                        ]
                        if 'buy_price' in dry_df.columns:
                            dry_cols.append('buy_price')
                            dry_labels.append('Buy Price')
                        dry_df = dry_df[dry_cols]
                        dry_df.columns = dry_labels
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
                            # Include all identifiers in the log entry
                            # (mirrors a regular CSV import row)
                            entry = {
                                "product_number": pnum,
                                "status": "✅" if ok else "❌",
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

                        except (DanDomainAPIError, ValueError, AttributeError) as exc:
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