import streamlit as st
import pandas as pd
import numpy as np
import math

st.title("Product Coverage Rate Optimizer 🚀")
st.write("Upload your product CSV file to calculate coverage rates and automatically adjust prices to hit at least a 50% margin.")

# Function to clean Danish price formats
def clean_price(price_str):
    if pd.isna(price_str): return 0.0
    if isinstance(price_str, str):
        price_str = price_str.replace('.', '').replace(',', '.')
    try:
        return float(price_str)
    except:
        return 0.0

# Function to beautify prices (always ends in 9, never decreases)
def beautify_price(price):
    if price == 0: return 0.0
    target = math.ceil(price)
    remainder = target % 10
    return float(target + (9 - remainder))

# Function to format numbers back to Danish standard
def format_dk(num):
    return f"{num:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

uploaded_file = st.file_uploader("Upload Product CSV", type=['csv'])

if uploaded_file is not None:
    # Read the file (skipping the "PRODUCTS" title line)
    df = pd.read_csv(uploaded_file, sep=';', skiprows=1)
    
    VAT_RATE = 0.25 # 25% Danish VAT
    
    # Clean the prices
    df['BUY_PRICE_NUM'] = df['BUY_PRICE'].apply(clean_price)
    df['PRICE_NUM'] = df['PRICE'].apply(clean_price)
    df['PRICE_EX_VAT_NUM'] = df['PRICE_NUM'] / (1 + VAT_RATE)
    
    # Calculate coverage rate
    df['COVERAGE_RATE'] = np.where(
        df['PRICE_EX_VAT_NUM'] > 0,
        (df['PRICE_EX_VAT_NUM'] - df['BUY_PRICE_NUM']) / df['PRICE_EX_VAT_NUM'],
        0.0
    )
    df.loc[(df['BUY_PRICE_NUM'] == 0) & (df['PRICE_EX_VAT_NUM'] > 0), 'COVERAGE_RATE'] = 1.0
    
    # Identify items needing adjustment
    needs_adjustment = (df['COVERAGE_RATE'] < 0.50) & (df['BUY_PRICE_NUM'] > 0)
    
    # Calculate target EX VAT price for a 50% margin
    df['NEW_PRICE_EX_VAT_NUM'] = df['PRICE_EX_VAT_NUM']
    df.loc[needs_adjustment, 'NEW_PRICE_EX_VAT_NUM'] = df.loc[needs_adjustment, 'BUY_PRICE_NUM'] / 0.50
    
    # Calculate inc VAT and beautify
    df['NEW_PRICE_NUM'] = df['NEW_PRICE_EX_VAT_NUM'] * (1 + VAT_RATE)
    df['FINAL_PRICE_NUM'] = df['PRICE_NUM']
    df.loc[needs_adjustment, 'FINAL_PRICE_NUM'] = df.loc[needs_adjustment, 'NEW_PRICE_NUM'].apply(beautify_price)
    
    # Recalculate final metrics
    df['FINAL_PRICE_EX_VAT'] = df['FINAL_PRICE_NUM'] / (1 + VAT_RATE)
    df['FINAL_COVERAGE_RATE'] = np.where(
        df['FINAL_PRICE_EX_VAT'] > 0,
        (df['FINAL_PRICE_EX_VAT'] - df['BUY_PRICE_NUM']) / df['FINAL_PRICE_EX_VAT'],
        0.0
    )
    df.loc[(df['BUY_PRICE_NUM'] == 0) & (df['FINAL_PRICE_EX_VAT'] > 0), 'FINAL_COVERAGE_RATE'] = 1.0

    # Format the outputs
    df['PRICE'] = df['FINAL_PRICE_NUM'].apply(format_dk)
    df['PRICE_EX_VAT'] = df['FINAL_PRICE_EX_VAT'].apply(format_dk)
    df['COVERAGE_RATE_%'] = (df['FINAL_COVERAGE_RATE'] * 100).round(2).astype(str).str.replace('.', ',') + '%'
    
    # Reorganize columns for final export
    export_cols = ['PRODUCT_ID', 'TITLE_DK', 'NUMBER', 'BUY_PRICE', 'PRICE_EX_VAT', 'PRICE', 'COVERAGE_RATE_%', 'VARIANT_ID', 'VARIANT_TYPES']
    final_df = df[export_cols]
    
    st.success(f"Successfully processed {len(df)} products! Adjusted prices for {needs_adjustment.sum()} items.")
    st.dataframe(final_df.head(10)) # Show preview

    # Convert to CSV format (adding the top PRODUCTS line back)
    csv_data = "PRODUCTS\n" + final_df.to_csv(sep=';', index=False)
    
    st.download_button(
        label="Download Updated Products CSV",
        data=csv_data.encode('utf-8'),
        file_name="updated_products.csv",
        mime="text/csv"
    )