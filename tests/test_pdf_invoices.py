"""Regression tests for PDF invoice parsing — the 5 multilingual test PDFs.

Each test loads a real PDF from the repository root, parses it through
``parse_supplier_file``, and verifies:

1. The DataFrame is non-empty with correct structure.
2. Contains expected line-item counts and key column candidates.
3. End-to-end matching against a synthetic catalogue produces results.
"""

from __future__ import annotations

import os
import pathlib
import unittest

import pandas as pd

from domain.supplier import (
    parse_supplier_file,
    detect_supplier_columns,
    _detect_ean_in_text,
    _extract_line_items_swedish_invoice,
    _extract_line_items_danish_invoice,
    _extract_line_items_german_invoice,
    _extract_line_items_spanish_proforma,
    _extract_pdf_line_items_from_text,
    _identify_line_item_section,
    _validate_table_concat,
)
from domain.invoice_ean import (
    detect_invoice_columns,
    match_invoice_to_products,
    build_matches_df,
)

# Repository root where the test PDFs live
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The 5 test PDFs
_PDFS = {
    'swedish': 'ARInvoice Invoice 419976.pdf',
    'dax_order': 'AUF_26403772_sm.pdf',
    'spanish': 'C51319-261000983-FINALPROFORMA.pdf',
    'danish': 'Invoice_20138516.pdf',
    'german': 'invoice_480276.pdf',
}


def _read_pdf(key: str) -> bytes:
    """Read a test PDF by its short key name."""
    path = _REPO_ROOT / _PDFS[key]
    if not path.exists():
        raise unittest.SkipTest(f"Test PDF not found: {path}")
    return path.read_bytes()


