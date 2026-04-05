"""Supplier price-list parsing and fuzzy SKU matching — no Streamlit dependency.

Handles CSV / PDF supplier files, auto-detects column mappings, performs
fuzzy SKU matching against the product catalogue, and detects discount
lines in multi-language formats.

The core matching functions (``match_supplier_to_products``,
``normalize_sku``, etc.) live in :mod:`domain.invoice_ean` which is the
single source of truth for all SKU matching logic.  They are re-exported
here for backward compatibility.

.. rubric:: Matching + Transformation Pipeline

The end-to-end flow for supplier/invoice data is:

1. **File parsing** — :func:`parse_supplier_file` reads CSV or PDF into
   a raw :class:`~pandas.DataFrame` with string columns.
2. **Column detection** — :func:`detect_supplier_columns` (or
   :func:`~domain.invoice_ean.detect_invoice_columns`) identifies which
   columns hold SKU, price, quantity, etc.  Uses LLM first, heuristics
   as fallback.
3. **SKU matching** — :func:`~domain.invoice_ean.match_supplier_to_products`
   fuzzy-matches supplier SKUs to the product catalogue.
4. **Export / result construction** —
   :func:`~domain.invoice_ean.build_export_from_matches` builds the final
   DataFrame with one row per logical line item.

**Design invariant**: after step 4, each (SKU, Product Number, Variant Name)
combination appears exactly once.  All reshaping (variant narrowing,
deduplication) happens inside DataFrame operations — no ad-hoc row-level
loops construct parallel data structures.

.. rubric:: Duplication prevention

Historical duplication of SKUs (e.g. "FB 400 XXS" appearing twice) was
caused by:

- Multiple catalogue rows sharing the same ``(NUMBER, VARIANT_TYPES)``
  with different ``VARIANT_ID`` values.
- Variant narrowing returning multiple rows when variant text was a
  substring of another variant (e.g. "XS" inside "XXS").

Both are now guarded by :func:`~domain.invoice_ean._dedupe_product_rows`
and :func:`~domain.invoice_ean._normalize_export_df`.
"""

from __future__ import annotations

import io
import json
import logging
import os
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
    # NOTE: 'beløb' (Danish for 'amount') intentionally appears in both
    # _QTY_NAMES and _PRICE_NAMES — it is contextually ambiguous and
    # _guess_candidates will surface it as a candidate for both fields,
    # letting the LLM or caller resolve the ambiguity.
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

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multilingual header/section detection for PDF line-item extraction
# ---------------------------------------------------------------------------

#: Max continuation lines to scan for size/colour info after a line item.
_MAX_CONTINUATION_LINES = 5

#: Max lines to feed to the LLM for line-item extraction (cost control).
_MAX_LLM_LINES = 80

#: Keywords that signal the start of a line-item section in multilingual invoices.
_LINE_SECTION_HEADERS = re.compile(
    r'\b(?:'
    r'Pos\b|Item\s*(?:no|number)?|Article|Artikel|Varenr|Produktnr|Prod\.\-Nr'
    r'|CODE\b|Vare\b'
    r')\b',
    re.IGNORECASE,
)

#: Keywords that signal non-line-item sections to skip.
_SKIP_SECTION_RE = re.compile(
    r'(?:'
    r'Customstariffe|Zolltarifnummer|Herkunftsland'
    r'|Bankverbindung|Gerichtsstand|Geschäftsführer'
    r'|Finanzamt|IBAN|BIC\b|CVR\b|VAT\s*(?:Details|amount)'
    r'|Rechnungsadresse|Lieferadresse|Shipping\s*Addres'
    r'|Fakturaadresse|Leveringsadresse|Delivery\s*(?:address|to)'
    r'|Payment\s*(?:ref|terms)|Betalingsbeting|Terms\s*of\s*(?:payment|Shipment)'
    r'|PAYMENT\s*TERMS'
    r'|Please\s*note|BENYT\s*FI-KODE|Ordrelinje\s*total|Ordre\s*total'
    r'|Total\s*f.r\s*moms|Total\s*moms|Fakturabeløb'
    r'|Der\s*opkræves|rentedebitering'
    r'|Inkl\.\s*MwSt|Shipment\s*weight'
    r'|Document\s*text|Applied\s*on\s*position'
    r'|Adresse\s+Tlf|Meterbuen'
    r')',
    re.IGNORECASE,
)

