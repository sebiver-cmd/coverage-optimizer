"""Tests for variant enrichment, variant-item-number matching, and failure fallback.

Covers:
- Variant enrichment normalization (mock SOAP responses).
- Matching prefers variant item number exact match.
- Failure modes: variant fetch failure does not block matching.
- api_products_to_dataframe extracts VARIANT_ITEMNUMBER.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from domain.invoice_ean import (
    match_invoice_to_products,
    match_supplier_to_products,
    build_matches_df,
)
from domain.pricing import api_products_to_dataframe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_products_with_variant_itemnumber(**overrides) -> pd.DataFrame:
    """Build a product catalogue DataFrame with VARIANT_ITEMNUMBER."""
    defaults = {
        'NUMBER': [
            '1910163', '1910163', '1910163',
            'SKU-BASIC',
        ],
        'TITLE_DK': [
            'Craft Jacket', 'Craft Jacket', 'Craft Jacket',
            'Basic Tee',
        ],
        'VARIANT_ID': ['101', '102', '103', ''],
        'VARIANT_TYPES': [
            '999000 // XL', '999000 // L', '999000 // M',
            '',
        ],
        'VARIANT_ITEMNUMBER': [
            '1910163-999000-XL', '1910163-999000-L', '1910163-999000-M',
            '',
        ],
        'EAN': [
            '5701234000001', '5701234000002', '5701234000003',
            '5701234000099',
        ],
        'BUY_PRICE': ['200,00', '200,00', '200,00', '50,00'],
        'PRICE': ['400,00', '400,00', '400,00', '100,00'],
        'BUY_PRICE_NUM': [200.0, 200.0, 200.0, 50.0],
        'PRICE_NUM': [400.0, 400.0, 400.0, 100.0],
        'PRODUCT_ID': ['1', '1', '1', '2'],
        'PRODUCER': ['Craft', 'Craft', 'Craft', 'Other'],
        'PRODUCER_ID': [10, 10, 10, 20],
        'ONLINE': [True, True, True, True],
    }
    defaults.update(overrides)
    return pd.DataFrame(defaults)


def _make_invoice(**overrides) -> pd.DataFrame:
    """Build a minimal invoice DataFrame."""
    defaults = {
        'sku': ['1910163-999000-XL', 'SKU-BASIC'],
        'qty': [2, 5],
    }
    defaults.update(overrides)
    return pd.DataFrame(defaults)


# ---------------------------------------------------------------------------
# 1. api_products_to_dataframe extracts VARIANT_ITEMNUMBER
# ---------------------------------------------------------------------------

class TestVariantItemNumberExtraction:
    """Verify api_products_to_dataframe populates VARIANT_ITEMNUMBER."""

    def test_variant_itemnumber_populated_from_api_data(self):
        """When variant dict has ItemNumber, it appears in the DataFrame."""
        products = [{
            'Id': 1,
            'Title': 'Craft Jacket',
            'ItemNumber': '1910163',
            'Price': 400,
            'BuyingPrice': 200,
            'Ean': '',
            'Status': True,
            'Producer': 'Craft',
            'ProducerId': 10,
            'VariantTypes': 'Size',
            'Variants': [
                {
                    'Id': 101,
                    'ItemNumber': '1910163-999000-XL',
                    'Title': '999000 // XL',
                    'Price': 400,
                    'BuyingPrice': 200,
                    'Ean': '5701234000001',
                },
                {
                    'Id': 102,
                    'ItemNumber': '1910163-999000-L',
                    'Title': '999000 // L',
                    'Price': 400,
                    'BuyingPrice': 200,
                    'Ean': '5701234000002',
                },
            ],
        }]
        df = api_products_to_dataframe(products)
        assert 'VARIANT_ITEMNUMBER' in df.columns
        assert df.iloc[0]['VARIANT_ITEMNUMBER'] == '1910163-999000-XL'
        assert df.iloc[1]['VARIANT_ITEMNUMBER'] == '1910163-999000-L'

    def test_variant_itemnumber_empty_when_not_in_api(self):
        """When variant dict lacks ItemNumber, column is empty string."""
        products = [{
            'Id': 2,
            'Title': 'Basic Tee',
            'ItemNumber': 'SKU-BASIC',
            'Price': 100,
            'BuyingPrice': 50,
            'Ean': '',
            'Status': True,
            'Producer': 'Other',
            'ProducerId': 20,
            'Variants': [],
        }]
        df = api_products_to_dataframe(products)
        assert 'VARIANT_ITEMNUMBER' in df.columns
        assert df.iloc[0]['VARIANT_ITEMNUMBER'] == ''

    def test_variant_itemnumber_from_zeep_like_objects(self):
        """Simulate zeep-style attribute access (non-dict variants)."""

        class FakeVariant:
            def __init__(self, vid, item_number, title, price, buy, ean):
                self.Id = vid
                self.ItemNumber = item_number
                self.Title = title
                self.Price = price
                self.BuyingPrice = buy
                self.Ean = ean

        products = [{
            'Id': 3,
            'Title': 'Widget',
            'ItemNumber': 'W-100',
            'Price': 50,
            'BuyingPrice': 25,
            'Ean': '',
            'Status': True,
            'Producer': '',
            'ProducerId': None,
            'Variants': [
                FakeVariant(201, 'W-100-RED', 'Red', 50, 25, ''),
                FakeVariant(202, 'W-100-BLU', 'Blue', 50, 25, ''),
            ],
        }]
        df = api_products_to_dataframe(products)
        assert df.iloc[0]['VARIANT_ITEMNUMBER'] == 'W-100-RED'
        assert df.iloc[1]['VARIANT_ITEMNUMBER'] == 'W-100-BLU'


# ---------------------------------------------------------------------------
# 2. Matching prefers variant item number exact match
# ---------------------------------------------------------------------------

class TestVariantItemNumberMatching:
    """Variant item numbers are used as primary exact-match keys."""

    def test_invoice_sku_matches_variant_itemnumber_exactly(self):
        """Invoice SKU '1910163-999000-XL' matches the XL variant directly."""
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = _make_invoice(
            sku=['1910163-999000-XL'],
            qty=[2],
        )
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        matches = result['matches']
        assert '1910163-999000-XL' in matches
        entry = matches['1910163-999000-XL']
        assert entry['score'] == 100
        assert entry['method'] == 'variant-itemnumber-exact'
        # Verify composite_lookup resolves to the right base + variant
        num, vtype = result['composite_lookup'].get(entry['sku'], ('', ''))
        assert num == '1910163'
        assert 'XL' in vtype

    def test_variant_itemnumber_match_does_not_drift_to_base(self):
        """The match should resolve to a specific variant, not the base."""
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = _make_invoice(
            sku=['1910163-999000-L'],
            qty=[1],
        )
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        entry = result['matches']['1910163-999000-L']
        assert entry['score'] == 100
        # Must resolve to a variant-level key, not the bare base number
        num, vtype = result['composite_lookup'][entry['sku']]
        assert num == '1910163'
        assert 'L' in vtype

    def test_ean_still_highest_priority(self):
        """EAN exact match (score 100) still wins over variant-itemnumber."""
        products_df = _make_products_with_variant_itemnumber()
        # Invoice SKU is a bare EAN matching the M variant
        invoice_df = _make_invoice(
            sku=['5701234000003'],
            qty=[1],
        )
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        entry = result['matches']['5701234000003']
        assert entry['score'] == 100
        assert entry['method'] == 'ean-exact'

    def test_build_matches_df_with_variant_itemnumber(self):
        """build_matches_df correctly handles variant-itemnumber-exact method."""
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = _make_invoice(
            sku=['1910163-999000-XL'],
            qty=[2],
        )
        match_data = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        mdf = build_matches_df(products_df, match_data)
        assert not mdf.empty
        row = mdf.iloc[0]
        assert row['matched_number'] == '1910163'
        assert row['match_method_detail'] == 'variant-itemnumber-exact'
        assert row['match_score'] == 100
        assert row['status'] == 'ok'
        # Should resolve to the XL variant
        assert 'XL' in row['matched_variant']

    def test_craft_sku_with_size_index_matches_variant_itemnumber(self):
        """Craft SKU '1910163-999000-7' normalises to XL and matches variant."""
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = _make_invoice(
            sku=['1910163-999000-7'],
            qty=[1],
        )
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        entry = result['matches']['1910163-999000-7']
        # Should match the XL variant (7 → XL in Craft size map)
        assert entry['score'] == 100
        # The method should be craft-exact or variant-itemnumber-exact
        assert entry['method'] in ('craft-exact', 'variant-itemnumber-exact', 'craft-variant-itemnumber-exact')

    def test_no_variant_itemnumber_falls_back_to_existing_matching(self):
        """When VARIANT_ITEMNUMBER is empty, existing matching still works."""
        products_df = _make_products_with_variant_itemnumber(
            VARIANT_ITEMNUMBER=['', '', '', ''],
        )
        invoice_df = _make_invoice(
            sku=['SKU-BASIC'],
            qty=[3],
        )
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        entry = result['matches']['SKU-BASIC']
        assert entry['score'] == 100
        assert entry['method'] == 'sku-exact'


# ---------------------------------------------------------------------------
# 3. Variant enrichment (enrich_variants)
# ---------------------------------------------------------------------------

class TestEnrichVariants:
    """Test the enrich_variants function with mocked SOAP responses."""

    def test_enrichment_populates_missing_variant_itemnumber(self):
        """When VARIANT_ITEMNUMBER is empty, enrichment fills it in."""
        from domain.product_loader import enrich_variants

        df = pd.DataFrame({
            'NUMBER': ['1910163', '1910163'],
            'VARIANT_ID': ['101', '102'],
            'VARIANT_TYPES': ['XL', 'L'],
            'VARIANT_ITEMNUMBER': ['', ''],
            'VARIANT_TITLE': ['', ''],
            'VARIANT_EAN': ['', ''],
        })

        mock_client = MagicMock()
        mock_client.get_variants_by_item_number.return_value = [
            {'Id': 101, 'ItemNumber': '1910163-999000-XL', 'Title': '999000 // XL', 'Ean': '5701234000001'},
            {'Id': 102, 'ItemNumber': '1910163-999000-L', 'Title': '999000 // L', 'Ean': '5701234000002'},
        ]

        enriched = enrich_variants(df, mock_client)
        assert enriched.at[0, 'VARIANT_ITEMNUMBER'] == '1910163-999000-XL'
        assert enriched.at[1, 'VARIANT_ITEMNUMBER'] == '1910163-999000-L'
        assert enriched.at[0, 'VARIANT_TITLE'] == '999000 // XL'
        assert enriched.at[1, 'VARIANT_EAN'] == '5701234000002'

    def test_enrichment_skips_already_populated_rows(self):
        """Rows with existing VARIANT_ITEMNUMBER are not overwritten."""
        from domain.product_loader import enrich_variants

        df = pd.DataFrame({
            'NUMBER': ['1910163'],
            'VARIANT_ID': ['101'],
            'VARIANT_TYPES': ['XL'],
            'VARIANT_ITEMNUMBER': ['ALREADY-SET'],
            'VARIANT_TITLE': [''],
            'VARIANT_EAN': [''],
        })

        mock_client = MagicMock()
        # Should NOT be called since row is already enriched
        enriched = enrich_variants(df, mock_client)
        mock_client.get_variants_by_item_number.assert_not_called()
        assert enriched.at[0, 'VARIANT_ITEMNUMBER'] == 'ALREADY-SET'

    def test_enrichment_creates_missing_columns(self):
        """When target columns don't exist, they are created."""
        from domain.product_loader import enrich_variants

        df = pd.DataFrame({
            'NUMBER': ['ABC'],
            'VARIANT_ID': ['10'],
            'VARIANT_TYPES': ['Red'],
        })

        mock_client = MagicMock()
        mock_client.get_variants_by_item_number.return_value = [
            {'Id': 10, 'ItemNumber': 'ABC-RED', 'Title': 'Red', 'Ean': ''},
        ]

        enriched = enrich_variants(df, mock_client)
        assert 'VARIANT_ITEMNUMBER' in enriched.columns
        assert 'VARIANT_TITLE' in enriched.columns
        assert 'VARIANT_EAN' in enriched.columns
        assert enriched.at[0, 'VARIANT_ITEMNUMBER'] == 'ABC-RED'

    def test_enrichment_empty_dataframe(self):
        """Empty DataFrame is returned unchanged."""
        from domain.product_loader import enrich_variants

        df = pd.DataFrame()
        mock_client = MagicMock()
        result = enrich_variants(df, mock_client)
        assert result.empty


