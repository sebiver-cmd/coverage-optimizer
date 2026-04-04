"""Tests for domain/invoice_ean.py — invoice-to-EAN barcode matching."""

from __future__ import annotations

import re
from unittest.mock import patch

import pandas as pd
import pytest

from domain.invoice_ean import (
    detect_invoice_columns,
    build_ean_export,
    match_invoice_to_products,
    match_supplier_to_products,
    build_export_from_matches,
    build_matches_df,
    export_from_matches_df,
    generate_barcode_pdf,
    normalize_sku,
    extract_sku_from_description,
    suggest_column_mapping,
    _render_barcode_image,
    _variant_in_context,
    _parse_qty,
    _extract_numeric_hints,
    _has_variant_hint,
    _generate_barcode_pdf_zd421,
    _generate_barcode_pdf_fast_scan,
    _normalize_craft_sku,
    _CRAFT_SIZE_INDEX_TO_LABEL,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_products(**overrides) -> pd.DataFrame:
    """Build a minimal product catalogue DataFrame."""
    defaults = {
        'NUMBER': ['SKU-001', 'SKU-002', 'SKU-003'],
        'TITLE_DK': ['Widget A', 'Widget B', 'Widget C'],
        'VARIANT_ID': ['', '10', ''],
        'VARIANT_TYPES': ['', 'Red / Large', ''],
        'EAN': ['5701234567890', '5709876543210', ''],
        'BUY_PRICE': ['100,00', '200,00', '50,00'],
        'PRICE': ['200,00', '400,00', '100,00'],
        'BUY_PRICE_NUM': [100.0, 200.0, 50.0],
        'PRICE_NUM': [200.0, 400.0, 100.0],
        'PRODUCT_ID': ['1', '2', '3'],
        'PRODUCER': ['Brand A', 'Brand B', 'Brand A'],
        'PRODUCER_ID': [1, 2, 1],
        'ONLINE': [True, True, False],
    }
    defaults.update(overrides)
    return pd.DataFrame(defaults)


def _make_invoice(**overrides) -> pd.DataFrame:
    """Build a minimal invoice DataFrame."""
    defaults = {
        'Article': ['SKU-001', 'SKU-002', 'UNKNOWN-99'],
        'Quantity': ['5', '10', '2'],
        'Description': ['Widget A', 'Widget B', 'Mystery'],
    }
    defaults.update(overrides)
    return pd.DataFrame(defaults)


# ---------------------------------------------------------------------------
# detect_invoice_columns
# ---------------------------------------------------------------------------

class TestDetectInvoiceColumns:
    """Tests for auto-detection of invoice column mappings."""

    def test_detects_standard_columns(self):
        df = pd.DataFrame(columns=['Article', 'Quantity', 'Description'])
        result = detect_invoice_columns(df)
        assert result['qty'] == 'Quantity'

    def test_detects_danish_qty(self):
        df = pd.DataFrame(columns=['Varenr', 'Antal', 'Navn'])
        result = detect_invoice_columns(df)
        assert result['sku'] == 'Varenr'
        assert result['qty'] == 'Antal'

    def test_detects_german_qty(self):
        df = pd.DataFrame(columns=['Artikelnr', 'Anzahl', 'Beschreibung'])
        result = detect_invoice_columns(df)
        assert result['qty'] == 'Anzahl'

    def test_detects_qty_abbreviation(self):
        df = pd.DataFrame(columns=['SKU', 'Qty', 'Name'])
        result = detect_invoice_columns(df)
        assert result['qty'] == 'Qty'

    def test_detects_pcs(self):
        df = pd.DataFrame(columns=['Item', 'Pcs', 'Title'])
        result = detect_invoice_columns(df)
        assert result['qty'] == 'Pcs'

    def test_returns_none_when_no_qty_column(self):
        df = pd.DataFrame(columns=['SKU', 'Price', 'Total'])
        result = detect_invoice_columns(df)
        assert result['qty'] is None

    def test_sku_detection_delegates_to_supplier(self):
        df = pd.DataFrame(columns=['ItemNumber', 'Units', 'Title'])
        result = detect_invoice_columns(df)
        assert result['sku'] is not None

    def test_partial_match_in_column_name(self):
        df = pd.DataFrame(columns=['product_sku_code', 'total_quantity', 'desc'])
        result = detect_invoice_columns(df)
        assert result['sku'] == 'product_sku_code'
        assert result['qty'] == 'total_quantity'


# ---------------------------------------------------------------------------
# build_ean_export
# ---------------------------------------------------------------------------

class TestBuildEanExport:
    """Tests for building the EAN export document."""

    def test_basic_matching(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert not result.empty
        assert 'EAN' in result.columns
        assert 'SKU' in result.columns
        assert 'Product Number' in result.columns
        assert 'Title' in result.columns
        assert 'Variant Name' in result.columns
        assert 'Amount' in result.columns
        assert 'Match %' in result.columns

    def test_quantities_are_parsed(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        sku1_row = result.loc[result['SKU'] == 'SKU-001']
        assert not sku1_row.empty
        assert sku1_row.iloc[0]['Amount'] == 5.0

        sku2_row = result.loc[result['SKU'] == 'SKU-002']
        assert not sku2_row.empty
        assert sku2_row.iloc[0]['Amount'] == 10.0

    def test_ean_values_are_included(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        sku1_row = result.loc[result['SKU'] == 'SKU-001']
        assert sku1_row.iloc[0]['EAN'] == '5701234567890'

    def test_variant_name_is_included(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        sku2_row = result.loc[result['SKU'] == 'SKU-002']
        assert sku2_row.iloc[0]['Variant Name'] == 'Red / Large'

    def test_unmatched_invoice_lines_excluded(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=90,
        )
        # UNKNOWN-99 should not match any product at threshold=90
        unknown = result.loc[result['SKU'] == 'UNKNOWN-99']
        assert unknown.empty

    def test_no_qty_column_defaults_to_one(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', None, threshold=70,
        )
        assert not result.empty
        assert (result['Amount'] == 1.0).all()

    def test_empty_invoice_returns_empty(self):
        products = _make_products()
        invoice = pd.DataFrame(columns=['Article', 'Quantity'])
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert result.empty
        assert list(result.columns) == [
            'SKU', 'Product Number', 'Title', 'Variant Name',
            'Amount', 'EAN', 'Match %',
        ]

    def test_no_matches_returns_empty(self):
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['NOPE-1', 'NOPE-2'],
            'Quantity': ['1', '1'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=95,
        )
        assert result.empty

    def test_high_threshold_reduces_matches(self):
        products = _make_products()
        invoice = _make_invoice()
        result_low = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        result_high = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=99,
        )
        assert len(result_high) <= len(result_low)

    def test_product_with_empty_ean(self):
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['SKU-003'],
            'Quantity': ['3'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        if not result.empty:
            assert result.iloc[0]['EAN'] == ''

    def test_comma_decimal_quantity(self):
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['SKU-001'],
            'Quantity': ['2,5'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert not result.empty
        assert result.iloc[0]['Amount'] == 2.5

    def test_invalid_quantity_defaults_to_one(self):
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['SKU-001'],
            'Quantity': ['abc'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert not result.empty
        assert result.iloc[0]['Amount'] == 1.0

    def test_match_score_included(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert not result.empty
        # Exact matches should score 100
        sku1_row = result.loc[result['SKU'] == 'SKU-001']
        assert sku1_row.iloc[0]['Match %'] == 100

    def test_multiple_variants_expanded(self):
        """When multiple product rows share a NUMBER, all should appear."""
        products = _make_products(
            NUMBER=['SKU-001', 'SKU-001', 'SKU-002'],
            VARIANT_ID=['', '11', ''],
            VARIANT_TYPES=['', 'Blue / Small', ''],
            EAN=['5701234567890', '5701234567891', '5709876543210'],
            TITLE_DK=['Widget A', 'Widget A', 'Widget B'],
            BUY_PRICE=['100,00', '110,00', '200,00'],
            PRICE=['200,00', '220,00', '400,00'],
            BUY_PRICE_NUM=[100.0, 110.0, 200.0],
            PRICE_NUM=[200.0, 220.0, 400.0],
            PRODUCT_ID=['1', '1', '2'],
            PRODUCER=['Brand A', 'Brand A', 'Brand B'],
            PRODUCER_ID=[1, 1, 2],
            ONLINE=[True, True, True],
        )
        invoice = pd.DataFrame({
            'Article': ['SKU-001'],
            'Quantity': ['5'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        # Both variants of SKU-001 should be in the result
        assert len(result) == 2
        eans = set(result['EAN'].tolist())
        assert '5701234567890' in eans
        assert '5701234567891' in eans

    def test_output_columns_order(self):
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        if not result.empty:
            expected = [
                'SKU', 'Product Number', 'Title', 'Variant Name',
                'Amount', 'EAN', 'Match %',
            ]
            assert list(result.columns) == expected


class TestVariantNarrowing:
    """Tests for narrowing variant matches by variant name."""

    @staticmethod
    def _size_products():
        """Products with size-based variants (e.g. dining table)."""
        return _make_products(
            NUMBER=['TBL-01', 'TBL-01', 'TBL-01', 'SKU-002'],
            VARIANT_ID=['10', '11', '12', ''],
            VARIANT_TYPES=['160 cm', '190 cm', '220 cm', ''],
            EAN=['5700000000160', '5700000000190', '5700000000220', '5709876543210'],
            TITLE_DK=['Dining Table', 'Dining Table', 'Dining Table', 'Widget B'],
            BUY_PRICE=['500,00', '600,00', '700,00', '200,00'],
            PRICE=['1000,00', '1200,00', '1400,00', '400,00'],
            BUY_PRICE_NUM=[500.0, 600.0, 700.0, 200.0],
            PRICE_NUM=[1000.0, 1200.0, 1400.0, 400.0],
            PRODUCT_ID=['1', '1', '1', '2'],
            PRODUCER=['Brand A', 'Brand A', 'Brand A', 'Brand B'],
            PRODUCER_ID=[1, 1, 1, 2],
            ONLINE=[True, True, True, True],
        )

    def test_narrows_by_variant_in_sku(self):
        """Invoice SKU 'TBL-01 190 cm' should match only the 190 cm variant."""
        products = self._size_products()
        invoice = pd.DataFrame({
            'Article': ['TBL-01 190 cm'],
            'Quantity': ['1'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == '190 cm'
        assert result.iloc[0]['EAN'] == '5700000000190'

    def test_narrows_by_variant_in_description(self):
        """Variant name in description column narrows the match."""
        products = self._size_products()
        invoice = pd.DataFrame({
            'Article': ['TBL-01'],
            'Quantity': ['2'],
            'Description': ['Dining Table 220 cm'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == '220 cm'
        assert result.iloc[0]['EAN'] == '5700000000220'

    def test_no_variant_hint_returns_all(self):
        """When no variant info in invoice, all variants are returned."""
        products = self._size_products()
        invoice = pd.DataFrame({
            'Article': ['TBL-01'],
            'Quantity': ['3'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        # All 3 size variants should be returned
        assert len(result) == 3

    def test_no_narrowing_for_single_variant(self):
        """Single-row products are never narrowed (no-op)."""
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        sku1 = result.loc[result['SKU'] == 'SKU-001']
        assert len(sku1) == 1

    def test_color_variant_narrowing(self):
        """Narrowing works with color variants too."""
        products = _make_products(
            NUMBER=['SHIRT-01', 'SHIRT-01', 'SHIRT-01'],
            VARIANT_ID=['20', '21', '22'],
            VARIANT_TYPES=['Red', 'Blue', 'Green'],
            EAN=['5700000000001', '5700000000002', '5700000000003'],
            TITLE_DK=['T-Shirt', 'T-Shirt', 'T-Shirt'],
            BUY_PRICE=['50,00', '50,00', '50,00'],
            PRICE=['100,00', '100,00', '100,00'],
            BUY_PRICE_NUM=[50.0, 50.0, 50.0],
            PRICE_NUM=[100.0, 100.0, 100.0],
            PRODUCT_ID=['5', '5', '5'],
            PRODUCER=['Brand C', 'Brand C', 'Brand C'],
            PRODUCER_ID=[3, 3, 3],
            ONLINE=[True, True, True],
        )
        invoice = pd.DataFrame({
            'Article': ['SHIRT-01'],
            'Quantity': ['4'],
            'Description': ['T-Shirt Blue'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'Blue'
        assert result.iloc[0]['EAN'] == '5700000000002'

    def test_case_insensitive_narrowing(self):
        """Variant narrowing is case-insensitive."""
        products = self._size_products()
        invoice = pd.DataFrame({
            'Article': ['TBL-01 160 CM'],
            'Quantity': ['1'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == '160 cm'

    def test_desc_col_none_ignored(self):
        """Passing invoice_desc_col=None doesn't break anything."""
        products = self._size_products()
        invoice = pd.DataFrame({
            'Article': ['TBL-01'],
            'Quantity': ['1'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col=None,
        )
        assert len(result) == 3

    def test_backward_compat_no_desc_col(self):
        """Calling without invoice_desc_col still works (backward compat)."""
        products = _make_products()
        invoice = _make_invoice()
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert not result.empty


# ---------------------------------------------------------------------------
# _parse_qty
# ---------------------------------------------------------------------------

class TestParseQty:
    """Tests for robust quantity parsing."""

    def test_simple_integer(self):
        assert _parse_qty('5') == 5.0

    def test_simple_float(self):
        assert _parse_qty('2.5') == 2.5

    def test_comma_decimal(self):
        assert _parse_qty('2,5') == 2.5

    def test_unit_suffix_stk(self):
        assert _parse_qty('5stk') == 5.0

    def test_unit_suffix_pcs(self):
        assert _parse_qty('10pcs') == 10.0

    def test_unit_suffix_with_space(self):
        assert _parse_qty('7 stk') == 7.0

    def test_empty_string(self):
        assert _parse_qty('') == 1.0

    def test_none_value(self):
        assert _parse_qty(None) == 1.0

    def test_nan_value(self):
        assert _parse_qty(float('nan')) == 1.0

    def test_non_numeric(self):
        assert _parse_qty('abc') == 1.0

    def test_numeric_from_int(self):
        assert _parse_qty(10) == 10.0

    def test_numeric_from_float(self):
        assert _parse_qty(3.5) == 3.5


# ---------------------------------------------------------------------------
# _variant_in_context (word boundary matching)
# ---------------------------------------------------------------------------

class TestVariantInContext:
    """Tests for word-boundary variant matching."""

    def test_exact_word_match(self):
        assert _variant_in_context('XL', 'psw 003 xl')

    def test_short_variant_no_false_positive(self):
        """'S' should NOT match inside 'PSW'."""
        assert not _variant_in_context('S', 'psw 003')

    def test_short_variant_matches_standalone(self):
        """'S' should match when it's a standalone word."""
        assert _variant_in_context('S', 'shirt s')

    def test_l_not_in_xl(self):
        """'L' should NOT match inside 'XL'."""
        assert not _variant_in_context('L', 'psw 003 xl')

    def test_l_standalone(self):
        """'L' should match when standalone."""
        assert _variant_in_context('L', 'shirt l blue')

    def test_size_cm_match(self):
        assert _variant_in_context('190 cm', 'tbl-01 190 cm')

    def test_slash_separated(self):
        assert _variant_in_context('Red', 'red/large')

    def test_empty_variant(self):
        assert not _variant_in_context('', 'some context')

    def test_case_insensitive(self):
        assert _variant_in_context('XL', 'PSW 003 XL')

    # --- Size-alias matching ---

    def test_alias_large_matches_l(self):
        """Variant 'Large' should match context containing abbreviation 'L'."""
        assert _variant_in_context('Large', 'pss 001 l')

    def test_alias_l_matches_large(self):
        """Variant 'L' should match context containing full name 'large'."""
        assert _variant_in_context('L', 'guard large')

    def test_alias_small_matches_s(self):
        """Variant 'Small' should match context containing abbreviation 'S'."""
        assert _variant_in_context('Small', 'pss 001 s')

    def test_alias_s_matches_small(self):
        """Variant 'S' should match context containing full name 'small'."""
        assert _variant_in_context('S', 'guard small')

    def test_alias_medium_matches_m(self):
        """Variant 'Medium' should match context containing 'M'."""
        assert _variant_in_context('Medium', 'pss 001 m')

    def test_alias_m_matches_medium(self):
        """Variant 'M' should match context containing 'medium'."""
        assert _variant_in_context('M', 'guard medium')

    def test_alias_xl_matches_x_large(self):
        """Variant 'XL' should match context containing 'x-large'."""
        assert _variant_in_context('XL', 'guard x-large')

    def test_alias_x_large_matches_xl(self):
        """Variant 'X-Large' should match context containing 'xl'."""
        assert _variant_in_context('X-Large', 'pss 001 xl')

    def test_alias_xs_matches_x_small(self):
        """Variant 'XS' should match context containing 'x-small'."""
        assert _variant_in_context('XS', 'guard x-small')

    def test_alias_x_small_matches_xs(self):
        """Variant 'X-Small' should match context containing 'xs'."""
        assert _variant_in_context('X-Small', 'pss 001 xs')

    def test_alias_xxl_matches_xx_large(self):
        """Variant 'XXL' should match context containing 'xx-large'."""
        assert _variant_in_context('XXL', 'guard xx-large')

    def test_alias_no_false_positive_non_size(self):
        """Non-size variants should NOT alias-match unrelated context."""
        assert not _variant_in_context('Red', 'pss 001 l')

    def test_alias_s_not_in_psw(self):
        """Size alias for 'Small' should NOT match inside 'PSW'."""
        assert not _variant_in_context('Small', 'psw 003')

    # --- Composite variant splitting on "/" and "//" ---

    def test_double_slash_composite_matches_color(self):
        """'rød//Large' should match when context contains 'rød'."""
        assert _variant_in_context('rød//Large', 'shirt rød')

    def test_double_slash_composite_matches_size(self):
        """'rød//Large' should match when context contains 'Large'."""
        assert _variant_in_context('rød//Large', 'shirt large')

    def test_double_slash_composite_matches_size_alias(self):
        """'rød//Large' should match 'L' via size alias."""
        assert _variant_in_context('rød//Large', 'shirt l')

    def test_double_slash_no_match(self):
        """'rød//Large' should NOT match unrelated context."""
        assert not _variant_in_context('rød//Large', 'shirt blå')

    def test_single_slash_composite_matches_part(self):
        """'Red / Large' should match when context contains 'red'."""
        assert _variant_in_context('Red / Large', 'guard red')

    def test_single_slash_composite_matches_size(self):
        """'Red / Large' should match context with 'large'."""
        assert _variant_in_context('Red / Large', 'guard large')

    def test_composite_no_false_positive(self):
        """'Red / Large' should NOT match unrelated context."""
        assert not _variant_in_context('Red / Large', 'guard blue')

    def test_double_slash_narrows_correct_variant(self):
        """Narrowing with double-slash variants picks the right one."""
        products = _make_products(
            NUMBER=['SH-01', 'SH-01', 'SH-01', 'SH-01'],
            VARIANT_ID=['30', '31', '32', '33'],
            VARIANT_TYPES=[
                'rød//Large', 'rød//Small', 'blå//Large', 'blå//Small',
            ],
            EAN=['5700000000010', '5700000000020',
                 '5700000000030', '5700000000040'],
            TITLE_DK=['Shirt', 'Shirt', 'Shirt', 'Shirt'],
            BUY_PRICE=['50,00', '50,00', '50,00', '50,00'],
            PRICE=['100,00', '100,00', '100,00', '100,00'],
            BUY_PRICE_NUM=[50.0, 50.0, 50.0, 50.0],
            PRICE_NUM=[100.0, 100.0, 100.0, 100.0],
            PRODUCT_ID=['6', '6', '6', '6'],
            PRODUCER=['Brand D', 'Brand D', 'Brand D', 'Brand D'],
            PRODUCER_ID=[4, 4, 4, 4],
            ONLINE=[True, True, True, True],
        )
        # Invoice says "Large" — should narrow to both "Large" variants
        invoice = pd.DataFrame({
            'Article': ['SH-01'],
            'Quantity': ['2'],
            'Description': ['Shirt Large'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) == 2
        assert set(result['Variant Name']) == {'rød//Large', 'blå//Large'}


# ---------------------------------------------------------------------------
# Stricter SKU matching with title + SKU
# ---------------------------------------------------------------------------

class TestStricterSKUMatching:
    """Tests for matching that uses both SKU and description/title."""

    @staticmethod
    def _size_variants():
        """Products with S/M/L/XL variants."""
        return _make_products(
            NUMBER=['PSW 003', 'PSW 003', 'PSW 003', 'PSW 003'],
            VARIANT_ID=['10', '11', '12', '13'],
            VARIANT_TYPES=['S', 'M', 'L', 'XL'],
            EAN=['5700000000001', '5700000000002', '5700000000003', '5700000000004'],
            TITLE_DK=['Shirt', 'Shirt', 'Shirt', 'Shirt'],
            BUY_PRICE=['50,00', '50,00', '50,00', '50,00'],
            PRICE=['100,00', '100,00', '100,00', '100,00'],
            BUY_PRICE_NUM=[50.0, 50.0, 50.0, 50.0],
            PRICE_NUM=[100.0, 100.0, 100.0, 100.0],
            PRODUCT_ID=['1', '1', '1', '1'],
            PRODUCER=['Brand A', 'Brand A', 'Brand A', 'Brand A'],
            PRODUCER_ID=[1, 1, 1, 1],
            ONLINE=[True, True, True, True],
        )

    def test_sku_with_variant_suffix_xl(self):
        """Invoice SKU 'PSW 003 XL' should match only XL variant."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003 XL'],
            'Quantity': ['3'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'XL'
        assert result.iloc[0]['EAN'] == '5700000000004'

    def test_sku_with_variant_suffix_s(self):
        """Invoice SKU 'PSW 003 S' should match only S variant."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003 S'],
            'Quantity': ['2'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'S'
        assert result.iloc[0]['EAN'] == '5700000000001'

    def test_sku_with_variant_suffix_m(self):
        """Invoice SKU 'PSW 003 M' should match only M variant."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003 M'],
            'Quantity': ['4'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'M'
        assert result.iloc[0]['EAN'] == '5700000000002'

    def test_amount_preserved_with_variant_sku(self):
        """Amount should be correctly read even with variant-appended SKUs."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003 XL', 'PSW 003 M'],
            'Quantity': ['7', '12'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        xl_rows = result.loc[result['Variant Name'] == 'XL']
        assert not xl_rows.empty
        assert xl_rows.iloc[0]['Amount'] == 7.0

        m_rows = result.loc[result['Variant Name'] == 'M']
        assert not m_rows.empty
        assert m_rows.iloc[0]['Amount'] == 12.0

    def test_description_col_narrows_variant(self):
        """Description column should help narrow to the correct variant."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003'],
            'Quantity': ['5'],
            'Description': ['Shirt XL'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'XL'

    def test_base_sku_without_variant_hint_returns_all(self):
        """Base SKU without variant info returns all variants."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003'],
            'Quantity': ['1'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        assert len(result) == 4

    def test_vtype_mismatch_falls_back_to_narrowing(self):
        """When composite vtype doesn't match VARIANT_TYPES exactly,
        fall back to _narrow_variants using description context."""
        products = _make_products(
            NUMBER=['PSS 001', 'PSS 001', 'PSS 001'],
            VARIANT_ID=['10', '11', '12'],
            VARIANT_TYPES=['Small', 'Medium', 'Large'],
            EAN=['5700000000001', '5700000000002', '5700000000003'],
            TITLE_DK=['Guard', 'Guard', 'Guard'],
            BUY_PRICE=['10,00'] * 3,
            PRICE=['20,00'] * 3,
            BUY_PRICE_NUM=[10.0] * 3,
            PRICE_NUM=[20.0] * 3,
            PRODUCT_ID=['1'] * 3,
            PRODUCER=['Brand'] * 3,
            PRODUCER_ID=[1] * 3,
            ONLINE=[True] * 3,
        )
        # Use two-step API with crafted match_data where composite vtype
        # "L" doesn't match any VARIANT_TYPES exactly (they're "Large",
        # "Medium", "Small"), but the description contains "Large".
        match_data = {
            'matches': {
                'PSS 001 L': {
                    'sku': 'PSS 001 L', 'score': 100, 'alternatives': [],
                },
            },
            'composite_lookup': {
                'PSS 001': ('PSS 001', ''),
                'PSS 001 L': ('PSS 001', 'L'),
            },
            'title_lookup': {'PSS 001': 'Guard'},
            'qty_map': {'PSS 001 L': 5.0},
            'desc_map': {'PSS 001 L': 'Guard Large'},
        }
        result = build_export_from_matches(products, match_data)
        # vtype "L" doesn't match "Large" exactly, but the description
        # "Guard Large" lets _narrow_variants find the right variant.
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'Large'
        assert result.iloc[0]['Amount'] == 5.0

    def test_invoice_deduplicates_repeated_skus(self):
        """Duplicate invoice SKUs should not produce duplicate rows."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003 XL', 'PSW 003 XL', 'PSW 003 XL'],
            'Quantity': ['3', '5', '7'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        # Only one row for PSW 003 XL, with the first-seen quantity
        xl_rows = result.loc[result['Variant Name'] == 'XL']
        assert len(xl_rows) == 1
        assert xl_rows.iloc[0]['Amount'] == 3.0

    def test_size_abbrev_l_resolves_to_large_via_vtype(self):
        """Abbreviation 'L' in composite vtype resolves to 'Large' variant
        via size-alias matching — without needing a description hint."""
        products = _make_products(
            NUMBER=['PSS 001', 'PSS 001', 'PSS 001'],
            VARIANT_ID=['10', '11', '12'],
            VARIANT_TYPES=['Small', 'Medium', 'Large'],
            EAN=['5700000000001', '5700000000002', '5700000000003'],
            TITLE_DK=['Guard', 'Guard', 'Guard'],
            BUY_PRICE=['10,00'] * 3,
            PRICE=['20,00'] * 3,
            BUY_PRICE_NUM=[10.0] * 3,
            PRICE_NUM=[20.0] * 3,
            PRODUCT_ID=['1'] * 3,
            PRODUCER=['Brand'] * 3,
            PRODUCER_ID=[1] * 3,
            ONLINE=[True] * 3,
        )
        match_data = {
            'matches': {
                'PSS 001 L': {
                    'sku': 'PSS 001 L', 'score': 100, 'alternatives': [],
                },
            },
            'composite_lookup': {
                'PSS 001': ('PSS 001', ''),
                'PSS 001 L': ('PSS 001', 'L'),
            },
            'title_lookup': {'PSS 001': 'Guard'},
            'qty_map': {'PSS 001 L': 5.0},
            'desc_map': {},  # No description!
        }
        result = build_export_from_matches(products, match_data)
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'Large'
        assert result.iloc[0]['Amount'] == 5.0

    def test_size_full_name_resolves_to_abbreviation_via_vtype(self):
        """Full name 'Large' in composite vtype resolves to 'L' variant."""
        products = self._size_variants()  # variants: S, M, L, XL
        match_data = {
            'matches': {
                'PSW 003 Large': {
                    'sku': 'PSW 003 Large', 'score': 100, 'alternatives': [],
                },
            },
            'composite_lookup': {
                'PSW 003': ('PSW 003', ''),
                'PSW 003 Large': ('PSW 003', 'Large'),
            },
            'title_lookup': {'PSW 003': 'Shirt'},
            'qty_map': {'PSW 003 Large': 3.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'L'
        assert result.iloc[0]['Amount'] == 3.0

    def test_size_abbrev_xs_resolves_to_x_small(self):
        """Abbreviation 'XS' resolves to 'X-Small' variant."""
        products = _make_products(
            NUMBER=['FB 400', 'FB 400', 'FB 400'],
            VARIANT_ID=['10', '11', '12'],
            VARIANT_TYPES=['X-Small', 'Small', 'Medium'],
            EAN=['5700000000001', '5700000000002', '5700000000003'],
            TITLE_DK=['Chest Guard', 'Chest Guard', 'Chest Guard'],
            BUY_PRICE=['10,00'] * 3,
            PRICE=['20,00'] * 3,
            BUY_PRICE_NUM=[10.0] * 3,
            PRICE_NUM=[20.0] * 3,
            PRODUCT_ID=['1'] * 3,
            PRODUCER=['Brand'] * 3,
            PRODUCER_ID=[1] * 3,
            ONLINE=[True] * 3,
        )
        match_data = {
            'matches': {
                'FB 400 XS': {
                    'sku': 'FB 400 XS', 'score': 100, 'alternatives': [],
                },
            },
            'composite_lookup': {
                'FB 400': ('FB 400', ''),
                'FB 400 XS': ('FB 400', 'XS'),
            },
            'title_lookup': {'FB 400': 'Chest Guard'},
            'qty_map': {'FB 400 XS': 2.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'X-Small'

    def test_size_alias_narrowing_via_description(self):
        """Size alias in _narrow_variants: 'Large' in desc matches 'L' variant."""
        products = self._size_variants()  # variants: S, M, L, XL
        invoice = pd.DataFrame({
            'Article': ['PSW 003'],
            'Quantity': ['5'],
            'Description': ['Shirt Large'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'L'

    def test_size_alias_end_to_end_invoice(self):
        """End-to-end: invoice 'PSS 001 L' matches product with 'Large' variant."""
        products = _make_products(
            NUMBER=['PSS 001', 'PSS 001', 'PSS 001'],
            VARIANT_ID=['10', '11', '12'],
            VARIANT_TYPES=['Small', 'Medium', 'Large'],
            EAN=['5700000000001', '5700000000002', '5700000000003'],
            TITLE_DK=['Guard', 'Guard', 'Guard'],
            BUY_PRICE=['10,00'] * 3,
            PRICE=['20,00'] * 3,
            BUY_PRICE_NUM=[10.0] * 3,
            PRICE_NUM=[20.0] * 3,
            PRODUCT_ID=['1'] * 3,
            PRODUCER=['Brand'] * 3,
            PRODUCER_ID=[1] * 3,
            ONLINE=[True] * 3,
        )
        invoice = pd.DataFrame({
            'Article': ['PSS 001 L'],
            'Quantity': ['8'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        # Should resolve to "Large" variant via size-alias matching
        large_rows = result.loc[result['Variant Name'] == 'Large']
        assert len(large_rows) == 1
        assert large_rows.iloc[0]['Amount'] == 8.0


# ---------------------------------------------------------------------------
# Regression test: FB 400 XXS duplication bug
# ---------------------------------------------------------------------------

class TestFB400XXSDuplication:
    """Regression test for the FB 400 XXS spurious-duplication bug.

    When an invoice line like "FB 400 XXS qty=2" matches a product with
    multiple size variants (2X-Small, X-Small, Small, Medium), the export
    pipeline previously emitted **one row per variant**, each carrying the
    full qty=2.  This was wrong — the invoice specified a single size
    ("XXS") so the export should have only one row.

    The root cause was:
    1. Fuzzy matching returned the plain product NUMBER (no composite
       variant type), so ``vtype`` was empty.
    2. ``_narrow_variants`` could not match "XXS" to "2X-Small" because
       "2x-small" was missing from the size-alias table.
    3. With narrowing returning all rows, the loop blindly created one
       export row per variant with the same ``Amount``.

    The fix:
    - Added "2x-small" / "2xs" to the XXS size-alias group.
    - Redesigned the export pipeline to be DataFrame-native with a
      per-group variant-narrowing step that detects unresolved variant
      hints and limits output to one row when appropriate.
    """

    @staticmethod
    def _fb400_products():
        """Products mimicking the FB 400 catalogue with size variants."""
        return _make_products(
            NUMBER=['TO-FB400', 'TO-FB400', 'TO-FB400', 'TO-FB400'],
            VARIANT_ID=['10', '11', '12', '13'],
            VARIANT_TYPES=['2X-Small', 'X-Small', 'Small', 'Medium'],
            EAN=['5700000000001', '5700000000002', '5700000000003', '5700000000004'],
            TITLE_DK=[
                'Brystbeskytter og sportstop WKF - hvid',
                'Brystbeskytter og sportstop WKF - hvid',
                'Brystbeskytter og sportstop WKF - hvid',
                'Brystbeskytter og sportstop WKF - hvid',
            ],
            BUY_PRICE=['100,00'] * 4,
            PRICE=['200,00'] * 4,
            BUY_PRICE_NUM=[100.0] * 4,
            PRICE_NUM=[200.0] * 4,
            PRODUCT_ID=['1'] * 4,
            PRODUCER=['Tokaido'] * 4,
            PRODUCER_ID=[1] * 4,
            ONLINE=[True] * 4,
        )

    def test_fb400_xxs_via_composite_vtype(self):
        """Composite vtype "XXS" narrows to "2X-Small" via size alias."""
        products = self._fb400_products()
        match_data = {
            'matches': {
                'FB 400 XXS': {
                    'sku': 'TO-FB400 XXS',
                    'score': 85,
                    'alternatives': [],
                },
            },
            'composite_lookup': {
                'TO-FB400': ('TO-FB400', ''),
                'TO-FB400 XXS': ('TO-FB400', 'XXS'),
            },
            'title_lookup': {'TO-FB400': 'Brystbeskytter og sportstop WKF'},
            'qty_map': {'FB 400 XXS': 2.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)

        # Must produce exactly ONE row — not one per variant
        assert len(result) == 1, (
            f"Expected 1 row for FB 400 XXS, got {len(result)}:\n{result}"
        )
        assert result.iloc[0]['SKU'] == 'FB 400 XXS'
        assert result.iloc[0]['Variant Name'] == '2X-Small'
        assert result.iloc[0]['Amount'] == 2.0
        assert result.iloc[0]['EAN'] == '5700000000001'

    def test_fb400_xxs_via_narrowing_no_vtype(self):
        """When vtype is empty but inv_sku contains 'XXS', narrow to 2X-Small."""
        products = self._fb400_products()
        match_data = {
            'matches': {
                'FB 400 XXS': {
                    'sku': 'TO-FB400',
                    'score': 80,
                    'alternatives': [],
                },
            },
            'composite_lookup': {
                'TO-FB400': ('TO-FB400', ''),
            },
            'title_lookup': {'TO-FB400': 'Brystbeskytter og sportstop WKF'},
            'qty_map': {'FB 400 XXS': 2.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)

        # Even without composite vtype, "XXS" in the invoice SKU should
        # narrow via _variant_in_context (size alias 2x-small ↔ xxs)
        # to the single 2X-Small variant.
        assert len(result) == 1, (
            f"Expected 1 row for FB 400 XXS, got {len(result)}:\n{result}"
        )
        assert result.iloc[0]['SKU'] == 'FB 400 XXS'
        assert result.iloc[0]['Variant Name'] == '2X-Small'
        assert result.iloc[0]['Amount'] == 2.0

    def test_fb400_base_sku_returns_all_variants(self):
        """Base SKU 'TO-FB400' without size hint returns all 4 variants."""
        products = self._fb400_products()
        match_data = {
            'matches': {
                'TO-FB400': {
                    'sku': 'TO-FB400',
                    'score': 100,
                    'alternatives': [],
                },
            },
            'composite_lookup': {
                'TO-FB400': ('TO-FB400', ''),
            },
            'title_lookup': {'TO-FB400': 'Brystbeskytter og sportstop WKF'},
            'qty_map': {'TO-FB400': 5.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)

        # No variant hint in the SKU text → all 4 variants should appear
        assert len(result) == 4, (
            f"Expected 4 rows for base TO-FB400, got {len(result)}:\n{result}"
        )
        # All rows should have the same quantity
        assert (result['Amount'] == 5.0).all()

    def test_fb400_xxs_quantity_not_replicated(self):
        """Quantity must not be replicated across multiple variants."""
        products = self._fb400_products()
        match_data = {
            'matches': {
                'FB 400 XXS': {
                    'sku': 'TO-FB400',
                    'score': 80,
                    'alternatives': [],
                },
            },
            'composite_lookup': {
                'TO-FB400': ('TO-FB400', ''),
            },
            'title_lookup': {},
            'qty_map': {'FB 400 XXS': 2.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)

        # Total exported amount must equal the invoice qty (2),
        # not qty × number_of_variants (2 × 4 = 8).
        total_amount = result['Amount'].sum()
        assert total_amount == 2.0, (
            f"Total amount {total_amount} != 2.0 — "
            f"quantity was replicated across {len(result)} rows"
        )

    def test_fb400_end_to_end_via_build_ean_export(self):
        """End-to-end test: build_ean_export with FB 400 XXS invoice line."""
        products = self._fb400_products()
        invoice = pd.DataFrame({
            'Article': ['FB 400 XXS'],
            'Quantity': ['2'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        # The fuzzy match should find TO-FB400; the pipeline should
        # narrow to 2X-Small (or at most a single variant).
        fb400_rows = result.loc[result['Product Number'] == 'TO-FB400']
        assert len(fb400_rows) <= 1, (
            f"Expected at most 1 row for FB 400 XXS, got {len(fb400_rows)}:\n"
            f"{fb400_rows}"
        )
        if not fb400_rows.empty:
            assert fb400_rows.iloc[0]['Amount'] == 2.0


# ---------------------------------------------------------------------------
# Manual overrides and match_invoice_to_products
# ---------------------------------------------------------------------------

class TestManualOverrides:
    """Tests for manual override matching via the two-step API."""

    @staticmethod
    def _size_variants():
        return _make_products(
            NUMBER=['PSW 003', 'PSW 003', 'PSW 003', 'PSW 003'],
            VARIANT_ID=['10', '11', '12', '13'],
            VARIANT_TYPES=['S', 'M', 'L', 'XL'],
            EAN=['5700000000001', '5700000000002', '5700000000003', '5700000000004'],
            TITLE_DK=['Shirt', 'Shirt', 'Shirt', 'Shirt'],
            BUY_PRICE=['50,00', '50,00', '50,00', '50,00'],
            PRICE=['100,00', '100,00', '100,00', '100,00'],
            BUY_PRICE_NUM=[50.0, 50.0, 50.0, 50.0],
            PRICE_NUM=[100.0, 100.0, 100.0, 100.0],
            PRODUCT_ID=['1', '1', '1', '1'],
            PRODUCER=['Brand A', 'Brand A', 'Brand A', 'Brand A'],
            PRODUCER_ID=[1, 1, 1, 1],
            ONLINE=[True, True, True, True],
        )

    def test_match_invoice_returns_dict_keys(self):
        """match_invoice_to_products returns the expected keys."""
        products = self._size_variants()
        invoice = pd.DataFrame({
            'Article': ['PSW 003 XL'],
            'Quantity': ['3'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert 'matches' in mdata
        assert 'composite_lookup' in mdata
        assert 'title_lookup' in mdata
        assert 'qty_map' in mdata
        assert 'desc_map' in mdata

    def test_manual_override_adds_unmatched(self):
        """Manual overrides can add matches for previously unmatched SKUs."""
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['NOPE-1'],
            'Quantity': ['5'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article', 'Quantity', threshold=95,
        )
        # Verify it would be unmatched without override
        result_no_override = build_export_from_matches(products, mdata)
        assert result_no_override.empty

        # Manually override to match SKU-001
        result_with = build_export_from_matches(
            products, mdata,
            manual_overrides={'NOPE-1': 'SKU-001'},
        )
        assert len(result_with) == 1
        assert result_with.iloc[0]['Product Number'] == 'SKU-001'
        assert result_with.iloc[0]['Amount'] == 5.0

    def test_manual_override_replaces_auto_match(self):
        """Manual overrides replace existing auto-matched results."""
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['SKU-001'],
            'Quantity': ['3'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        # Auto match should find SKU-001
        auto_result = build_export_from_matches(products, mdata)
        assert auto_result.iloc[0]['Product Number'] == 'SKU-001'

        # Override to SKU-002
        result = build_export_from_matches(
            products, mdata,
            manual_overrides={'SKU-001': 'SKU-002'},
        )
        assert result.iloc[0]['Product Number'] == 'SKU-002'

    def test_manual_override_via_build_ean_export(self):
        """build_ean_export accepts manual_overrides for backward compat."""
        products = _make_products()
        invoice = pd.DataFrame({
            'Article': ['NOPE-1'],
            'Quantity': ['2'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=95,
            manual_overrides={'NOPE-1': 'SKU-003'},
        )
        assert len(result) == 1
        assert result.iloc[0]['Product Number'] == 'SKU-003'

    def test_name_matching_improves_ean_results(self):
        """Passing description column enables name-based reranking."""
        products = _make_products(
            NUMBER=['AB-100', 'AB-101'],
            VARIANT_ID=['', ''],
            VARIANT_TYPES=['', ''],
            EAN=['5700000000010', '5700000000020'],
            TITLE_DK=['Karate Gi White', 'Boxing Glove Red'],
            BUY_PRICE=['50,00', '60,00'],
            PRICE=['100,00', '120,00'],
            BUY_PRICE_NUM=[50.0, 60.0],
            PRICE_NUM=[100.0, 120.0],
            PRODUCT_ID=['1', '2'],
            PRODUCER=['Brand A', 'Brand B'],
            PRODUCER_ID=[1, 2],
            ONLINE=[True, True],
        )
        invoice = pd.DataFrame({
            'Article': ['AB100'],
            'Quantity': ['1'],
            'Description': ['Karate Gi White'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
            invoice_desc_col='Description',
        )
        assert not result.empty
        assert result.iloc[0]['Product Number'] == 'AB-100'


class TestPDFVariantSuffixParsing:
    """Tests for PDF invoice regex parsing with variant suffixes."""

    def _parse_with_regex(self, lines: list[str]) -> pd.DataFrame:
        """Run the invoice regex parser used by parse_supplier_file.

        Reproduces the _item_re + _art_split logic from the last-resort
        fallback of the PDF parsing pipeline.
        """
        import re

        full_text = '\n'.join(lines)

        _item_re = re.compile(
            r'^\s*(\d{2,4})\s+'
            r'(.+?)\s+'
            r'(\d+\s*(?:pcs|prs|Paar|Stk|St|ml|kg|sets?|pieces?)\w*)\s+'
            r'(\d+[,.]\d+)\s+'
            r'(\d+[,.]\d+)\s*$',
            re.IGNORECASE | re.MULTILINE,
        )
        _item_matches = _item_re.findall(full_text)
        rows: list[dict] = []
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
            rows.append({
                'Item': _m[0],
                'Article No': _art,
                'Designation': _desc,
                'Qty': _m[2],
                'Unit Price': _m[3],
                'Item Value': _m[4],
            })
        return pd.DataFrame(rows).astype(str)

    def test_variant_suffix_included_in_article(self):
        """Variant suffixes (XS, S, M, L, XL) should be part of Article No."""
        lines = [
            '053 PSW 003 XS Shin-/instep guard ELASTIC, white, size 12prs 9,90 118,80',
            '072 PSR 021 M Shin-/Instep Guard TOKAIDO KANJI, color: 10prs 22,90 229,00',
            '062 ZPRSB01 S ZEBRA Pro Shin-Instep Guard, color: 6prs 29,00 174,00',
        ]
        df = self._parse_with_regex(lines)
        articles = df['Article No'].tolist()
        assert 'PSW 003 XS' in articles
        assert 'PSR 021 M' in articles
        assert 'ZPRSB01 S' in articles

    def test_non_variant_articles_unchanged(self):
        """Articles without variant suffixes keep their original form."""
        lines = [
            '001 ATBK 190 Karategi, TOKAIDO Bujin Kuro, 14 oz., black, 6pcs 41,90 251,40',
            '090 RH 003 RHINOC Sport All Purpose Cleaner Spray, 500 24pcs 5,00 120,00',
        ]
        df = self._parse_with_regex(lines)
        articles = df['Article No'].tolist()
        assert 'ATBK 190' in articles
        assert 'RH 003' in articles

    def test_xxs_suffix_included(self):
        """Multi-character size suffixes like XXS are included."""
        lines = [
            '083 FB 400 XXS Chest protector TOKAIDO, SET, white, CE, size 2pcs 27,00 54,00',
        ]
        df = self._parse_with_regex(lines)
        assert 'FB 400 XXS' in df['Article No'].tolist()

    def test_sr_suffix_included(self):
        """Product code suffix SR is included in Article No."""
        lines = [
            '001 FMU 042 SR Mouth Guard BIT FIT, color: white/blue, size: 100pcs 2,90 290,00',
        ]
        df = self._parse_with_regex(lines)
        assert 'FMU 042 SR' in df['Article No'].tolist()

    def test_long_uppercase_word_not_captured(self):
        """Long uppercase words (>4 chars) are not captured as suffixes."""
        lines = [
            '090 RH 003 RHINOC Sport All Purpose Cleaner Spray, 500 24pcs 5,00 120,00',
        ]
        df = self._parse_with_regex(lines)
        assert df.iloc[0]['Article No'] == 'RH 003'
        assert 'RHINOC' in df.iloc[0]['Designation']

    def test_comma_in_article_number(self):
        """Commas in article numbers (e.g. 'GTR 4,5') are preserved."""
        lines = [
            '010 GTR 4,5 Gummi-Trainingsring, Gr. 4,5 8pcs 12,50 100,00',
        ]
        df = self._parse_with_regex(lines)
        assert df.iloc[0]['Article No'] == 'GTR 4,5'

    def test_comma_article_with_variant_suffix(self):
        """Comma article numbers can also carry a variant suffix."""
        lines = [
            '011 GTR 3,5 M Gummi-Trainingsring medium, Gr. 3,5 4pcs 14,00 56,00',
        ]
        df = self._parse_with_regex(lines)
        assert df.iloc[0]['Article No'] == 'GTR 3,5 M'


# ---------------------------------------------------------------------------
# Barcode image rendering
# ---------------------------------------------------------------------------

class TestRenderBarcodeImage:
    """Tests for _render_barcode_image()."""

    def test_valid_ean13(self):
        """Valid 13-digit EAN returns a PNG buffer."""
        buf = _render_barcode_image('5701234567890')
        assert buf is not None
        data = buf.read()
        assert len(data) > 0
        # PNG magic bytes
        assert data[:4] == b'\x89PNG'

    def test_valid_ean13_twelve_digits(self):
        """12-digit EAN (check digit computed) returns a PNG buffer."""
        buf = _render_barcode_image('570123456789')
        assert buf is not None
        assert buf.read()[:4] == b'\x89PNG'

    def test_valid_ean8(self):
        """Valid 8-digit EAN returns a PNG buffer."""
        buf = _render_barcode_image('96385074')
        assert buf is not None
        assert buf.read()[:4] == b'\x89PNG'

    def test_empty_string(self):
        """Empty string returns None."""
        assert _render_barcode_image('') is None

    def test_none_value(self):
        """None returns None."""
        assert _render_barcode_image(None) is None

    def test_non_numeric(self):
        """Non-numeric string returns None."""
        assert _render_barcode_image('ABCDEF') is None

    def test_strips_non_digits(self):
        """Non-digit characters are stripped before rendering."""
        buf = _render_barcode_image('570-1234-567890')
        assert buf is not None


# ---------------------------------------------------------------------------
# Barcode PDF generation
# ---------------------------------------------------------------------------

class TestGenerateBarcodePdf:
    """Tests for generate_barcode_pdf()."""

    def test_basic_pdf_output(self):
        """Generates a valid PDF with barcode labels."""
        df = pd.DataFrame({
            'SKU': ['INV-001', 'INV-002'],
            'Product Number': ['SKU-001', 'SKU-002'],
            'Title': ['Widget A', 'Widget B'],
            'Variant Name': ['', 'Red / Large'],
            'Amount': [3, 1],
            'EAN': ['5701234567890', '5709876543210'],
            'Match %': [100, 85],
        })
        pdf_bytes = generate_barcode_pdf(df)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert pdf_bytes[:5] == b'%PDF-'

    def test_empty_dataframe(self):
        """Empty DataFrame produces a valid PDF with placeholder text."""
        df = pd.DataFrame(columns=[
            'SKU', 'Product Number', 'Title', 'Variant Name',
            'Amount', 'EAN', 'Match %',
        ])
        pdf_bytes = generate_barcode_pdf(df)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_missing_ean_handled(self):
        """Rows without a valid EAN produce labels with text fallback."""
        df = pd.DataFrame({
            'SKU': ['INV-001'],
            'Product Number': ['SKU-001'],
            'Title': ['Widget A'],
            'Variant Name': [''],
            'Amount': [1],
            'EAN': [''],
            'Match %': [100],
        })
        pdf_bytes = generate_barcode_pdf(df)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_multiple_pages(self):
        """Many rows produce a multi-page PDF."""
        n = 30  # Enough for multiple pages (fits ~10 per page)
        df = pd.DataFrame({
            'SKU': [f'INV-{i:03d}' for i in range(n)],
            'Product Number': [f'P-{i:03d}' for i in range(n)],
            'Title': [f'Product {i}' for i in range(n)],
            'Variant Name': [''] * n,
            'Amount': [1] * n,
            'EAN': ['5701234567890'] * n,
            'Match %': [100] * n,
        })
        pdf_bytes = generate_barcode_pdf(df)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_barcode_lib_missing_fallback(self):
        """When python-barcode is missing, labels render text instead."""
        df = pd.DataFrame({
            'SKU': ['INV-001'],
            'Product Number': ['SKU-001'],
            'Title': ['Widget A'],
            'Variant Name': [''],
            'Amount': [1],
            'EAN': ['5701234567890'],
            'Match %': [100],
        })
        with patch(
            'domain.invoice_ean._render_barcode_image', return_value=None,
        ):
            pdf_bytes = generate_barcode_pdf(df)
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b'%PDF-'


# ---------------------------------------------------------------------------
# Translation aliases (Danish ↔ English)
# ---------------------------------------------------------------------------

class TestTranslationAliases:
    """Tests for colour/material translation matching in _variant_in_context."""

    def test_danish_roed_matches_red(self):
        assert _variant_in_context('rød', 'shirt red large')

    def test_english_red_matches_danish_roed(self):
        assert _variant_in_context('red', 'shirt rød large')

    def test_danish_blaa_matches_blue(self):
        assert _variant_in_context('blå', 'hat blue')

    def test_english_blue_matches_danish_blaa(self):
        assert _variant_in_context('blue', 'hat blå')

    def test_danish_hvid_matches_white(self):
        assert _variant_in_context('hvid', 'jacket white')

    def test_english_white_matches_danish_hvid(self):
        assert _variant_in_context('white', 'jacket hvid')

    def test_danish_sort_matches_black(self):
        assert _variant_in_context('sort', 'belt black')

    def test_english_black_matches_danish_sort(self):
        assert _variant_in_context('black', 'belt sort')

    def test_danish_groen_matches_green(self):
        assert _variant_in_context('grøn', 'pants green')

    def test_danish_gul_matches_yellow(self):
        assert _variant_in_context('gul', 'towel yellow')

    def test_grey_matches_gray(self):
        """'grey' and 'gray' are both valid English spellings."""
        assert _variant_in_context('grey', 'scarf gray')

    def test_danish_graa_matches_grey(self):
        assert _variant_in_context('grå', 'scarf grey')

    def test_danish_brun_matches_brown(self):
        assert _variant_in_context('brun', 'bag brown')

    def test_danish_laeder_matches_leather(self):
        assert _variant_in_context('læder', 'bag leather')

    def test_no_false_positive_translation(self):
        """Translation should not match unrelated context."""
        assert not _variant_in_context('rød', 'shirt blue size m')

    def test_composite_variant_with_translation(self):
        """Composite 'rød//Large' should match 'red' in context."""
        assert _variant_in_context('rød//Large', 'shirt red')

    def test_composite_variant_translation_plus_size_alias(self):
        """'rød//L' should match 'red' or 'large' in context."""
        assert _variant_in_context('rød//L', 'shirt large')
        assert _variant_in_context('rød//L', 'shirt red')

    def test_translation_case_insensitive(self):
        assert _variant_in_context('RØD', 'shirt red')
        assert _variant_in_context('Red', 'shirt rød')

    def test_translation_narrows_variant_in_export(self):
        """Danish colour in description should narrow English variant."""
        products = _make_products(
            NUMBER=['SH-01', 'SH-01'],
            VARIANT_ID=['10', '11'],
            VARIANT_TYPES=['red', 'blue'],
            EAN=['5700000000010', '5700000000020'],
            TITLE_DK=['Shirt', 'Shirt'],
            BUY_PRICE=['50,00', '50,00'],
            PRICE=['100,00', '100,00'],
            BUY_PRICE_NUM=[50.0, 50.0],
            PRICE_NUM=[100.0, 100.0],
            PRODUCT_ID=['6', '6'],
            PRODUCER=['Brand D', 'Brand D'],
            PRODUCER_ID=[4, 4],
            ONLINE=[True, True],
        )
        invoice = pd.DataFrame({
            'Article': ['SH-01'],
            'Quantity': ['1'],
            'Description': ['Shirt rød'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) == 1
        assert result.iloc[0]['Variant Name'] == 'red'


# ---------------------------------------------------------------------------
# search_products
# ---------------------------------------------------------------------------

class TestSearchProducts:
    """Tests for the search_products function."""

    def test_search_by_sku(self):
        from domain.invoice_ean import search_products
        results = search_products('AFK110', ['AFK110', 'BCD220', 'XYZ999'])
        assert len(results) > 0
        assert results[0][0] == 'AFK110'

    def test_search_by_name(self):
        from domain.invoice_ean import search_products
        results = search_products(
            'Judogi white',
            ['AFK110', 'BCD220'],
            product_names={'AFK110': 'Judogi white', 'BCD220': 'Belt black'},
        )
        assert len(results) > 0
        skus = [r[0] for r in results]
        assert 'AFK110' in skus

    def test_search_empty_query(self):
        from domain.invoice_ean import search_products
        results = search_products('', ['AFK110'])
        assert results == []

    def test_search_empty_catalogue(self):
        from domain.invoice_ean import search_products
        results = search_products('AFK110', [])
        assert results == []

    def test_search_respects_top_n(self):
        from domain.invoice_ean import search_products
        results = search_products(
            'A',
            [f'A{i}' for i in range(20)],
            top_n=3,
        )
        assert len(results) <= 3


# ---------------------------------------------------------------------------
# normalize_sku edge cases
# ---------------------------------------------------------------------------

class TestNormalizeSku:
    """Tests for normalize_sku with tricky strings."""

    def test_comma_and_spaces(self):
        assert normalize_sku(' GTBL 4,5') == 'GTBL45'

    def test_dots_and_spaces(self):
        assert normalize_sku('GTBL 4.5') == 'GTBL45'

    def test_hyphens(self):
        assert normalize_sku('14105-003-XXS') == '14105003XXS'

    def test_prefix_stripping(self):
        assert normalize_sku('TO-AFK-110') == 'AFK110'

    def test_prefix_with_space_not_stripped(self):
        # PR followed by space (not hyphen) should NOT strip the prefix
        assert normalize_sku('PR 1720-U-0') == 'PR1720U0'

    def test_lowercase_converted(self):
        assert normalize_sku('adiWOBG1') == 'ADIWOBG1'

    def test_empty_string(self):
        assert normalize_sku('') == ''

    def test_slashes_removed(self):
        assert normalize_sku('ABC/123') == 'ABC123'

    def test_webshop_and_invoice_match(self):
        """GTBL 4,5 from invoice and GTBL4.5 from webshop normalise identically."""
        assert normalize_sku(' GTBL 4,5') == normalize_sku('GTBL4.5')
        assert normalize_sku(' GTBL 4,5') == normalize_sku('GTBL-4.5')
        assert normalize_sku(' GTBL 4,5') == normalize_sku('GTBL 4.5')


# ---------------------------------------------------------------------------
# Spanish colour abbreviation translations
# ---------------------------------------------------------------------------

class TestSpanishColorCodes:
    """Tests for Spanish colour abbreviation matching via _TRANSLATION_GROUPS."""

    def test_ne_matches_black(self):
        assert _variant_in_context('black', 'item NE color')

    def test_az_matches_blue(self):
        assert _variant_in_context('blue', 'item AZ color')

    def test_ro_matches_red(self):
        assert _variant_in_context('red', 'item RO something')

    def test_bl_matches_white(self):
        assert _variant_in_context('white', 'item BL here')

    def test_ve_matches_green(self):
        assert _variant_in_context('green', 'item VE code')

    def test_negro_matches_sort(self):
        assert _variant_in_context('sort', 'shirt negro')

    def test_azul_matches_blaa(self):
        assert _variant_in_context('blå', 'shirt azul')

    def test_proforma_sku_context_narrows_black(self):
        """Proforma SKU suffix '-NE' should match Black/sort variant."""
        assert _variant_in_context('sort', 'PR 1721-U-NE JR FOREARM MITT')
        assert _variant_in_context('black', 'PR 1721-U-NE JR FOREARM MITT')

    def test_proforma_sku_context_narrows_blue(self):
        assert _variant_in_context('blue', 'PRO 20916-XS-AZ WT NEW MASK')

    def test_proforma_sku_context_narrows_red(self):
        assert _variant_in_context('red', 'PR 2055-S-RO HEAD GEAR')

    def test_proforma_sku_context_narrows_white(self):
        assert _variant_in_context('white', 'PR 2055-M-BL HEAD GEAR')


# ---------------------------------------------------------------------------
# German colour translations
# ---------------------------------------------------------------------------

class TestGermanColorTranslations:
    """Tests for German colour name matching via _TRANSLATION_GROUPS."""

    def test_schwarz_matches_black(self):
        assert _variant_in_context('black', 'Box-Top schwarz/weiß')

    def test_weiss_matches_white(self):
        assert _variant_in_context('white', 'Box-Top schwarz/weiß')
        assert _variant_in_context('white', 'Box-Top schwarz/weiss')

    def test_blau_matches_blue(self):
        assert _variant_in_context('blue', 'Box-Top blau/weiß')

    def test_rot_matches_red(self):
        assert _variant_in_context('red', 'Box-Top rot/weiß')

    def test_gruen_matches_green(self):
        assert _variant_in_context('green', 'item grün here')
        assert _variant_in_context('green', 'item gruen here')

    def test_gelb_matches_yellow(self):
        assert _variant_in_context('yellow', 'item gelb here')

    def test_grau_matches_grey(self):
        assert _variant_in_context('grey', 'item grau here')

    def test_leder_matches_leather(self):
        assert _variant_in_context('leather', 'Boxhandschuhe Leder')


# ---------------------------------------------------------------------------
# extract_sku_from_description  (German "Produkt / Dienst" column)
# ---------------------------------------------------------------------------

class TestExtractSkuFromDescription:
    """Tests for embedded SKU extraction from German descriptions."""

    def test_adibtt02(self):
        assert extract_sku_from_description(
            'adidas Box-Top schwarz/weiß, ADIBTT02'
        ) == 'ADIBTT02'

    def test_adiwobg1(self):
        assert extract_sku_from_description(
            'adidas World Boxing Boxhandschuhe Leder, adiWOBG1'
        ) == 'adiWOBG1'

    def test_adiwobh1(self):
        assert extract_sku_from_description(
            'adidas World Boxing Kopfschutz Leder, adiWOBH1'
        ) == 'adiWOBH1'

    def test_adibts02(self):
        assert extract_sku_from_description(
            'adidas Box-Short blau/weiß, ADIBTS02'
        ) == 'ADIBTS02'

    def test_no_embedded_sku(self):
        assert extract_sku_from_description('Widget with no SKU') is None

    def test_empty_string(self):
        assert extract_sku_from_description('') is None

    def test_none_safe(self):
        assert extract_sku_from_description(None) is None

    def test_word_only_after_comma_no_digit(self):
        """Pure alphabetic word after comma should NOT be extracted."""
        assert extract_sku_from_description('Something, Leder') is None

    def test_fallback_matching_uses_extracted_sku(self):
        """Unmatched internal Prod.-Nr. should fall back to description SKU."""
        products = _make_products(
            NUMBER=['ADIBTT02', 'ADIBTT02'],
            VARIANT_ID=['', '10'],
            VARIANT_TYPES=['', 'M'],
            EAN=['5700000000010', '5700000000020'],
            TITLE_DK=['Box-Top', 'Box-Top'],
            BUY_PRICE=['50,00', '50,00'],
            PRICE=['100,00', '100,00'],
            BUY_PRICE_NUM=[50.0, 50.0],
            PRICE_NUM=[100.0, 100.0],
            PRODUCT_ID=['1', '1'],
            PRODUCER=['adidas', 'adidas'],
            PRODUCER_ID=[1, 1],
            ONLINE=[True, True],
        )
        # Invoice uses internal Prod.-Nr. but description has the real SKU
        invoice = pd.DataFrame({
            'Article': ['702074002'],
            'Quantity': ['5'],
            'Description': ['adidas Box-Top schwarz/weiß, ADIBTT02'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=70,
            invoice_desc_col='Description',
        )
        assert len(result) >= 1
        assert result.iloc[0]['Product Number'] == 'ADIBTT02'


# ---------------------------------------------------------------------------
# suggest_column_mapping  (AI column mapping)
# ---------------------------------------------------------------------------

class TestSuggestColumnMapping:
    """Tests for the AI column-mapping implementation."""

    def test_returns_none_without_api_key(self):
        df = pd.DataFrame({'A': [1], 'B': [2]})
        assert suggest_column_mapping(df) is None

    def test_accepts_model_kwarg(self):
        df = pd.DataFrame({'A': [1]})
        assert suggest_column_mapping(df, model='gpt-4o') is None

    def test_returns_none_for_empty_df(self):
        df = pd.DataFrame()
        assert suggest_column_mapping(df, api_key='test-key') is None

    def test_valid_mapping_via_injectable_llm_call(self):
        df = pd.DataFrame({'Artikelnr': ['A1'], 'Pris': [100]})

        def fake_llm(prompt, key, model):
            return '{"Artikelnr": "sku", "Pris": "price"}'

        result = suggest_column_mapping(df, api_key='k', llm_call=fake_llm)
        assert result == {'sku': 'Artikelnr', 'price': 'Pris'}

    def test_llm_returning_none_gives_none(self):
        df = pd.DataFrame({'X': [1]})

        def fake_llm(prompt, key, model):
            return None

        assert suggest_column_mapping(
            df, api_key='k', llm_call=fake_llm,
        ) is None


# ---------------------------------------------------------------------------
# ZD421 Label Printer format
# ---------------------------------------------------------------------------

def _sample_export_df(n=2):
    """Build a small export DataFrame for barcode PDF tests."""
    return pd.DataFrame({
        'SKU': [f'INV-{i:03d}' for i in range(n)],
        'Product Number': [f'SKU-{i:03d}' for i in range(n)],
        'Title': [f'Product {i}' for i in range(n)],
        'Variant Name': [''] * n,
        'Amount': [1] * n,
        'EAN': ['5701234567890'] * n,
        'Match %': [100] * n,
    })


class TestGenerateBarcodePdfZd421:
    """Tests for the ZD421 label-printer format."""

    def test_basic_pdf_output(self):
        """Generates a valid PDF."""
        pdf_bytes = _generate_barcode_pdf_zd421(_sample_export_df())
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_page_size_100x50mm_landscape(self):
        """Each page should be 100 mm × 50 mm (landscape)."""
        pdf_bytes = _generate_barcode_pdf_zd421(_sample_export_df(1))
        # Parse raw PDF to check MediaBox dimensions
        text = pdf_bytes.decode('latin-1')
        boxes = re.findall(r'/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]', text)
        assert len(boxes) >= 1, "No MediaBox found in PDF"
        # fpdf2 writes dimensions in points (1 mm ≈ 2.8346 pt)
        w_pt = float(boxes[0][2])
        h_pt = float(boxes[0][3])
        w_mm = w_pt / 2.8346
        h_mm = h_pt / 2.8346
        assert abs(w_mm - 100) < 1, f"Width {w_mm:.1f} mm != 100 mm"
        assert abs(h_mm - 50) < 1, f"Height {h_mm:.1f} mm != 50 mm"

    def test_one_barcode_per_page(self):
        """N rows must produce exactly N pages."""
        n = 5
        pdf_bytes = _generate_barcode_pdf_zd421(_sample_export_df(n))
        text = pdf_bytes.decode('latin-1')
        pages = re.findall(r'/Type\s*/Page\b(?!s)', text)
        assert len(pages) == n, f"Expected {n} pages, found {len(pages)}"

    def test_empty_dataframe(self):
        """Empty DataFrame produces a valid PDF with placeholder."""
        df = pd.DataFrame(columns=[
            'SKU', 'Product Number', 'Title', 'Variant Name',
            'Amount', 'EAN', 'Match %',
        ])
        pdf_bytes = _generate_barcode_pdf_zd421(df)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_missing_ean_handled(self):
        """Row without valid EAN renders text fallback."""
        df = _sample_export_df(1)
        df['EAN'] = ['']
        pdf_bytes = _generate_barcode_pdf_zd421(df)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_barcode_lib_missing_fallback(self):
        """Text fallback when python-barcode unavailable."""
        with patch(
            'domain.invoice_ean._render_barcode_image', return_value=None,
        ):
            pdf_bytes = _generate_barcode_pdf_zd421(_sample_export_df(1))
        assert pdf_bytes[:5] == b'%PDF-'


# ---------------------------------------------------------------------------
# Fast Scan format
# ---------------------------------------------------------------------------

class TestGenerateBarcodePdfFastScan:
    """Tests for the compact fast-scan format."""

    def test_basic_pdf_output(self):
        """Generates a valid PDF."""
        pdf_bytes = _generate_barcode_pdf_fast_scan(_sample_export_df())
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_multiple_barcodes_per_page(self):
        """Fewer pages than rows means multiple barcodes per page."""
        n = 12
        pdf_bytes = _generate_barcode_pdf_fast_scan(_sample_export_df(n))
        text = pdf_bytes.decode('latin-1')
        pages = re.findall(r'/MediaBox', text)
        # With 3 cols × 7 rows = 21 per page, 12 items → 1 page
        assert len(pages) < n, (
            f"Expected fewer pages than {n} rows, got {len(pages)}"
        )

    def test_a4_page_size(self):
        """Pages should be standard A4 (210 mm × 297 mm)."""
        pdf_bytes = _generate_barcode_pdf_fast_scan(_sample_export_df(1))
        text = pdf_bytes.decode('latin-1')
        boxes = re.findall(r'/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]', text)
        assert len(boxes) >= 1
        w_pt = float(boxes[0][2])
        h_pt = float(boxes[0][3])
        w_mm = w_pt / 2.8346
        h_mm = h_pt / 2.8346
        assert abs(w_mm - 210) < 1, f"Width {w_mm:.1f} mm != 210 mm"
        assert abs(h_mm - 297) < 1, f"Height {h_mm:.1f} mm != 297 mm"

    def test_labels_within_page_bounds(self):
        """All labels must fit within the A4 page (210 × 297 mm)."""
        # Fast-scan layout: 3 cols × label_w=60 + margins + gaps
        # margin_x=7, gap_x=3  →  max x = 7 + 3*(60+3) - 3 = 193 mm < 210 ✓
        # margin_y=7, gap_y=2, label_h=38  →  7 rows: 7 + 7*(38+2) - 2 = 285 mm < 297 ✓
        n = 21  # exactly fills first page (3 cols × 7 rows)
        pdf_bytes = _generate_barcode_pdf_fast_scan(_sample_export_df(n))
        text = pdf_bytes.decode('latin-1')
        pages = re.findall(r'/MediaBox', text)
        assert len(pages) == 1, f"21 items should fit on 1 page, got {len(pages)}"

    def test_empty_dataframe(self):
        """Empty DataFrame produces a valid PDF with placeholder."""
        df = pd.DataFrame(columns=[
            'SKU', 'Product Number', 'Title', 'Variant Name',
            'Amount', 'EAN', 'Match %',
        ])
        pdf_bytes = _generate_barcode_pdf_fast_scan(df)
        assert pdf_bytes[:5] == b'%PDF-'

    def test_many_items_multi_page(self):
        """Many items produce multiple pages."""
        n = 50  # more than one page
        pdf_bytes = _generate_barcode_pdf_fast_scan(_sample_export_df(n))
        text = pdf_bytes.decode('latin-1')
        pages = re.findall(r'/Type\s*/Page\b(?!s)', text)
        assert len(pages) >= 2, f"50 items should need ≥2 pages, got {len(pages)}"


# ---------------------------------------------------------------------------
# Format dispatcher (generate_barcode_pdf with export_format)
# ---------------------------------------------------------------------------

class TestBarcodeFormatDispatcher:
    """Tests that generate_barcode_pdf dispatches correctly."""

    def test_default_is_standard(self):
        """No explicit format ⇒ standard A4 layout."""
        pdf_bytes = generate_barcode_pdf(_sample_export_df())
        assert pdf_bytes[:5] == b'%PDF-'
        # Standard uses A4
        text = pdf_bytes.decode('latin-1')
        boxes = re.findall(r'/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]', text)
        w_mm = float(boxes[0][2]) / 2.8346
        assert abs(w_mm - 210) < 1

    def test_zd421_format(self):
        """Passing 'zd421_label' uses the label printer layout."""
        pdf_bytes = generate_barcode_pdf(
            _sample_export_df(1), export_format='zd421_label',
        )
        text = pdf_bytes.decode('latin-1')
        boxes = re.findall(r'/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]', text)
        w_mm = float(boxes[0][2]) / 2.8346
        assert abs(w_mm - 100) < 1

    def test_fast_scan_format(self):
        """Passing 'fast_scan' uses the compact grid layout."""
        n = 12
        pdf_bytes = generate_barcode_pdf(
            _sample_export_df(n), export_format='fast_scan',
        )
        text = pdf_bytes.decode('latin-1')
        pages = re.findall(r'/MediaBox', text)
        assert len(pages) < n  # multiple items per page

    def test_backwards_compatible(self):
        """Calling without export_format still works (no TypeError)."""
        pdf_bytes = generate_barcode_pdf(_sample_export_df())
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0


# ---------------------------------------------------------------------------
# _extract_numeric_hints
# ---------------------------------------------------------------------------

class TestExtractNumericHints:
    """Tests for numeric hint extraction from invoice text."""

    def test_european_decimal_comma(self):
        assert 3.0 in _extract_numeric_hints('GTBL 3,0')

    def test_standard_decimal_point(self):
        assert 3.5 in _extract_numeric_hints('GTBL 3.5')

    def test_integer(self):
        assert 265.0 in _extract_numeric_hints('Belt 265 cm')

    def test_multiple_numbers(self):
        nums = _extract_numeric_hints('Belt 265 cm / 3.5')
        assert 265.0 in nums
        assert 3.5 in nums

    def test_no_numbers(self):
        assert _extract_numeric_hints('GTBL Blue') == set()

    def test_number_embedded_in_sku(self):
        """Numbers that are part of word boundaries should not be extracted."""
        nums = _extract_numeric_hints('FB400')
        # '400' is preceded by a letter, so not extracted
        assert 400.0 not in nums

    def test_number_after_space(self):
        nums = _extract_numeric_hints('FB 400')
        assert 400.0 in nums


# ---------------------------------------------------------------------------
# _has_variant_hint — numeric hint detection
# ---------------------------------------------------------------------------

class TestHasVariantHintNumeric:
    """Tests for _has_variant_hint with numeric hints."""

    def test_gtbl_30_detected(self):
        """GTBL 3,0 has numeric hint 3.0 not in product number."""
        assert _has_variant_hint('GTBL 3,0', 'TO-GTBL') is True

    def test_same_number_no_hint(self):
        """Same numbers in text and product number → no hint."""
        assert _has_variant_hint('PSW 003', 'PSW 003') is False

    def test_same_sku_no_hint(self):
        """Identical text and number → no hint."""
        assert _has_variant_hint('SKU-001', 'SKU-001') is False

    def test_extra_number_detected(self):
        """Extra number in text (190) triggers hint."""
        assert _has_variant_hint('TBL-01 190 cm', 'TBL-01') is True

    def test_size_alias_still_works(self):
        """Size-alias detection still works alongside numeric."""
        assert _has_variant_hint('PSW 003 XL', 'PSW 003') is True


# ---------------------------------------------------------------------------
# GTBL-style belt duplication regression test
# ---------------------------------------------------------------------------

class TestGTBLBeltDuplication:
    """Regression tests for GTBL-style numeric variant mis-narrowing.

    Invoice line ``GTBL 3,0`` qty=10 should NOT produce 4 rows for
    all belt variants (265/275/285/295 cm) each with qty=10.
    """

    @staticmethod
    def _belt_products():
        """Product catalogue with belt variants."""
        return _make_products(
            NUMBER=['TO-GTBL', 'TO-GTBL', 'TO-GTBL', 'TO-GTBL'],
            VARIANT_ID=['10', '11', '12', '13'],
            VARIANT_TYPES=[
                '265 cm / 3.5', '275 cm / 4.0',
                '285 cm / 4.5', '295 cm / 5.0',
            ],
            EAN=['5700000000001', '5700000000002',
                 '5700000000003', '5700000000004'],
            TITLE_DK=['Bælte WKF - blå'] * 4,
            BUY_PRICE=['50,00'] * 4,
            PRICE=['100,00'] * 4,
            BUY_PRICE_NUM=[50.0] * 4,
            PRICE_NUM=[100.0] * 4,
            PRODUCT_ID=['1'] * 4,
            PRODUCER=['Brand'] * 4,
            PRODUCER_ID=[1] * 4,
            ONLINE=[True] * 4,
        )

    def test_gtbl_30_not_four_rows(self):
        """GTBL 3,0 qty=10 must NOT produce 4 rows with qty=10 each."""
        products = self._belt_products()
        invoice = pd.DataFrame({
            'Article': ['GTBL 3,0'],
            'Quantity': ['10'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        # Must produce at most 1 row (either the matching variant or
        # an ambiguous single row), NOT 4 duplicated rows.
        assert len(result) <= 1
        if not result.empty:
            assert result.iloc[0]['Amount'] == 10.0

    def test_gtbl_35_narrows_to_specific_variant(self):
        """GTBL 3,5 should narrow to the 265 cm / 3.5 variant."""
        products = self._belt_products()
        invoice = pd.DataFrame({
            'Article': ['GTBL 3,5'],
            'Quantity': ['5'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert '3.5' in result.iloc[0]['Variant Name']
        assert result.iloc[0]['Amount'] == 5.0

    def test_gtbl_45_narrows_to_specific_variant(self):
        """GTBL 4,5 should narrow to the 285 cm / 4.5 variant."""
        products = self._belt_products()
        invoice = pd.DataFrame({
            'Article': ['GTBL 4,5'],
            'Quantity': ['3'],
        })
        result = build_ean_export(
            products, invoice, 'Article', 'Quantity', threshold=50,
        )
        assert len(result) == 1
        assert '4.5' in result.iloc[0]['Variant Name']
        assert result.iloc[0]['Amount'] == 3.0

    def test_gtbl_base_without_hint_returns_one_row(self):
        """Base GTBL without a numeric hint still limits to 1 row due
        to numeric hint from the product number normalization."""
        products = self._belt_products()
        # Use match_data directly with no numeric hint
        match_data = {
            'matches': {
                'GTBL': {
                    'sku': 'TO-GTBL', 'score': 90, 'alternatives': [],
                },
            },
            'composite_lookup': {
                'TO-GTBL': ('TO-GTBL', ''),
            },
            'title_lookup': {'TO-GTBL': 'Bælte WKF - blå'},
            'qty_map': {'GTBL': 10.0},
            'desc_map': {},
        }
        result = build_export_from_matches(products, match_data)
        # All 4 variants returned — base SKU with no hint
        assert len(result) == 4


# ---------------------------------------------------------------------------
# EAN cross-check in matching
# ---------------------------------------------------------------------------

class TestEANCrossCheck:
    """Tests for EAN-based cross-checking in match_invoice_to_products."""

    @staticmethod
    def _products_with_ean():
        return _make_products(
            NUMBER=['PROD-A', 'PROD-B'],
            VARIANT_ID=['', ''],
            VARIANT_TYPES=['', ''],
            EAN=['5701234567890', '5709876543210'],
            TITLE_DK=['Product Alpha', 'Product Beta'],
            BUY_PRICE=['100,00', '200,00'],
            PRICE=['200,00', '400,00'],
            BUY_PRICE_NUM=[100.0, 200.0],
            PRICE_NUM=[200.0, 400.0],
            PRODUCT_ID=['1', '2'],
            PRODUCER=['Brand A', 'Brand B'],
            PRODUCER_ID=[1, 2],
            ONLINE=[True, True],
        )

    def test_bare_ean_as_sku_matches_product(self):
        """Invoice SKU that is a bare EAN gets matched via EAN lookup."""
        products = self._products_with_ean()
        invoice = pd.DataFrame({
            'Article': ['5701234567890'],
            'Quantity': ['3'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        result = mdata['matches'].get('5701234567890')
        assert result is not None
        assert result['sku'] is not None
        assert result['score'] == 100

    def test_ean_in_description_matches(self):
        """EAN in description field is used for cross-check."""
        products = self._products_with_ean()
        invoice = pd.DataFrame({
            'Article': ['UNKNOWN-SKU'],
            'Quantity': ['2'],
            'Description': ['Product Alpha EAN: 5701234567890'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article', 'Quantity',
            threshold=70, invoice_desc_col='Description',
        )
        result = mdata['matches'].get('UNKNOWN-SKU')
        assert result is not None
        assert result['sku'] is not None
        assert result['score'] == 100

    def test_sku_and_ean_agree(self):
        """When both SKU and EAN match the same product, result is consistent."""
        products = self._products_with_ean()
        invoice = pd.DataFrame({
            'Article': ['PROD-A'],
            'Quantity': ['5'],
        })
        mdata = match_invoice_to_products(
            products, invoice, 'Article', 'Quantity', threshold=70,
        )
        result = mdata['matches'].get('PROD-A')
        assert result is not None
        assert result['sku'] is not None
        # Should resolve to PROD-A
        number, _ = mdata['composite_lookup'].get(result['sku'], (result['sku'], ''))
        assert number == 'PROD-A'


# ---------------------------------------------------------------------------
# build_matches_df
# ---------------------------------------------------------------------------

class TestBuildMatchesDf:
    """Tests for the matches DataFrame builder."""

    @staticmethod
    def _simple_match_data():
        """Simple match data with one match and one unmatched."""
        return {
            'matches': {
                'INV-001': {
                    'sku': 'PROD-A', 'score': 95, 'alternatives': [],
                },
                'INV-002': {
                    'sku': None, 'score': 0,
                    'alternatives': [('PROD-B', 40)],
                },
            },
            'composite_lookup': {
                'PROD-A': ('PROD-A', ''),
                'PROD-B': ('PROD-B', ''),
            },
            'title_lookup': {
                'PROD-A': 'Product Alpha',
                'PROD-B': 'Product Beta',
            },
            'qty_map': {'INV-001': 5.0, 'INV-002': 3.0},
            'desc_map': {'INV-001': 'Alpha desc', 'INV-002': 'Beta desc'},
        }

    @staticmethod
    def _simple_products():
        return _make_products(
            NUMBER=['PROD-A', 'PROD-B'],
            VARIANT_ID=['', ''],
            VARIANT_TYPES=['', ''],
            EAN=['5701234567890', '5709876543210'],
            TITLE_DK=['Product Alpha', 'Product Beta'],
            BUY_PRICE=['100,00', '200,00'],
            PRICE=['200,00', '400,00'],
            BUY_PRICE_NUM=[100.0, 200.0],
            PRICE_NUM=[200.0, 400.0],
            PRODUCT_ID=['1', '2'],
            PRODUCER=['Brand A', 'Brand B'],
            PRODUCER_ID=[1, 2],
            ONLINE=[True, True],
        )

    def test_has_correct_columns(self):
        """matches_df has all expected columns."""
        products = self._simple_products()
        mdata = self._simple_match_data()
        mdf = build_matches_df(products, mdata)
        expected_cols = [
            'src_row_id', 'src_type', 'src_sku',
            'src_sku_craft_normalized', 'src_description',
            'src_qty', 'matched_number', 'matched_variant',
            'matched_title', 'matched_ean', 'match_score',
            'match_source', 'match_method_detail', 'status',
        ]
        assert list(mdf.columns) == expected_cols

    def test_auto_matched_rows(self):
        """Automatically matched rows have source 'auto-sku'."""
        products = self._simple_products()
        mdata = self._simple_match_data()
        mdf = build_matches_df(products, mdata)
        auto = mdf.loc[mdf['match_source'] == 'auto-sku']
        assert len(auto) >= 1
        assert auto.iloc[0]['src_sku'] == 'INV-001'
        assert auto.iloc[0]['matched_number'] == 'PROD-A'
        assert auto.iloc[0]['match_score'] == 95

    def test_unmatched_rows(self):
        """Unmatched rows have source 'unmatched' and status 'needs-manual'."""
        products = self._simple_products()
        mdata = self._simple_match_data()
        mdf = build_matches_df(products, mdata)
        unmatched = mdf.loc[mdf['match_source'] == 'unmatched']
        assert len(unmatched) == 1
        assert unmatched.iloc[0]['src_sku'] == 'INV-002'
        assert unmatched.iloc[0]['status'] == 'needs-manual'

    def test_manual_override(self):
        """Manual overrides produce rows with source 'manual'."""
        products = self._simple_products()
        mdata = self._simple_match_data()
        mdf = build_matches_df(
            products, mdata,
            manual_overrides={'INV-002': 'PROD-B'},
        )
        manual = mdf.loc[mdf['match_source'] == 'manual']
        assert len(manual) == 1
        assert manual.iloc[0]['src_sku'] == 'INV-002'
        assert manual.iloc[0]['matched_number'] == 'PROD-B'
        assert manual.iloc[0]['match_score'] == 100

    def test_empty_matches(self):
        """Empty match data returns empty DataFrame with correct columns."""
        products = self._simple_products()
        mdata = {
            'matches': {},
            'composite_lookup': {},
            'title_lookup': {},
            'qty_map': {},
            'desc_map': {},
        }
        mdf = build_matches_df(products, mdata)
        assert mdf.empty
        assert 'src_sku' in mdf.columns


# ---------------------------------------------------------------------------
# export_from_matches_df
# ---------------------------------------------------------------------------

class TestExportFromMatchesDf:
    """Tests for converting matches_df to export format."""

    def test_basic_export(self):
        """Matched rows are exported with correct columns."""
        mdf = pd.DataFrame({
            'src_row_id': [0, 1],
            'src_type': ['invoice', 'invoice'],
            'src_sku': ['INV-001', 'INV-002'],
            'src_description': ['Desc A', 'Desc B'],
            'src_qty': [5.0, 3.0],
            'matched_number': ['PROD-A', ''],
            'matched_variant': ['Red', ''],
            'matched_title': ['Product A', ''],
            'matched_ean': ['5701234567890', ''],
            'match_score': [95, 0],
            'match_source': ['auto-sku', 'unmatched'],
            'status': ['ok', 'needs-manual'],
        })
        export = export_from_matches_df(mdf)
        assert len(export) == 1  # only the matched row
        assert export.iloc[0]['SKU'] == 'INV-001'
        assert export.iloc[0]['Product Number'] == 'PROD-A'
        assert export.iloc[0]['Variant Name'] == 'Red'
        assert export.iloc[0]['Amount'] == 5.0
        assert export.iloc[0]['EAN'] == '5701234567890'
        assert export.iloc[0]['Match %'] == 95

    def test_manual_rows_included(self):
        """Manually overridden rows are included in export."""
        mdf = pd.DataFrame({
            'src_row_id': [0, 1],
            'src_type': ['invoice', 'invoice'],
            'src_sku': ['INV-001', 'INV-002'],
            'src_description': ['', ''],
            'src_qty': [5.0, 3.0],
            'matched_number': ['PROD-A', 'PROD-B'],
            'matched_variant': ['', ''],
            'matched_title': ['A', 'B'],
            'matched_ean': ['123', '456'],
            'match_score': [95, 100],
            'match_source': ['auto-sku', 'manual'],
            'status': ['ok', 'ok'],
        })
        export = export_from_matches_df(mdf)
        assert len(export) == 2

    def test_unmatched_rows_excluded(self):
        """Unmatched rows are not in the export."""
        mdf = pd.DataFrame({
            'src_row_id': [0],
            'src_type': ['invoice'],
            'src_sku': ['INV-001'],
            'src_description': [''],
            'src_qty': [5.0],
            'matched_number': [''],
            'matched_variant': [''],
            'matched_title': [''],
            'matched_ean': [''],
            'match_score': [0],
            'match_source': ['unmatched'],
            'status': ['needs-manual'],
        })
        export = export_from_matches_df(mdf)
        assert export.empty

    def test_empty_matches_df(self):
        """Empty matches_df produces empty export."""
        mdf = pd.DataFrame(columns=[
            'src_row_id', 'src_type', 'src_sku',
            'src_sku_craft_normalized', 'src_description',
            'src_qty', 'matched_number', 'matched_variant',
            'matched_title', 'matched_ean', 'match_score',
            'match_source', 'status',
        ])
        export = export_from_matches_df(mdf)
        assert export.empty
        assert list(export.columns) == [
            'SKU', 'Product Number', 'Title', 'Variant Name',
            'Amount', 'EAN', 'Match %',
        ]

    def test_export_column_order(self):
        """Export has the canonical column order."""
        mdf = pd.DataFrame({
            'src_row_id': [0],
            'src_type': ['invoice'],
            'src_sku': ['INV-001'],
            'src_description': [''],
            'src_qty': [5.0],
            'matched_number': ['PROD-A'],
            'matched_variant': [''],
            'matched_title': ['Title'],
            'matched_ean': ['123'],
            'match_score': [90],
            'match_source': ['auto-sku'],
            'status': ['ok'],
        })
        export = export_from_matches_df(mdf)
        assert list(export.columns) == [
            'SKU', 'Product Number', 'Title', 'Variant Name',
            'Amount', 'EAN', 'Match %',
        ]

    def test_legacy_inv_columns_accepted(self):
        """export_from_matches_df accepts legacy inv_* column names."""
        mdf = pd.DataFrame({
            'inv_row_id': [0],
            'inv_sku': ['INV-001'],
            'inv_description': ['Desc'],
            'inv_qty': [2.0],
            'matched_number': ['PROD-A'],
            'matched_variant': ['Red'],
            'matched_title': ['Title'],
            'matched_ean': ['123'],
            'match_score': [90],
            'match_source': ['auto-sku'],
            'status': ['ok'],
        })
        export = export_from_matches_df(mdf)
        assert len(export) == 1
        assert export.iloc[0]['SKU'] == 'INV-001'
        assert export.iloc[0]['Amount'] == 2.0


# ---------------------------------------------------------------------------
# Supplier-style input through unified pipeline
# ---------------------------------------------------------------------------

class TestSupplierViaUnifiedPipeline:
    """Verify that supplier-style input through build_matches_df produces
    the same quality and structure as invoice/EAN input."""

    @staticmethod
    def _catalogue():
        """Product catalogue with variants and EANs."""
        return _make_products(
            NUMBER=[
                'PROD-A', 'PROD-A', 'PROD-B', 'PROD-C',
                'PROD-D', 'PROD-D',
            ],
            VARIANT_ID=['v1', 'v2', 'v3', '', 'v4', 'v5'],
            VARIANT_TYPES=[
                'Small', 'Large', '', '', 'Red', 'Blue',
            ],
            EAN=[
                '5701234567890', '5701234567891', '5709876543210',
                '', '1234567890128', '1234567890135',
            ],
            TITLE_DK=[
                'Widget A', 'Widget A', 'Widget B', 'Widget C',
                'Colourful D', 'Colourful D',
            ],
            BUY_PRICE=['10', '10', '20', '30', '15', '15'],
            PRICE=['20', '20', '40', '60', '30', '30'],
            BUY_PRICE_NUM=[10.0, 10.0, 20.0, 30.0, 15.0, 15.0],
            PRICE_NUM=[20.0, 20.0, 40.0, 60.0, 30.0, 30.0],
            PRODUCT_ID=['1', '1', '2', '3', '4', '4'],
            PRODUCER=['Brand', 'Brand', 'Brand', 'Brand',
                      'Brand', 'Brand'],
            PRODUCER_ID=[1, 1, 2, 3, 4, 4],
            ONLINE=[True] * 6,
        )

    def test_supplier_input_uses_same_pipeline(self):
        """Supplier-style input through match_invoice_to_products and
        build_matches_df produces a valid matches_df with 'supplier'
        src_type."""
        catalogue = self._catalogue()
        supplier_df = pd.DataFrame({
            'Artikelnr': ['PROD-A', 'PROD-B', 'PROD-C', 'UNKNOWN-X'],
            'Beschreibung': ['Widget A Small', 'Widget B', 'C item', '???'],
            'Preis': ['10,50', '22,00', '35,00', '99,00'],
        })

        mdata = match_invoice_to_products(
            products_df=catalogue,
            invoice_df=supplier_df,
            invoice_sku_col='Artikelnr',
            invoice_qty_col=None,
            threshold=70,
            invoice_desc_col='Beschreibung',
        )

        mdf = build_matches_df(
            catalogue, mdata, src_type='supplier',
        )

        # Schema check
        assert 'src_type' in mdf.columns
        assert 'src_sku' in mdf.columns
        assert (mdf['src_type'] == 'supplier').all()

        # Known matches should be found
        matched = mdf.loc[mdf['match_source'] != 'unmatched']
        matched_skus = set(matched['src_sku'])
        assert 'PROD-A' in matched_skus
        assert 'PROD-B' in matched_skus
        assert 'PROD-C' in matched_skus

    def test_supplier_ean_crosscheck(self):
        """Supplier SKUs that are bare EANs get matched via EAN cross-check."""
        catalogue = self._catalogue()
        supplier_df = pd.DataFrame({
            'SKU': ['5701234567890'],
            'Name': ['EAN-based item'],
        })

        mdata = match_invoice_to_products(
            products_df=catalogue,
            invoice_df=supplier_df,
            invoice_sku_col='SKU',
            invoice_qty_col=None,
            threshold=70,
            invoice_desc_col='Name',
        )

        mdf = build_matches_df(catalogue, mdata, src_type='supplier')

        matched = mdf.loc[mdf['match_source'] != 'unmatched']
        assert len(matched) >= 1
        assert matched.iloc[0]['matched_number'] == 'PROD-A'
        assert matched.iloc[0]['match_score'] == 100

    def test_supplier_and_invoice_produce_same_schema(self):
        """Both src_type='invoice' and 'supplier' produce identical schemas."""
        catalogue = self._catalogue()
        simple_df = pd.DataFrame({
            'SKU': ['PROD-A'],
            'Qty': ['1'],
        })
        mdata = match_invoice_to_products(
            catalogue, simple_df, 'SKU', 'Qty', threshold=70,
        )
        inv_mdf = build_matches_df(catalogue, mdata, src_type='invoice')
        sup_mdf = build_matches_df(catalogue, mdata, src_type='supplier')
        assert list(inv_mdf.columns) == list(sup_mdf.columns)
        assert (inv_mdf['src_type'] == 'invoice').all()
        assert (sup_mdf['src_type'] == 'supplier').all()

    def test_supplier_numeric_variant_narrowing(self):
        """Numeric variant narrowing works for supplier input too."""
        catalogue = _make_products(
            NUMBER=['BELT-01', 'BELT-01', 'BELT-01'],
            VARIANT_ID=['v1', 'v2', 'v3'],
            VARIANT_TYPES=['265 cm / 3.0', '275 cm / 3.5', '285 cm / 4.0'],
            EAN=['', '', ''],
            TITLE_DK=['Belt', 'Belt', 'Belt'],
            BUY_PRICE=['50', '50', '50'],
            PRICE=['100', '100', '100'],
            BUY_PRICE_NUM=[50.0, 50.0, 50.0],
            PRICE_NUM=[100.0, 100.0, 100.0],
            PRODUCT_ID=['1', '1', '1'],
            PRODUCER=['Brand', 'Brand', 'Brand'],
            PRODUCER_ID=[1, 1, 1],
            ONLINE=[True, True, True],
        )
        supplier_df = pd.DataFrame({
            'Article': ['BELT-01 3,5'],
            'Name': ['Belt 275'],
        })
        mdata = match_invoice_to_products(
            catalogue, supplier_df, 'Article', None,
            threshold=60, invoice_desc_col='Name',
        )
        mdf = build_matches_df(catalogue, mdata, src_type='supplier')
        matched = mdf.loc[mdf['match_source'] != 'unmatched']
        # Should narrow to the 3.5 variant
        assert len(matched) >= 1
        assert '3.5' in matched.iloc[0]['matched_variant']


class TestManualOverridesInMatchesDf:
    """Verify that manual overrides stored in matches_df are respected
    by export_from_matches_df without re-running matching logic."""

    def test_manual_override_appears_in_export(self):
        """A manually overridden row is included in the export output."""
        mdf = pd.DataFrame({
            'src_row_id': [0, 1],
            'src_type': ['supplier', 'supplier'],
            'src_sku': ['SUP-001', 'SUP-002'],
            'src_description': ['Item A', 'Item B'],
            'src_qty': [10.0, 5.0],
            'matched_number': ['PROD-A', 'PROD-B'],
            'matched_variant': ['', 'Red'],
            'matched_title': ['Product A', 'Product B'],
            'matched_ean': ['111', '222'],
            'match_score': [80, 100],
            'match_source': ['auto-sku', 'manual'],
            'status': ['ok', 'ok'],
        })
        export = export_from_matches_df(mdf)
        assert len(export) == 2
        manual_row = export.loc[export['SKU'] == 'SUP-002']
        assert len(manual_row) == 1
        assert manual_row.iloc[0]['Product Number'] == 'PROD-B'
        assert manual_row.iloc[0]['Match %'] == 100

    def test_export_does_not_rerun_matching(self):
        """export_from_matches_df purely reads the DataFrame — it does
        not call any matching functions."""
        # Construct a DF with nonsensical but consistent data;
        # if export tried to match, it would fail.
        mdf = pd.DataFrame({
            'src_row_id': [0],
            'src_type': ['supplier'],
            'src_sku': ['FAKE-SKU'],
            'src_description': [''],
            'src_qty': [1.0],
            'matched_number': ['CUSTOM-PROD'],
            'matched_variant': ['Custom Variant'],
            'matched_title': ['Custom Title'],
            'matched_ean': ['9999999999999'],
            'match_score': [100],
            'match_source': ['manual'],
            'status': ['ok'],
        })
        export = export_from_matches_df(mdf)
        assert len(export) == 1
        assert export.iloc[0]['Product Number'] == 'CUSTOM-PROD'
        assert export.iloc[0]['Variant Name'] == 'Custom Variant'

    def test_inline_edit_overwrites_auto_match(self):
        """Simulates the UI flow: user edits matches_df directly to
        change an auto-matched row to a manual selection."""
        mdf = pd.DataFrame({
            'src_row_id': [0],
            'src_type': ['supplier'],
            'src_sku': ['SUP-001'],
            'src_description': ['Widget'],
            'src_qty': [3.0],
            'matched_number': ['WRONG-PROD'],
            'matched_variant': [''],
            'matched_title': ['Wrong Title'],
            'matched_ean': ['000'],
            'match_score': [60],
            'match_source': ['auto-sku'],
            'status': ['ok'],
        })
        # Simulate user overriding the match in the DataFrame
        mdf.loc[0, 'matched_number'] = 'CORRECT-PROD'
        mdf.loc[0, 'matched_variant'] = 'Blue'
        mdf.loc[0, 'matched_title'] = 'Correct Title'
        mdf.loc[0, 'matched_ean'] = '999'
        mdf.loc[0, 'match_score'] = 100
        mdf.loc[0, 'match_source'] = 'manual'

        export = export_from_matches_df(mdf)
        assert len(export) == 1
        assert export.iloc[0]['Product Number'] == 'CORRECT-PROD'
        assert export.iloc[0]['Variant Name'] == 'Blue'
        assert export.iloc[0]['Match %'] == 100


# ---------------------------------------------------------------------------
# Craft-style SKU normalization (size index → size label)
# ---------------------------------------------------------------------------

class TestNormalizeCraftSku:
    """Unit tests for _normalize_craft_sku."""

    def test_basic_xl(self):
        assert _normalize_craft_sku('1910163-999000-7') == '1910163-999000-XL'

    def test_basic_2xl(self):
        assert _normalize_craft_sku('1910163-999000-8') == '1910163-999000-2XL'

    def test_basic_m(self):
        assert _normalize_craft_sku('1910163-999000-5') == '1910163-999000-M'

    def test_basic_l(self):
        assert _normalize_craft_sku('1910163-999000-6') == '1910163-999000-L'

    def test_basic_s(self):
        assert _normalize_craft_sku('1910163-999000-4') == '1910163-999000-S'

    def test_basic_xs(self):
        assert _normalize_craft_sku('1910163-999000-3') == '1910163-999000-XS'

    def test_basic_xxs(self):
        assert _normalize_craft_sku('1910163-999000-2') == '1910163-999000-XXS'

    def test_basic_3xl(self):
        assert _normalize_craft_sku('1910163-999000-9') == '1910163-999000-3XL'

    def test_basic_4xl(self):
        assert _normalize_craft_sku('1910163-999000-10') == '1910163-999000-4XL'

    def test_non_matching_string_returns_none(self):
        assert _normalize_craft_sku('AFK-110') is None

    def test_non_craft_sku_returns_none(self):
        assert _normalize_craft_sku('PROD-A') is None

    def test_empty_string_returns_none(self):
        assert _normalize_craft_sku('') is None

    def test_unknown_index_returns_none(self):
        # Index "1" is not in the mapping
        assert _normalize_craft_sku('1910163-999000-1') is None

    def test_too_short_base_returns_none(self):
        # Base must be at least 5 digits
        assert _normalize_craft_sku('1234-999-7') is None

    def test_leading_trailing_whitespace(self):
        assert _normalize_craft_sku('  1910163-999000-7  ') == '1910163-999000-XL'

    def test_all_mapping_entries_round_trip(self):
        """Every entry in _CRAFT_SIZE_INDEX_TO_LABEL produces a result."""
        for idx, label in _CRAFT_SIZE_INDEX_TO_LABEL.items():
            result = _normalize_craft_sku(f'12345-678-{idx}')
            assert result == f'12345-678-{label}', f'idx={idx}'


class TestCraftSkuMatching:
    """Integration tests: Craft SKU matching without EAN."""

    @staticmethod
    def _craft_catalogue():
        """Product catalogue with Craft-style SKUs as NUMBER."""
        return _make_products(
            NUMBER=['1910163-999000-XL', '1910163-999000-L',
                    '1910163-999000-M'],
            TITLE_DK=['Craft Jacket XL', 'Craft Jacket L',
                       'Craft Jacket M'],
            VARIANT_ID=['', '', ''],
            VARIANT_TYPES=['', '', ''],
            EAN=['', '', ''],
            BUY_PRICE=['100', '100', '100'],
            PRICE=['200', '200', '200'],
            BUY_PRICE_NUM=[100.0, 100.0, 100.0],
            PRICE_NUM=[200.0, 200.0, 200.0],
            PRODUCT_ID=['10', '11', '12'],
            PRODUCER=['Craft', 'Craft', 'Craft'],
            PRODUCER_ID=[5, 5, 5],
            ONLINE=[True, True, True],
        )

    def test_invoice_size_index_matches_catalogue_label(self):
        """Invoice SKU '1910163-999000-7' matches catalogue '1910163-999000-XL'."""
        catalogue = self._craft_catalogue()
        invoice_df = pd.DataFrame({
            'SKU': ['1910163-999000-7'],
            'Qty': ['2'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty', threshold=70,
        )
        m = mdata['matches']['1910163-999000-7']
        assert m['sku'] is not None
        assert m['score'] == 100
        # The matched SKU should be the XL variant
        assert m['sku'] == '1910163-999000-XL'

    def test_multiple_craft_skus_match_correctly(self):
        """Multiple Craft invoice SKUs each match the correct catalogue entry."""
        catalogue = self._craft_catalogue()
        invoice_df = pd.DataFrame({
            'SKU': ['1910163-999000-7', '1910163-999000-6',
                    '1910163-999000-5'],
            'Qty': ['1', '1', '1'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty', threshold=70,
        )
        assert mdata['matches']['1910163-999000-7']['score'] == 100
        assert mdata['matches']['1910163-999000-6']['score'] == 100
        assert mdata['matches']['1910163-999000-5']['score'] == 100
        # Each should match the corresponding size
        assert mdata['matches']['1910163-999000-7']['sku'] == '1910163-999000-XL'
        assert mdata['matches']['1910163-999000-6']['sku'] == '1910163-999000-L'
        assert mdata['matches']['1910163-999000-5']['sku'] == '1910163-999000-M'

    def test_build_matches_df_has_craft_normalized_column(self):
        """build_matches_df populates src_sku_craft_normalized for Craft SKUs."""
        catalogue = self._craft_catalogue()
        invoice_df = pd.DataFrame({
            'SKU': ['1910163-999000-7'],
            'Qty': ['1'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty', threshold=70,
        )
        mdf = build_matches_df(catalogue, mdata)
        assert 'src_sku_craft_normalized' in mdf.columns
        row = mdf.iloc[0]
        assert row['src_sku_craft_normalized'] == '1910163-999000-XL'

    def test_non_craft_sku_has_empty_craft_normalized(self):
        """Non-Craft SKUs have empty src_sku_craft_normalized."""
        catalogue = _make_products()
        invoice_df = pd.DataFrame({
            'SKU': ['SKU-001'],
            'Qty': ['1'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty', threshold=70,
        )
        mdf = build_matches_df(catalogue, mdata)
        assert 'src_sku_craft_normalized' in mdf.columns
        assert mdf.iloc[0]['src_sku_craft_normalized'] == ''

    def test_ean_still_wins_over_craft(self):
        """EAN cross-match (score=100) still wins when both EAN and Craft apply."""
        catalogue = _make_products(
            NUMBER=['1910163-999000-XL'],
            TITLE_DK=['Craft Jacket XL'],
            VARIANT_ID=[''],
            VARIANT_TYPES=[''],
            EAN=['5701234567890'],
            BUY_PRICE=['100'],
            PRICE=['200'],
            BUY_PRICE_NUM=[100.0],
            PRICE_NUM=[200.0],
            PRODUCT_ID=['10'],
            PRODUCER=['Craft'],
            PRODUCER_ID=[5],
            ONLINE=[True],
        )
        # Invoice SKU is a bare EAN
        invoice_df = pd.DataFrame({
            'SKU': ['5701234567890'],
            'Qty': ['1'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty', threshold=70,
        )
        m = mdata['matches']['5701234567890']
        assert m['score'] == 100

    def test_supplier_flow_also_uses_craft_normalization(self):
        """Supplier matching (via same pipeline) also benefits from Craft norm."""
        catalogue = self._craft_catalogue()
        supplier_df = pd.DataFrame({
            'SKU': ['1910163-999000-7'],
            'Qty': ['3'],
        })
        mdata = match_invoice_to_products(
            catalogue, supplier_df, 'SKU', 'Qty', threshold=70,
        )
        mdf = build_matches_df(catalogue, mdata, src_type='supplier')
        assert mdf.iloc[0]['match_score'] == 100
        assert mdf.iloc[0]['src_type'] == 'supplier'
        assert mdf.iloc[0]['src_sku_craft_normalized'] == '1910163-999000-XL'

    def test_craft_exact_beats_fuzzy_near_miss(self):
        """Craft exact-match must beat fuzzy near-miss with a similar base SKU.

        Regression test: invoice SKU '1910163-999000-7' (EVOLVE PANTS XL)
        must match '1910163-999000-XL' and NOT drift to the similar
        '1910173-999000' (a completely different product).
        """
        catalogue = _make_products(
            NUMBER=[
                '1910163-999000-XL', '1910163-999000-L',
                '1910163-999000-M', '1910173-999000',
            ],
            TITLE_DK=[
                'Craft EVOLVE PANTS M Black XL',
                'Craft EVOLVE PANTS M Black L',
                'Craft EVOLVE PANTS M Black M',
                'Craft t-shirt Progress 2.0 Solid Jersey',
            ],
            VARIANT_ID=['', '', '', ''],
            VARIANT_TYPES=['', '', '', 'X-Large / 42'],
            EAN=['', '', '', '7318573536943'],
            BUY_PRICE=['100', '100', '100', '80'],
            PRICE=['200', '200', '200', '160'],
            BUY_PRICE_NUM=[100.0, 100.0, 100.0, 80.0],
            PRICE_NUM=[200.0, 200.0, 200.0, 160.0],
            PRODUCT_ID=['10', '11', '12', '20'],
            PRODUCER=['Craft', 'Craft', 'Craft', 'Craft'],
            PRODUCER_ID=[5, 5, 5, 5],
            ONLINE=[True, True, True, True],
        )
        invoice_df = pd.DataFrame({
            'SKU': ['1910163-999000-7'],
            'Qty': ['2'],
            'Description': ['EVOLVE PANTS M Black XL'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty',
            threshold=70, invoice_desc_col='Description',
        )
        m = mdata['matches']['1910163-999000-7']
        # Must match the CORRECT product, not the near-miss
        assert m['sku'] == '1910163-999000-XL', (
            f"Expected '1910163-999000-XL' but got '{m['sku']}'"
        )
        assert m['score'] == 100
        assert m.get('method') == 'craft-exact'

    def test_craft_exact_beats_fuzzy_in_build_matches_df(self):
        """build_matches_df reports correct product and method for Craft exact.

        End-to-end regression: the matched_number must be the correct
        Craft product, match_method_detail must be 'craft-exact', and
        the wrong product must NOT appear.
        """
        catalogue = _make_products(
            NUMBER=[
                '1910163-999000-XL', '1910163-999000-L',
                '1910173-999000',
            ],
            TITLE_DK=[
                'Craft EVOLVE PANTS M Black XL',
                'Craft EVOLVE PANTS M Black L',
                'Craft t-shirt Progress 2.0 Solid Jersey',
            ],
            VARIANT_ID=['', '', ''],
            VARIANT_TYPES=['', '', 'X-Large / 42'],
            EAN=['', '', '7318573536943'],
            BUY_PRICE=['100', '100', '80'],
            PRICE=['200', '200', '160'],
            BUY_PRICE_NUM=[100.0, 100.0, 80.0],
            PRICE_NUM=[200.0, 200.0, 160.0],
            PRODUCT_ID=['10', '11', '20'],
            PRODUCER=['Craft', 'Craft', 'Craft'],
            PRODUCER_ID=[5, 5, 5],
            ONLINE=[True, True, True],
        )
        invoice_df = pd.DataFrame({
            'SKU': ['1910163-999000-7'],
            'Qty': ['2'],
            'Description': ['EVOLVE PANTS M Black XL'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty',
            threshold=70, invoice_desc_col='Description',
        )
        mdf = build_matches_df(catalogue, mdata)
        row = mdf.iloc[0]
        assert row['matched_number'] == '1910163-999000-XL'
        assert row['match_score'] == 100
        assert row['match_method_detail'] == 'craft-exact'
        assert row['src_sku_craft_normalized'] == '1910163-999000-XL'
        # Must NOT be the wrong product
        assert row['matched_number'] != '1910173-999000'

    def test_supplier_craft_exact_beats_fuzzy_near_miss(self):
        """Supplier flow: Craft exact-match beats fuzzy near-miss.

        Uses match_supplier_to_products directly to verify the
        deterministic Craft matching at the lowest level.
        """
        product_skus = [
            '1910163-999000-XL', '1910163-999000-L',
            '1910173-999000',
        ]
        result = match_supplier_to_products(
            ['1910163-999000-7'],
            product_skus,
            threshold=70,
            supplier_names={'1910163-999000-7': 'EVOLVE PANTS M Black XL'},
            product_names={
                '1910163-999000-XL': 'Craft EVOLVE PANTS',
                '1910163-999000-L': 'Craft EVOLVE PANTS',
                '1910173-999000': 'Craft t-shirt Progress 2.0',
            },
        )
        m = result['1910163-999000-7']
        assert m['sku'] == '1910163-999000-XL'
        assert m['score'] == 100
        assert m['method'] == 'craft-exact'

    def test_match_method_detail_column_populated(self):
        """match_method_detail column is populated for various match types."""
        catalogue = self._craft_catalogue()
        invoice_df = pd.DataFrame({
            'SKU': ['1910163-999000-7'],
            'Qty': ['1'],
        })
        mdata = match_invoice_to_products(
            catalogue, invoice_df, 'SKU', 'Qty', threshold=70,
        )
        mdf = build_matches_df(catalogue, mdata)
        assert 'match_method_detail' in mdf.columns
        row = mdf.iloc[0]
        assert row['match_method_detail'] == 'craft-exact'
