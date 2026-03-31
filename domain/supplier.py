"""Supplier price-list parsing and fuzzy SKU matching — no Streamlit dependency.

Handles CSV / PDF supplier files, auto-detects column mappings, performs
fuzzy SKU matching against the product catalogue, and detects discount
lines in multi-language formats.
"""

from __future__ import annotations

import io
import re

import pandas as pd
from rapidfuzz import fuzz, process as rfprocess

try:
    import pdfplumber
    _PDF_SUPPORT = True
except ImportError:
    _PDF_SUPPORT = False


# --- Encoding helpers ---

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


# --- Currency conversion rates to DKK ---

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


# --- Multi-language column name patterns for auto-detection ---

_SKU_NAMES = [
    'sku', 'article', 'article no', 'artikelnr', 'varenr', 'varenummer',
    'item', 'item number', 'itemnumber', 'part', 'part number',
    'partnumber', 'artikelnummer', 'product number', 'productnumber',
    'produktnummer', 'model', 'mpn', 'reference', 'ref', 'nummer',
    'number', 'art.nr', 'art. nr.', 'artnr', 'bestillingsnr',
    'ordernumber', 'artikelcode', 'itemcode', 'code', 'produktnr',
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


def _dedupe_columns(headers: list[str]) -> list[str]:
    """Make column names unique to prevent concat/reindex errors."""
    seen: dict[str, int] = {}
    result: list[str] = []
    for h in headers:
        if h in seen:
            seen[h] += 1
            result.append(f"{h}_{seen[h]}" if h else f"_{seen[h]}")
        else:
            seen[h] = 0
            result.append(h)
    return result


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
                        headers = _dedupe_columns(
                            [str(h or '').strip() for h in table[0]]
                        )
                        rows = [
                            [str(c or '').strip() for c in row]
                            for row in table[1:]
                        ]
                        tables.append(pd.DataFrame(rows, columns=headers))
        if tables:
            try:
                return pd.concat(tables, ignore_index=True)
            except Exception:
                tables = []

        # Retry with relaxed table-detection (text-based strategies)
        _text_settings = {
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
        }
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables(table_settings=_text_settings) or []):
                    if table and len(table) > 1 and len(table[0]) > 1:
                        headers = _dedupe_columns(
                            [str(h or '').strip() for h in table[0]]
                        )
                        # Skip tables where the majority of headers are empty
                        # — those are unlikely to be real structured data.
                        non_empty = sum(
                            1 for h in headers
                            if h and not h.startswith('_')
                            and len(h) > 1 and not h.isdigit()
                        )
                        if non_empty < max(2, len(headers) // 2):
                            continue
                        rows = [
                            [str(c or '').strip() for c in row]
                            for row in table[1:]
                        ]
                        tables.append(pd.DataFrame(rows, columns=headers))
        if tables:
            try:
                return pd.concat(tables, ignore_index=True)
            except Exception:
                tables = []

        # Fallback: extract raw text and parse as CSV-like data
        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        if text_parts:
            full_text = '\n'.join(text_parts)
            for sep in [';', ',', '\t', '|']:
                try:
                    df = pd.read_csv(
                        io.StringIO(full_text), sep=sep, dtype=str,
                    )
                    if len(df.columns) > 1:
                        return df
                except (pd.errors.ParserError, pd.errors.EmptyDataError,
                        ValueError, TypeError):
                    continue
            # Try whitespace separator (common in pdfplumber text output)
            try:
                df = pd.read_csv(
                    io.StringIO(full_text), sep=r'\s{2,}',
                    engine='python', dtype=str,
                )
                if len(df.columns) > 1:
                    return df
            except (pd.errors.ParserError, pd.errors.EmptyDataError,
                    ValueError, TypeError):
                pass

            # Try regex extraction for invoice / order-style PDFs whose
            # lines follow:  ItemNo  ArticleNo  Description  Qty+Unit  Price  Total
            _item_re = re.compile(
                r'^\s*(\d{2,4})\s+'
                r'(.+?)\s+'
                r'(\d+\s*(?:pcs|prs|Paar|Stk|St|ml|kg|sets?|pieces?)\w*)\s+'
                r'([\d]+[,.][\d]+)\s+'
                r'([\d]+[,.]\d+)\s*$',
                re.IGNORECASE | re.MULTILINE,
            )
            _item_matches = _item_re.findall(full_text)
            if _item_matches:
                _parsed_rows: list[dict] = []
                for _m in _item_matches:
                    _art_desc = _m[1].strip()
                    _art_split = re.match(
                        r'^([A-Z0-9][A-Z0-9 ./-]*?\d[\w]*)\s+(.+)$',
                        _art_desc,
                    )
                    if _art_split:
                        _art = _art_split.group(1).strip()
                        _desc = _art_split.group(2).strip()
                    else:
                        _art = _art_desc
                        _desc = ''
                    _parsed_rows.append({
                        'Item': _m[0],
                        'Article No': _art,
                        'Designation': _desc,
                        'Qty': _m[2],
                        'Unit Price': _m[3],
                        'Item Value': _m[4],
                    })
                return pd.DataFrame(_parsed_rows).astype(str)

        raise ValueError(
            "No tables found in the PDF. "
            "Try converting it to CSV first."
        )

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