# ---------------------------------------------------------------------------
# 4. Failure modes — graceful fallback
# ---------------------------------------------------------------------------

class TestVariantEnrichmentFailure:
    """Variant fetch failure returns gracefully and does not block matching."""

    def test_api_failure_returns_df_unchanged(self):
        """When API call fails, DataFrame is returned with empty enrichment cols."""
        from domain.product_loader import enrich_variants

        df = pd.DataFrame({
            'NUMBER': ['1910163'],
            'VARIANT_ID': ['101'],
            'VARIANT_TYPES': ['XL'],
            'VARIANT_ITEMNUMBER': [''],
        })

        mock_client = MagicMock()
        mock_client.get_variants_by_item_number.side_effect = Exception("Network error")

        enriched = enrich_variants(df, mock_client)
        # Should NOT crash; VARIANT_ITEMNUMBER stays empty
        assert enriched.at[0, 'VARIANT_ITEMNUMBER'] == ''

    def test_partial_failure_enriches_what_it_can(self):
        """When some products fail, others are still enriched."""
        from domain.product_loader import enrich_variants

        df = pd.DataFrame({
            'NUMBER': ['GOOD-1', 'BAD-1'],
            'VARIANT_ID': ['10', '20'],
            'VARIANT_TYPES': ['Red', 'Blue'],
            'VARIANT_ITEMNUMBER': ['', ''],
            'VARIANT_TITLE': ['', ''],
            'VARIANT_EAN': ['', ''],
        })

        def side_effect(item_number):
            if item_number == 'GOOD-1':
                return [{'Id': 10, 'ItemNumber': 'GOOD-1-RED', 'Title': 'Red', 'Ean': ''}]
            raise Exception("Timeout")

        mock_client = MagicMock()
        mock_client.get_variants_by_item_number.side_effect = side_effect

        enriched = enrich_variants(df, mock_client)
        assert enriched.at[0, 'VARIANT_ITEMNUMBER'] == 'GOOD-1-RED'
        assert enriched.at[1, 'VARIANT_ITEMNUMBER'] == ''  # failed gracefully

    def test_matching_works_without_variant_itemnumber_column(self):
        """When products_df has no VARIANT_ITEMNUMBER, matching still works."""
        products_df = pd.DataFrame({
            'NUMBER': ['SKU-001', 'SKU-002'],
            'TITLE_DK': ['Widget A', 'Widget B'],
            'VARIANT_ID': ['', ''],
            'VARIANT_TYPES': ['', ''],
            'EAN': ['', ''],
            'BUY_PRICE': ['100,00', '200,00'],
            'PRICE': ['200,00', '400,00'],
            'BUY_PRICE_NUM': [100.0, 200.0],
            'PRICE_NUM': [200.0, 400.0],
            'PRODUCT_ID': ['1', '2'],
            'PRODUCER': ['A', 'B'],
            'PRODUCER_ID': [1, 2],
            'ONLINE': [True, True],
        })
        invoice_df = pd.DataFrame({
            'sku': ['SKU-001'],
            'qty': [1],
        })
        # No VARIANT_ITEMNUMBER column at all — should not crash
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        assert 'SKU-001' in result['matches']
        assert result['matches']['SKU-001']['score'] == 100


