"""Per-tenant product cache backed by Redis (Task 3.2).

Provides helpers to cache HostedShop product reads in Redis so that
repeated ``/optimize`` and ``/catalog/products`` calls with the same
credentials avoid redundant SOAP fetches.

When ``REDIS_URL`` is not set, all operations are silent no-ops — the
code path is identical to the pre-cache behaviour.

Cache keys never contain plaintext credentials.  A SHA-256 hash of
``api_username + site_id + server-side salt`` is used instead.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from typing import Any, Optional

import redis as redis_lib

from backend.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis connection (lazy singleton)
# ---------------------------------------------------------------------------

_redis_client: Optional[redis_lib.Redis] = None


def get_redis() -> Optional[redis_lib.Redis]:
    """Return a lazy Redis client, or *None* when ``REDIS_URL`` is unset."""
    global _redis_client  # noqa: PLW0603
    settings = get_settings()
    if not settings.redis_url:
        return None
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(
            settings.redis_url,
            decode_responses=False,  # we store bytes (gzipped JSON)
        )
    return _redis_client


def _reset_redis() -> None:
    """Reset the cached Redis client (for tests)."""
    global _redis_client  # noqa: PLW0603
    _redis_client = None


# ---------------------------------------------------------------------------
# Caller-key derivation
# ---------------------------------------------------------------------------


def build_caller_key(api_username: str, site_id: int) -> str:
    """Derive a stable cache key component from caller identity.

    Returns a hex-encoded SHA-256 hash of
    ``api_username + str(site_id) + salt``.  The salt is read from
    ``CACHE_KEY_SALT`` (via settings).  No plaintext credentials appear
    in the returned value.
    """
    settings = get_settings()
    raw = f"{api_username}:{site_id}:{settings.cache_key_salt}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Cache key builders
# ---------------------------------------------------------------------------

_PREFIX_PRODUCTS = "products"
_PREFIX_PRODUCTS_ENRICHED = "products_enriched"


def _products_key(caller_key: str, site_id: int) -> str:
    return f"{_PREFIX_PRODUCTS}:{caller_key}:{site_id}"


def _enriched_key(caller_key: str, site_id: int) -> str:
    return f"{_PREFIX_PRODUCTS_ENRICHED}:{caller_key}:{site_id}"


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialize(value: Any) -> bytes:
    """Serialise *value* as gzipped JSON bytes."""
    raw = json.dumps(value, default=str).encode()
    return gzip.compress(raw)


def _deserialize(data: bytes) -> Any:
    """Deserialise gzipped JSON bytes."""
    raw = gzip.decompress(data)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Generic get / set
# ---------------------------------------------------------------------------


def cache_get_json(key: str) -> Optional[Any]:
    """Fetch and deserialise a JSON value from Redis.

    Returns *None* on cache miss or when Redis is unavailable.
    """
    r = get_redis()
    if r is None:
        return None
    try:
        data = r.get(key)
        if data is None:
            return None
        return _deserialize(data)
    except Exception:
        logger.debug("cache_get_json failed for key=%s", key, exc_info=True)
        return None


def cache_set_json(key: str, value: Any, ttl_s: Optional[int] = None) -> bool:
    """Serialise *value* as gzipped JSON and store in Redis.

    Returns *True* on success, *False* if skipped or failed.  Payloads
    exceeding ``cache_max_payload_kb`` are silently skipped.
    """
    r = get_redis()
    if r is None:
        return False
    try:
        payload = _serialize(value)
        settings = get_settings()
        max_bytes = settings.cache_max_payload_kb * 1024
        if len(payload) > max_bytes:
            logger.warning(
                "Cache payload too large (%d KB > %d KB limit); skipping key=%s",
                len(payload) // 1024,
                settings.cache_max_payload_kb,
                key,
            )
            return False
        if ttl_s is None:
            ttl_s = settings.product_cache_ttl_s
        r.set(key, payload, ex=ttl_s)
        return True
    except Exception:
        logger.debug("cache_set_json failed for key=%s", key, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Product-specific helpers
# ---------------------------------------------------------------------------


def get_cached_products(caller_key: str, site_id: int) -> Optional[list[dict]]:
    """Retrieve cached base-product list (pre-filter, pre-enrichment)."""
    return cache_get_json(_products_key(caller_key, site_id))


def set_cached_products(
    caller_key: str, site_id: int, products: list[dict]
) -> bool:
    """Store the base-product list in cache."""
    return cache_set_json(_products_key(caller_key, site_id), products)


def get_cached_enriched_products(
    caller_key: str, site_id: int
) -> Optional[list[dict]]:
    """Retrieve cached enriched-product rows (catalog endpoint)."""
    return cache_get_json(_enriched_key(caller_key, site_id))


def set_cached_enriched_products(
    caller_key: str, site_id: int, rows: list[dict]
) -> bool:
    """Store enriched-product rows in cache."""
    return cache_set_json(_enriched_key(caller_key, site_id), rows)


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def invalidate_products_cache(caller_key: str, site_id: int) -> None:
    """Delete all product cache entries for the given caller + site.

    Uses explicit key deletion (no ``SCAN``).  Failures are logged but
    never propagated — cache invalidation must not break the apply flow.
    """
    r = get_redis()
    if r is None:
        return
    keys = [
        _products_key(caller_key, site_id),
        _enriched_key(caller_key, site_id),
    ]
    try:
        r.delete(*keys)
        logger.info("Invalidated product cache keys for site_id=%s", site_id)
    except Exception:
        logger.warning(
            "Failed to invalidate product cache keys for site_id=%s",
            site_id,
            exc_info=True,
        )
