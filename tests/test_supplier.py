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
    _heuristic_detect_supplier_columns,
    _guess_candidates,
    normalize_sku,
    match_supplier_to_products,
    _dedupe_columns,
)
from domain.invoice_ean import (
    detect_invoice_columns,
    _heuristic_detect_invoice_columns,
    suggest_column_mapping,
    _build_mapping_prompt,
    _parse_llm_mapping_response,
    INTERNAL_FIELDS,
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


# ===================================================================
# AI-assisted column mapping — suggest_column_mapping
# ===================================================================

class TestBuildMappingPrompt(unittest.TestCase):
    """Tests for the prompt builder helper."""

    def test_contains_column_names(self):
        df = pd.DataFrame({'Art Nr': ['X1'], 'Pris': [99]})
        prompt = _build_mapping_prompt(df)
        self.assertIn('Art Nr', prompt)
        self.assertIn('Pris', prompt)

    def test_contains_internal_fields(self):
        df = pd.DataFrame({'A': [1]})
        prompt = _build_mapping_prompt(df)
        for field in INTERNAL_FIELDS:
            self.assertIn(field, prompt)

    def test_limits_rows_to_five(self):
        df = pd.DataFrame({'A': list(range(20))})
        prompt = _build_mapping_prompt(df)
        # The prompt should contain a markdown table with exactly 5 data rows
        # (header + separator + 5 rows = 7 lines starting with '|')
        table_lines = [
            ln for ln in prompt.splitlines() if ln.strip().startswith('|')
        ]
        # header + separator + 5 data rows
        self.assertEqual(len(table_lines), 7)

    def test_includes_heuristic_hints(self):
        """Prompt should include heuristic candidate suggestions."""
        df = pd.DataFrame({'SKU': ['X1'], 'Price': [99]})
        prompt = _build_mapping_prompt(df)
        self.assertIn('Heuristic suggestions', prompt)
        self.assertIn('suggested candidates', prompt)
        self.assertIn('SKU', prompt)

    def test_no_hints_for_unknown_columns(self):
        """No hints section when no heuristic candidates are found."""
        df = pd.DataFrame({'Foo': ['X1'], 'Bar': [99]})
        prompt = _build_mapping_prompt(df)
        self.assertNotIn('Heuristic suggestions', prompt)


class TestParseLLMMappingResponse(unittest.TestCase):
    """Tests for _parse_llm_mapping_response validation."""

    def test_valid_json_mapping(self):
        raw = '{"SKU": "sku", "Price": "price", "Name": "description"}'
        result = _parse_llm_mapping_response(raw, ['SKU', 'Price', 'Name'])
        self.assertEqual(result, {
            'sku': 'SKU', 'price': 'Price', 'description': 'Name',
        })

    def test_json_with_null_values_excluded(self):
        raw = '{"SKU": "sku", "Extra": null}'
        result = _parse_llm_mapping_response(raw, ['SKU', 'Extra'])
        self.assertEqual(result, {'sku': 'SKU'})

    def test_nonexistent_column_skipped(self):
        raw = '{"MissingCol": "sku", "Price": "price"}'
        result = _parse_llm_mapping_response(raw, ['Price'])
        self.assertEqual(result, {'price': 'Price'})

    def test_unknown_internal_field_skipped(self):
        raw = '{"SKU": "sku", "Price": "total_cost"}'
        result = _parse_llm_mapping_response(raw, ['SKU', 'Price'])
        self.assertEqual(result, {'sku': 'SKU'})

    def test_malformed_json_returns_none(self):
        result = _parse_llm_mapping_response(
            'not json at all', ['A'],
        )
        self.assertIsNone(result)

    def test_empty_string_returns_none(self):
        self.assertIsNone(_parse_llm_mapping_response('', ['A']))

    def test_none_input_returns_none(self):
        self.assertIsNone(_parse_llm_mapping_response(None, ['A']))

    def test_json_in_code_fence(self):
        raw = '```json\n{"SKU": "sku"}\n```'
        result = _parse_llm_mapping_response(raw, ['SKU'])
        self.assertEqual(result, {'sku': 'SKU'})

    def test_json_with_surrounding_text(self):
        raw = 'Here is the mapping:\n{"SKU": "sku"}\nDone!'
        result = _parse_llm_mapping_response(raw, ['SKU'])
        self.assertEqual(result, {'sku': 'SKU'})

    def test_all_invalid_mappings_returns_none(self):
        raw = '{"BadCol": "badfield"}'
        result = _parse_llm_mapping_response(raw, ['SKU'])
        self.assertIsNone(result)

    def test_case_insensitive_field_matching(self):
        raw = '{"SKU": "SKU", "Price": "Price"}'
        result = _parse_llm_mapping_response(raw, ['SKU', 'Price'])
        self.assertEqual(result, {'sku': 'SKU', 'price': 'Price'})

    def test_first_mapping_per_field_wins(self):
        """When two columns map to the same internal field, keep the first."""
        raw = '{"Col1": "sku", "Col2": "sku"}'
        result = _parse_llm_mapping_response(raw, ['Col1', 'Col2'])
        self.assertEqual(result, {'sku': 'Col1'})


class TestSuggestColumnMappingIntegration(unittest.TestCase):
    """End-to-end tests for suggest_column_mapping with mocked LLM."""

    def test_valid_mapping_returned(self):
        df = pd.DataFrame({
            'Artikelnr': ['X1', 'X2'],
            'Enhedspris': [100, 200],
            'Beskrivelse': ['Widget', 'Gadget'],
        })

        def fake_llm(prompt, key, model):
            return (
                '{"Artikelnr": "sku", "Enhedspris": "price", '
                '"Beskrivelse": "description"}'
            )

        result = suggest_column_mapping(
            df, api_key='test', llm_call=fake_llm,
        )
        self.assertEqual(result['sku'], 'Artikelnr')
        self.assertEqual(result['price'], 'Enhedspris')
        self.assertEqual(result['description'], 'Beskrivelse')

    def test_no_api_key_returns_none(self):
        df = pd.DataFrame({'A': [1]})
        result = suggest_column_mapping(df)
        self.assertIsNone(result)

    def test_empty_df_returns_none(self):
        df = pd.DataFrame()
        result = suggest_column_mapping(df, api_key='test')
        self.assertIsNone(result)

    def test_llm_failure_returns_none(self):
        df = pd.DataFrame({'A': [1], 'B': [2]})

        def failing_llm(prompt, key, model):
            return None

        result = suggest_column_mapping(
            df, api_key='test', llm_call=failing_llm,
        )
        self.assertIsNone(result)

    def test_malformed_llm_response_returns_none(self):
        df = pd.DataFrame({'A': [1]})

        def bad_llm(prompt, key, model):
            return 'I cannot process this request.'

        result = suggest_column_mapping(
            df, api_key='test', llm_call=bad_llm,
        )
        self.assertIsNone(result)

    def test_partial_mapping_accepted(self):
        """LLM maps only some columns — partial result is fine."""
        df = pd.DataFrame({
            'Col1': ['A1'], 'Col2': [100], 'Col3': ['Foo'],
        })

        def partial_llm(prompt, key, model):
            return '{"Col1": "sku", "Col3": null}'

        result = suggest_column_mapping(
            df, api_key='test', llm_call=partial_llm,
        )
        self.assertEqual(result, {'sku': 'Col1'})

    def test_llm_references_nonexistent_columns(self):
        df = pd.DataFrame({'Real': [1]})

        def bad_cols_llm(prompt, key, model):
            return '{"Fake": "sku", "Real": "price"}'

        result = suggest_column_mapping(
            df, api_key='test', llm_call=bad_cols_llm,
        )
        self.assertEqual(result, {'price': 'Real'})

    def test_model_param_forwarded(self):
        df = pd.DataFrame({'A': [1]})
        captured = {}

        def capture_llm(prompt, key, model):
            captured['model'] = model
            return '{"A": "sku"}'

        suggest_column_mapping(
            df, api_key='test', model='gpt-4o', llm_call=capture_llm,
        )
        self.assertEqual(captured['model'], 'gpt-4o')

    def test_api_key_from_env(self):
        df = pd.DataFrame({'A': [1]})
        captured = {}

        def capture_llm(prompt, key, model):
            captured['key'] = key
            return '{"A": "sku"}'

        with patch.dict('os.environ', {'OPENAI_API_KEY': 'env-key'}):
            suggest_column_mapping(df, llm_call=capture_llm)
        self.assertEqual(captured['key'], 'env-key')

    def test_reexported_from_supplier_module(self):
        """suggest_column_mapping should be accessible from domain.supplier."""
        from domain.supplier import suggest_column_mapping as sup_scm
        self.assertIs(sup_scm, suggest_column_mapping)

    def test_heuristic_gap_fill_after_llm(self):
        """LLM maps sku only; heuristic fills unambiguous price."""
        df = pd.DataFrame({
            'Art': ['X1', 'X2'],
            'Pris': [100, 200],
            'Notes': ['a', 'b'],
        })

        def partial_llm(prompt, key, model):
            return '{"Art": "sku"}'

        result = suggest_column_mapping(
            df, api_key='test', llm_call=partial_llm,
        )
        self.assertEqual(result['sku'], 'Art')
        # 'Pris' is an unambiguous price candidate — should be gap-filled
        self.assertEqual(result.get('price'), 'Pris')

    def test_heuristic_gap_fill_ambiguous_not_filled(self):
        """When multiple candidates exist for a field, gap-fill skips it."""
        df = pd.DataFrame({
            'Art': ['X1'],
            'Price': [100],
            'Cost Price': [80],
        })

        def sku_only_llm(prompt, key, model):
            return '{"Art": "sku"}'

        result = suggest_column_mapping(
            df, api_key='test', llm_call=sku_only_llm,
        )
        self.assertEqual(result['sku'], 'Art')
        # Both 'Price' and 'Cost Price' match → ambiguous → not gap-filled
        self.assertNotIn('price', result)


# ===================================================================
# _guess_candidates — unified heuristic helper
# ===================================================================

class TestGuessCandidates(unittest.TestCase):
    """Tests for domain.supplier._guess_candidates."""

    def test_returns_all_fields(self):
        candidates = _guess_candidates(['A', 'B'])
        for field in INTERNAL_FIELDS:
            self.assertIn(field, candidates)
            self.assertIsInstance(candidates[field], list)

    def test_exact_match(self):
        candidates = _guess_candidates(['SKU', 'Price', 'Qty'])
        self.assertIn('SKU', candidates['sku'])
        self.assertIn('Price', candidates['price'])
        self.assertIn('Qty', candidates['qty'])

    def test_substring_match(self):
        candidates = _guess_candidates(['Unit Price', 'ArticleNumber'])
        self.assertIn('Unit Price', candidates['price'])
        self.assertIn('ArticleNumber', candidates['sku'])

    def test_danish_headers(self):
        candidates = _guess_candidates(['Varenr', 'Pris', 'Rabat', 'Valuta', 'Beskrivelse'])
        self.assertEqual(candidates['sku'], ['Varenr'])
        self.assertEqual(candidates['price'], ['Pris'])
        self.assertEqual(candidates['discount'], ['Rabat'])
        self.assertEqual(candidates['currency'], ['Valuta'])
        self.assertEqual(candidates['description'], ['Beskrivelse'])

    def test_no_match_returns_empty_list(self):
        candidates = _guess_candidates(['Foo', 'Bar', 'Baz'])
        for field in INTERNAL_FIELDS:
            self.assertEqual(candidates[field], [])

    def test_multiple_candidates_ordered(self):
        """Multiple columns matching the same field are listed best-first."""
        candidates = _guess_candidates(['SKU', 'Article', 'Item Number'])
        self.assertGreater(len(candidates['sku']), 1)
        # 'SKU' is an exact match — should come first
        self.assertEqual(candidates['sku'][0], 'SKU')


# ===================================================================
# detect_supplier_columns — LLM-first with heuristic fallback
# ===================================================================

class TestDetectSupplierColumnsLLMFirst(unittest.TestCase):
    """Tests for the LLM-first flow in detect_supplier_columns."""

    def test_llm_mapping_used_when_available(self):
        df = pd.DataFrame({
            'Col A': ['X1'], 'Col B': [100], 'Col C': ['Widget'],
        })

        def fake_llm(prompt, key, model):
            return '{"Col A": "sku", "Col B": "price", "Col C": "description"}'

        result = detect_supplier_columns(
            df, api_key='test', llm_call=fake_llm,
        )
        self.assertEqual(result['sku'], 'Col A')
        self.assertEqual(result['price'], 'Col B')
        self.assertEqual(result['description'], 'Col C')

    def test_heuristic_fallback_when_no_api_key(self):
        """Without API key, falls back to pure heuristic detection."""
        df = pd.DataFrame(columns=['SKU', 'Price', 'Description'])
        result = detect_supplier_columns(df)
        self.assertEqual(result['sku'], 'SKU')
        self.assertEqual(result['price'], 'Price')
        self.assertEqual(result['description'], 'Description')

    def test_heuristic_fallback_when_llm_fails(self):
        df = pd.DataFrame(columns=['SKU', 'Price'])

        def failing_llm(prompt, key, model):
            return None

        result = detect_supplier_columns(
            df, api_key='test', llm_call=failing_llm,
        )
        self.assertEqual(result['sku'], 'SKU')
        self.assertEqual(result['price'], 'Price')

    def test_heuristic_fallback_when_llm_unusable(self):
        """LLM returns garbage → heuristic fallback kicks in."""
        df = pd.DataFrame(columns=['SKU', 'Price'])

        def garbage_llm(prompt, key, model):
            return 'I cannot process this'

        result = detect_supplier_columns(
            df, api_key='test', llm_call=garbage_llm,
        )
        self.assertEqual(result['sku'], 'SKU')
        self.assertEqual(result['price'], 'Price')

    def test_returns_all_five_fields(self):
        """Result dict always has sku, price, discount, currency, description."""
        df = pd.DataFrame(columns=['X', 'Y'])
        result = detect_supplier_columns(df)
        for field in ('sku', 'price', 'discount', 'currency', 'description'):
            self.assertIn(field, result)


# ===================================================================
# detect_invoice_columns — LLM-first with heuristic fallback
# ===================================================================

class TestDetectInvoiceColumnsLLMFirst(unittest.TestCase):
    """Tests for the LLM-first flow in detect_invoice_columns."""

    def test_llm_mapping_used_when_available(self):
        df = pd.DataFrame({
            'Col A': ['X1'], 'Col B': [5], 'Col C': ['Widget'],
        })

        def fake_llm(prompt, key, model):
            return '{"Col A": "sku", "Col B": "qty", "Col C": "description"}'

        result = detect_invoice_columns(
            df, api_key='test', llm_call=fake_llm,
        )
        self.assertEqual(result['sku'], 'Col A')
        self.assertEqual(result['qty'], 'Col B')
        self.assertEqual(result['description'], 'Col C')

    def test_heuristic_fallback_when_no_api_key(self):
        df = pd.DataFrame(columns=['Varenr', 'Antal', 'Beskrivelse'])
        result = detect_invoice_columns(df)
        self.assertEqual(result['sku'], 'Varenr')
        self.assertEqual(result['qty'], 'Antal')
        self.assertEqual(result['description'], 'Beskrivelse')

    def test_heuristic_fallback_when_llm_fails(self):
        df = pd.DataFrame(columns=['SKU', 'Qty', 'Name'])

        def failing_llm(prompt, key, model):
            return None

        result = detect_invoice_columns(
            df, api_key='test', llm_call=failing_llm,
        )
        self.assertEqual(result['sku'], 'SKU')
        self.assertEqual(result['qty'], 'Qty')

    def test_returns_three_fields(self):
        """Result dict always has sku, qty, description."""
        df = pd.DataFrame(columns=['X', 'Y'])
        result = detect_invoice_columns(df)
        for field in ('sku', 'qty', 'description'):
            self.assertIn(field, result)

    def test_no_separate_regex_only_entry_point(self):
        """Confirm detect_invoice_columns tries LLM before heuristics.

        When an llm_call is provided and returns a valid mapping,
        the result should come from the LLM, not the heuristic path.
        """
        df = pd.DataFrame({
            'Alpha': ['X1'], 'Beta': [5],
        })

        def fake_llm(prompt, key, model):
            return '{"Alpha": "sku", "Beta": "qty"}'

        result = detect_invoice_columns(
            df, api_key='test', llm_call=fake_llm,
        )
        # LLM-provided mapping should be used
        self.assertEqual(result['sku'], 'Alpha')
        self.assertEqual(result['qty'], 'Beta')


# ===================================================================
# _heuristic helpers — direct tests
# ===================================================================

class TestHeuristicHelpers(unittest.TestCase):
    """Direct tests for _heuristic_detect_supplier_columns and
    _heuristic_detect_invoice_columns."""

    def test_heuristic_supplier_detects_known_patterns(self):
        df = pd.DataFrame(columns=['Artikelnr', 'Pris', 'Rabat', 'Valuta', 'Navn'])
        result = _heuristic_detect_supplier_columns(df)
        self.assertEqual(result['sku'], 'Artikelnr')
        self.assertEqual(result['price'], 'Pris')
        self.assertEqual(result['discount'], 'Rabat')
        self.assertEqual(result['currency'], 'Valuta')
        self.assertEqual(result['description'], 'Navn')

    def test_heuristic_invoice_detects_known_patterns(self):
        df = pd.DataFrame(columns=['SKU', 'Qty', 'Description'])
        result = _heuristic_detect_invoice_columns(df)
        self.assertEqual(result['sku'], 'SKU')
        self.assertEqual(result['qty'], 'Qty')
        self.assertEqual(result['description'], 'Description')


# ===================================================================
# DataFrame-centric deduplication — FB 400 XXS scenario
# ===================================================================

def _make_products_df(rows: list[dict]) -> pd.DataFrame:
    """Helper to build a mini product catalogue DataFrame."""
    cols = ['NUMBER', 'TITLE_DK', 'VARIANT_ID', 'VARIANT_TYPES', 'EAN']
    data = []
    for r in rows:
        data.append({c: r.get(c, '') for c in cols})
    return pd.DataFrame(data)


def _make_invoice_df(rows: list[dict], cols=None) -> pd.DataFrame:
    """Helper to build a mini invoice DataFrame."""
    if cols is None:
        cols = list(rows[0].keys()) if rows else []
    return pd.DataFrame(rows, columns=cols)


class TestBuildExportNoDuplication(unittest.TestCase):
    """Regression tests for the FB 400 XXS duplication scenario.

    When the product catalogue has multiple rows for the same
    (NUMBER, VARIANT_TYPES) — e.g. different VARIANT_IDs sharing the
    same type text — the export must NOT duplicate the SKU row.
    """

    def _run_export(self, products, invoice_rows, inv_sku_col='SKU',
                    inv_qty_col='Qty', inv_desc_col=None):
        from domain.invoice_ean import (
            match_invoice_to_products, build_export_from_matches,
        )
        products_df = _make_products_df(products)
        invoice_df = _make_invoice_df(invoice_rows)
        mdata = match_invoice_to_products(
            products_df, invoice_df, inv_sku_col,
            inv_qty_col, threshold=60,
            invoice_desc_col=inv_desc_col,
        )
        return build_export_from_matches(products_df, mdata)

    def test_fb400_xxs_not_duplicated(self):
        """SKU 'FB 400 XXS' should appear exactly once, not duplicated
        across multiple catalogue entries for the same variant type."""
        products = [
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Fighter Belt',
             'VARIANT_ID': '101', 'VARIANT_TYPES': 'XXS', 'EAN': '111'},
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Fighter Belt',
             'VARIANT_ID': '102', 'VARIANT_TYPES': 'XXS', 'EAN': '222'},
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Fighter Belt',
             'VARIANT_ID': '103', 'VARIANT_TYPES': 'XS', 'EAN': '333'},
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Fighter Belt',
             'VARIANT_ID': '104', 'VARIANT_TYPES': 'S', 'EAN': '444'},
        ]
        invoice = [{'SKU': 'FB 400 XXS', 'Qty': '5', 'Desc': 'Belt XXS'}]
        export = self._run_export(products, invoice, inv_desc_col='Desc')

        # Should have exactly one row for "FB 400 XXS"
        xxs_rows = export[export['SKU'] == 'FB 400 XXS']
        self.assertEqual(len(xxs_rows), 1, "FB 400 XXS should not be duplicated")
        self.assertEqual(xxs_rows.iloc[0]['Variant Name'], 'XXS')
        self.assertEqual(xxs_rows.iloc[0]['Amount'], 5.0)

    def test_quantity_not_replicated_across_variants(self):
        """When catalogue has duplicate variant entries, qty must NOT be
        replicated per catalogue row."""
        products = [
            {'NUMBER': 'AB100', 'TITLE_DK': 'Gloves',
             'VARIANT_ID': '1', 'VARIANT_TYPES': 'M', 'EAN': ''},
            {'NUMBER': 'AB100', 'TITLE_DK': 'Gloves',
             'VARIANT_ID': '2', 'VARIANT_TYPES': 'M', 'EAN': '555'},
        ]
        invoice = [{'SKU': 'AB100 M', 'Qty': '10'}]
        export = self._run_export(products, invoice)

        m_rows = export[export['SKU'] == 'AB100 M']
        self.assertEqual(len(m_rows), 1, "Should be deduplicated to one row")
        self.assertEqual(m_rows.iloc[0]['Amount'], 10.0)
        # Should prefer the row with EAN
        self.assertEqual(m_rows.iloc[0]['EAN'], '555')

    def test_distinct_variants_not_collapsed(self):
        """Different variant types should NOT be collapsed — only
        duplicate (NUMBER, VARIANT_TYPES) combos are deduplicated."""
        products = [
            {'NUMBER': 'CD200', 'TITLE_DK': 'Shirt',
             'VARIANT_ID': '1', 'VARIANT_TYPES': 'S', 'EAN': '111'},
            {'NUMBER': 'CD200', 'TITLE_DK': 'Shirt',
             'VARIANT_ID': '2', 'VARIANT_TYPES': 'M', 'EAN': '222'},
            {'NUMBER': 'CD200', 'TITLE_DK': 'Shirt',
             'VARIANT_ID': '3', 'VARIANT_TYPES': 'L', 'EAN': '333'},
        ]
        # Invoice SKU matches base product (no variant in SKU text)
        invoice = [{'SKU': 'CD200', 'Qty': '3'}]
        export = self._run_export(products, invoice)

        # All three distinct variants should appear (no dedup here)
        cd_rows = export[export['Product Number'] == 'CD200']
        self.assertEqual(len(cd_rows), 3)
        variant_names = set(cd_rows['Variant Name'])
        self.assertEqual(variant_names, {'S', 'M', 'L'})

    def test_single_variant_exact_match(self):
        """When variant narrowing finds an exact match, only that row appears."""
        products = [
            {'NUMBER': 'EF300', 'TITLE_DK': 'Pants',
             'VARIANT_ID': '1', 'VARIANT_TYPES': 'XXS', 'EAN': '111'},
            {'NUMBER': 'EF300', 'TITLE_DK': 'Pants',
             'VARIANT_ID': '2', 'VARIANT_TYPES': 'XS', 'EAN': '222'},
            {'NUMBER': 'EF300', 'TITLE_DK': 'Pants',
             'VARIANT_ID': '3', 'VARIANT_TYPES': 'S', 'EAN': '333'},
        ]
        invoice = [{'SKU': 'EF300 XS', 'Qty': '7'}]
        export = self._run_export(products, invoice)

        xs_rows = export[export['SKU'] == 'EF300 XS']
        self.assertEqual(len(xs_rows), 1)
        self.assertEqual(xs_rows.iloc[0]['Variant Name'], 'XS')
        self.assertEqual(xs_rows.iloc[0]['Amount'], 7.0)

    def test_no_variant_product_not_duplicated(self):
        """Product with no variants should produce exactly one row."""
        products = [
            {'NUMBER': 'GH500', 'TITLE_DK': 'Simple Product',
             'VARIANT_ID': '', 'VARIANT_TYPES': '', 'EAN': '999'},
        ]
        invoice = [{'SKU': 'GH500', 'Qty': '2'}]
        export = self._run_export(products, invoice)

        self.assertEqual(len(export), 1)
        self.assertEqual(export.iloc[0]['SKU'], 'GH500')
        self.assertEqual(export.iloc[0]['Amount'], 2.0)


