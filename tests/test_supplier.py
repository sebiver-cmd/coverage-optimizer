"""Tests for domain/supplier.py — parse_supplier_file and helpers."""

from __future__ import annotations

import io
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd

from domain.supplier import (
    parse_supplier_file,
    detect_encoding,
    detect_supplier_columns,
    normalize_sku,
    match_supplier_to_products,
    _dedupe_columns,
)


# ---------------------------------------------------------------------------
# Helpers — tiny PDF creation via fpdf2
# ---------------------------------------------------------------------------

def _make_table_pdf(headers: list[str], rows: list[list[str]]) -> bytes:
    """Create a minimal single-page PDF with a proper table."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', size=10)
    col_w = 180 / max(len(headers), 1)
    for h in headers:
        pdf.cell(col_w, 8, h, border=1)
    pdf.ln()
    for row in rows:
        for cell in row:
            pdf.cell(col_w, 8, cell, border=1)
        pdf.ln()
    return pdf.output()


def _make_text_pdf(lines: list[str]) -> bytes:
    """Create a PDF whose pages contain only free-form text (no table grid)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font('Helvetica', size=10)
    for line in lines:
        pdf.cell(0, 8, line)
        pdf.ln()
    return pdf.output()


def _make_empty_pdf() -> bytes:
    """Create a PDF with a blank page (no text, no tables)."""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    return pdf.output()


# ===================================================================
# Tests
# ===================================================================

class TestDetectEncoding(unittest.TestCase):
    def test_utf8_bom(self):
        self.assertEqual(detect_encoding(b'\xef\xbb\xbfhello'), 'utf-8-sig')

    def test_valid_utf8(self):
        self.assertEqual(detect_encoding('Æble'.encode('utf-8')), 'utf-8')

    def test_fallback_cp1252(self):
        raw = 'Ærø'.encode('cp1252')
        self.assertEqual(detect_encoding(raw), 'cp1252')


class TestNormalizeSku(unittest.TestCase):
    def test_strips_prefix_and_separators(self):
        self.assertEqual(normalize_sku('TO-AFK-110'), 'AFK110')

    def test_uppercases(self):
        self.assertEqual(normalize_sku('abc123'), 'ABC123')

    def test_empty_string(self):
        self.assertEqual(normalize_sku(''), '')

    def test_comma_removed(self):
        """Commas in article numbers (e.g. European decimal) are stripped."""
        self.assertEqual(normalize_sku('GTR 4,5'), 'GTR45')

    def test_comma_and_prefix(self):
        """Prefix + comma normalises the same as comma-only."""
        self.assertEqual(normalize_sku('TO-GTR-4,5'), 'GTR45')


class TestDetectSupplierColumns(unittest.TestCase):
    def test_detects_all_columns(self):
        df = pd.DataFrame(columns=['SKU', 'Price', 'Discount', 'Currency', 'Description'])
        result = detect_supplier_columns(df)
        self.assertEqual(result['sku'], 'SKU')
        self.assertEqual(result['price'], 'Price')
        self.assertEqual(result['discount'], 'Discount')
        self.assertEqual(result['currency'], 'Currency')
        self.assertEqual(result['description'], 'Description')

    def test_danish_column_names(self):
        df = pd.DataFrame(columns=['Varenr', 'Pris', 'Rabat', 'Valuta', 'Navn'])
        result = detect_supplier_columns(df)
        self.assertEqual(result['sku'], 'Varenr')
        self.assertEqual(result['price'], 'Pris')
        self.assertEqual(result['discount'], 'Rabat')
        self.assertEqual(result['currency'], 'Valuta')
        self.assertEqual(result['description'], 'Navn')


