"""Read-only product catalogue endpoint.

Fetches products from the DanDomain SOAP API, expands variants into
individual rows, and optionally enriches variant-level fields
(``VARIANT_ITEMNUMBER``, ``VARIANT_TITLE``, ``VARIANT_EAN``) via
``Product_GetVariantsByItemNumber`` (see HostedShop docs:
*"Returns the ProductVariant(s) with the indicated ItemNumber"*,
param ``string $ItemNumber``, returns ``ProductVariant[]``).

This endpoint is **read-only** — it never writes to the webshop.

The frontend should call this endpoint instead of fetching products
directly via the SOAP client.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dandomain_api import DanDomainClient, DanDomainAPIError
from domain.product_loader import fetch_products, filter_products, enrich_variants
from backend.cache import (
    build_caller_key,
    get_cached_enriched_products,
    set_cached_enriched_products,
)
from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CatalogRequest(BaseModel):
    """Parameters accepted by ``POST /catalog/products``."""

    api_username: str = Field(
        default="", description="DanDomain API employee username (email)."
    )
    api_password: str = Field(
        default="", description="DanDomain API employee password."
    )
    site_id: int = Field(default=1, description="Site / shop ID (default 1).")
    credential_id: UUID | None = Field(
        default=None,
        description="UUID of a stored vault credential (used when auth is enabled).",
    )
    include_offline: bool = Field(
        default=False,
        description=(
            "When false (default), only online products are returned. "
            "Set to true to include offline / inactive products."
        ),
    )
    include_variants: bool = Field(
        default=True,
        description=(
            "When true (default), each product variant is expanded into "
            "its own row.  When false, only one row per base product."
        ),
    )
    brand_ids: list[int] | None = Field(
        default=None,
        description=(
            "Filter to specific brand / producer IDs.  "
            "When null or empty, all brands are included."
        ),
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/catalog", tags=["catalog"], dependencies=[Depends(require_role("viewer"))])


@router.post("/products")
def get_catalog_products(payload: CatalogRequest) -> list[dict]:
    """Return variant-enriched product rows from the DanDomain catalogue.

    Pipeline
    --------
    1. Connect to the DanDomain SOAP API (read-only).
    2. Fetch all products via ``get_products_batch`` → ``Product_GetAll``.
    3. Resolve brand names (``get_all_brands``).
    4. Expand variants into individual rows via ``api_products_to_dataframe``.
    5. Apply inclusion filters (online/offline, brand).
    6. Optionally enrich variant-level fields using
       ``Product_GetVariantsByItemNumber`` (HostedShop docs:
       *"Returns the ProductVariant(s) with the indicated ItemNumber"*,
       param ``string $ItemNumber``, returns ``ProductVariant[]``).
    7. Return rows as a list of dicts.

    **Safety**: read-only, timeouts & retries reuse existing patterns
    in ``dandomain_api.py``.  Partial enrichment failures are tolerated.
    Credentials are never logged.
    """
    try:
        # --- Product cache (Task 3.2) ---
        caller_key = build_caller_key(payload.api_username, payload.site_id)

        # Only use enriched-product cache when variants are included (default).
        use_cache = payload.include_variants
        cached_rows = get_cached_enriched_products(caller_key, payload.site_id) if use_cache else None

        if cached_rows is not None:
            # Cache hit — apply filters and return
            import pandas as pd

            df = pd.DataFrame(cached_rows)
            df = filter_products(
                df,
                include_offline=payload.include_offline,
                brand_ids=payload.brand_ids,
            )
            if df.empty:
                return []
            logger.info("Enriched product cache HIT for site_id=%s", payload.site_id)
        else:
            # Cache miss — full SOAP fetch + enrichment pipeline
            with DanDomainClient(
                username=payload.api_username,
                password=payload.api_password,
                caller_key=caller_key,
            ) as client:
                df, _brand_id_map = fetch_products(
                    client,
                    include_variants=payload.include_variants,
                )

                if df.empty:
                    return []

                # --- Variant enrichment (on full unfiltered set) ---
                if payload.include_variants:
                    try:
                        df = enrich_variants(df, client)
                    except Exception:
                        logger.warning(
                            "Variant enrichment encountered an error; "
                            "returning best-effort data",
                            exc_info=True,
                        )

            # Store enriched unfiltered DataFrame in cache
            if use_cache and not df.empty:
                try:
                    set_cached_enriched_products(
                        caller_key,
                        payload.site_id,
                        df.to_dict(orient="records"),
                    )
                except Exception:
                    logger.debug("Failed to populate enriched product cache", exc_info=True)

            # Apply filters after caching
            df = filter_products(
                df,
                include_offline=payload.include_offline,
                brand_ids=payload.brand_ids,
            )

            if df.empty:
                return []

            logger.info("Enriched product cache MISS for site_id=%s", payload.site_id)

        # Ensure VARIANT_ITEMNUMBER column exists even if empty
        for col in ("VARIANT_ITEMNUMBER", "VARIANT_TITLE", "VARIANT_EAN"):
            if col not in df.columns:
                df[col] = ""

        # Select and rename columns for the response
        output_cols = [
            "NUMBER", "TITLE_DK", "VARIANT_ID", "VARIANT_TYPES", "EAN",
            "VARIANT_ITEMNUMBER", "VARIANT_TITLE", "VARIANT_EAN",
            "PRODUCER", "PRODUCER_ID", "ONLINE",
            "PRODUCT_ID", "BUY_PRICE_NUM", "PRICE_NUM",
            "BUY_PRICE", "PRICE",
        ]

        # Only include columns that exist in the DataFrame
        available_cols = [c for c in output_cols if c in df.columns]
        result_df = df[available_cols].copy()

        # Fill NaN with empty strings for string columns, 0 for numeric
        for col in result_df.columns:
            if result_df[col].dtype == object:
                result_df[col] = result_df[col].fillna("")
            else:
                result_df[col] = result_df[col].fillna(0)

        return result_df.to_dict(orient="records")

    except HTTPException:
        raise
    except DanDomainAPIError as exc:
        logger.warning("DanDomain API error during catalog fetch: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("Validation error during catalog fetch: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error during catalog fetch")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error: {type(exc).__name__}",
        ) from exc
