import streamlit as st
import pandas as pd
import numpy as np
import math

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
]


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
def process_products(raw_bytes: bytes) -> pd.DataFrame:
    """Parse uploaded CSV bytes and return an optimised product DataFrame."""
    from io import BytesIO

    df = pd.read_csv(BytesIO(raw_bytes), sep=';', skiprows=1)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")

    # Clean the prices
    df['BUY_PRICE_NUM'] = df['BUY_PRICE'].apply(clean_price)
    df['PRICE_NUM'] = df['PRICE'].apply(clean_price)
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

    # Format the outputs
    df['PRICE'] = df['FINAL_PRICE_NUM'].apply(format_dk)
    df['PRICE_EX_VAT'] = df['FINAL_PRICE_EX_VAT'].apply(format_dk)
    df['COVERAGE_RATE_%'] = (
        (df['FINAL_COVERAGE_RATE'] * 100).round(2).astype(str).str.replace('.', ',') + '%'
    )

    return df[EXPORT_COLUMNS], int(needs_adjustment.sum())


# --- Streamlit UI ---
st.title("Product Coverage Rate Optimizer 🚀")
st.write(
    "Upload your product CSV file to calculate coverage rates "
    "and automatically adjust prices to hit at least a 50% margin."
)

uploaded_file = st.file_uploader("Upload Product CSV", type=['csv'])

if uploaded_file is not None:
    try:
        raw_bytes = uploaded_file.getvalue()
        final_df, adjusted_count = process_products(raw_bytes)
    except ValueError as exc:
        st.error(str(exc))
    except Exception as exc:
        st.error(f"Failed to process CSV: {exc}")
    else:
        st.success(
            f"Successfully processed {len(final_df)} products! "
            f"Adjusted prices for {adjusted_count} items."
        )
        st.dataframe(final_df.head(10))

        csv_data = "PRODUCTS\n" + final_df.to_csv(sep=';', index=False)
        st.download_button(
            label="Download Updated Products CSV",
            data=csv_data.encode('utf-8'),
            file_name="updated_products.csv",
            mime="text/csv",
        )