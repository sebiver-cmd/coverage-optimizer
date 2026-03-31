"""Tests for domain/invoice_ean.py — invoice-to-EAN barcode matching."""

from __future__ import annotations

import pandas as pd
import pytest

from domain.invoice_ean import (
    detect_invoice_columns,
    build_ean_export,
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