#: Regex for EAN detection: 8, 12, or 13 consecutive digits (with optional
#: spaces) that could be an EAN barcode.
_EAN_RE = re.compile(r'\b(\d[\d ]{6,12}\d)\b')
_VALID_EAN_LENGTHS = (8, 12, 13)


def _detect_ean_in_text(text: str) -> str:
    """Find the first valid EAN (8/12/13 digits) in *text*.

    Returns the bare digit string or empty string if none found.
    """
    for m in _EAN_RE.finditer(text):
        digits = m.group(1).replace(' ', '')
        if len(digits) in _VALID_EAN_LENGTHS and digits.isdigit():
            return digits
    return ''


# ---------------------------------------------------------------------------
# PDF text-based line-item extraction patterns (per layout type)
# ---------------------------------------------------------------------------

def _extract_line_items_swedish_invoice(full_text: str) -> pd.DataFrame | None:
    r"""Extract line items from Swedish-style invoices (Budo & Fitness).

    Layout: lines split across two text lines::

        1 14105-       BUDO-NORD KAMPVÄST      2.00 STYCK 399.20 0.00% 50% 399.20
          003-XXS      Blå-Röd XXS/0

    The item number and description span two lines.
    """
    # Look for the header line to confirm layout
    if not re.search(
        r'Pos\s+Item\s*no\s+Description\s+Quantity\s+Unit\s+Unit\s*price',
        full_text, re.IGNORECASE,
    ):
        return None

    # Match pairs of lines:
    # Line 1: Pos  ItemNo-   Description...  Qty  Unit  UnitPrice  VAT%  Disc%  Total
    # Line 2:      -suffix   desc-continued
    _line1_re = re.compile(
        r'^\s*(\d{1,4})\s+'          # Pos
        r'(\S+?-)\s+'                 # Item no prefix (e.g. "14105-")
        r'(.+?)\s+'                   # Description part 1
        r'(\d+(?:[.,]\d+)?)\s+'       # Quantity
        r'(\w+)\s+'                   # Unit (STYCK, ST, etc.)
        r'(\d+(?:[.,]\d+)?)\s+'       # Unit price
        r'(\d+(?:[.,]\d+)?%?)\s+'     # VAT %
        r'(\d+(?:[.,]\d+)?%?)\s+'     # Disc %
        r'([\d.,]+)\s*$',             # Total
        re.MULTILINE,
    )
    _line2_re = re.compile(
        r'^\s*(\d[\w-]+)\s+(.*?)\s*$',   # Item no suffix (e.g. "003-XXS") + desc
    )

    lines = full_text.splitlines()
    parsed_rows: list[dict] = []

    i = 0
    while i < len(lines):
        m1 = _line1_re.match(lines[i])
        if m1:
            item_no_prefix = m1.group(2)
            desc1 = m1.group(3).strip()
            qty = m1.group(4)
            unit_price = m1.group(6)
            discount = m1.group(8).replace('%', '')
            total = m1.group(9)

            # Try to read continuation line
            item_no_suffix = ''
            desc2 = ''
            if i + 1 < len(lines):
                m2 = _line2_re.match(lines[i + 1])
                if m2 and not _line1_re.match(lines[i + 1]):
                    item_no_suffix = m2.group(1)
                    desc2 = m2.group(2).strip()
                    i += 1

            sku = (item_no_prefix + item_no_suffix).strip()
            description = f"{desc1} {desc2}".strip() if desc2 else desc1
            ean_detected = _detect_ean_in_text(f"{sku} {description}")

            parsed_rows.append({
                'Article No': sku,
                'Description': description,
                'Qty': qty,
                'Unit Price': unit_price,
                'Discount': discount,
                'Line Total': total,
                'ean_detected': ean_detected,
            })
        i += 1

    if parsed_rows:
        return pd.DataFrame(parsed_rows).astype(str)
    return None


