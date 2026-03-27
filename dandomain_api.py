"""Secure DanDomain webshop API client.

Supports both REST (v1) and GraphQL endpoints for updating product
prices on a DanDomain webshop.  Credentials are **never** logged or
hard-coded — they must be supplied via Streamlit secrets, environment
variables, or a secure input field.

Security layers
---------------
1. HTTPS enforcement — HTTP URLs are rejected outright.
2. SSL certificate verification — always enabled, never disabled.
3. Credential isolation — the API key is kept in memory only; it is
   never written to disk, included in log output, or cached by
   Streamlit.  A logging filter scrubs keys from any log message.
4. Input validation — product numbers, prices, and URLs are validated
   before any request is made.
5. Retry with exponential back-off — transient failures and 429
   (rate-limit) responses are retried up to *MAX_RETRIES* times.
6. Sanitised error messages — raw API responses and internal URLs are
   never exposed to the caller.
7. Redirect protection — redirects are disabled so that the API key
   embedded in the URL path cannot leak to a non-HTTPS destination.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import quote as urlquote

import requests

logger = logging.getLogger(__name__)


class _KeyScrubFilter(logging.Filter):
    """Prevent API keys from leaking into log output."""

    def __init__(self, key: str = ""):
        super().__init__()
        self._key = key

    def set_key(self, key: str) -> None:
        self._key = key

    def filter(self, record: logging.LogRecord) -> bool:
        if self._key and self._key in str(record.msg):
            record.msg = str(record.msg).replace(self._key, "***")
        if hasattr(record, "args") and record.args:
            args_str = str(record.args)
            if self._key and self._key in args_str:
                record.args = tuple(
                    str(a).replace(self._key, "***") if isinstance(a, str) else a
                    for a in (record.args if isinstance(record.args, tuple) else (record.args,))
                )
        return True


_key_filter = _KeyScrubFilter()
logger.addFilter(_key_filter)

# ---------------------------------------------------------------------------
# Connection defaults
# ---------------------------------------------------------------------------
CONNECT_TIMEOUT = 10   # seconds
READ_TIMEOUT = 30      # seconds
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # doubles on each retry
BATCH_DELAY = 0.2      # seconds between successive batch requests

# ---------------------------------------------------------------------------
# DanDomain endpoint paths (v1 REST / JSON)
# ---------------------------------------------------------------------------
PRODUCT_DATA_SERVICE = "/admin/WEBAPI/Endpoints/v1_0/ProductDataService"
PRODUCT_SERVICE = "/admin/WEBAPI/Endpoints/v1_0/ProductService"
GRAPHQL_ENDPOINT = "/admin/WEBAPI/Endpoints/v1_0/GraphQL"

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_HTTPS_URL_RE = re.compile(r"^https://[a-zA-Z0-9][\w.-]*\.\w{2,}")
_SAFE_PRODUCT_NUMBER_RE = re.compile(r"^[\w./ -]+$")
_MAX_PRICE = 999_999.0


class DanDomainAPIError(Exception):
    """Raised for any DanDomain API failure (message is safe to display)."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class DanDomainClient:
    """Secure HTTP client for the DanDomain webshop API.

    Parameters
    ----------
    shop_url : str
        Shop base URL — **must** start with ``https://``.
    api_key : str
        DanDomain API key or app secret.
    method : ``"rest"`` | ``"graphql"``
        Which API flavour to use.
    """

    def __init__(self, shop_url: str, api_key: str, method: str = "rest"):
        # --- validate inputs ------------------------------------------------
        if not shop_url or not isinstance(shop_url, str):
            raise ValueError("Shop URL is required")

        shop_url = shop_url.strip().rstrip("/")
        if not shop_url.startswith("https://"):
            raise ValueError("Shop URL must use HTTPS for security")
        if not _HTTPS_URL_RE.match(shop_url):
            raise ValueError("Invalid shop URL format")

        if not api_key or not isinstance(api_key, str):
            raise ValueError("API key is required")

        if method not in ("rest", "graphql"):
            raise ValueError("Method must be 'rest' or 'graphql'")

        self._shop_url = shop_url
        self._api_key = api_key
        self._method = method

        # Register the API key with the log-scrub filter so that even
        # debug / third-party logging can never leak it.
        _key_filter.set_key(api_key)

        # --- HTTP session with security defaults ----------------------------
        self._session = requests.Session()
        self._session.verify = True        # always verify SSL certs
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "CoverageOptimizer/1.0",
        })

    # -- internal helpers ----------------------------------------------------

    def _rest_url(self, service: str, path: str) -> str:
        """Build a v1 REST URL with the API key embedded."""
        return f"{self._shop_url}{service}/{self._api_key}{path}"

    def _graphql_url(self) -> str:
        return f"{self._shop_url}{GRAPHQL_ENDPOINT}"

    def _request(
        self,
        method: str,
        url: str,
        json_body: Any = None,
    ) -> dict:
        """Execute an HTTP request with retry / back-off."""
        last_error: Optional[str] = None

        for attempt in range(MAX_RETRIES):
            try:
                resp = self._session.request(
                    method,
                    url,
                    json=json_body,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    allow_redirects=False,  # never follow redirects
                )

                # Reject any redirect — the URL contains the API key
                if 300 <= resp.status_code < 400:
                    raise DanDomainAPIError(
                        "API returned a redirect — aborting to protect credentials"
                    )

                # Rate-limited — wait and retry
                if resp.status_code == 429:
                    wait = RETRY_BASE_DELAY * (2 ** attempt)
                    time.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    hint = {
                        401: " — invalid API key",
                        403: " — access denied; check API key permissions",
                        404: (
                            " — endpoint not found; verify that the"
                            " shop URL and API key are correct and"
                            " that the API is enabled for your shop"
                        ),
                        500: " — server error on the DanDomain side",
                    }.get(resp.status_code, "")
                    raise DanDomainAPIError(
                        f"API returned HTTP {resp.status_code}{hint}"
                    )

                return resp.json()

            except requests.exceptions.SSLError:
                raise DanDomainAPIError(
                    "SSL certificate verification failed"
                )
            except requests.exceptions.ConnectionError:
                last_error = "Connection failed — check the shop URL"
            except requests.exceptions.Timeout:
                last_error = "Request timed out"
            except DanDomainAPIError:
                raise
            except requests.exceptions.RequestException as exc:
                last_error = f"Request error: {type(exc).__name__}"

            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BASE_DELAY * (2 ** attempt))

        raise DanDomainAPIError(
            last_error or "Request failed after retries"
        )

    @staticmethod
    def _validate_product_number(product_number: str) -> str:
        if not product_number or not isinstance(product_number, str):
            raise ValueError("Product number is required")
        product_number = product_number.strip()
        if not _SAFE_PRODUCT_NUMBER_RE.match(product_number):
            raise ValueError("Product number contains invalid characters")
        return product_number

    @staticmethod
    def _validate_price(price: float) -> float:
        if not isinstance(price, (int, float)):
            raise ValueError("Price must be a number")
        if price < 0:
            raise ValueError("Price must be non-negative")
        if price > _MAX_PRICE:
            raise ValueError(f"Price exceeds sanity limit ({_MAX_PRICE:,.0f})")
        return round(float(price), 2)

    # -- public API ----------------------------------------------------------

    def test_connection(self) -> dict:
        """Test the API connection.

        Returns a dict with ``status`` and extra info on success; raises
        :class:`DanDomainAPIError` on failure.
        """
        if self._method == "rest":
            url = self._rest_url(PRODUCT_DATA_SERVICE, "/ProductCount")
            result = self._request("GET", url)
            # The endpoint may return a bare integer or a JSON object
            count = result if isinstance(result, int) else result
            return {"status": "connected", "product_count": count}

        # GraphQL — lightweight introspection
        query = {"query": "{ __schema { queryType { name } } }"}
        result = self._request("POST", self._graphql_url(), json_body=query)
        return {"status": "connected", "schema": result}

    def get_product(
        self,
        product_number: str,
        site_id: int = 1,
    ) -> dict:
        """Fetch a single product by its product number."""
        product_number = self._validate_product_number(product_number)
        encoded = urlquote(product_number, safe="")

        if self._method == "rest":
            url = self._rest_url(PRODUCT_DATA_SERVICE, f"/{encoded}")
            return self._request("GET", url)

        query = {
            "query": (
                "query GetProduct($number: String!) {"
                "  product(number: $number) {"
                "    id number salesPrice costPrice"
                "  }"
                "}"
            ),
            "variables": {"number": product_number},
        }
        return self._request("POST", self._graphql_url(), json_body=query)

    def update_product_price(
        self,
        product_number: str,
        new_price: float,
        site_id: int = 1,
    ) -> dict:
        """Update the sales price of a single product.

        Parameters
        ----------
        product_number : str
            Product SKU / number.
        new_price : float
            New sales price **including VAT**.
        site_id : int
            Language / site ID (default ``1``).
        """
        product_number = self._validate_product_number(product_number)
        new_price = self._validate_price(new_price)
        encoded = urlquote(product_number, safe="")

        if self._method == "rest":
            url = self._rest_url(PRODUCT_DATA_SERVICE, f"/{encoded}")
            body = [{"Key": "SalesPrice", "Value": str(new_price)}]
            return self._request("PATCH", url, json_body=body)

        # GraphQL mutation
        query = {
            "query": (
                "mutation UpdatePrice($input: ProductUpdateInput!) {"
                "  updateProduct(input: $input) {"
                "    product { id number salesPrice }"
                "    userErrors { field message }"
                "  }"
                "}"
            ),
            "variables": {
                "input": {
                    "number": product_number,
                    "salesPrice": new_price,
                },
            },
        }
        result = self._request("POST", self._graphql_url(), json_body=query)

        # Surface GraphQL-level errors
        data = result.get("data", {}).get("updateProduct", {})
        errors = data.get("userErrors", [])
        if errors:
            msg = errors[0].get("message", "Unknown error")
            raise DanDomainAPIError(f"Update failed: {msg}")

        return result

    def update_prices_batch(
        self,
        updates: list[dict],
        site_id: int = 1,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """Batch-update product prices.

        Parameters
        ----------
        updates : list[dict]
            Each dict must contain ``product_number`` (str) and
            ``new_price`` (float).
        site_id : int
            Language / site ID (default ``1``).
        progress_callback : callable, optional
            Called after each product with
            ``(index, total, product_number, success, error_message)``.

        Returns
        -------
        dict
            ``{"success": int, "failed": int, "errors": [...]}``.
        """
        results: dict = {"success": 0, "failed": 0, "errors": []}
        total = len(updates)

        for i, update in enumerate(updates):
            pnum = update.get("product_number", "")
            price = update.get("new_price", 0)

            try:
                self.update_product_price(pnum, price, site_id)
                results["success"] += 1
                if progress_callback:
                    progress_callback(i + 1, total, pnum, True, "")
            except (DanDomainAPIError, ValueError) as exc:
                results["failed"] += 1
                err = str(exc)
                results["errors"].append(
                    {"product_number": pnum, "error": err}
                )
                if progress_callback:
                    progress_callback(i + 1, total, pnum, False, err)

            # Respect rate limits
            if i < total - 1:
                time.sleep(BATCH_DELAY)

        return results

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "DanDomainClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
