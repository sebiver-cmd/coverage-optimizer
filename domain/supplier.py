"""Supplier price-list parsing and fuzzy SKU matching — no Streamlit dependency.

Handles CSV / PDF supplier files, auto-detects column mappings, performs
fuzzy SKU matching against the product catalogue, and detects discount
lines in multi-language formats.

The core matching functions (``match_supplier_to_products``,
``normalize_sku``, etc.) live in :mod:`domain.invoice_ean` which is the
single source of truth for all SKU matching logic.  They are re-exported
here for backward compatibility.
"""

from __future__ import annotations

import io
import re

import pandas as pd

try:
    import pdfplumber
    _PDF_SUPPORT = True
except ImportError:
    _PDF_SUPPORT = False

# Lazy re-export of matching functions from the canonical location
# (domain.invoice_ean) so that existing callers keep working without
# introducing a circular import.
_REEXPORTED = frozenset({
    'normalize_sku', 'match_supplier_to_products', 'search_products',
    'suggest_column_mapping',
})


def __getattr__(name: str):
    if name in _REEXPORTED:
        import domain.invoice_ean as _ean
        return getattr(_ean, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    'prod.-nr.', 'prod.nr.', 'prod-nr',
]
_PRICE_NAMES = [
    'price', 'pris', 'preis', 'unit price', 'enhedspris',
    'stückpreis', 'cost', 'cost price', 'kostpris', 'indkøbspris',
    'net', 'net price', 'nettopris', 'nettopreis', 'amount',
    'beløb', 'betrag', 'prix', 'precio', 'prezzo',
]
_QTY_NAMES = [
    'quantity', 'qty', 'antal', 'anzahl', 'amount', 'count',
    'mængde', 'stk', 'pcs', 'units', 'beløb', 'menge',
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

#: Mapping from internal field name to the pattern list used by heuristics.
_FIELD_PATTERNS: dict[str, list[str]] = {
    'sku': _SKU_NAMES,
    'price': _PRICE_NAMES,
    'qty': _QTY_NAMES,
    'discount': _DISCOUNT_NAMES,
    'currency': _CURRENCY_NAMES,
    'description': _DESCRIPTION_NAMES,
}


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


def _guess_candidates(headers: list[str]) -> dict[str, list[str]]:
    """Return likely header candidates for each internal field based on
    regex/name hints.

    This is the single place where header-name heuristics live.  The
    returned dict maps each internal field (``'sku'``, ``'qty'``,
    ``'price'``, ``'description'``, ``'discount'``, ``'currency'``) to
    a list of candidate column names from *headers* that look like a
    plausible match, ordered best-first.
    """
    lower_map = {h.lower().strip(): h for h in headers}
    result: dict[str, list[str]] = {}

    for field, patterns in _FIELD_PATTERNS.items():
        candidates: list[str] = []
        seen: set[str] = set()

        # Pass 1: exact match on lowercased header
        for pat in patterns:
            if pat in lower_map:
                orig = lower_map[pat]
                if orig not in seen:
                    candidates.append(orig)
                    seen.add(orig)

        # Pass 2: substring match
        for pat in patterns:
            for lc, orig in lower_map.items():
                if pat in lc and orig not in seen:
                    candidates.append(orig)
                    seen.add(orig)

        result[field] = candidates

    return result


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
            except (pd.errors.InvalidIndexError, ValueError):
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
            except (pd.errors.InvalidIndexError, ValueError):
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
                r'(\d+[,.]\d+)\s+'
                r'(\d+[,.]\d+)\s*$',
                re.IGNORECASE | re.MULTILINE,
            )
            _item_matches = _item_re.findall(full_text)
            if _item_matches:
                _parsed_rows: list[dict] = []
                for _m in _item_matches:
                    _art_desc = _m[1].strip()
                    _art_split = re.match(
                        r'^([A-Z0-9][A-Z0-9 ./,-]*?\d[\w]*'
                        r'(?:\s+[A-Z]{1,4})?'
                        r')\s+(.+)$',
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


def _heuristic_detect_supplier_columns(df):
    """Pure heuristic/regex column detection for supplier files.

    Returns ``{'sku': ..., 'price': ..., 'discount': ..., 'currency': ...,
    'description': ...}`` where each value is the detected column name or
    ``None``.  This is the internal fallback used when the LLM path is
    unavailable or fails.
    """
    candidates = _guess_candidates(list(df.columns))
    return {
        field: (candidates[field][0] if candidates[field] else None)
        for field in ('sku', 'price', 'discount', 'currency', 'description')
    }


def detect_supplier_columns(df, *, api_key=None, model='gpt-4o-mini',
                            llm_call=None):
    """Auto-detect SKU, price, discount, currency, and description columns.

    Uses :func:`~domain.invoice_ean.suggest_column_mapping` (LLM-based) as
    the primary detection mechanism.  Falls back to pure heuristic matching
    when the LLM is unavailable, fails, or returns an unusable mapping.

    The optional keyword arguments are forwarded to
    ``suggest_column_mapping``.
    """
    # --- LLM-first path ---
    try:
        import domain.invoice_ean as _ean
        mapping = _ean.suggest_column_mapping(
            df, api_key=api_key, model=model, llm_call=llm_call,
        )
    except Exception:
        mapping = None

    if mapping and any(mapping.get(f) for f in ('sku', 'price')):
        # Fill in any gaps from heuristics
        candidates = _guess_candidates(list(df.columns))
        used_cols = set(mapping.values())
        for field in ('sku', 'price', 'discount', 'currency', 'description'):
            if not mapping.get(field) and candidates[field]:
                # Only fill unambiguous gaps (single candidate not already
                # used by another field)
                free = [c for c in candidates[field] if c not in used_cols]
                if len(free) == 1:
                    mapping[field] = free[0]
                    used_cols.add(free[0])
        return {
            field: mapping.get(field)
            for field in ('sku', 'price', 'discount', 'currency',
                          'description')
        }

    # --- Heuristic fallback ---
    return _heuristic_detect_supplier_columns(df)


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