class TestBuildExportIncompleteLLMMapping(unittest.TestCase):
    """Ensure the DataFrame-centric design prevents duplication even
    when the LLM output is incomplete and heuristic fallback is used."""

    def test_heuristic_fallback_still_prevents_duplication(self):
        """Even without LLM mapping, the deduplication pipeline
        should prevent row duplication from catalogue duplicates."""
        from domain.invoice_ean import (
            match_invoice_to_products, build_export_from_matches,
        )
        products = _make_products_df([
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Belt',
             'VARIANT_ID': '1', 'VARIANT_TYPES': 'XXS', 'EAN': '111'},
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Belt',
             'VARIANT_ID': '2', 'VARIANT_TYPES': 'XXS', 'EAN': ''},
            {'NUMBER': 'FB 400', 'TITLE_DK': 'Belt',
             'VARIANT_ID': '3', 'VARIANT_TYPES': 'XS', 'EAN': '333'},
        ])
        invoice = pd.DataFrame({
            'Article No': ['FB 400 XXS'],
            'Qty': ['5'],
            'Designation': ['Belt XXS'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article No', 'Qty',
            threshold=60, invoice_desc_col='Designation',
        )
        export = build_export_from_matches(products, mdata)

        xxs_rows = export[export['Variant Name'] == 'XXS']
        self.assertEqual(
            len(xxs_rows), 1,
            "Duplicate catalogue entries for XXS should be deduplicated",
        )
        self.assertEqual(xxs_rows.iloc[0]['Amount'], 5.0)
        # Should prefer the row with EAN
        self.assertEqual(xxs_rows.iloc[0]['EAN'], '111')


# ===================================================================
# debug_print_mapping helper
# ===================================================================

class TestDebugPrintMapping(unittest.TestCase):
    """Tests for the developer debugging helper."""

    def test_returns_string_with_basic_info(self):
        from domain.supplier import debug_print_mapping

        df = pd.DataFrame({'SKU': ['X1', 'X2'], 'Price': [10, 20]})
        mapping = {'sku': 'SKU', 'price': 'Price'}
        result = debug_print_mapping(df, mapping)
        self.assertIn('Column Mapping Debug', result)
        self.assertIn('SKU', result)
        self.assertIn('Price', result)
        self.assertIn("'sku': 'SKU'", result)

    def test_handles_none_mapping(self):
        from domain.supplier import debug_print_mapping

        df = pd.DataFrame({'A': [1]})
        result = debug_print_mapping(df)
        self.assertIn('None', result)

    def test_includes_llm_raw(self):
        from domain.supplier import debug_print_mapping

        df = pd.DataFrame({'A': [1]})
        result = debug_print_mapping(
            df, llm_raw='{"A": "sku"}',
        )
        self.assertIn('LLM raw response', result)
        self.assertIn('{"A": "sku"}', result)

    def test_includes_final_df(self):
        from domain.supplier import debug_print_mapping

        df = pd.DataFrame({'A': [1]})
        final = pd.DataFrame({'SKU': ['X1'], 'Amount': [5]})
        result = debug_print_mapping(df, final_df=final)
        self.assertIn('Final DataFrame', result)
        self.assertIn('X1', result)

    def test_writes_to_out(self):
        from domain.supplier import debug_print_mapping

        df = pd.DataFrame({'A': [1]})
        buf = io.StringIO()
        debug_print_mapping(df, out=buf)
        self.assertIn('Column Mapping Debug', buf.getvalue())


# ===================================================================
# _dedupe_product_rows — direct unit tests
# ===================================================================

class TestDedupeProductRows(unittest.TestCase):
    """Tests for the internal _dedupe_product_rows helper."""

    def test_single_row_unchanged(self):
        from domain.invoice_ean import _dedupe_product_rows

        df = _make_products_df([
            {'NUMBER': 'A1', 'VARIANT_TYPES': 'S', 'EAN': '111'},
        ])
        result = _dedupe_product_rows(df)
        self.assertEqual(len(result), 1)

    def test_unique_variants_unchanged(self):
        from domain.invoice_ean import _dedupe_product_rows

        df = _make_products_df([
            {'NUMBER': 'A1', 'VARIANT_TYPES': 'S', 'EAN': '111'},
            {'NUMBER': 'A1', 'VARIANT_TYPES': 'M', 'EAN': '222'},
            {'NUMBER': 'A1', 'VARIANT_TYPES': 'L', 'EAN': '333'},
        ])
        result = _dedupe_product_rows(df)
        self.assertEqual(len(result), 3)

    def test_duplicate_variants_deduped(self):
        from domain.invoice_ean import _dedupe_product_rows

        df = _make_products_df([
            {'NUMBER': 'A1', 'VARIANT_TYPES': 'S', 'EAN': '111'},
            {'NUMBER': 'A1', 'VARIANT_TYPES': 'S', 'EAN': ''},
        ])
        result = _dedupe_product_rows(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]['EAN'], '111')

    def test_prefers_row_with_ean(self):
        from domain.invoice_ean import _dedupe_product_rows

        df = _make_products_df([
            {'NUMBER': 'B2', 'VARIANT_TYPES': 'M', 'EAN': ''},
            {'NUMBER': 'B2', 'VARIANT_TYPES': 'M', 'EAN': '999'},
        ])
        result = _dedupe_product_rows(df)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]['EAN'], '999')

    def test_empty_df_unchanged(self):
        from domain.invoice_ean import _dedupe_product_rows

        df = _make_products_df([])
        result = _dedupe_product_rows(df)
        self.assertEqual(len(result), 0)