class TestParseSupplierFileCSV(unittest.TestCase):
    """CSV / text parsing branch of parse_supplier_file."""

    def test_semicolon_separated(self):
        content = 'SKU;Price\nA001;100\nA002;200'
        df = parse_supplier_file(content.encode(), 'prices.csv')
        self.assertEqual(len(df), 2)
        self.assertIn('SKU', df.columns)
        self.assertIn('Price', df.columns)

    def test_comma_separated(self):
        content = 'SKU,Price\nA001,100\nA002,200'
        df = parse_supplier_file(content.encode(), 'prices.csv')
        self.assertEqual(len(df), 2)

    def test_tab_separated(self):
        content = 'SKU\tPrice\nA001\t100'
        df = parse_supplier_file(content.encode(), 'file.tsv')
        self.assertEqual(len(df), 1)

    def test_explicit_encoding(self):
        text = 'Varenr;Pris\nÆ01;99'
        raw = text.encode('latin-1')
        df = parse_supplier_file(raw, 'file.csv', encoding='latin-1')
        self.assertEqual(df.iloc[0, 0], 'Æ01')

    def test_unparseable_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parse_supplier_file(b'just one column', 'file.csv')
        self.assertIn('Could not parse', str(ctx.exception))


class TestParseSupplierFilePDFTables(unittest.TestCase):
    """PDF table-extraction branch of parse_supplier_file."""

    def test_pdf_with_table(self):
        pdf_bytes = _make_table_pdf(
            ['SKU', 'Price'],
            [['A001', '100'], ['A002', '200']],
        )
        df = parse_supplier_file(pdf_bytes, 'supplier.pdf')
        self.assertGreaterEqual(len(df), 2)
        # At least two columns extracted
        self.assertGreaterEqual(len(df.columns), 2)


class TestParseSupplierFilePDFTextFallback(unittest.TestCase):
    """PDF text-extraction fallback when no tables are detected."""

    def test_csv_text_in_pdf_parsed(self):
        """A PDF with CSV-formatted text (semicolons) should be parseable."""
        lines = ['SKU;Price', 'A001;100', 'A002;200']
        pdf_bytes = _make_text_pdf(lines)
        df = parse_supplier_file(pdf_bytes, 'prices.pdf')
        self.assertGreaterEqual(len(df), 2)
        # Should have two columns
        self.assertGreaterEqual(len(df.columns), 2)

    def test_comma_text_in_pdf_parsed(self):
        """A PDF with CSV-formatted text (commas) should be parseable."""
        lines = ['SKU,Price', 'X01,50', 'X02,60']
        pdf_bytes = _make_text_pdf(lines)
        df = parse_supplier_file(pdf_bytes, 'file.pdf')
        self.assertGreaterEqual(len(df), 2)
        self.assertGreaterEqual(len(df.columns), 2)

    def test_whitespace_text_in_pdf_parsed(self):
        """A PDF with whitespace-separated text should be parseable."""
        lines = ['SKU    Price    Description', 'A001    100    Widget', 'A002    200    Gadget']
        pdf_bytes = _make_text_pdf(lines)
        df = parse_supplier_file(pdf_bytes, 'ws.pdf')
        self.assertGreaterEqual(len(df), 2)
        self.assertGreaterEqual(len(df.columns), 2)

    def test_empty_pdf_raises(self):
        """A completely blank PDF should raise ValueError."""
        pdf_bytes = _make_empty_pdf()
        with self.assertRaises(ValueError) as ctx:
            parse_supplier_file(pdf_bytes, 'empty.pdf')
        self.assertIn('No tables found', str(ctx.exception))


class TestParseSupplierFilePDFRelaxedTableExtraction(unittest.TestCase):
    """PDF table extraction with text-based strategies (relaxed settings)."""

    def test_text_aligned_table_pdf(self):
        """A PDF with text-aligned columns (no borders) should be parseable
        via relaxed table-detection settings."""
        lines = ['SKU     Price', 'B001     150', 'B002     250']
        pdf_bytes = _make_text_pdf(lines)
        df = parse_supplier_file(pdf_bytes, 'aligned.pdf')
        self.assertGreaterEqual(len(df), 2)
        self.assertGreaterEqual(len(df.columns), 2)