def _extract_line_items_danish_invoice(full_text: str) -> pd.DataFrame | None:
    r"""Extract line items from Danish-style invoices (NewWave / Craft).

    Layout: single-line items like::

        1910163-999000-7 EVOLVE PANTS M Black XL 26-03-26 1 320,00 58% 134,40

    Header: ``Vare Lev.dato Mængde Pris Rabat Beløb``
    """
    if not re.search(r'Vare\b.*Lev\.dato.*Mængde.*Pris.*Rabat.*Beløb',
                     full_text, re.IGNORECASE):
        return None

    # Match: SKU  Description  Date  Qty  Price  Discount%  Amount
    _line_re = re.compile(
        r'^(\d[\w-]+(?:-\d+)+)\s+'        # SKU (e.g. 1910163-999000-7)
        r'(.+?)\s+'                         # Description
        r'(\d{2}-\d{2}-\d{2})\s+'           # Delivery date
        r'(\d+)\s+'                          # Qty
        r'([\d.,]+)\s+'                      # Price
        r'(\d+%?)\s+'                        # Discount
        r'([\d.,]+)\s*$',                    # Amount
        re.MULTILINE,
    )

    parsed_rows: list[dict] = []
    for m in _line_re.finditer(full_text):
        sku = m.group(1).strip()
        description = m.group(2).strip()
        qty = m.group(4)
        price = m.group(5)
        discount = m.group(6).replace('%', '')
        amount = m.group(7)
        ean_detected = _detect_ean_in_text(f"{sku} {description}")

        parsed_rows.append({
            'Article No': sku,
            'Description': description,
            'Qty': qty,
            'Unit Price': price,
            'Discount': discount,
            'Line Total': amount,
            'ean_detected': ean_detected,
        })

    if parsed_rows:
        return pd.DataFrame(parsed_rows).astype(str)
    return None


def _extract_line_items_german_invoice(full_text: str) -> pd.DataFrame | None:
    r"""Extract line items from German-style invoices (ju-sports / Meiners).

    Layout: multi-line items::

        702074002 adidas Box-Top schwarz/weiß, ADIBTT02 5 16,25 € 81,25 €
        Größe: M
        Herkunftsland: CN
        Zolltarifnummer (nach HS): 6109902000

    Header: ``Prod.-Nr. Produkt / Dienst Anzahl USt. Stückpreis Gesamt``
    """
    if not re.search(
        r'Prod\.\-Nr\.\s+Produkt\s*/\s*Dienst\s+Anzahl',
        full_text, re.IGNORECASE,
    ):
        return None

    # Match item lines: ProdNr  Description  Qty  Price€  Total€
    _line_re = re.compile(
        r'^(\d{6,12})\s+'                         # Prod.-Nr.
        r'(.+?)\s+'                                 # Produkt / Dienst
        r'(\d+)\s+'                                 # Anzahl
        r'([\d.,]+)\s*€\s+'                         # Stückpreis
        r'([\d.,]+)\s*€\s*$',                       # Gesamt
        re.MULTILINE,
    )

    # Also gather size/colour info from continuation lines
    _size_re = re.compile(r'Größe:\s*(.+)', re.IGNORECASE)
    _color_re = re.compile(
        r'(?:Genaue\s+Farbbezeichnung|Farbe):\s*(.+?)(?:\s*\||$)',
        re.IGNORECASE,
    )

    lines = full_text.splitlines()
    parsed_rows: list[dict] = []

    for i, line in enumerate(lines):
        m = _line_re.match(line)
        if m:
            sku = m.group(1).strip()
            description = m.group(2).strip()
            qty = m.group(3)
            unit_price = m.group(4)
            total = m.group(5)

            # Scan next few lines for size/colour
            extras: list[str] = []
            for j in range(i + 1, min(i + _MAX_CONTINUATION_LINES, len(lines))):
                nxt = lines[j].strip()
                if _line_re.match(nxt):
                    break
                if _SKIP_SECTION_RE.search(nxt):
                    continue
                sm = _size_re.match(nxt)
                if sm:
                    extras.append(sm.group(1).strip())
                cm = _color_re.match(nxt)
                if cm:
                    extras.append(cm.group(1).strip())

            if extras:
                description = f"{description} ({', '.join(extras)})"
            ean_detected = _detect_ean_in_text(f"{sku} {description}")

            parsed_rows.append({
                'Article No': sku,
                'Description': description,
                'Qty': qty,
                'Unit Price': unit_price,
                'Line Total': total,
                'ean_detected': ean_detected,
            })

    if parsed_rows:
        return pd.DataFrame(parsed_rows).astype(str)
    return None