# ---------------------------------------------------------------------------
# 5. get_variants_by_item_number (DanDomainClient)
# ---------------------------------------------------------------------------

class TestGetVariantsByItemNumber:
    """Test the DanDomainClient.get_variants_by_item_number method."""

    @patch('dandomain_api.serialize_object')
    @patch('dandomain_api.ZeepClient')
    @patch('dandomain_api.Transport')
    def test_returns_normalized_variant_list(self, mock_transport, mock_zeep, mock_serialize):
        """Successful call returns a list of variant dicts."""
        from dandomain_api import DanDomainClient

        mock_service = MagicMock()
        mock_zeep.return_value.service = mock_service

        # Mock Solution_Connect for __init__
        mock_service.Solution_Connect.return_value = True
        # Mock Product_SetFields / Product_SetVariantFields
        mock_service.Product_SetFields.return_value = None
        mock_service.Product_SetVariantFields.return_value = None

        # serialize_object converts zeep objects to dicts
        mock_serialize.return_value = {
            'Id': 101,
            'ItemNumber': '1910163-999000-XL',
            'Title': '999000 // XL',
            'Ean': '5701234000001',
        }

        mock_service.Product_GetVariantsByItemNumber.return_value = [MagicMock()]

        client = DanDomainClient('user@example.com', 'secret')
        variants = client.get_variants_by_item_number('1910163')
        assert len(variants) == 1
        assert variants[0]['Id'] == 101
        assert variants[0]['ItemNumber'] == '1910163-999000-XL'

    @patch('dandomain_api.ZeepClient')
    @patch('dandomain_api.Transport')
    def test_returns_empty_on_failure(self, mock_transport, mock_zeep):
        """API failure returns empty list, does not raise."""
        from dandomain_api import DanDomainClient, DanDomainAPIError

        mock_service = MagicMock()
        mock_zeep.return_value.service = mock_service

        mock_service.Solution_Connect.return_value = True
        mock_service.Product_SetFields.return_value = None
        mock_service.Product_SetVariantFields.return_value = None
        mock_service.Product_GetVariantsByItemNumber.side_effect = (
            DanDomainAPIError("Not found")
        )

        client = DanDomainClient('user@example.com', 'secret')
        variants = client.get_variants_by_item_number('NONEXISTENT')
        assert variants == []

    @patch('dandomain_api.ZeepClient')
    @patch('dandomain_api.Transport')
    def test_returns_empty_on_none_response(self, mock_transport, mock_zeep):
        """None response returns empty list."""
        from dandomain_api import DanDomainClient

        mock_service = MagicMock()
        mock_zeep.return_value.service = mock_service

        mock_service.Solution_Connect.return_value = True
        mock_service.Product_SetFields.return_value = None
        mock_service.Product_SetVariantFields.return_value = None
        mock_service.Product_GetVariantsByItemNumber.return_value = None

        client = DanDomainClient('user@example.com', 'secret')
        variants = client.get_variants_by_item_number('1910163')
        assert variants == []


