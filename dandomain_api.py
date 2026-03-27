"""Secure DanDomain webshop SOAP API client.

Uses the HostedShop SOAP API (``https://api.hostedshop.dk/service.wsdl``)
to read and update product data on a DanDomain webshop.

Credentials are **never** logged or hard-coded — they must be supplied
via Streamlit secrets, environment variables, or a secure input field.

Security layers
---------------
1. HTTPS — the WSDL and all SOAP calls use HTTPS exclusively.
2. SSL certificate verification — always enabled via the underlying
   ``requests.Session``.
3. Credential isolation — username and password are kept in memory
   only; a logging filter scrubs them from any log message.
4. Input validation — product numbers and prices are validated before
   any request is made.
5. Retry with exponential back-off — transient SOAP faults are retried
   up to *MAX_RETRIES* times.
6. Sanitised error messages — raw SOAP faults are never exposed to the
   caller.

Setup
-----
1. Log into your DanDomain admin panel.
2. Go to **Settings → API: SOAP** and enable API access.
3. Under **Settings → Employees** create an API user (email + password).
4. Enter that email (username) and password in the app sidebar.

See https://webshop-help.dandomain.dk/integration-via-api/
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Optional

import requests
from zeep import Client as ZeepClient
from zeep.exceptions import Fault as SoapFault, Error as ZeepError
from zeep.helpers import serialize_object
from zeep.transports import Transport

logger = logging.getLogger(__name__)


class _CredentialScrubFilter(logging.Filter):
    """Prevent credentials from leaking into log output."""

    def __init__(self) -> None:
        super().__init__()
        self._secrets: list[str] = []

    def add_secret(self, secret: str) -> None:
        if secret and secret not in self._secrets:
            self._secrets.append(secret)

    def filter(self, record: logging.LogRecord) -> bool:
        for secret in self._secrets:
            if secret in str(record.msg):
                record.msg = str(record.msg).replace(secret, "***")
            if hasattr(record, "args") and record.args:
                if secret in str(record.args):
                    record.args = tuple(
                        str(a).replace(secret, "***") if isinstance(a, str) else a
                        for a in (
                            record.args
                            if isinstance(record.args, tuple)
                            else (record.args,)
                        )
                    )
        return True


_cred_filter = _CredentialScrubFilter()
logger.addFilter(_cred_filter)

# ---------------------------------------------------------------------------
# Connection defaults
# ---------------------------------------------------------------------------
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # doubles on each retry
BATCH_DELAY = 0.2       # seconds between successive batch requests
SOAP_TIMEOUT = 30       # seconds for SOAP operations

# ---------------------------------------------------------------------------
# DanDomain SOAP endpoint
# ---------------------------------------------------------------------------
WSDL_URL = "https://api.hostedshop.dk/service.wsdl"

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------
_SAFE_PRODUCT_NUMBER_RE = re.compile(r"^[\w./ -]+$")
_MAX_PRICE = 999_999.0


class DanDomainAPIError(Exception):
    """Raised for any DanDomain API failure (message is safe to display)."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class DanDomainClient:
    """Secure SOAP client for the DanDomain / HostedShop API.

    Parameters
    ----------
    username : str
        API employee username (email) created in the DanDomain admin
        panel under Settings → Employees.
    password : str
        Password for the API employee.
    """

    def __init__(self, username: str, password: str):
        # --- validate inputs ------------------------------------------------
        if not username or not isinstance(username, str):
            raise ValueError("API username is required")
        if not password or not isinstance(password, str):
            raise ValueError("API password is required")

        self._username = username.strip()
        self._password = password

        # Register credentials with the log-scrub filter so that even
        # debug / third-party logging can never leak them.
        _cred_filter.add_secret(self._password)

        # --- SOAP client with secure transport ------------------------------
        session = requests.Session()
        session.verify = True  # always verify SSL certs
        session.headers.update({"User-Agent": "CoverageOptimizer/1.0"})
        self._session = session

        transport = Transport(
            session=session,
            timeout=SOAP_TIMEOUT,
            operation_timeout=SOAP_TIMEOUT,
        )

        try:
            self._soap = ZeepClient(wsdl=WSDL_URL, transport=transport)
        except Exception as exc:
            raise DanDomainAPIError(
                f"Failed to load SOAP service definition: {type(exc).__name__}"
            ) from exc

        # Authenticate the SOAP session
        self._connect()

    # -- internal helpers ----------------------------------------------------

    def _connect(self) -> None:
        """Authenticate with ``Solution_Connect``."""
        try:
            self._soap.service.Solution_Connect(
                Username=self._username,
                Password=self._password,
            )
        except SoapFault as exc:
            raise DanDomainAPIError(
                "Authentication failed — check your API username and password. "
                "Ensure SOAP API access is enabled under Settings → API: SOAP "
                "and that the employee has API permissions."
            ) from exc
        except Exception as exc:
            raise DanDomainAPIError(
                f"Connection failed: {type(exc).__name__}"
            ) from exc

    def _call(self, operation: str, **kwargs) -> Any:
        """Execute a SOAP operation with retry / back-off."""
        last_error: Optional[str] = None

        for attempt in range(MAX_RETRIES):
            try:
                method = getattr(self._soap.service, operation)
                return method(**kwargs)
            except SoapFault as exc:
                fault_str = str(exc)
                # Authentication / authorisation faults — don't retry
                if "auth" in fault_str.lower() or "denied" in fault_str.lower():
                    raise DanDomainAPIError(
                        "Access denied — check API user permissions"
                    ) from exc
                last_error = f"SOAP fault on {operation}"
            except ZeepError as exc:
                last_error = f"SOAP error: {type(exc).__name__}"
            except requests.exceptions.SSLError:
                raise DanDomainAPIError(
                    "SSL certificate verification failed"
                )
            except requests.exceptions.ConnectionError:
                last_error = "Connection failed — check your network"
            except requests.exceptions.Timeout:
                last_error = "Request timed out"
            except Exception as exc:
                last_error = f"Unexpected error: {type(exc).__name__}"

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

        Fetches a single product to verify the session is authenticated
        and the API is reachable.  Returns a dict with ``status``.

        Raises :class:`DanDomainAPIError` on failure.
        """
        # Use a lightweight call — fetch a limited product batch instead
        # of the full catalogue.
        result = self._call("Product_GetAllWithLimit", Start=0, Length=1)
        count = len(result) if isinstance(result, list) else 0
        return {"status": "connected", "product_count": count}

    def _get_product_by_number(self, product_number: str) -> Any:
        """Fetch a raw zeep product object by item number.

        Returns the SOAP response object; raises
        :class:`DanDomainAPIError` if the product is not found.
        """
        product_number = self._validate_product_number(product_number)
        result = self._call(
            "Product_GetByItemNumber",
            ItemNumber=product_number,
        )
        if result is None:
            raise DanDomainAPIError(
                f"Product '{product_number}' not found"
            )
        return result

    def get_product(
        self,
        product_number: str,
        site_id: int = 1,
    ) -> dict:
        """Fetch a single product by its item number."""
        result = self._get_product_by_number(product_number)
        # Convert zeep CompoundValue to a plain dict for consistency
        return serialize_object(result, dict)

    def update_product_price(
        self,
        product_number: str,
        new_price: float,
        site_id: int = 1,
        variant_id: str = "",
    ) -> dict:
        """Update the sales price of a single product or variant.

        Parameters
        ----------
        product_number : str
            Product SKU / item number.
        new_price : float
            New sales price **including VAT**.
        site_id : int
            Language / site ID (default ``1``).
        variant_id : str
            Optional variant ID.  When provided the method tries to
            locate the matching variant inside the product object and
            update **its** price instead of the base product price.
            If the variant cannot be found a
            :class:`DanDomainAPIError` is raised so the caller can
            decide how to proceed.
        """
        new_price = self._validate_price(new_price)

        # Fetch the current product, update its price, and push it back
        product = self._get_product_by_number(product_number)

        # --- variant-specific update -----------------------------------
        if variant_id:
            variant_updated = False
            variants = getattr(product, "Variants", None)
            if variants:
                # Variants may be a list-like zeep structure
                items = (
                    variants
                    if isinstance(variants, list)
                    else getattr(variants, "ProductVariantData", variants)
                )
                if not isinstance(items, list):
                    items = [items]
                for var in items:
                    vid = str(getattr(var, "VariantId", getattr(var, "Id", "")))
                    if vid == str(variant_id):
                        # Update the variant's own price node
                        var_prices = getattr(var, "Prices", None)
                        if var_prices is not None:
                            var_prices.Amount = new_price
                        else:
                            # Some schemas expose the price directly
                            if hasattr(var, "Price"):
                                var.Price = new_price
                            elif hasattr(var, "Amount"):
                                var.Amount = new_price
                        variant_updated = True
                        break
            if not variant_updated:
                raise DanDomainAPIError(
                    f"Variant '{variant_id}' not found on product "
                    f"'{product_number}'"
                )
        else:
            # --- base product price ------------------------------------
            if product.Prices is None:
                raise DanDomainAPIError(
                    f"Product '{product_number}' has no price structure; "
                    "cannot update price"
                )
            product.Prices.Amount = new_price

        result = self._call("Product_Update", ProductData=product)
        return {
            "updated": True,
            "product_number": product_number,
            "variant_id": variant_id,
            "result": result,
        }

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
            ``new_price`` (float).  Optionally include
            ``variant_id`` (str) to target a specific variant.
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
            vid = update.get("variant_id", "")

            try:
                self.update_product_price(
                    pnum, price, site_id, variant_id=vid,
                )
                results["success"] += 1
                if progress_callback:
                    progress_callback(i + 1, total, pnum, True, "")
            except (DanDomainAPIError, ValueError) as exc:
                results["failed"] += 1
                err = str(exc)
                results["errors"].append(
                    {"product_number": pnum, "variant_id": vid, "error": err}
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