def _extract_line_items_spanish_proforma(full_text: str) -> pd.DataFrame | None:
    r"""Extract line items from Spanish-style proforma invoices (PROFORMA).

    Layout: single-line items::

        PR 1720-U-0 FOREARM MITT U 10,00 18,440 184,40
        PRO 15723-S-0 WT SHIN GUARD - "SILVER FIT" S 20,00 12,790 255,80

    Header: ``CODE DESCRIPTION UNITS PRICE % AMOUNT``
    """
    if not re.search(
        r'CODE\s+DESCRIPTION\s+UNITS\s+PRICE\s+%?\s*AMOUNT',
        full_text, re.IGNORECASE,
    ):
        return None

    # Match: CODE  DESCRIPTION  UNITS  PRICE  AMOUNT
    # The CODE has pattern like "PR 1720-U-0" or "PRO 15723-S-0"
    _line_re = re.compile(
        r'^(PRO?\s+\d[\w-]+(?:-[\w]+)*)\s+'   # CODE
        r'(.+?)\s+'                             # DESCRIPTION
        r'([\d.,]+)\s+'                         # UNITS
        r'([\d.,]+)\s+'                         # PRICE
        r'([\d.,]+)\s*$',                        # AMOUNT
        re.MULTILINE,
    )

    parsed_rows: list[dict] = []
    for m in _line_re.finditer(full_text):
        sku = m.group(1).strip()
        description = m.group(2).strip()
        qty = m.group(3)
        unit_price = m.group(4)
        amount = m.group(5)
        ean_detected = _detect_ean_in_text(f"{sku} {description}")

        parsed_rows.append({
            'Article No': sku,
            'Description': description,
            'Qty': qty,
            'Unit Price': unit_price,
            'Line Total': amount,
            'ean_detected': ean_detected,
        })

    if parsed_rows:
        return pd.DataFrame(parsed_rows).astype(str)
    return None


