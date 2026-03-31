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

    def test_empty_pdf_raises(self):
        """A completely blank PDF should raise ValueError."""
        pdf_bytes = _make_empty_pdf()
        with self.assertRaises(ValueError) as ctx:
            parse_supplier_file(pdf_bytes, 'empty.pdf')
        self.assertIn('No tables found', str(ctx.exception))


class TestParseSupplierFilePDFNoPdfplumber(unittest.TestCase):
    """When pdfplumber is not installed."""

    def test_raises_when_no_pdfplumber(self):
        with patch('domain.supplier._PDF_SUPPORT', False):
            with self.assertRaises(ValueError) as ctx:
                parse_supplier_file(b'%PDF-dummy', 'file.pdf')
            self.assertIn('pdfplumber', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