# ---------------------------------------------------------------------------
# 6. VARIANT_TYPES must NOT be corrupted by format_int_col
# ---------------------------------------------------------------------------

class TestVariantTypesNotCorrupted:
    """Ensure VARIANT_TYPES retains human-readable text after DataFrame creation."""

    def test_variant_types_preserved_for_text_values(self):
        """Text like 'X-Large / 42' must not be mangled by format_int_col."""
        products = [{
            'Id': 1,
            'Title': 'Jacket',
            'ItemNumber': 'J-100',
            'Price': 400,
            'BuyingPrice': 200,
            'Ean': '',
            'Status': True,
            'Producer': '',
            'ProducerId': None,
            'VariantTypes': 'Size',
            'Variants': [
                {
                    'Id': 10,
                    'ItemNumber': 'J-100-XL',
                    'Title': 'X-Large / 42',
                    'Price': 400,
                    'BuyingPrice': 200,
                    'Ean': '',
                },
                {
                    'Id': 11,
                    'ItemNumber': 'J-100-S',
                    'Title': 'Small',
                    'Price': 400,
                    'BuyingPrice': 200,
                    'Ean': '',
                },
            ],
        }]
        df = api_products_to_dataframe(products)
        assert df.iloc[0]['VARIANT_TYPES'] == 'X-Large / 42'
        assert df.iloc[1]['VARIANT_TYPES'] == 'Small'

    def test_variant_types_preserved_for_craft_composite(self):
        """Craft-style '999000 // XL' stays unchanged."""
        products = [{
            'Id': 2,
            'Title': 'Craft Jacket',
            'ItemNumber': '1910163',
            'Price': 400,
            'BuyingPrice': 200,
            'Ean': '',
            'Status': True,
            'Producer': 'Craft',
            'ProducerId': 10,
            'VariantTypes': 'Color, Size',
            'Variants': [
                {
                    'Id': 101,
                    'ItemNumber': '1910163-999000-XL',
                    'Title': '999000 // XL',
                    'Price': 400,
                    'BuyingPrice': 200,
                    'Ean': '',
                },
            ],
        }]
        df = api_products_to_dataframe(products)
        assert df.iloc[0]['VARIANT_TYPES'] == '999000 // XL'

    def test_variant_types_empty_string_for_non_variant_product(self):
        """Non-variant products have empty-string VARIANT_TYPES."""
        products = [{
            'Id': 3,
            'Title': 'Basic Tee',
            'ItemNumber': 'BT-001',
            'Price': 100,
            'BuyingPrice': 50,
            'Ean': '',
            'Status': True,
            'Producer': '',
            'ProducerId': None,
            'Variants': [],
        }]
        df = api_products_to_dataframe(products)
        assert df.iloc[0]['VARIANT_TYPES'] == ''


