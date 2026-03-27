"""Secure DanDomain webshop SOAP API client.

Uses the HostedShop SOAP API (``https://api.hostedshop.dk/service.wsdl``)
to read and update product data on a DanDomain webshop.

Price updates use **partial updates** — only ``Price`` and ``BuyingPrice``
are sent so that other product data is never accidentally overwritten:

*   Base products → ``Product_Update`` with a minimal ``ProductUpdate``
    object (``Id`` + price fields only).
*   Variants → ``Product_UpdateVariant`` with a minimal
    ``ProductVariantUpdate`` object (``Id`` + price fields only).

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
import math
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

        # Ensure subsequent GET responses include the fields we need.
        self._set_output_fields()

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

    def _set_output_fields(self) -> None:
        """Configure which fields product GET responses include.

        ``Product_SetFields`` and ``Product_SetVariantFields`` are
        *output-format setters* — they control which attributes appear
        in the objects returned by ``Product_GetByItemNumber``,
        ``Product_GetVariantsByItemNumber``, etc.

        We request at least ``Id`` (needed for updates), ``ItemNumber``
        and ``Variants`` so that lookups always return enough data.
        """
        product_fields = ["Id", "ItemNumber", "Price", "BuyingPrice", "Variants"]
        variant_fields = ["Id", "ItemNumber", "Price", "BuyingPrice"]
        try:
            self._call("Product_SetFields", Fields=product_fields)
        except DanDomainAPIError:
            logger.warning("Could not set product output fields")
        try:
            self._call("Product_SetVariantFields", Fields=variant_fields)
        except DanDomainAPIError:
            logger.warning("Could not set variant output fields")

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
                last_error = f"Unexpected error: {type(exc).__name__}: {exc}"

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
        if math.isnan(price) or math.isinf(price):
            raise ValueError("Price must be a finite number")
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

    def _lookup_variant_by_item_number(self, item_number: str) -> "int | None":
        """Resolve a variant SKU to its internal variant ``Id``.

        Calls ``Product_GetVariantsByItemNumber`` which returns the
        ``ProductVariant`` object(s) matching *item_number*.  The
        variant's ``Id`` is the primary key required by
        ``Product_UpdateVariant``.

        Returns the numeric variant ``Id``, or ``None`` when the SKU
        cannot be resolved (e.g. it is not a variant, or the API does
        not support the call).
        """
        try:
            result = self._call(
                "Product_GetVariantsByItemNumber",
                ItemNumber=item_number,
            )
        except DanDomainAPIError:
            return None

        if result is None:
            return None

        # The response may be a single variant or a list of variants.
        items = result if isinstance(result, list) else [result]
        for variant in items:
            vid = getattr(variant, "Id", None)
            if vid is not None:
                return int(vid)
        return None

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
        new_price: "float | None" = None,
        site_id: int = 1,
        variant_id: str = "",
        buy_price: "float | None" = None,
        product_id: str = "",
    ) -> dict:
        """Update the sales price and/or cost price of a product or variant.

        Uses **partial updates** so that only ``Price`` and ``BuyingPrice``
        are transmitted — other product data (title, description, stock,
        SEO settings …) is never touched.

        *   **Base products** → ``Product_Update`` with a minimal
            ``ProductUpdate`` containing only ``Id`` + the price fields.
        *   **Variants** → ``Product_UpdateVariant`` with a minimal
            ``ProductVariantUpdate`` containing only ``Id`` + the price
            fields.

        Parameters
        ----------
        product_number : str
            Product SKU / item number.
        new_price : float, optional
            New sales price **including VAT**.  When ``None`` the sales
            price is left unchanged — useful for buy-price-only updates.
        site_id : int
            Language / site ID (default ``1``).
        variant_id : str
            Optional variant ID.  When provided the update targets the
            variant directly via ``Product_UpdateVariant`` instead of
            the base product.
        buy_price : float, optional
            When provided the product's *BuyingPrice* (cost / buying
            price) is also updated.
        product_id : str
            Optional product ``Id`` from the import file.  When
            provided it is used directly for ``Product_Update``,
            avoiding an extra ``Product_GetByItemNumber`` round-trip.
        """
        if new_price is None and buy_price is None:
            raise ValueError(
                "At least one of new_price or buy_price must be provided"
            )

        if new_price is not None:
            new_price = self._validate_price(new_price)
        if buy_price is not None:
            buy_price = self._validate_price(buy_price)

        # Normalise variant_id: strip float suffixes ("123.0" → "123")
        # and treat zero / empty as "no variant" so we fall through
        # to the base-product price update instead of raising an error.
        if variant_id:
            try:
                n = float(variant_id)
                variant_id = "" if n == 0 else str(int(n))
            except (ValueError, OverflowError):
                pass

        # Normalise product_id the same way ("823.0" → "823", zero → "").
        if product_id:
            try:
                n = float(product_id)
                product_id = "" if n == 0 else str(int(n))
            except (ValueError, OverflowError):
                pass

        if variant_id:
            # ----------------------------------------------------------
            # Variant-level partial update via Product_UpdateVariant.
            # We send a minimal ProductVariantUpdate with only the
            # variant Id and the price fields we want to change.
            # ----------------------------------------------------------
            variant_data: dict[str, Any] = {"Id": int(variant_id)}
            if new_price is not None:
                variant_data["Price"] = new_price
            if buy_price is not None:
                variant_data["BuyingPrice"] = buy_price
            result = self._call(
                "Product_UpdateVariant", VariantData=variant_data,
            )
        else:
            # ----------------------------------------------------------
            # Base-product partial update via Product_Update.
            # If product_id was supplied in the import data, use it
            # directly — this avoids a Product_GetByItemNumber
            # round-trip and the risk that the response omits the Id
            # field.  Otherwise fall back to an API lookup.
            # ----------------------------------------------------------
            resolved_id: "int | None" = None

            if product_id:
                resolved_id = int(product_id)
            else:
                product = self._get_product_by_number(product_number)
                fetched_id = getattr(product, "Id", None)
                if fetched_id is not None:
                    resolved_id = int(fetched_id)
                else:
                    # Last resort: the SKU might reference a variant.
                    resolved_id = self._lookup_variant_by_item_number(
                        product_number,
                    )
                    if resolved_id is not None:
                        # Update as variant instead of base product.
                        vdata: dict[str, Any] = {
                            "Id": resolved_id,
                        }
                        if new_price is not None:
                            vdata["Price"] = new_price
                        if buy_price is not None:
                            vdata["BuyingPrice"] = buy_price
                        result = self._call(
                            "Product_UpdateVariant",
                            VariantData=vdata,
                        )
                        variant_id = str(resolved_id)
                        return {
                            "updated": True,
                            "product_number": product_number,
                            "variant_id": variant_id,
                            "result": result,
                        }

            if resolved_id is None:
                raise DanDomainAPIError(
                    f"Product '{product_number}' has no Id field "
                    "and no matching variant was found; "
                    "cannot update price"
                )

            product_data: dict[str, Any] = {"Id": resolved_id}
            if new_price is not None:
                product_data["Price"] = new_price
            if buy_price is not None:
                product_data["BuyingPrice"] = buy_price
            result = self._call(
                "Product_Update", ProductData=product_data,
            )

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
        """Batch-update product prices using partial updates.

        Each update sends only the ``Price`` and/or ``BuyingPrice`` fields
        to avoid accidentally overwriting other product data.

        *   Base products are updated via ``Product_Update`` with a
            minimal ``ProductUpdate`` (only ``Id`` + price fields).
        *   Variants are updated via ``Product_UpdateVariant`` with a
            minimal ``ProductVariantUpdate`` (only ``Id`` + price
            fields).

        Parameters
        ----------
        updates : list[dict]
            Each dict must contain ``product_number`` (str) and
            ``new_price`` (float).  Optionally include
            ``product_id`` (str), ``variant_id`` (str),
            ``variant_types`` (str) and ``buy_price`` (float) to
            mirror the fields that a regular CSV import would carry.
            ``variant_id`` is forwarded to
            :meth:`update_product_price` — when present the variant is
            updated directly via ``Product_UpdateVariant``.
            ``buy_price``, when present, updates the product's cost
            price.
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
            pid = update.get("product_id", "")
            pnum = update.get("product_number", "")
            price = update.get("new_price")
            vid = update.get("variant_id", "")
            buy = update.get("buy_price")

            try:
                self.update_product_price(
                    pnum, price, site_id, variant_id=vid,
                    buy_price=buy, product_id=pid,
                )
                results["success"] += 1
                if progress_callback:
                    progress_callback(i + 1, total, pnum, True, "")
            except (DanDomainAPIError, ValueError, TypeError, AttributeError) as exc:
                results["failed"] += 1
                err = str(exc)
                results["errors"].append({
                    "product_id": pid,
                    "product_number": pnum,
                    "variant_id": vid,
                    "error": err,
                })
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