def _make_synthetic_catalogue() -> pd.DataFrame:
    """Build a small synthetic product catalogue for matching tests.

    Contains products that might match SKUs found in the test invoices.
    """
    rows = [
        # Swedish invoice items (Budo-Nord)
        {'NUMBER': '14105-003', 'VARIANT_TYPES': 'XXS', 'EAN': '5701234567890', 'TITLE_DK': 'BUDO-NORD KAMPVÄST'},
        {'NUMBER': '14105-003', 'VARIANT_TYPES': 'XS', 'EAN': '5701234567891', 'TITLE_DK': 'BUDO-NORD KAMPVÄST'},
        {'NUMBER': '14105-003', 'VARIANT_TYPES': 'S', 'EAN': '', 'TITLE_DK': 'BUDO-NORD KAMPVÄST'},
        {'NUMBER': '14105-003', 'VARIANT_TYPES': 'M', 'EAN': '', 'TITLE_DK': 'BUDO-NORD KAMPVÄST'},
        # DAX order items
        {'NUMBER': 'FMU 042', 'VARIANT_TYPES': 'SR', 'EAN': '', 'TITLE_DK': 'Mouth Guard BIT FIT'},
        {'NUMBER': 'ATBK', 'VARIANT_TYPES': '190', 'EAN': '', 'TITLE_DK': 'Karategi TOKAIDO Bujin Kuro'},
        {'NUMBER': 'ATBK', 'VARIANT_TYPES': '170', 'EAN': '', 'TITLE_DK': 'Karategi TOKAIDO Bujin Kuro'},
        {'NUMBER': 'AFK', 'VARIANT_TYPES': '170', 'EAN': '', 'TITLE_DK': 'Judogi DAX KIDS'},
        # Spanish proforma items
        {'NUMBER': 'PR 1720-U-0', 'VARIANT_TYPES': '', 'EAN': '', 'TITLE_DK': 'FOREARM MITT'},
        {'NUMBER': 'PR 1613-U-AZ', 'VARIANT_TYPES': '', 'EAN': '', 'TITLE_DK': 'DOUBLE HAND MITT BLUE'},
        {'NUMBER': 'PRO 15723-S-0', 'VARIANT_TYPES': '', 'EAN': '', 'TITLE_DK': 'WT SHIN GUARD'},
        # Danish invoice items
        {'NUMBER': '1910163-999000-7', 'VARIANT_TYPES': '', 'EAN': '', 'TITLE_DK': 'EVOLVE PANTS'},
        {'NUMBER': '1910142-430000-5', 'VARIANT_TYPES': '', 'EAN': '', 'TITLE_DK': 'EVOLVE TEE'},
        # German invoice items
        {'NUMBER': 'ADIBTT02', 'VARIANT_TYPES': 'M', 'EAN': '', 'TITLE_DK': 'adidas Box-Top'},
        {'NUMBER': 'adiWOBG1', 'VARIANT_TYPES': '10 oz', 'EAN': '', 'TITLE_DK': 'adidas Boxing Gloves'},
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Test: EAN detection helper
# ---------------------------------------------------------------------------

class TestEANDetection(unittest.TestCase):
    """Tests for the _detect_ean_in_text helper."""

    def test_finds_ean13(self):
        self.assertEqual(_detect_ean_in_text('EAN: 5701234567890'), '5701234567890')

    def test_finds_ean8(self):
        self.assertEqual(_detect_ean_in_text('code 12345678 end'), '12345678')

    def test_finds_ean_with_spaces(self):
        # "57012345 67890" is captured as 13 digits = valid EAN-13
        result = _detect_ean_in_text('barcode 57012345 67890')
        self.assertEqual(result, '5701234567890')

    def test_empty_when_no_ean(self):
        self.assertEqual(_detect_ean_in_text('no barcode here'), '')

    def test_ignores_short_numbers(self):
        self.assertEqual(_detect_ean_in_text('code 12345'), '')


# ---------------------------------------------------------------------------
# Test: line-item section identification
# ---------------------------------------------------------------------------

class TestIdentifyLineItemSection(unittest.TestCase):
    """Tests for multilingual header detection and section filtering."""

    def test_detects_german_header(self):
        text = "Header\nProd.-Nr. Produkt\n12345 Widget 5 10,00\nTotal 50,00"
        section = _identify_line_item_section(text)
        self.assertIn('Prod.-Nr.', section)
        self.assertIn('12345', section)
        self.assertNotIn('Total 50,00', section)

    def test_detects_danish_header(self):
        text = "Info\nVare Lev.dato Mængde Pris Rabat\n1234 Product 1 10,00\nOrdrelinje total"
        section = _identify_line_item_section(text)
        self.assertIn('Vare', section)
        self.assertIn('1234', section)

    def test_skips_address_blocks(self):
        text = "Item no Description\nRechnungsadresse foo\n12345 Widget"
        section = _identify_line_item_section(text)
        self.assertIn('12345', section)
        self.assertNotIn('Rechnungsadresse', section)


# ---------------------------------------------------------------------------
# Test: validate_table_concat
# ---------------------------------------------------------------------------

class TestValidateTableConcat(unittest.TestCase):

    def test_good_table_accepted(self):
        df = pd.DataFrame({
            'SKU': ['A', 'B', 'C', 'D'],
            'Price': ['10', '20', '30', '40'],
            'Qty': ['1', '2', '3', '4'],
        })
        result = _validate_table_concat([df])
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 4)

    def test_mostly_nan_rejected(self):
        df = pd.DataFrame({
            'SKU': ['A', '', '', ''],
            'Price': ['', '', '', ''],
            'Qty': ['', '', '', ''],
            'Extra1': ['', '', '', ''],
            'Extra2': ['', '', '', ''],
        })
        result = _validate_table_concat([df])
        self.assertIsNone(result)

    def test_too_few_rows_rejected(self):
        df = pd.DataFrame({
            'SKU': ['A'],
            'Price': ['10'],
        })
        result = _validate_table_concat([df])
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Test: parsing each PDF
# ---------------------------------------------------------------------------