class TestParseSupplierFilePDFNoPdfplumber(unittest.TestCase):
    """When pdfplumber is not installed."""

    def test_raises_when_no_pdfplumber(self):
        with patch('domain.supplier._PDF_SUPPORT', False):
            with self.assertRaises(ValueError) as ctx:
                parse_supplier_file(b'%PDF-dummy', 'file.pdf')
            self.assertIn('pdfplumber', str(ctx.exception))


class TestDedupeColumns(unittest.TestCase):
    """Tests for _dedupe_columns helper."""

    def test_unique_headers_unchanged(self):
        self.assertEqual(
            _dedupe_columns(['A', 'B', 'C']),
            ['A', 'B', 'C'],
        )

    def test_empty_string_duplicates(self):
        result = _dedupe_columns(['', '', ''])
        self.assertEqual(len(result), len(set(result)))

    def test_mixed_duplicates(self):
        result = _dedupe_columns(['SKU', '', 'Price', ''])
        self.assertEqual(len(result), len(set(result)))
        self.assertEqual(result[0], 'SKU')
        self.assertEqual(result[2], 'Price')


class TestParseSupplierFilePDFDuplicateHeaders(unittest.TestCase):
    """PDFs whose relaxed extraction produces duplicate column names."""

    def test_duplicate_column_headers_do_not_crash(self):
        """Concat of tables with duplicate empty headers must not raise
        'Reindexing only valid with uniquely valued Index objects'."""
        # Build two DataFrames with duplicate column names — the exact
        # situation that caused the original crash.
        headers_a = ['X', '', '', 'Y']
        headers_b = ['X', '', 'Z']
        rows_a = [['1', '2', '3', '4']]
        rows_b = [['a', 'b', 'c']]

        import pandas as _pd
        from domain.supplier import _dedupe_columns
        df_a = _pd.DataFrame(rows_a, columns=_dedupe_columns(headers_a))
        df_b = _pd.DataFrame(rows_b, columns=_dedupe_columns(headers_b))
        # Should not raise
        result = _pd.concat([df_a, df_b], ignore_index=True)
        self.assertEqual(len(result), 2)


class TestParseSupplierFilePDFInvoiceRegex(unittest.TestCase):
    """Invoice-style PDFs parsed by the regex fallback."""

    def test_invoice_lines_extracted(self):
        """PDF with invoice-style lines (item no, article, qty, price)
        should be parsed by the regex fallback when table extraction
        returns nothing useful."""
        lines = [
            'Proforma Invoice No. 12345',
            'Item Article No Designation QtyUnit Unit Price Item Value',
            '[EUR] [EUR]',
            '001 AFK 150 Judogi DAX KIDS white, size 150 10pcs 22,00 220,00',
            'Customstariffe No 62043290',
            '002 ATBK 190 Karategi TOKAIDO, black, size 190 5pcs 41,90 209,50',
            'Customstariffe No 62042280',
        ]
        pdf_bytes = _make_text_pdf(lines)
        # Mock table extraction to return nothing so we hit text fallback
        _orig_open = __import__('pdfplumber').open

        class _FakePage:
            def __init__(self, real_page):
                self._real = real_page

            def extract_tables(self, **kw):
                return []

            def extract_text(self, **kw):
                return self._real.extract_text(**kw)

        class _FakePDF:
            def __init__(self, real_pdf):
                self._real = real_pdf
                self.pages = [_FakePage(p) for p in real_pdf.pages]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self._real.__exit__(*a)

        def _mock_open(f):
            real = _orig_open(f)
            return _FakePDF(real.__enter__())

        with patch('domain.supplier.pdfplumber.open', side_effect=_mock_open):
            df = parse_supplier_file(pdf_bytes, 'invoice.pdf')
        self.assertGreaterEqual(len(df), 2)
        self.assertIn('Article No', df.columns)
        self.assertIn('Unit Price', df.columns)

    def test_article_no_column_detected(self):
        df = pd.DataFrame(columns=['Article No', 'Designation', 'Unit Price'])
        result = detect_supplier_columns(df)
        self.assertEqual(result['sku'], 'Article No')
        self.assertEqual(result['price'], 'Unit Price')
        self.assertEqual(result['description'], 'Designation')