def _extract_line_items_generic(full_text: str) -> pd.DataFrame | None:
    """Generic line-item extraction as a last-resort regex fallback.

    Tries the existing item regex (ItemNo + ArticleNo + Designation +
    Qty+Unit + Price + Total) and also a broader pattern that catches
    lines with a numeric item/position number followed by a SKU-like
    code, quantities, and prices.
    """
    # Existing regex for DAX-style invoices
    _item_re = re.compile(
        r'^\s*(\d{2,4})\s+'
        r'(.+?)\s+'
        r'(\d+\s*(?:pcs|prs|Paar|Stk|St|ml|kg|sets?|pieces?|STYCK)\w*)\s+'
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
            ean_detected = _detect_ean_in_text(f"{_art} {_desc}")
            _parsed_rows.append({
                'Item': _m[0],
                'Article No': _art,
                'Designation': _desc,
                'Qty': _m[2],
                'Unit Price': _m[3],
                'Item Value': _m[4],
                'ean_detected': ean_detected,
            })
        return pd.DataFrame(_parsed_rows).astype(str)
    return None


def _extract_pdf_line_items_from_text(full_text: str) -> pd.DataFrame | None:
    """Try all layout-specific extractors in order, return first success.

    This is the main dispatcher for text-based PDF line-item extraction.
    It tries layout-specific patterns before falling back to generic ones.
    """
    for extractor in (
        _extract_line_items_swedish_invoice,
        _extract_line_items_danish_invoice,
        _extract_line_items_german_invoice,
        _extract_line_items_spanish_proforma,
        _extract_line_items_generic,
    ):
        result = extractor(full_text)
        if result is not None and not result.empty:
            return result
    return None


# ---------------------------------------------------------------------------
# LLM-assisted PDF line-item extraction
# ---------------------------------------------------------------------------

def _identify_line_item_section(full_text: str) -> str:
    """Extract the likely line-item section from full invoice text.

    Uses multilingual header detection and skip-section filtering to
    narrow down the text that should be fed to an LLM for structured
    extraction.
    """
    lines = full_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    blank_count = 0

    for line in lines:
        stripped = line.strip()

        # Start collecting when we see a header-like line
        if not in_section and _LINE_SECTION_HEADERS.search(stripped):
            in_section = True
            section_lines.append(stripped)
            blank_count = 0
            continue

        if in_section:
            # Stop at summary/footer sections
            if re.search(
                r'(?:Total|Summe|I alt|Subtotal|Net amount|Nettobetrag'
                r'|Amount without VAT|To pay|VAT Details'
                r'|Ordrelinje total|Ordre total'
                r'|TOTAL GROSS|TAX BASE)',
                stripped, re.IGNORECASE,
            ):
                break

            # Skip known non-line-item lines
            if _SKIP_SECTION_RE.search(stripped):
                continue

            if not stripped:
                blank_count += 1
                if blank_count > 3:
                    break
                continue
            else:
                blank_count = 0

            section_lines.append(stripped)

    return '\n'.join(section_lines)


def _build_line_item_extraction_prompt(text_section: str) -> str:
    """Build a prompt for LLM-based line-item extraction from PDF text."""
    # Limit to ~80 lines to keep costs down
    lines = text_section.splitlines()[:_MAX_LLM_LINES]
    sample = '\n'.join(lines)

    return (
        "You are a data extraction assistant. Below is text extracted from "
        "a PDF invoice or order document. Extract ALL line items as a JSON "
        "array.\n\n"
        "Each line item should be a JSON object with these fields:\n"
        "- sku: The article/item/product number or code\n"
        "- description: Product name or designation\n"
        "- qty: Quantity ordered/invoiced (number)\n"
        "- unit_price: Price per unit (string, keep original decimal format)\n"
        "- line_total: Total amount for this line (string)\n"
        "- ean: EAN/barcode if visible (string of 8/12/13 digits, or empty)\n"
        "- discount: Discount percentage if shown (string, or empty)\n\n"
        "Rules:\n"
        "- Extract ONLY actual product line items, not subtotals or headers\n"
        "- If a line item spans multiple text lines, combine them\n"
        "- Keep original number formatting (commas, periods)\n"
        "- Return ONLY the JSON array, no other text\n\n"
        f"Invoice text:\n{sample}\n"
    )


def _parse_pdf_line_items_llm(
    full_text: str,
    *,
    api_key: str | None = None,
    model: str = 'gpt-4o-mini',
    llm_call=None,
    tenant_id: str | None = None,
) -> pd.DataFrame | None:
    """LLM-assisted PDF line-item extraction.

    Identifies the likely line-item section, builds a prompt, and calls
    the LLM to produce structured JSON.  Returns a DataFrame or ``None``.

    Parameters
    ----------
    full_text : str
        The full text extracted from the PDF.
    api_key : str or None
        OpenAI-compatible API key.
    model : str
        Model name.
    llm_call : callable or None
        Injectable LLM call function.
    tenant_id : str or None
        Optional tenant identifier for LLM usage tracking (Task 5.3).
    """
    key = api_key or os.environ.get('OPENAI_API_KEY')
    if not key:
        return None

    section = _identify_line_item_section(full_text)
    if not section or len(section.strip()) < 20:
        return None

    prompt = _build_line_item_extraction_prompt(section)

    if llm_call is None:
        try:
            from domain.invoice_ean import _default_llm_call
            caller = _default_llm_call
        except ImportError:
            return None
    else:
        caller = llm_call

    raw_response = caller(prompt, key, model)
    if not raw_response:
        return None

    # Log LLM usage (Task 5.3)
    # Token estimate: ~4 chars per token; actual counts may differ for non-ASCII.
    try:
        prompt_tokens = len(prompt) // 4
        response_tokens = len(raw_response) // 4 if raw_response else 0
        _log.info(
            "LLM call completed",
            extra={
                "tenant_id": tenant_id,
                "tokens_used": prompt_tokens + response_tokens,
                "model": model,
            },
        )
    except Exception:
        pass

    # Parse the JSON array from the response
    text = raw_response.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

    # Find [ ... ] block
    bracket_start = text.find('[')
    bracket_end = text.rfind(']')
    if bracket_start == -1 or bracket_end == -1 or bracket_end <= bracket_start:
        _log.warning("No JSON array found in LLM line-item response")
        return None

    try:
        items = json.loads(text[bracket_start:bracket_end + 1])
    except (json.JSONDecodeError, ValueError):
        _log.warning("Failed to parse JSON from LLM line-item response")
        return None

    if not isinstance(items, list) or not items:
        return None

    # Normalize to DataFrame
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rows.append({
            'Article No': str(item.get('sku', '') or '').strip(),
            'Description': str(item.get('description', '') or '').strip(),
            'Qty': str(item.get('qty', '') or '').strip(),
            'Unit Price': str(item.get('unit_price', '') or '').strip(),
            'Line Total': str(item.get('line_total', '') or '').strip(),
            'Discount': str(item.get('discount', '') or '').strip(),
            'ean_detected': str(item.get('ean', '') or '').strip(),
        })

    if rows:
        return pd.DataFrame(rows).astype(str)
    return None


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


def _validate_table_concat(
    tables: list[pd.DataFrame],
) -> pd.DataFrame | None:
    """Concat extracted tables and validate the result is usable.

    Returns ``None`` if concatenation fails or the resulting DataFrame
    is mostly empty / NaN (which happens when pdfplumber extracts
    multiple incompatible table structures from the same PDF).
    """
    if not tables:
        return None

    # If all tables share compatible columns, concat them
    try:
        result = pd.concat(tables, ignore_index=True)
    except (pd.errors.InvalidIndexError, ValueError):
        return None

    # Must have at least 2 meaningful column names
    named_cols = sum(
        1 for c in result.columns
        if c and not c.startswith('_') and len(c) > 1 and not c.isdigit()
    )
    if named_cols < 2 or result.empty:
        return None

    # Check data quality: a good table shouldn't be mostly NaN.
    # Count cells that are non-empty (not '' and not NaN).
    total_cells = result.shape[0] * result.shape[1]
    if total_cells == 0:
        return None
    non_empty = result.apply(
        lambda col: col.astype(str).str.strip().replace('', pd.NA).notna().sum()
    ).sum()
    fill_ratio = non_empty / total_cells
    if fill_ratio < 0.3:
        return None

    # Must have at least 3 data rows to be considered a real line-item table
    if len(result) < 3:
        return None

    return result


def parse_supplier_file(raw_bytes: bytes, filename: str, encoding: str = 'auto',
                        *, llm_call=None):
    """Parse a supplier CSV or PDF into a DataFrame.

    Returns a plain ``DataFrame`` with string columns.

    Parameters
    ----------
    raw_bytes : bytes
        Raw file content.
    filename : str
        Original filename (used to detect extension).
    encoding : str
        Encoding hint for CSV files (``'auto'`` to detect).
    llm_call : callable or None
        Optional injectable LLM call function for PDF line-item extraction
        fallback.  Signature: ``(prompt, api_key, model) -> str | None``.
    """
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    if ext == 'pdf':
        if not _PDF_SUPPORT:
            raise ValueError(
                "PDF support requires the *pdfplumber* package. "
                "Install it with: pip install pdfplumber"
            )

        # --- Phase 1: pdfplumber table extraction (strict) ---
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
            result = _validate_table_concat(tables)
            if result is not None:
                return result
            tables = []

        # --- Phase 2: pdfplumber relaxed table-detection ---
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
            result = _validate_table_concat(tables)
            if result is not None:
                return result
            tables = []

        # --- Phase 3: text-based extraction ---
        text_parts: list[str] = []
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        if text_parts:
            full_text = '\n'.join(text_parts)

            # Phase 3a: Try layout-specific regex extractors
            regex_result = _extract_pdf_line_items_from_text(full_text)
            if regex_result is not None and not regex_result.empty:
                return regex_result

            # Phase 3b: Try CSV-like parsing
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
            # Try whitespace separator
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

            # Phase 3c: LLM-assisted extraction as last resort
            llm_result = _parse_pdf_line_items_llm(
                full_text, llm_call=llm_call,
            )
            if llm_result is not None and not llm_result.empty:
                return llm_result

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


# ---------------------------------------------------------------------------
# Debugging helpers
# ---------------------------------------------------------------------------

def debug_print_mapping(
    df: pd.DataFrame,
    mapping: dict | None = None,
    *,
    llm_raw: str | None = None,
    heuristic_repairs: dict | None = None,
    final_df: pd.DataFrame | None = None,
    out=None,
) -> str:
    """Return a human-readable summary of the column-mapping pipeline state.

    Designed for developer debugging of mapping errors.  Shows:

    - Raw DataFrame head (columns + first 3 rows).
    - The LLM's raw JSON response (if available).
    - The parsed mapping dict.
    - Any heuristic repairs that were applied.
    - The final unified DataFrame head (if available).

    Parameters
    ----------
    df : pd.DataFrame
        The raw parsed supplier/invoice DataFrame.
    mapping : dict or None
        The column mapping dict ``{'sku': col_name, ...}``.
    llm_raw : str or None
        The raw LLM response text (before parsing).
    heuristic_repairs : dict or None
        Any fields that were gap-filled by heuristics.
    final_df : pd.DataFrame or None
        The final export/unified DataFrame after all transformations.
    out : file-like or None
        If provided, also writes the summary to this stream.

    Returns
    -------
    str
        The formatted summary text.
    """
    lines: list[str] = []
    lines.append("=== Column Mapping Debug ===")
    lines.append(f"\nRaw DataFrame ({len(df)} rows, {len(df.columns)} cols):")
    lines.append(f"  Columns: {list(df.columns)}")
    if not df.empty:
        lines.append(f"  Head (3 rows):\n{df.head(3).to_string(index=False)}")
    else:
        lines.append("  (empty)")

    if llm_raw is not None:
        lines.append(f"\nLLM raw response:\n  {llm_raw!r}")

    if mapping is not None:
        lines.append(f"\nParsed mapping: {mapping}")
    else:
        lines.append("\nParsed mapping: None (LLM unavailable or failed)")

    if heuristic_repairs:
        lines.append(f"\nHeuristic repairs applied: {heuristic_repairs}")
    else:
        lines.append("\nHeuristic repairs: none")

    if final_df is not None:
        lines.append(
            f"\nFinal DataFrame ({len(final_df)} rows, "
            f"{len(final_df.columns)} cols):"
        )
        lines.append(f"  Columns: {list(final_df.columns)}")
        if not final_df.empty:
            lines.append(
                f"  Head (3 rows):\n{final_df.head(3).to_string(index=False)}"
            )
        else:
            lines.append("  (empty)")

    lines.append("\n=== End Debug ===")
    text = '\n'.join(lines)

    if out is not None:
        out.write(text)
        out.write('\n')

    return text