class TestSwedishInvoiceParsing(unittest.TestCase):
    """ARInvoice Invoice 419976.pdf — Swedish Budo & Fitness invoice."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_pdf('swedish')
        cls.df = parse_supplier_file(cls.raw, _PDFS['swedish'])

    def test_not_empty(self):
        self.assertFalse(self.df.empty)

    def test_has_expected_columns(self):
        cols = set(self.df.columns)
        self.assertIn('Article No', cols)
        self.assertIn('Description', cols)
        self.assertIn('Qty', cols)
        self.assertIn('Unit Price', cols)

    def test_row_count(self):
        self.assertEqual(len(self.df), 6)

    def test_article_numbers_include_suffix(self):
        """Article numbers should have the full form like '14105-003-XXS'."""
        art_nos = self.df['Article No'].tolist()
        self.assertTrue(any('003-XXS' in a for a in art_nos))
        self.assertTrue(any('003-XL' in a for a in art_nos))

    def test_descriptions_include_variant_info(self):
        descs = self.df['Description'].tolist()
        self.assertTrue(any('Blå-Röd' in d for d in descs))

    def test_quantities_are_numeric(self):
        for val in self.df['Qty']:
            float(val.replace(',', '.'))  # should not raise


class TestDAXOrderParsing(unittest.TestCase):
    """AUF_26403772_sm.pdf — DAX multi-page proforma invoice."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_pdf('dax_order')
        cls.df = parse_supplier_file(cls.raw, _PDFS['dax_order'])

    def test_not_empty(self):
        self.assertFalse(self.df.empty)

    def test_has_expected_columns(self):
        cols = set(self.df.columns)
        self.assertIn('Article No', cols)
        self.assertIn('Qty', cols)
        self.assertIn('Unit Price', cols)

    def test_row_count_at_least_100(self):
        self.assertGreaterEqual(len(self.df), 100)

    def test_first_item_is_fmu042(self):
        art_nos = self.df['Article No'].tolist()
        self.assertTrue(any('FMU 042' in a for a in art_nos))


class TestSpanishProformaParsing(unittest.TestCase):
    """C51319-261000983-FINALPROFORMA.pdf — Spanish proforma invoice."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_pdf('spanish')
        cls.df = parse_supplier_file(cls.raw, _PDFS['spanish'])

    def test_not_empty(self):
        self.assertFalse(self.df.empty)

    def test_has_expected_columns(self):
        cols = set(self.df.columns)
        self.assertIn('Article No', cols)
        self.assertIn('Description', cols)
        self.assertIn('Qty', cols)
        self.assertIn('Unit Price', cols)

    def test_row_count(self):
        self.assertGreaterEqual(len(self.df), 25)

    def test_contains_known_codes(self):
        art_nos = ' '.join(self.df['Article No'].tolist())
        self.assertIn('PR 1720-U-0', art_nos)
        self.assertIn('PRO 15723', art_nos)

    def test_no_metadata_rows(self):
        """Should not contain document metadata like 'INCOTERM' or dates."""
        all_text = ' '.join(self.df.values.flatten().astype(str))
        self.assertNotIn('INCOTERM', all_text)
        self.assertNotIn('Página', all_text)


class TestDanishInvoiceParsing(unittest.TestCase):
    """Invoice_20138516.pdf — Danish NewWave/Craft invoice."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_pdf('danish')
        cls.df = parse_supplier_file(cls.raw, _PDFS['danish'])

    def test_not_empty(self):
        self.assertFalse(self.df.empty)

    def test_has_expected_columns(self):
        cols = set(self.df.columns)
        self.assertIn('Article No', cols)
        self.assertIn('Description', cols)
        self.assertIn('Qty', cols)

    def test_row_count(self):
        self.assertEqual(len(self.df), 5)

    def test_article_numbers(self):
        art_nos = self.df['Article No'].tolist()
        self.assertIn('1910163-999000-7', art_nos)
        self.assertIn('1910142-430000-5', art_nos)

    def test_descriptions(self):
        descs = self.df['Description'].tolist()
        self.assertTrue(any('EVOLVE PANTS' in d for d in descs))
        self.assertTrue(any('EVOLVE TEE' in d for d in descs))


class TestGermanInvoiceParsing(unittest.TestCase):
    """invoice_480276.pdf — German ju-sports invoice."""

    @classmethod
    def setUpClass(cls):
        cls.raw = _read_pdf('german')
        cls.df = parse_supplier_file(cls.raw, _PDFS['german'])

    def test_not_empty(self):
        self.assertFalse(self.df.empty)

    def test_has_expected_columns(self):
        cols = set(self.df.columns)
        self.assertIn('Article No', cols)
        self.assertIn('Description', cols)
        self.assertIn('Qty', cols)
        self.assertIn('Unit Price', cols)

    def test_row_count_at_least_40(self):
        self.assertGreaterEqual(len(self.df), 40)

    def test_descriptions_include_size_info(self):
        """German invoice should include size/colour in description."""
        descs = self.df['Description'].tolist()
        # At least some items should have size info appended
        self.assertTrue(any('(' in d and ')' in d for d in descs))

    def test_article_numbers_are_numeric(self):
        """German invoice Prod.-Nr. values should be pure digit codes."""
        for val in self.df['Article No'].tolist():
            val_stripped = val.strip()
            if val_stripped:
                # The German regex extracts 6-12 digit Prod.-Nr. codes
                self.assertTrue(val_stripped.isdigit(),
                                f"Expected numeric Prod.-Nr., got: {val_stripped!r}")


