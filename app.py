import streamlit as st
import pandas as pd
import numpy as np
import math

# --- Page Configuration ---
st.set_page_config(
    page_title="Coverage Optimizer",
    page_icon="📊",
    layout="wide",
)

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
    st.header("⚙️ Settings")
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
    st.subheader("💰 PRICE Adjustment")
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

    st.divider()
    st.markdown(
        "**Coverage Optimizer** v1.0\n\n"
        "Calculates coverage rates and adjusts product prices to maintain "
        f"at least a **{int(MIN_COVERAGE_RATE * 100)}%** profit margin. "
        f"Prices are beautified to end in **{BEAUTIFY_LAST_DIGIT}**."
    )

# --- Main Content ---
st.title("📊 Product Coverage Optimizer")
st.markdown(
    "Upload your product CSV to calculate coverage rates "
    "and automatically adjust prices to hit at least a "
    f"**{int(MIN_COVERAGE_RATE * 100)}%** margin."
)

uploaded_file = st.file_uploader("Upload Product CSV", type=['csv'])

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
        col1, col2, col3 = st.columns(3)
        col1.metric("Total Products", f"{total:,}")
        col2.metric("Prices Adjusted", f"{adjusted_count:,}")
        col3.metric("Unchanged", f"{unchanged:,}")

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