# ---------------------------------------------------------------------------
# 7. Craft end-to-end: invoice SKU 1910163-999000-7 → variant XL
# ---------------------------------------------------------------------------

class TestCraftEndToEnd:
    """Full flow: Craft invoice SKU maps to variant item number via size index."""

    def test_craft_sku_resolves_to_variant_itemnumber(self):
        """Invoice '1910163-999000-7' must match variant '1910163-999000-XL'.

        Products catalogue has base NUMBER 1910163 with a variant row whose
        VARIANT_ITEMNUMBER is '1910163-999000-XL'.  Invoice SKU is
        '1910163-999000-7' (Craft size index 7 → XL).  The match must
        resolve to that variant with method 'variant-itemnumber-exact'.
        """
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = pd.DataFrame({
            'sku': ['1910163-999000-7'],
            'qty': [3],
        })
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        entry = result['matches']['1910163-999000-7']
        assert entry['score'] == 100
        assert entry['method'] == 'craft-variant-itemnumber-exact'

        # Verify it resolves to the XL variant, not the base product
        num, vtype = result['composite_lookup'][entry['sku']]
        assert num == '1910163'
        assert 'XL' in vtype

    def test_craft_sku_build_matches_df_output(self):
        """build_matches_df correctly represents the Craft → variant match."""
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = pd.DataFrame({
            'sku': ['1910163-999000-7'],
            'qty': [3],
        })
        match_data = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        mdf = build_matches_df(products_df, match_data)
        assert not mdf.empty
        row = mdf.iloc[0]
        assert row['matched_number'] == '1910163'
        assert 'XL' in row['matched_variant']
        assert row['match_method_detail'] == 'craft-variant-itemnumber-exact'
        assert row['match_score'] == 100
        assert row['status'] == 'ok'

    def test_multiple_craft_sizes_each_resolve_correctly(self):
        """Multiple Craft size-index SKUs each resolve to their correct variant."""
        products_df = _make_products_with_variant_itemnumber()
        invoice_df = pd.DataFrame({
            'sku': ['1910163-999000-7', '1910163-999000-6', '1910163-999000-5'],
            'qty': [1, 2, 3],
        })
        result = match_invoice_to_products(
            products_df, invoice_df,
            invoice_sku_col='sku',
            invoice_qty_col='qty',
        )
        # 7 → XL
        entry_xl = result['matches']['1910163-999000-7']
        assert entry_xl['score'] == 100
        num_xl, vtype_xl = result['composite_lookup'][entry_xl['sku']]
        assert 'XL' in vtype_xl

        # 6 → L
        entry_l = result['matches']['1910163-999000-6']
        assert entry_l['score'] == 100
        num_l, vtype_l = result['composite_lookup'][entry_l['sku']]
        assert 'L' in vtype_l

        # 5 → M
        entry_m = result['matches']['1910163-999000-5']
        assert entry_m['score'] == 100
        num_m, vtype_m = result['composite_lookup'][entry_m['sku']]
        assert 'M' in vtype_m
