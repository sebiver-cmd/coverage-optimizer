import os
import io
import re
import time

import streamlit as st
import pandas as pd
import numpy as np
import math
from rapidfuzz import fuzz, process as rfprocess

try:
    import pdfplumber
    _PDF_SUPPORT = True
except ImportError:
    _PDF_SUPPORT = False

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
    background: linear-gradient(180deg, #E3F2FD 0%, #BBDEFB 100%);
}
section[data-testid="stSidebar"] * {
    color: #0D47A1 !important;
}
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stTextInput label {
    font-weight: 600;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: #1565C0 !important;
}
section[data-testid="stSidebar"] .stCaption {
    color: #1976D2 !important;
    font-weight: 700;
    letter-spacing: 0.08em;
}

/* ---- Metrics cards ---- */
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #B3E5FC;
    border-radius: 16px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 1px 3px rgba(2,136,209,0.06), 0 4px 12px rgba(2,136,209,0.04);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(2,136,209,0.12);
}
div[data-testid="stMetric"] label {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    color: #546E7A !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-weight: 700;
    font-size: 2rem;
    color: #01579B !important;
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
    box-shadow: 0 6px 20px rgba(2,136,209,0.20);
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #29B6F6 0%, #0288D1 100%);
    color: #fff !important;
}

/* ---- Download buttons ---- */
.stDownloadButton > button {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    background: #E1F5FE;
    border: 1px solid #B3E5FC;
    color: #01579B !important;
    transition: all 0.15s ease;
}
.stDownloadButton > button:hover {
    background: #B3E5FC;
    border-color: #81D4FA;
    box-shadow: 0 2px 8px rgba(2,136,209,0.10);
}
/* ---- Download section compact layout ---- */
.download-row {
    display: flex;
    gap: 0.75rem;
    align-items: stretch;
    margin-top: 0.25rem;
}
.download-row .stDownloadButton { flex: 1; }
/* ---- Supplier match score badge ---- */
.match-score {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
}
.match-score.high   { background: #c8e6c9; color: #1b5e20; }
.match-score.medium { background: #fff9c4; color: #f57f17; }
.match-score.low    { background: #ffcdd2; color: #b71c1c; }

/* ---- Expanders ---- */
.streamlit-expanderHeader {
    font-weight: 600;
    font-size: 0.95rem;
    color: #01579B;
    border-radius: 12px;
}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.25rem;
    background: #E1F5FE;
    border-radius: 12px;
    padding: 0.25rem;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    padding: 0.5rem 1.5rem;
    color: #546E7A;
    transition: all 0.15s ease;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: #ffffff !important;
    color: #01579B !important;
    box-shadow: 0 1px 3px rgba(2,136,209,0.12);
}

/* ---- Dataframes ---- */
.stDataFrame {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #B3E5FC;
}

/* ---- Dividers ---- */
hr {
    border: none;
    border-top: 1px solid #B3E5FC;
    margin: 2rem 0;
}

/* ---- File uploader ---- */
section[data-testid="stFileUploader"] {
    border: 2px dashed #81D4FA;
    border-radius: 16px;
    padding: 1rem;
    background: #E1F5FE;
    transition: all 0.2s ease;
}
section[data-testid="stFileUploader"]:hover {
    border-color: #0288D1;
    background: #B3E5FC;
}

/* ---- Section headers ---- */
.section-header {
    font-size: 1.15rem;
    font-weight: 700;
    color: #01579B;
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
.status-pill.dry  { background: #B3E5FC; color: #01579B; }

/* ---- Hero header ---- */
.hero-header {
    margin-bottom: 0.25rem;
    font-size: 2rem;
    font-weight: 800;
    color: #01579B;
    letter-spacing: -0.02em;
}
.hero-sub {
    color: #546E7A;
    margin-top: 0;
    font-size: 1.05rem;
    line-height: 1.5;
    max-width: 700px;
}
.hero-sub strong { color: #0288D1; }

/* ---- Info cards ---- */
.info-card {
    background: #E1F5FE;
    border: 1px solid #B3E5FC;
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 0.75rem;
}
.info-card h4 {
    margin: 0 0 0.4rem 0;
    color: #01579B;
    font-size: 0.95rem;
}
.info-card p {
    margin: 0;
    color: #546E7A;
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
    background: rgba(2,136,209,0.08);
}
</style>
""", unsafe_allow_html=True)

# --- Constants ---
VAT_RATE = 0.25  # 25% Danish VAT
MIN_COVERAGE_RATE = 0.50  # Minimum acceptable profit margin (50%)
BEAUTIFY_LAST_DIGIT = 9  # Prices are rounded up to end in this digit
PRICE_EPSILON = 0.001  # Tolerance for floating-point price comparisons
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

# --- Supplier price list: currency conversion rates to DKK ---
DEFAULT_CURRENCY_RATES = {
    'DKK': 1.0,
    'EUR': 7.46,
    'USD': 6.88,
    'GBP': 8.69,
    'SEK': 0.64,
    'NOK': 0.64,
    'CHF': 7.72,
    'PLN': 1.73,
    'CNY': 0.95,
}

# --- Multi-language column name patterns for supplier file auto-detection ---
_SKU_NAMES = [
    'sku', 'article', 'artikelnr', 'varenr', 'varenummer', 'item',
    'item number', 'itemnumber', 'part', 'part number', 'partnumber',
    'artikelnummer', 'product number', 'productnumber', 'produktnummer',
    'model', 'mpn', 'reference', 'ref', 'nummer', 'number',
    'art.nr', 'art. nr.', 'artnr', 'bestillingsnr', 'ordernumber',
    'artikelcode', 'itemcode', 'code', 'produktnr',
]
_PRICE_NAMES = [
    'price', 'pris', 'preis', 'unit price', 'enhedspris',
    'stückpreis', 'cost', 'cost price', 'kostpris', 'indkøbspris',
    'net', 'net price', 'nettopris', 'nettopreis', 'amount',
    'beløb', 'betrag', 'prix', 'precio', 'prezzo',
]
_DISCOUNT_NAMES = [
    'discount', 'rabat', 'rabatt', 'remise', 'descuento',
    'sconto', 'korting',
]
_CURRENCY_NAMES = ['currency', 'valuta', 'währung', 'devise', 'moneda']
_DESCRIPTION_NAMES = [
    'description', 'beskrivelse', 'beschreibung', 'name', 'navn',
    'titel', 'title', 'product', 'produkt', 'designation',
]


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
        # Guard against "nan"/"NaN" strings that float() would accept
        if price_str.strip().lower() == 'nan':
            return 0.0
        price_str = price_str.replace('.', '').replace(',', '.')
    try:
        val = float(price_str)
        # Reject NaN / Inf that slipped through
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return val
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
    if encoding == 'auto':
        encoding = detect_encoding(raw_bytes)

    df = pd.read_csv(io.BytesIO(raw_bytes), sep=';', skiprows=1, encoding=encoding)

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


def api_products_to_dataframe(products: list[dict]) -> pd.DataFrame:
    """Convert a list of API product dicts to a DataFrame matching the CSV format.

    Each product may have variants — those are expanded into separate rows
    (one per variant), mirroring the structure of a CSV export.
    """
    rows: list[dict] = []
    for p in products:
        pid = p.get('Id', '')
        title = p.get('Title', '')
        item_number = p.get('ItemNumber', '')
        price = p.get('Price', 0)
        buy_price = p.get('BuyingPrice', 0)
        # Producer is a User object in the API; extract the brand name from it.
        # After serialize_object() it becomes a dict with Company, Firstname, etc.
        _producer_raw = p.get('Producer')
        if isinstance(_producer_raw, dict):
            producer = str(_producer_raw.get('Company', '') or '').strip()
            if not producer:
                _fname = str(_producer_raw.get('Firstname', '') or '').strip()
                _lname = str(_producer_raw.get('Lastname', '') or '').strip()
                producer = ' '.join(filter(None, [_fname, _lname]))
        else:
            producer = str(_producer_raw or '').strip()

        variants = p.get('Variants') or []
        if isinstance(variants, dict):
            items = variants.get('item', [])
            if not isinstance(items, list):
                items = [items]
        elif isinstance(variants, list):
            items = variants
        else:
            items = []

        variant_types = p.get('VariantTypes') or ''
        if isinstance(variant_types, dict):
            vt_items = variant_types.get('item', [])
            if isinstance(vt_items, list):
                variant_types = ', '.join(
                    str(getattr(vt, 'Title', vt) if hasattr(vt, 'Title') else vt)
                    for vt in vt_items
                )
            else:
                variant_types = str(vt_items)
        elif not isinstance(variant_types, str):
            variant_types = str(variant_types)

        if items:
            for v in items:
                vid = v.get('Id', '') if isinstance(v, dict) else getattr(v, 'Id', '')
                vprice = v.get('Price', price) if isinstance(v, dict) else getattr(v, 'Price', price)
                vbuy = v.get('BuyingPrice', buy_price) if isinstance(v, dict) else getattr(v, 'BuyingPrice', buy_price)
                rows.append({
                    'PRODUCT_ID': format_int_col(pid),
                    'TITLE_DK': title,
                    'NUMBER': item_number,
                    'BUY_PRICE': format_dk(float(vbuy or 0)),
                    'PRICE': format_dk(float(vprice or 0)),
                    'VARIANT_ID': format_int_col(vid),
                    'VARIANT_TYPES': variant_types,
                    'PRODUCER': producer,
                })
        else:
            rows.append({
                'PRODUCT_ID': format_int_col(pid),
                'TITLE_DK': title,
                'NUMBER': item_number,
                'BUY_PRICE': format_dk(float(buy_price or 0)),
                'PRICE': format_dk(float(price or 0)),
                'VARIANT_ID': '',
                'VARIANT_TYPES': '',
                'PRODUCER': producer,
            })

    if not rows:
        raise ValueError("No products found in the API response.")

    df = pd.DataFrame(rows)

    # Create numeric helper columns matching parse_csv() output
    df['BUY_PRICE_NUM'] = df['BUY_PRICE'].apply(clean_price)
    df['PRICE_NUM'] = df['PRICE'].apply(clean_price)

    for col in ('VARIANT_TYPES', 'VARIANT_ID', 'PRODUCT_ID'):
        if col in df.columns:
            df[col] = df[col].apply(format_int_col)

    return df


def _build_brand_id_map(raw_products: list[dict]) -> dict[str, int]:
    """Build a brand-name → ProducerId mapping from raw API product dicts.

    Used to enable targeted ``Product_GetByBrand`` calls on subsequent
    fetches instead of downloading the full product catalogue.
    """
    brand_id_map: dict[str, int] = {}
    for p in raw_products:
        _pr = p.get("Producer")
        if isinstance(_pr, dict):
            _name = str(_pr.get("Company", "") or "").strip()
        else:
            _name = str(_pr or "").strip()
        _pid = p.get("ProducerId")
        if _name and _pid:
            try:
                brand_id_map[_name] = int(_pid)
            except (ValueError, TypeError):
                pass
    return brand_id_map


# ---------------------------------------------------------------------------
# Supplier price-list helpers
# ---------------------------------------------------------------------------

def _detect_column(df_columns, patterns):
    """Find the best matching column name from a list of known patterns."""
    lower_map = {c.lower().strip(): c for c in df_columns}
    for pat in patterns:
        if pat in lower_map:
            return lower_map[pat]
    for pat in patterns:
        for lc, orig in lower_map.items():
            if pat in lc:
                return orig
    return None


def normalize_sku(sku: str) -> str:
    """Normalise a SKU for fuzzy comparison.

    Strips common prefixes (e.g. ``TO-``, ``DK-``), removes all
    separators (``-``, ``_``, space, ``.``, ``/``) and uppercases
    the result so that ``"AFK 110"`` and ``"TO-AFK-110"`` both become
    ``"AFK110"``.
    """
    s = str(sku).upper().strip()
    s = re.sub(r'^[A-Z]{1,3}[-_]', '', s)
    s = re.sub(r'[-_\s./]', '', s)
    return s


def parse_supplier_file(raw_bytes: bytes, filename: str, encoding: str = 'auto'):
    """Parse a supplier CSV or PDF into a DataFrame.

    Returns a plain ``DataFrame`` with string columns.
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext == 'pdf':
        if not _PDF_SUPPORT:
            raise ValueError(
                "PDF support requires the *pdfplumber* package. "
                "Install it with: pip install pdfplumber"
            )
        tables = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    if table and len(table) > 1:
                        headers = [str(h or '').strip() for h in table[0]]
                        rows = [
                            [str(c or '').strip() for c in row]
                            for row in table[1:]
                        ]
                        tables.append(pd.DataFrame(rows, columns=headers))
        if not tables:
            raise ValueError(
                "No tables found in the PDF. "
                "Try converting it to CSV first."
            )
        return pd.concat(tables, ignore_index=True)

    # Assume CSV-like for everything else
    if encoding == 'auto':
        encoding = detect_encoding(raw_bytes)
    text = raw_bytes.decode(encoding, errors='replace')
    for sep in [';', ',', '\t', '|']:
        try:
            df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str)
            if len(df.columns) > 1:
                return df
        except Exception:
            continue
    raise ValueError(
        "Could not parse the supplier file with any common separator."
    )


def detect_supplier_columns(df):
    """Auto-detect SKU, price, discount, currency, and description columns."""
    return {
        'sku': _detect_column(df.columns, _SKU_NAMES),
        'price': _detect_column(df.columns, _PRICE_NAMES),
        'discount': _detect_column(df.columns, _DISCOUNT_NAMES),
        'currency': _detect_column(df.columns, _CURRENCY_NAMES),
        'description': _detect_column(df.columns, _DESCRIPTION_NAMES),
    }


def match_supplier_to_products(supplier_skus, product_skus, threshold=65):
    """Fuzzy-match supplier SKUs to product SKUs.

    Returns ``{supplier_sku: (product_sku, score)}`` for every match
    above *threshold*.
    """
    norm_to_orig: dict[str, str] = {}
    for sku in product_skus:
        norm = normalize_sku(sku)
        if norm and norm not in norm_to_orig:
            norm_to_orig[norm] = sku

    norm_list = list(norm_to_orig.keys())
    matches: dict[str, tuple[str, int]] = {}

    for sup_sku in supplier_skus:
        norm_sup = normalize_sku(sup_sku)
        if not norm_sup:
            continue
        if norm_sup in norm_to_orig:
            matches[sup_sku] = (norm_to_orig[norm_sup], 100)
            continue
        result = rfprocess.extractOne(
            norm_sup, norm_list, scorer=fuzz.ratio, score_cutoff=threshold,
        )
        if result:
            match_norm, score, _ = result
            matches[sup_sku] = (norm_to_orig[match_norm], int(score))

    return matches


def detect_discount_lines(df, discount_col=None):
    """Detect discount information in a supplier DataFrame.

    Scans a dedicated discount column (if given) and all text cells
    for multi-language discount patterns (English, Danish, German …).
    Returns a list of ``{row, discount_pct, source}`` dicts.
    """
    discounts: list[dict] = []

    if discount_col and discount_col in df.columns:
        for idx, val in df[discount_col].items():
            if pd.notna(val):
                cleaned = str(val).replace('%', '').replace(',', '.').strip()
                try:
                    pct = float(cleaned)
                    if 0 < pct <= 100:
                        discounts.append({
                            'row': idx,
                            'discount_pct': pct,
                            'source': f"Column '{discount_col}'",
                        })
                except ValueError:
                    pass

    patterns = [
        r'(\d+(?:[.,]\d+)?)\s*%\s*(?:discount|off|rabat|rabatt|remise|sconto|korting)',
        r'(?:discount|rabat|rabatt|remise|sconto|korting)\s*:?\s*(\d+(?:[.,]\d+)?)\s*%',
    ]
    for col in df.columns:
        if col == discount_col:
            continue
        for idx, val in df[col].items():
            if pd.notna(val):
                val_lower = str(val).lower()
                for pat in patterns:
                    m = re.search(pat, val_lower)
                    if m:
                        try:
                            pct = float(m.group(1).replace(',', '.'))
                            if 0 < pct <= 100:
                                discounts.append({
                                    'row': idx,
                                    'discount_pct': pct,
                                    'source': f"Text in '{col}': {str(val)[:60]}",
                                })
                        except (ValueError, IndexError):
                            pass

    return discounts


def optimize_prices(
    df: pd.DataFrame,
    price_pct: float = 0.0,
    original_buy_prices: "pd.Series | None" = None,
) -> tuple:
    """Apply optional sales-price adjustment, calculate coverage and optimise.

    Parameters
    ----------
    price_pct : float
        Percentage adjustment to apply to all sales prices (PRICE)
        before recalculating coverage (e.g. 10.0 = +10 %).
        Changes both incl-VAT and ex-VAT prices.
    original_buy_prices : pd.Series, optional
        Original ``BUY_PRICE_NUM`` values before per-line editing.
        When supplied, products whose BUY_PRICE was changed are
        included in the import / push set even when their coverage
        rate already meets the minimum threshold.
    """
    df = df.copy()

    # Detect manual BUY_PRICE edits
    if original_buy_prices is not None:
        buy_price_changed = df['BUY_PRICE_NUM'] != original_buy_prices
    else:
        buy_price_changed = pd.Series(False, index=df.index)

    # Reformat BUY_PRICE from (possibly per-line edited) numeric values
    df['BUY_PRICE'] = df['BUY_PRICE_NUM'].apply(format_dk)

    # Apply optional sales-price adjustment (incl VAT)
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

    # Format original price columns
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
        "Adjust Sales Price (%)",
        min_value=-50.0,
        max_value=200.0,
        value=0.0,
        step=0.5,
        help=(
            "Increase or decrease all sales prices by this percentage "
            "before recalculating coverage rates. Changes both incl-VAT "
            "and ex-VAT prices. For example, enter 10 to simulate a "
            "10 % price increase across all products."
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
        "Coverage Optimizer v1.6<br>"
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
    f"Upload a product CSV or import directly from the DanDomain API to "
    f"calculate coverage rates and automatically adjust prices to at least a "
    f"<strong>{int(MIN_COVERAGE_RATE * 100)}%</strong> profit margin. "
    f"Variant-aware — handles products with multiple variants correctly."
    f"</p>",
    unsafe_allow_html=True,
)

import_mode = st.radio(
    "Product source",
    ["📁 Upload CSV", "🔌 Import from API"],
    horizontal=True,
    label_visibility="collapsed",
)

parsed_df = None
_import_error = False

if import_mode == "📁 Upload CSV":
    uploaded_file = st.file_uploader(
        "Upload Product CSV", type=['csv'], label_visibility="collapsed",
    )

    if uploaded_file is not None:
        try:
            raw_bytes = uploaded_file.getvalue()
            parsed_df = parse_csv(raw_bytes, selected_encoding)
        except ValueError as exc:
            st.error(str(exc))
            _import_error = True
        except Exception as exc:
            st.error(f"Failed to process CSV: {exc}")
            _import_error = True

else:  # Import from API
    if not api_ready:
        st.markdown(
            '<div class="info-card">'
            "<h4>🔌 API Not Connected</h4>"
            "<p>Configure your DanDomain API credentials in the sidebar "
            "to import products directly from your webshop.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        # Filtering options
        api_filter_col1, api_filter_col2 = st.columns(2)
        with api_filter_col1:
            _api_brands_available = st.session_state.get("_api_brands", [])
            selected_brands = st.multiselect(
                "Filter by brand / producer",
                options=_api_brands_available,
                default=[],
                placeholder=(
                    "All brands (no filter)"
                    if _api_brands_available
                    else "Fetch products first to filter by brand"
                ),
                disabled=not _api_brands_available,
                help=(
                    "Select one or more brands to include. "
                    "Leave empty to include all brands. "
                    "Fetch products from the API first to populate this list."
                ),
            )
        with api_filter_col2:
            only_online = st.checkbox(
                "Only active (online) products",
                value=True,
                help="When checked, only products marked as 'online' are imported.",
            )

        if st.button("📥 Fetch Products from API", type="primary"):
            try:
                with st.spinner("Fetching products from the API…"):
                    progress_text = st.empty()

                    def _api_progress(count):
                        progress_text.text(f"Fetched {count} products…")

                    # If brands are already selected and we have their IDs
                    # from a previous fetch, use the targeted
                    # Product_GetByBrand call for each brand.
                    _brand_id_map = st.session_state.get(
                        "_api_brand_id_map", {},
                    )
                    _use_brand_fetch = (
                        selected_brands
                        and _brand_id_map
                        and all(b in _brand_id_map for b in selected_brands)
                    )

                    with DanDomainClient(api_username, api_password) as client:
                        if _use_brand_fetch:
                            raw_products = []
                            for brand_name in selected_brands:
                                bid = _brand_id_map[brand_name]
                                raw_products.extend(
                                    client.get_products_by_brand(
                                        brand_id=bid,
                                        progress_callback=_api_progress,
                                    )
                                )
                        else:
                            raw_products = client.get_products_batch(
                                batch_size=100,
                                progress_callback=_api_progress,
                            )
                    progress_text.empty()

                # Apply online filter – exclude products explicitly marked
                # as offline rather than requiring an explicit "online" flag,
                # so products with a missing / None Online field are kept.
                if only_online:
                    raw_products = [
                        p for p in raw_products
                        if p.get('Online') is not False
                        and str(p.get('Online', '')).lower() not in ('false', '0', 'no')
                    ]

                if not raw_products:
                    st.warning("No products matched the selected filters.")
                else:
                    raw_df = api_products_to_dataframe(raw_products)
                    # Sort by brand / producer so products are grouped
                    # alphabetically (the API does not guarantee order).
                    raw_df = raw_df.sort_values(
                        "PRODUCER", key=lambda s: s.str.lower(),
                    ).reset_index(drop=True)
                    st.session_state["_api_raw_df"] = raw_df
                    # Extract unique sorted non-empty brand names for the multiselect
                    brands = sorted({b for b in raw_df["PRODUCER"].tolist() if b})
                    st.session_state["_api_brands"] = brands
                    # Build brand-name → ProducerId mapping so that
                    # subsequent fetches can use Product_GetByBrand.
                    st.session_state["_api_brand_id_map"] = _build_brand_id_map(
                        raw_products,
                    )
                    st.success(
                        f"✅ Loaded **{len(raw_df)}** product rows "
                        f"({len(raw_products)} base products)."
                    )
            except (DanDomainAPIError, ValueError, AttributeError) as exc:
                st.error(f"❌ API import failed: {exc}")
                _import_error = True

        # Derive parsed_df from cached raw data, applying brand filter from multiselect
        if "_api_raw_df" in st.session_state:
            _raw_df = st.session_state["_api_raw_df"]
            if selected_brands:
                parsed_df = _raw_df[_raw_df["PRODUCER"].isin(selected_brands)].reset_index(drop=True)
            else:
                parsed_df = _raw_df

if parsed_df is not None and not _import_error:
    # --- Apply persisted BUY_PRICE edits from data-editor state ---
    work_df = parsed_df.copy()
    for key in ("_ed_all", "_ed_adj", "_ed_imp"):
        for row_str, changes in (
            st.session_state.get(key, {}).get("edited_rows", {}).items()
        ):
            if "BUY_PRICE" not in changes:
                continue
            row_idx = int(row_str)
            # For filtered editors the positional index must be
            # mapped back to the original DataFrame index.
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

    # Show coverage rate change when price adjustment is active
    if total:
        # Baseline coverage: from original parsed data (no price adjustment)
        base_ex_vat = parsed_df['PRICE_NUM'] / (1 + VAT_RATE)
        base_coverage = calc_coverage_rate(
            parsed_df.assign(PRICE_EX_VAT_NUM=base_ex_vat),
            'PRICE_EX_VAT_NUM', 'BUY_PRICE_NUM',
        )
        base_avg = (base_coverage * 100).mean()
        # Current avg coverage from optimize_prices output (COVERAGE_RATE_%)
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
    # Build display DataFrames with a numeric BUY_PRICE column
    # so that the data editor can provide inline editing.
    _buy_price_col = "BUY_PRICE"
    _display_all = final_df.copy()
    _display_all[_buy_price_col] = work_df['BUY_PRICE_NUM']

    _disabled_cols = [
        c for c in _display_all.columns if c != _buy_price_col
    ]
    _col_config = {
        _buy_price_col: st.column_config.NumberColumn(
            "BUY_PRICE",
            help="Cost price – edit to match current supplier price",
            format="%.2f",
            min_value=0.0,
        ),
    }

    tab_all, tab_adjusted, tab_import, tab_supplier = st.tabs([
        "📋 All Products",
        "⚡ Adjusted Only",
        "📦 Import Preview",
        "📄 Supplier Match",
    ])

    with tab_all:
        st.data_editor(
            _display_all,
            disabled=_disabled_cols,
            column_config=_col_config,
            use_container_width=True,
            hide_index=True,
            key="_ed_all",
        )

    with tab_adjusted:
        _display_adj = _display_all[adjusted_mask].reset_index(drop=True)
        # Store the original-index mapping so edits can be applied
        st.session_state["_adj_index_map"] = list(
            _display_all.index[adjusted_mask]
        )
        if _display_adj.empty:
            st.info("✅ All products already meet the minimum margin — no adjustments needed.")
        else:
            st.data_editor(
                _display_adj,
                disabled=_disabled_cols,
                column_config=_col_config,
                use_container_width=True,
                hide_index=True,
                key="_ed_adj",
            )

    with tab_import:
        if import_df.empty:
            st.info("✅ No products needed adjustment — nothing to import.")
        else:
            adjusted_full = _display_all[adjusted_mask]
            # Store the original-index mapping for import preview edits
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
                        help="Cost price – edit to match current supplier price",
                        format="%.2f",
                        min_value=0.0,
                    ),
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

    # --- Supplier Match Tab ---
    with tab_supplier:
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

                # Let user confirm / override detected columns
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

                # Currency & match settings
                cur_col1, cur_col2, cur_col3 = st.columns(3)
                with cur_col1:
                    # Try to auto-detect currency from data
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

                # Perform matching
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

                    # Detect discounts
                    disc_col_name = (
                        sup_disc_col if sup_disc_col != '(none)' else None
                    )
                    disc_lines = detect_discount_lines(sup_df, disc_col_name)

                    if disc_lines:
                        with st.expander(
                            f"💰 {len(disc_lines)} discount line(s) detected",
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
                        # Build match results table
                        match_rows = []
                        for sup_sku, (prod_sku, score) in matches.items():
                            sup_row = sup_df.loc[
                                sup_df[sup_sku_col].astype(str).str.strip() == sup_sku
                            ]
                            if sup_row.empty:
                                continue
                            raw_price = str(
                                sup_row.iloc[0][sup_price_col]
                            )
                            price_val = clean_price(raw_price)

                            # Apply discount if found for this row
                            row_idx = sup_row.index[0]
                            disc_pct = 0.0
                            for d in disc_lines:
                                if d['row'] == row_idx:
                                    disc_pct = d['discount_pct']
                                    break
                            if disc_pct > 0:
                                price_val = price_val * (1 - disc_pct / 100)

                            # Convert to DKK
                            price_dkk = price_val * exchange_rate

                            # Find current cost in product data
                            prod_mask = (
                                work_df['NUMBER'].astype(str).str.strip()
                                == prod_sku
                            )
                            current_cost = 0.0
                            if prod_mask.any():
                                current_cost = work_df.loc[
                                    prod_mask, 'BUY_PRICE_NUM'
                                ].iloc[0]

                            # Calculate coverage change
                            prod_price_row = work_df.loc[prod_mask]
                            if not prod_price_row.empty:
                                sell_ex_vat = (
                                    prod_price_row['PRICE_NUM'].iloc[0]
                                    / (1 + VAT_RATE)
                                )
                                old_cov = (
                                    ((sell_ex_vat - current_cost)
                                     / sell_ex_vat * 100)
                                    if sell_ex_vat > 0 else 0.0
                                )
                                new_cov = (
                                    ((sell_ex_vat - price_dkk)
                                     / sell_ex_vat * 100)
                                    if sell_ex_vat > 0 else 0.0
                                )
                            else:
                                old_cov = 0.0
                                new_cov = 0.0

                            match_rows.append({
                                'Supplier SKU': sup_sku,
                                'Product SKU': prod_sku,
                                'Score': score,
                                f'Price ({sup_currency})': round(
                                    price_val, 2,
                                ),
                                'Discount %': disc_pct if disc_pct > 0 else '',
                                'Price (DKK)': round(price_dkk, 2),
                                'Current Cost': round(current_cost, 2),
                                'Diff': round(
                                    price_dkk - current_cost, 2
                                ),
                                'Old Coverage %': round(old_cov, 1),
                                'New Coverage %': round(new_cov, 1),
                                '_prod_sku': prod_sku,
                                '_new_cost': price_dkk,
                            })

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

                            # Apply button
                            if st.button(
                                "✅ Update Cost Prices from Supplier",
                                type="primary",
                                use_container_width=True,
                                key="_apply_supplier",
                            ):
                                updated = 0
                                for row in match_rows:
                                    prod_sku = row['_prod_sku']
                                    new_cost = row['_new_cost']
                                    mask = (
                                        work_df['NUMBER']
                                        .astype(str).str.strip()
                                        == prod_sku
                                    )
                                    if mask.any():
                                        work_df.loc[
                                            mask, 'BUY_PRICE_NUM'
                                        ] = new_cost
                                        updated += mask.sum()

                                if updated:
                                    # Persist edits so they survive
                                    # Streamlit re-runs
                                    if "_ed_all" not in st.session_state:
                                        st.session_state["_ed_all"] = {
                                            "edited_rows": {},
                                        }
                                    elif "edited_rows" not in st.session_state["_ed_all"]:
                                        st.session_state["_ed_all"]["edited_rows"] = {}
                                    edits = st.session_state["_ed_all"]["edited_rows"]
                                    for row in match_rows:
                                        prod_sku = row['_prod_sku']
                                        mask = (
                                            work_df['NUMBER']
                                            .astype(str).str.strip()
                                            == prod_sku
                                        )
                                        for idx in work_df.index[mask]:
                                            edits[str(idx)] = {
                                                "BUY_PRICE": row[
                                                    '_new_cost'
                                                ],
                                            }
                                    st.success(
                                        f"✅ Updated cost prices for "
                                        f"{updated} product row(s). "
                                        f"Changes are reflected in all "
                                        f"tabs."
                                    )
                                    st.rerun()
                                else:
                                    st.warning(
                                        "No products were updated."
                                    )
                else:
                    st.info(
                        "Select the **SKU** and **Price** columns above "
                        "to start matching."
                    )
        else:
            st.info(
                "📄 Upload a supplier price list (CSV or PDF) to match "
                "SKUs and update cost prices automatically."
            )

    # --- Downloads (compact) ---
    st.markdown(
        '<div class="section-header" style="margin-top:1rem;">'
        '📥 Download Reports</div>',
        unsafe_allow_html=True,
    )
    dl_col1, dl_col2 = st.columns(2, gap="small")

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
                    pid_raw = row.get('PRODUCT_ID', '')
                    pid = '' if pd.isna(pid_raw) else str(pid_raw).strip()
                    pnum = str(row['NUMBER']).strip()
                    new_price_val = clean_price(str(row['NEW_PRICE']))
                    orig_price_val = clean_price(str(row['PRICE']))
                    vid_raw = row.get('VARIANT_ID', '')
                    if pd.isna(vid_raw) or vid_raw == '':
                        vid = ''
                    else:
                        vid = str(vid_raw).strip()
                        # Normalise numeric IDs ("123.0" → "123")
                        # and treat zero as "no variant".
                        try:
                            n = float(vid)
                            vid = '' if n == 0 else str(int(n))
                        except (ValueError, OverflowError):
                            pass
                    vtypes_raw = row.get('VARIANT_TYPES', '')
                    vtypes = '' if pd.isna(vtypes_raw) else str(vtypes_raw).strip()
                    sales_price_changed = (
                        abs(new_price_val - orig_price_val) > PRICE_EPSILON
                    )
                    has_buy_change = bp_changed.loc[idx]
                    if pnum and (sales_price_changed or has_buy_change):
                        entry = {
                            "product_id": pid,
                            "product_number": pnum,
                            "variant_id": vid,
                            "variant_types": vtypes,
                        }
                        if sales_price_changed and new_price_val > 0:
                            entry["new_price"] = new_price_val
                        if has_buy_change:
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
                    dry_cols = ['product_id', 'product_number']
                    dry_labels = ['Product ID', 'Product Number']
                    if 'new_price' in dry_df.columns:
                        dry_cols.append('new_price')
                        dry_labels.append('New Price')
                    dry_cols.extend(['variant_id', 'variant_types'])
                    dry_labels.extend(['Variant ID', 'Variant Types'])
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