# ===================================================================
# _normalize_export_df — direct unit tests
# ===================================================================

class TestNormalizeExportDf(unittest.TestCase):
    """Tests for the final export DataFrame normalization."""

    def test_no_duplicates_unchanged(self):
        from domain.invoice_ean import _normalize_export_df

        df = pd.DataFrame({
            'SKU': ['A', 'B'],
            'Product Number': ['P1', 'P2'],
            'Variant Name': ['S', 'M'],
            'Title': ['T1', 'T2'],
            'Amount': [1, 2],
            'EAN': ['111', '222'],
            'Match %': [90, 85],
        })
        result = _normalize_export_df(df)
        self.assertEqual(len(result), 2)

    def test_duplicates_deduped(self):
        from domain.invoice_ean import _normalize_export_df

        df = pd.DataFrame({
            'SKU': ['A', 'A'],
            'Product Number': ['P1', 'P1'],
            'Variant Name': ['S', 'S'],
            'Title': ['T1', 'T1'],
            'Amount': [5, 5],
            'EAN': ['', '111'],
            'Match %': [90, 90],
        })
        result = _normalize_export_df(df)
        self.assertEqual(len(result), 1)
        # Should prefer row with EAN
        self.assertEqual(result.iloc[0]['EAN'], '111')

    def test_empty_df_unchanged(self):
        from domain.invoice_ean import _normalize_export_df

        df = pd.DataFrame(columns=[
            'SKU', 'Product Number', 'Variant Name',
            'Title', 'Amount', 'EAN', 'Match %',
        ])
        result = _normalize_export_df(df)
        self.assertEqual(len(result), 0)


# ===================================================================
# Enhanced LLM prompt — domain-specific checks
# ===================================================================

class TestBuildMappingPromptEnhanced(unittest.TestCase):
    """Tests for the improved LLM prompt content."""

    def test_prompt_distinguishes_qty_from_price(self):
        """Prompt should include guidance on distinguishing qty from price."""
        df = pd.DataFrame({'Antal': [5], 'Pris': [99.50]})
        prompt = _build_mapping_prompt(df)
        self.assertIn('count of units', prompt)
        self.assertIn('NOT a price', prompt)

    def test_prompt_explains_sku_vs_variant(self):
        """Prompt should explain that SKU is the product code, not variant."""
        df = pd.DataFrame({'Art': ['FB 400 XXS']})
        prompt = _build_mapping_prompt(df)
        self.assertIn('variant', prompt.lower())
        self.assertIn('base product', prompt.lower())

    def test_prompt_warns_about_line_totals(self):
        """Prompt should warn about not mapping line totals to price."""
        df = pd.DataFrame({'Item Value': [220.00]})
        prompt = _build_mapping_prompt(df)
        self.assertIn('line-item total', prompt.lower())


if __name__ == '__main__':
    unittest.main()