class TestMatchSupplierToProducts(unittest.TestCase):
    """Tests for match_supplier_to_products with enhanced matching."""

    def test_exact_match_after_normalization(self):
        result = match_supplier_to_products(
            ['TO-AFK-110'], ['AFK110'],
        )
        self.assertEqual(result['TO-AFK-110']['sku'], 'AFK110')
        self.assertEqual(result['TO-AFK-110']['score'], 100)
        self.assertEqual(result['TO-AFK-110']['alternatives'], [])

    def test_fuzzy_match_returns_best(self):
        result = match_supplier_to_products(
            ['AFK110X'], ['AFK110', 'BCD220'], threshold=50,
        )
        self.assertEqual(result['AFK110X']['sku'], 'AFK110')
        self.assertGreater(result['AFK110X']['score'], 50)

    def test_no_match_returns_alternatives(self):
        result = match_supplier_to_products(
            ['ZZZZZ'], ['AFK110', 'BCD220'], threshold=95,
        )
        self.assertIsNone(result['ZZZZZ']['sku'])
        self.assertEqual(result['ZZZZZ']['score'], 0)
        self.assertIsInstance(result['ZZZZZ']['alternatives'], list)

    def test_name_based_reranking(self):
        """When SKU scores are close, name similarity should pick the
        correct product."""
        result = match_supplier_to_products(
            ['AFK11'], ['AFK110', 'AFK120'], threshold=50,
            supplier_names={'AFK11': 'Judogi white'},
            product_names={'AFK110': 'Judogi white', 'AFK120': 'Belt black'},
        )
        self.assertEqual(result['AFK11']['sku'], 'AFK110')

    def test_alternatives_returned(self):
        result = match_supplier_to_products(
            ['AFK1'], ['AFK110', 'AFK120', 'AFK130'],
            threshold=30, top_n=3,
        )
        data = result['AFK1']
        total = (1 if data['sku'] else 0) + len(data['alternatives'])
        self.assertGreaterEqual(total, 2)

    def test_empty_sku_skipped(self):
        result = match_supplier_to_products(['', '  '], ['AFK110'])
        self.assertEqual(len(result), 0)

    def test_name_fallback_suggestions(self):
        """When SKU gives no match, name-based suggestions should appear."""
        result = match_supplier_to_products(
            ['ZZZZ'], ['AFK110'], threshold=95,
            supplier_names={'ZZZZ': 'Judogi white'},
            product_names={'AFK110': 'Judogi white'},
        )
        data = result['ZZZZ']
        self.assertIsNone(data['sku'])
        self.assertGreater(len(data['alternatives']), 0)
        # The name-based suggestion should include AFK110
        alt_skus = [sku for sku, _ in data['alternatives']]
        self.assertIn('AFK110', alt_skus)

    def test_result_dict_structure(self):
        """Every match result must have 'sku', 'score', 'alternatives'."""
        result = match_supplier_to_products(
            ['AFK110', 'UNKNOWN'], ['AFK110'], threshold=90,
        )
        for key in result.values():
            self.assertIn('sku', key)
            self.assertIn('score', key)
            self.assertIn('alternatives', key)

    def test_top_n_limits_alternatives(self):
        result = match_supplier_to_products(
            ['A'], ['A1', 'A2', 'A3', 'A4', 'A5', 'A6'],
            threshold=10, top_n=2,
        )
        data = result['A']
        # Alternatives list should not exceed top_n
        self.assertLessEqual(len(data['alternatives']), 2)

    def test_comma_article_exact_match(self):
        """Supplier SKU with comma matches product with same comma."""
        result = match_supplier_to_products(
            ['GTR 4,5'], ['TO-GTR-4,5'],
        )
        self.assertEqual(result['GTR 4,5']['sku'], 'TO-GTR-4,5')
        self.assertEqual(result['GTR 4,5']['score'], 100)


if __name__ == '__main__':
    unittest.main()
