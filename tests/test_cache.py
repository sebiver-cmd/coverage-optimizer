"""Tests for backend.cache — per-tenant product cache (Task 3.2).

Validates:
- ``build_caller_key`` produces a deterministic hash that hides credentials.
- Cache hit skips the SOAP fetch function.
- Cache miss calls SOAP fetch and stores the result.
- Invalidation deletes the expected keys.
- "Redis not configured" means all cache functions are silent no-ops.
- Config defaults for cache settings.

No real Redis is required — tests mock the ``redis`` client.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd
import pytest

from backend.config import Settings, get_settings


# ---------------------------------------------------------------------------
# Caller-key hashing
# ---------------------------------------------------------------------------


class TestBuildCallerKey:
    """``build_caller_key`` must produce a SHA-256 hash hiding credentials."""

    def test_returns_hex_sha256(self, monkeypatch):
        monkeypatch.setenv("CACHE_KEY_SALT", "test-salt")
        from backend.cache import build_caller_key

        key = build_caller_key("user@example.com", 1)
        # Must be a 64-char hex string (SHA-256)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_does_not_contain_credentials(self, monkeypatch):
        monkeypatch.setenv("CACHE_KEY_SALT", "test-salt")
        from backend.cache import build_caller_key

        key = build_caller_key("user@example.com", 1)
        assert "user@example.com" not in key
        assert "test-salt" not in key

    def test_deterministic(self, monkeypatch):
        monkeypatch.setenv("CACHE_KEY_SALT", "test-salt")
        from backend.cache import build_caller_key

        a = build_caller_key("user@example.com", 1)
        b = build_caller_key("user@example.com", 1)
        assert a == b

    def test_different_users_different_keys(self, monkeypatch):
        monkeypatch.setenv("CACHE_KEY_SALT", "test-salt")
        from backend.cache import build_caller_key

        a = build_caller_key("user1@example.com", 1)
        b = build_caller_key("user2@example.com", 1)
        assert a != b

    def test_different_sites_different_keys(self, monkeypatch):
        monkeypatch.setenv("CACHE_KEY_SALT", "test-salt")
        from backend.cache import build_caller_key

        a = build_caller_key("user@example.com", 1)
        b = build_caller_key("user@example.com", 2)
        assert a != b

    def test_uses_salt(self, monkeypatch):
        from backend.cache import build_caller_key

        monkeypatch.setenv("CACHE_KEY_SALT", "salt-a")
        a = build_caller_key("user@example.com", 1)

        get_settings.cache_clear()
        monkeypatch.setenv("CACHE_KEY_SALT", "salt-b")
        b = build_caller_key("user@example.com", 1)

        assert a != b


# ---------------------------------------------------------------------------
# Serialisation round-trip
# ---------------------------------------------------------------------------


class TestSerialisation:
    """_serialize / _deserialize must round-trip correctly."""

    def test_round_trip(self):
        from backend.cache import _serialize, _deserialize

        data = [{"NUMBER": "SKU-001", "PRICE_NUM": 123.45}]
        serialized = _serialize(data)
        assert isinstance(serialized, bytes)
        result = _deserialize(serialized)
        assert result == data

    def test_output_is_gzipped(self):
        from backend.cache import _serialize

        data = {"hello": "world"}
        serialized = _serialize(data)
        # gzip magic number
        assert serialized[:2] == b"\x1f\x8b"


# ---------------------------------------------------------------------------
# Cache get / set with mocked Redis
# ---------------------------------------------------------------------------


class TestCacheGetSetJson:
    """cache_get_json / cache_set_json with a mocked Redis client."""

    def _make_mock_redis(self, store=None):
        """Create a mock Redis client backed by a dict."""
        if store is None:
            store = {}
        r = MagicMock()

        def _get(key):
            return store.get(key)

        def _set(key, value, ex=None):
            store[key] = value

        def _delete(*keys):
            for k in keys:
                store.pop(k, None)

        r.get = MagicMock(side_effect=_get)
        r.set = MagicMock(side_effect=_set)
        r.delete = MagicMock(side_effect=_delete)
        return r, store

    @patch("backend.cache.get_redis")
    def test_cache_miss_returns_none(self, mock_get_redis):
        from backend.cache import cache_get_json

        mock_r, _ = self._make_mock_redis()
        mock_get_redis.return_value = mock_r

        result = cache_get_json("nonexistent:key")
        assert result is None

    @patch("backend.cache.get_redis")
    def test_cache_set_then_get(self, mock_get_redis):
        from backend.cache import cache_get_json, cache_set_json

        store = {}
        mock_r, store = self._make_mock_redis(store)
        mock_get_redis.return_value = mock_r

        data = [{"NUMBER": "SKU-001", "PRICE_NUM": 100.0}]
        ok = cache_set_json("test:key", data, ttl_s=60)
        assert ok is True

        result = cache_get_json("test:key")
        assert result == data

    @patch("backend.cache.get_redis")
    def test_set_skips_oversized_payload(self, mock_get_redis, monkeypatch):
        from backend.cache import cache_set_json

        monkeypatch.setenv("CACHE_MAX_PAYLOAD_KB", "0")  # 0 KB = nothing fits
        mock_r, _ = self._make_mock_redis()
        mock_get_redis.return_value = mock_r

        data = [{"NUMBER": "SKU-001"}]
        ok = cache_set_json("test:key", data, ttl_s=60)
        assert ok is False

    @patch("backend.cache.get_redis")
    def test_set_accepts_payload_under_limit(self, mock_get_redis, monkeypatch):
        from backend.cache import cache_set_json

        monkeypatch.setenv("CACHE_MAX_PAYLOAD_KB", "1")  # 1 KB limit
        mock_r, _ = self._make_mock_redis()
        mock_get_redis.return_value = mock_r

        # Small payload should fit within 1 KB
        data = {"key": "val"}
        ok = cache_set_json("test:key", data, ttl_s=60)
        assert ok is True

    def test_get_returns_none_when_redis_unavailable(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import cache_get_json, _reset_redis

        _reset_redis()
        result = cache_get_json("any:key")
        assert result is None

    def test_set_returns_false_when_redis_unavailable(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import cache_set_json, _reset_redis

        _reset_redis()
        ok = cache_set_json("any:key", {"data": 1}, ttl_s=60)
        assert ok is False


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


class TestInvalidation:
    """invalidate_products_cache must delete expected keys."""

    @patch("backend.cache.get_redis")
    def test_deletes_both_keys(self, mock_get_redis):
        from backend.cache import invalidate_products_cache

        mock_r = MagicMock()
        mock_get_redis.return_value = mock_r

        invalidate_products_cache("abc123", 1)

        mock_r.delete.assert_called_once()
        deleted_keys = mock_r.delete.call_args[0]
        assert "products:abc123:1" in deleted_keys
        assert "products_enriched:abc123:1" in deleted_keys

    @patch("backend.cache.get_redis")
    def test_invalidation_failure_does_not_raise(self, mock_get_redis):
        from backend.cache import invalidate_products_cache

        mock_r = MagicMock()
        mock_r.delete.side_effect = Exception("Redis down")
        mock_get_redis.return_value = mock_r

        # Must not raise
        invalidate_products_cache("abc123", 1)

    def test_invalidation_noop_when_redis_unavailable(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import invalidate_products_cache, _reset_redis

        _reset_redis()
        # Must not raise
        invalidate_products_cache("abc123", 1)


# ---------------------------------------------------------------------------
# Redis not configured → no-ops
# ---------------------------------------------------------------------------


class TestRedisNotConfigured:
    """When REDIS_URL is unset, all cache functions are silent no-ops."""

    def test_get_redis_returns_none(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import get_redis, _reset_redis

        _reset_redis()
        assert get_redis() is None

    def test_get_cached_products_returns_none(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import get_cached_products, _reset_redis

        _reset_redis()
        assert get_cached_products("key", 1) is None

    def test_set_cached_products_returns_false(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import set_cached_products, _reset_redis

        _reset_redis()
        assert set_cached_products("key", 1, []) is False

    def test_get_cached_enriched_products_returns_none(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import get_cached_enriched_products, _reset_redis

        _reset_redis()
        assert get_cached_enriched_products("key", 1) is None

    def test_set_cached_enriched_products_returns_false(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        from backend.cache import set_cached_enriched_products, _reset_redis

        _reset_redis()
        assert set_cached_enriched_products("key", 1, []) is False


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestCacheConfigDefaults:
    """Cache-related settings have correct defaults."""

    def test_product_cache_ttl_s_default(self, monkeypatch):
        monkeypatch.delenv("PRODUCT_CACHE_TTL_S", raising=False)
        s = Settings()
        assert s.product_cache_ttl_s == 900

    def test_cache_key_salt_default(self, monkeypatch):
        monkeypatch.delenv("CACHE_KEY_SALT", raising=False)
        s = Settings()
        assert s.cache_key_salt == "change-me"

    def test_cache_max_payload_kb_default(self, monkeypatch):
        monkeypatch.delenv("CACHE_MAX_PAYLOAD_KB", raising=False)
        s = Settings()
        assert s.cache_max_payload_kb == 5120

    def test_cache_key_salt_is_secret(self, monkeypatch):
        monkeypatch.setenv("CACHE_KEY_SALT", "my-secret-salt")
        s = Settings()
        safe = s.to_safe_dict()
        assert safe["cache_key_salt"] == "***"
        assert "my-secret-salt" not in str(safe)


# ---------------------------------------------------------------------------
# Optimizer endpoint — cache hit skips SOAP
# ---------------------------------------------------------------------------


class TestOptimizerCacheIntegration:
    """POST /optimize uses product cache when available."""

    def _make_raw_products(self):
        return [
            {
                "Id": 1,
                "Number": "SKU-001",
                "Title": [{"Value": "Test Product", "SiteId": 1}],
                "ProducerId": 10,
                "Producer": "BrandA",
                "Price": "500.00",
                "BuyingPrice": "250.00",
                "ProductStatus": 1,
                "Ean": "5701234000001",
                "Variants": [],
            },
        ]

    def _make_mock_client(self, raw_products=None, brands=None):
        if raw_products is None:
            raw_products = self._make_raw_products()
        if brands is None:
            brands = {10: "BrandA"}

        mock = MagicMock()
        mock.get_products_batch.return_value = raw_products
        mock.get_all_brands.return_value = brands
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    @patch("backend.cache.get_redis")
    @patch("backend.optimizer_api.DanDomainClient")
    def test_cache_miss_calls_soap(self, mock_cls, mock_get_redis, monkeypatch):
        """On cache miss, SOAP is called and result is stored."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_client = self._make_mock_client()
        mock_cls.return_value = mock_client

        # Mock Redis to return None (cache miss), then accept set
        store = {}
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_r.set.side_effect = lambda k, v, ex=None: store.update({k: v})
        mock_get_redis.return_value = mock_r

        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.post(
            "/optimize/",
            json={
                "api_username": "user@test.dk",
                "api_password": "secret",
            },
        )
        assert resp.status_code == 200
        # SOAP was called
        mock_client.get_products_batch.assert_called()
        # Cache was written
        assert mock_r.set.called

    @patch("backend.cache.get_redis")
    @patch("backend.optimizer_api.DanDomainClient")
    def test_cache_hit_skips_soap(self, mock_cls, mock_get_redis, monkeypatch):
        """On cache hit, SOAP is NOT called."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_client = self._make_mock_client()
        mock_cls.return_value = mock_client

        # Prepare cached data: a serialised DataFrame-records list
        from domain.pricing import api_products_to_dataframe
        from backend.cache import _serialize

        raw_products = self._make_raw_products()
        df = api_products_to_dataframe(raw_products)
        df = df.sort_values(
            "PRODUCER", key=lambda s: s.str.lower(),
        ).reset_index(drop=True)
        cached_records = df.to_dict(orient="records")
        cached_bytes = _serialize(cached_records)

        mock_r = MagicMock()
        mock_r.get.return_value = cached_bytes
        mock_get_redis.return_value = mock_r

        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.post(
            "/optimize/",
            json={
                "api_username": "user@test.dk",
                "api_password": "secret",
            },
        )
        assert resp.status_code == 200
        # SOAP was NOT called (cache hit)
        mock_client.get_products_batch.assert_not_called()


# ---------------------------------------------------------------------------
# Catalog endpoint — cache hit skips SOAP + enrichment
# ---------------------------------------------------------------------------


class TestCatalogCacheIntegration:
    """POST /catalog/products uses enriched product cache."""

    def _make_raw_products(self):
        return [
            {
                "Id": 1,
                "Number": "SKU-001",
                "Title": [{"Value": "Test Product", "SiteId": 1}],
                "ProducerId": 10,
                "Producer": "BrandA",
                "Price": "500.00",
                "BuyingPrice": "250.00",
                "ProductStatus": 1,
                "Ean": "5701234000001",
                "Variants": [
                    {
                        "Id": 101,
                        "ItemNumber": "SKU-001-RED",
                        "Price": "500.00",
                        "BuyingPrice": "250.00",
                        "Ean": "5701234000002",
                        "VariantTypeValues": [
                            {"Value": "Red", "VariantType": {"Value": "Color"}}
                        ],
                    },
                ],
            },
        ]

    def _make_mock_client(self, raw_products=None, brands=None):
        if raw_products is None:
            raw_products = self._make_raw_products()
        if brands is None:
            brands = {10: "BrandA"}

        mock = MagicMock()
        mock.get_products_batch.return_value = raw_products
        mock.get_all_brands.return_value = brands
        mock.get_variants_by_item_number.return_value = []
        mock.__enter__ = MagicMock(return_value=mock)
        mock.__exit__ = MagicMock(return_value=False)
        return mock

    @patch("backend.cache.get_redis")
    @patch("backend.catalog_api.DanDomainClient")
    def test_catalog_cache_miss_calls_soap(self, mock_cls, mock_get_redis, monkeypatch):
        """On cache miss, SOAP fetch + enrichment run and cache is populated."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_client = self._make_mock_client()
        mock_cls.return_value = mock_client

        store = {}
        mock_r = MagicMock()
        mock_r.get.return_value = None
        mock_r.set.side_effect = lambda k, v, ex=None: store.update({k: v})
        mock_get_redis.return_value = mock_r

        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.post(
            "/catalog/products",
            json={
                "api_username": "user@test.dk",
                "api_password": "secret",
            },
        )
        assert resp.status_code == 200
        mock_client.get_products_batch.assert_called()
        assert mock_r.set.called

    @patch("backend.cache.get_redis")
    @patch("backend.catalog_api.DanDomainClient")
    def test_catalog_cache_hit_skips_soap(self, mock_cls, mock_get_redis, monkeypatch):
        """On cache hit, SOAP is NOT called."""
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        mock_client = self._make_mock_client()
        mock_cls.return_value = mock_client

        # Build cached enriched records
        from domain.pricing import api_products_to_dataframe
        from backend.cache import _serialize

        raw_products = self._make_raw_products()
        df = api_products_to_dataframe(raw_products)
        df = df.sort_values(
            "PRODUCER", key=lambda s: s.str.lower(),
        ).reset_index(drop=True)
        # Add enrichment columns that would normally be present
        for col in ("VARIANT_ITEMNUMBER", "VARIANT_TITLE", "VARIANT_EAN"):
            if col not in df.columns:
                df[col] = ""
        cached_records = df.to_dict(orient="records")
        cached_bytes = _serialize(cached_records)

        mock_r = MagicMock()
        mock_r.get.return_value = cached_bytes
        mock_get_redis.return_value = mock_r

        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.post(
            "/catalog/products",
            json={
                "api_username": "user@test.dk",
                "api_password": "secret",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) > 0
        # SOAP was NOT called (cache hit)
        mock_client.get_products_batch.assert_not_called()


# ---------------------------------------------------------------------------
# Apply endpoint — cache invalidation
# ---------------------------------------------------------------------------


class TestApplyCacheInvalidation:
    """POST /apply-prices/apply invalidates cache after successful apply."""

    @patch("backend.cache.get_redis")
    @patch("backend.apply_real_api.DanDomainClient")
    def test_apply_invalidates_cache(
        self, mock_cls, mock_get_redis, monkeypatch, tmp_path
    ):
        """After a successful apply, product cache keys are deleted."""
        monkeypatch.setenv("SB_OPTIMA_ENABLE_APPLY", "true")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

        # Patch batch dir and audit log in the apply_real_api module namespace
        import backend.apply_real_api as ara

        batch_dir = tmp_path / "batches"
        batch_dir.mkdir()
        audit_log = tmp_path / "audit.log"
        monkeypatch.setattr(ara, "BATCH_DIR", batch_dir)
        monkeypatch.setattr(ara, "AUDIT_LOG", audit_log)

        # Create a batch manifest
        import uuid

        batch_id = str(uuid.uuid4())
        manifest = {
            "batch_id": batch_id,
            "changes": [
                {
                    "NUMBER": "SKU-001",
                    "new_price": 200.0,
                    "change_pct": 5.0,
                    "buy_price": 100.0,
                    "product_id": "1",
                }
            ],
        }
        manifest_path = batch_dir / f"{batch_id}.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        # Mock DanDomainClient
        mock_client = MagicMock()
        mock_client.update_prices_batch.return_value = {
            "success": 1,
            "errors": [],
        }
        mock_cls.return_value = mock_client

        # Mock Redis
        mock_r = MagicMock()
        mock_get_redis.return_value = mock_r

        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.post(
            "/apply-prices/apply",
            json={
                "batch_id": batch_id,
                "confirm": True,
                "api_username": "user@test.dk",
                "api_password": "secret",
            },
        )
        assert resp.status_code == 200
        # Cache invalidation was called (delete on the mock)
        mock_r.delete.assert_called_once()
        deleted_keys = mock_r.delete.call_args[0]
        # Should contain products and products_enriched keys
        assert any("products:" in k for k in deleted_keys)
        assert any("products_enriched:" in k for k in deleted_keys)