# ---------------------------------------------------------------------------
# Test: column detection works on parsed PDFs
# ---------------------------------------------------------------------------

class TestColumnDetectionOnPDFs(unittest.TestCase):
    """Verify that detect_supplier_columns / detect_invoice_columns can
    find SKU, qty, and price/description columns on the parsed DataFrames."""

    def _check_detection(self, pdf_key: str):
        raw = _read_pdf(pdf_key)
        df = parse_supplier_file(raw, _PDFS[pdf_key])
        cols_supplier = detect_supplier_columns(df)
        cols_invoice = detect_invoice_columns(df)
        # At least one detection should find a SKU column
        sku_found = (cols_supplier.get('sku') is not None
                     or cols_invoice.get('sku') is not None)
        self.assertTrue(sku_found,
                        f"No SKU column detected for {pdf_key}: "
                        f"supplier={cols_supplier}, invoice={cols_invoice}")

    def test_swedish(self):
        self._check_detection('swedish')

    def test_dax_order(self):
        self._check_detection('dax_order')

    def test_spanish(self):
        self._check_detection('spanish')

    def test_danish(self):
        self._check_detection('danish')

    def test_german(self):
        self._check_detection('german')


# ---------------------------------------------------------------------------
# Test: end-to-end matching pipeline
# ---------------------------------------------------------------------------

class TestEndToEndMatching(unittest.TestCase):
    """Lightweight end-to-end tests: parse → column detect → match → matches_df."""

    @classmethod
    def setUpClass(cls):
        cls.catalogue = _make_synthetic_catalogue()

    def _run_pipeline(self, pdf_key: str):
        """Parse a PDF, detect columns, run matching, return matches_df."""
        raw = _read_pdf(pdf_key)
        df = parse_supplier_file(raw, _PDFS[pdf_key])

        # Detect columns
        cols = detect_invoice_columns(df)
        sku_col = cols.get('sku')
        qty_col = cols.get('qty')
        desc_col = cols.get('description')

        if sku_col is None:
            cols_s = detect_supplier_columns(df)
            sku_col = cols_s.get('sku')
            desc_col = desc_col or cols_s.get('description')

        self.assertIsNotNone(sku_col,
                             f"No SKU column found for {pdf_key}")

        # Run matching
        match_data = match_invoice_to_products(
            self.catalogue, df, sku_col, qty_col,
            threshold=50, invoice_desc_col=desc_col,
        )
        matches_df = build_matches_df(
            self.catalogue, match_data, src_type='invoice',
        )
        return matches_df

    def test_swedish_produces_matches(self):
        mdf = self._run_pipeline('swedish')
        self.assertFalse(mdf.empty)
        self.assertGreater(len(mdf), 0)
        # Should have standard matches columns
        self.assertIn('src_sku', mdf.columns)
        self.assertIn('match_score', mdf.columns)

    def test_dax_produces_matches(self):
        mdf = self._run_pipeline('dax_order')
        self.assertFalse(mdf.empty)
        # At least some items should match our synthetic catalogue
        matched = mdf[mdf['match_score'].astype(float) > 0]
        self.assertGreater(len(matched), 0)

    def test_spanish_produces_matches(self):
        mdf = self._run_pipeline('spanish')
        self.assertFalse(mdf.empty)
        matched = mdf[mdf['match_score'].astype(float) > 0]
        self.assertGreater(len(matched), 0)

    def test_danish_produces_matches(self):
        mdf = self._run_pipeline('danish')
        self.assertFalse(mdf.empty)
        matched = mdf[mdf['match_score'].astype(float) > 0]
        self.assertGreater(len(matched), 0)

    def test_german_produces_matches(self):
        mdf = self._run_pipeline('german')
        self.assertFalse(mdf.empty)
        self.assertGreater(len(mdf), 0)

    def test_ean_cross_match_triggers(self):
        """When catalogue has an EAN and it appears in the invoice,
        the match should be score=100."""
        # Build a tiny invoice with a known EAN as the SKU
        inv_df = pd.DataFrame({
            'Article No': ['5701234567890'],
            'Description': ['Test product'],
            'Qty': ['1'],
        })
        match_data = match_invoice_to_products(
            self.catalogue, inv_df, 'Article No', 'Qty',
            threshold=50, invoice_desc_col='Description',
        )
        matches_df = build_matches_df(
            self.catalogue, match_data, src_type='invoice',
        )
        # The EAN 5701234567890 maps to 14105-003 XXS
        ean_rows = matches_df[matches_df['match_score'].astype(float) == 100]
        self.assertGreater(len(ean_rows), 0,
                           "EAN cross-match should trigger score=100")


# ---------------------------------------------------------------------------
# Test: LLM extraction prompt building
# ---------------------------------------------------------------------------

class TestLLMExtractionPrompt(unittest.TestCase):
    """Tests for the LLM-assisted extraction prompt and parsing."""

    def test_build_prompt_contains_instructions(self):
        from domain.supplier import _build_line_item_extraction_prompt
        prompt = _build_line_item_extraction_prompt("Item 001 Widget 5 10.00")
        self.assertIn('JSON', prompt)
        self.assertIn('sku', prompt)
        self.assertIn('description', prompt)

    def test_parse_llm_response_success(self):
        from domain.supplier import _parse_pdf_line_items_llm
        # Mock the LLM to return a valid JSON array
        mock_response = '[{"sku": "ABC123", "description": "Widget", "qty": "5", "unit_price": "10.00", "line_total": "50.00", "ean": "", "discount": ""}]'

        def mock_llm(prompt, key, model):
            return mock_response

        result = _parse_pdf_line_items_llm(
            "Item 001 ABC123 Widget 5 10.00 50.00",
            api_key='test-key',
            llm_call=mock_llm,
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]['Article No'], 'ABC123')

    def test_parse_llm_no_api_key(self):
        from domain.supplier import _parse_pdf_line_items_llm
        # Should return None when no API key
        result = _parse_pdf_line_items_llm(
            "some text", api_key=None, llm_call=None,
        )
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Test: individual extractor functions
# ---------------------------------------------------------------------------

class TestExtractorFunctions(unittest.TestCase):
    """Tests for individual layout-specific extractors."""

    def test_swedish_extractor_returns_none_for_non_matching_text(self):
        result = _extract_line_items_swedish_invoice("Random text with no invoice structure")
        self.assertIsNone(result)

    def test_danish_extractor_returns_none_for_non_matching_text(self):
        result = _extract_line_items_danish_invoice("Not a Danish invoice")
        self.assertIsNone(result)

    def test_german_extractor_returns_none_for_non_matching_text(self):
        result = _extract_line_items_german_invoice("Not a German invoice")
        self.assertIsNone(result)

    def test_spanish_extractor_returns_none_for_non_matching_text(self):
        result = _extract_line_items_spanish_proforma("Not a Spanish invoice")
        self.assertIsNone(result)

    def test_dispatcher_tries_all_extractors(self):
        # A text that matches no known layout
        result = _extract_pdf_line_items_from_text("No invoice data here")
        self.assertIsNone(result)

    def test_spanish_extractor_basic(self):
        text = (
            "CODE DESCRIPTION UNITS PRICE % AMOUNT\n"
            "PR 1720-U-0 FOREARM MITT U 10,00 18,440 184,40\n"
            "PR 1613-U-AZ DOUBLE MITT BLUE 20,00 8,650 173,00\n"
        )
        result = _extract_line_items_spanish_proforma(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)
        self.assertEqual(result.iloc[0]['Article No'], 'PR 1720-U-0')

    def test_danish_extractor_basic(self):
        text = (
            "Vare Lev.dato Mængde Pris Rabat Beløb\n"
            "1910163-999000-7 EVOLVE PANTS M Black XL 26-03-26 1 320,00 58% 134,40\n"
        )
        result = _extract_line_items_danish_invoice(text)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]['Article No'], '1910163-999000-7')


if __name__ == '__main__':
    unittest.main()
