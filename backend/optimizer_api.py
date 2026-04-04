"""Read-only price-optimisation endpoint.

Fetches products from the DanDomain SOAP API, runs the pricing pipeline
(coverage-rate analysis, minimum-margin adjustment, beautification, and
percentage rules), and returns suggested price changes **without writing
anything back to the webshop**.

Product and variant selection
-----------------------------
The inclusion logic here is **intentionally aligned** with the Streamlit
Price Optimizer (``ui/pages/price_optimizer.py``):

*   Products are fetched via ``DanDomainClient.get_products_batch()``
    (``Product_GetAll`` with extended fields including ``Variants``).
*   Variants are expanded into separate rows by
    ``domain.pricing.api_products_to_dataframe`` — exactly the same
    helper the Dashboard / Price Optimizer UI uses.
*   Default filters match the Streamlit defaults:
    -  ``include_offline=False`` → only online products (same as the
       UI checkbox *"Only active (online) products"* defaulting to
       checked).
    -  ``include_variants=True`` → variants expanded into individual
       rows (the UI always includes variants).
    -  No brand filter by default (all brands).
*   ``summary.total_products`` equals the number of **rows** (base
    products + their variant rows) after filtering — the same number
    displayed as *"Total Products"* in the Streamlit Price Optimizer.
*   ``summary.base_products`` is the count of distinct ``PRODUCT_ID``
    values (i.e. unique base products, before variant expansion).
*   ``summary.total_rows`` is an explicit alias for ``total_products``
    for callers that prefer the unambiguous name.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dandomain_api import DanDomainClient, DanDomainAPIError
from domain.pricing import (
    VAT_RATE,
    MIN_COVERAGE_RATE,
    optimize_prices,
    calc_coverage_rate,
)
from domain.product_loader import load_products_for_optimization
from backend.cache import (
    build_caller_key,
    get_cached_products,
    set_cached_products,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class OptimizeRequest(BaseModel):
    """Parameters accepted by ``POST /optimize``."""

    api_username: str = Field(..., description="DanDomain API employee username (email).")
    api_password: str = Field(..., description="DanDomain API employee password.")
    site_id: int = Field(default=1, description="Site / shop ID (default 1).")

    # Pricing-pipeline parameters (mirror the Price Optimizer UI controls)
    price_pct: float = Field(
        default=0.0,
        ge=-50.0,
        le=200.0,
        description=(
            "Percentage adjustment applied to all new sales prices "
            "after coverage-based adjustment and beautification."
        ),
    )
    beautify_digit: int = Field(
        default=9,
        description="Round adjusted prices up to the nearest integer ending in this digit (9, 0, or 5).",
    )

    # Filters — defaults match the Streamlit Price Optimizer
    # (ui/pages/price_optimizer.py) so that the same credentials and
    # default parameters produce the same product set.
    brand_ids: Optional[list[int]] = Field(
        default=None,
        description=(
            "Filter to specific brand / producer IDs. "
            "When null or empty, all brands are included."
        ),
    )
    include_offline: bool = Field(
        default=False,
        description=(
            "When false (default), only products with an active / online "
            "status are included — matching the Streamlit UI checkbox "
            "'Only active (online) products' which defaults to checked. "
            "Set to true to include offline / inactive products as well."
        ),
    )
    include_variants: bool = Field(
        default=True,
        description=(
            "When true (default), each product variant is expanded into "
            "its own row with variant-specific pricing — matching the "
            "Streamlit Price Optimizer behaviour.  When false, only one "
            "row per base product is returned (using the base product's "
            "price and buy-price, ignoring individual variant prices)."
        ),
    )


class ProductRow(BaseModel):
    """Per-product optimisation result returned in ``OptimizeResponse.rows``."""

    product_id: str
    title: str
    item_number: str
    producer: str = ""
    buy_price: float
    current_price: float
    current_price_ex_vat: float
    current_coverage_pct: float
    suggested_price: float
    suggested_price_ex_vat: float
    suggested_coverage_pct: float
    needs_adjustment: bool
    variant_id: str = ""
    variant_types: str = ""
    ean: str = ""


class OptimizeSummary(BaseModel):
    """Aggregate statistics for the optimisation run.

    ``total_products`` is the number of **rows** (base products + variant
    rows) after filtering — the same number displayed as *"Total Products"*
    in the Streamlit Price Optimizer (``ui/pages/price_optimizer.py``).

    ``base_products`` is the number of distinct ``PRODUCT_ID`` values
    (unique base products, before variant expansion).

    ``total_rows`` is an explicit alias for ``total_products`` for
    callers that prefer the unambiguous name.
    """

    total_products: int
    base_products: int = 0
    total_rows: int = 0
    adjusted_count: int
    unchanged_count: int
    adjusted_pct: float
    avg_current_coverage_pct: float
    avg_suggested_coverage_pct: float


class OptimizeResponse(BaseModel):
    """Full response from ``POST /optimize``."""

    summary: OptimizeSummary
    rows: list[ProductRow]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/optimize", tags=["optimize"])


@router.post("/", response_model=OptimizeResponse)
def run_optimization(payload: OptimizeRequest) -> OptimizeResponse:
    """Run the read-only pricing optimisation pipeline.

    The product and variant selection logic mirrors the Streamlit Price
    Optimizer (``ui/pages/price_optimizer.py``) so that, for the same
    shop credentials and equivalent default parameters, the output
    (row count, adjusted count, etc.) is consistent with the UI.

    Pipeline
    --------
    1. Connect to the DanDomain SOAP API.
    2. Fetch all products (``get_products_batch`` → ``Product_GetAll``).
    3. Resolve brand names (``get_all_brands``).
    4. Optionally strip variant data (when ``include_variants=False``).
    5. Convert raw products to a DataFrame via
       ``api_products_to_dataframe`` — the same helper the UI uses —
       which expands each variant into its own row.
    6. Apply inclusion filters (online / brand) matching the UI defaults.
    7. Run ``optimize_prices`` with the caller's settings.
    8. Return suggested prices — **no data is written** to the webshop.
    """

    try:
        # --- Product cache (Task 3.2) ---
        caller_key = build_caller_key(payload.api_username, payload.site_id)

        # Cache is only used when include_variants=True (the default and
        # overwhelmingly common case).  The include_variants=False path is
        # rare and bypasses cache to avoid complexity.
        use_cache = payload.include_variants
        cached_records = get_cached_products(caller_key, payload.site_id) if use_cache else None

        if cached_records is not None:
            # Cache hit — rebuild DataFrame from cached records
            import pandas as pd
            from domain.product_loader import filter_products

            df = pd.DataFrame(cached_records)
            df = filter_products(
                df,
                include_offline=payload.include_offline,
                brand_ids=payload.brand_ids,
            )
            logger.info("Product cache HIT for site_id=%s", payload.site_id)
        else:
            # Cache miss — fetch via SOAP
            from domain.product_loader import fetch_products, filter_products

            with DanDomainClient(
                username=payload.api_username,
                password=payload.api_password,
            ) as client:
                unfiltered_df, _brand_id_map = fetch_products(
                    client,
                    include_variants=payload.include_variants,
                )

            # Store unfiltered DataFrame in cache when fetched with variants
            if use_cache and not unfiltered_df.empty:
                try:
                    set_cached_products(
                        caller_key,
                        payload.site_id,
                        unfiltered_df.to_dict(orient="records"),
                    )
                except Exception:
                    logger.debug("Failed to populate product cache", exc_info=True)

            df = filter_products(
                unfiltered_df,
                include_offline=payload.include_offline,
                brand_ids=payload.brand_ids,
            )
            logger.info("Product cache MISS for site_id=%s", payload.site_id)

        if df.empty:
            raise HTTPException(
                status_code=404,
                detail="No products match the specified filters.",
            )

        # --- Run pricing pipeline (read-only) ---
        final_df, adjusted_count, adjusted_mask, _import_df = optimize_prices(
            df,
            price_pct=payload.price_pct,
            beautify_digit=payload.beautify_digit,
        )

        # Attach PRODUCER column (same as UI)
        if "PRODUCER" in df.columns:
            pos = final_df.columns.get_loc("NUMBER") + 1
            final_df.insert(pos, "PRODUCER", df["PRODUCER"].values)

        # --- Summary metrics ---
        total = len(final_df)
        unchanged = total - adjusted_count

        # Count distinct base products (unique PRODUCT_ID values) in
        # the filtered set — comparable to the "base products" number
        # shown on the Streamlit Dashboard.
        if "PRODUCT_ID" in df.columns:
            base_products = int(df["PRODUCT_ID"].nunique())
        else:
            logger.warning("PRODUCT_ID column missing after api_products_to_dataframe; using total row count")
            base_products = total

        base_ex_vat = df["PRICE_NUM"] / (1 + VAT_RATE)
        base_coverage = calc_coverage_rate(
            df.assign(PRICE_EX_VAT_NUM=base_ex_vat),
            "PRICE_EX_VAT_NUM",
            "BUY_PRICE_NUM",
        )
        avg_current = float((base_coverage * 100).mean()) if total else 0.0

        new_cov_vals = (
            final_df["NEW_COVERAGE_RATE_%"]
            .str.replace("%", "", regex=False)
            .str.replace(",", ".", regex=False)
            .astype(float)
        )
        avg_suggested = float(new_cov_vals.mean()) if total else 0.0

        summary = OptimizeSummary(
            total_products=total,
            base_products=base_products,
            total_rows=total,
            adjusted_count=adjusted_count,
            unchanged_count=unchanged,
            adjusted_pct=round(adjusted_count / total * 100, 2) if total else 0.0,
            avg_current_coverage_pct=round(avg_current, 2),
            avg_suggested_coverage_pct=round(avg_suggested, 2),
        )

        # --- Build per-product rows ---
        rows: list[ProductRow] = []
        for i in range(total):
            row = final_df.iloc[i]
            rows.append(
                ProductRow(
                    product_id=str(row.get("PRODUCT_ID", "")),
                    title=str(row.get("TITLE_DK", "")),
                    item_number=str(row.get("NUMBER", "")),
                    producer=str(row.get("PRODUCER", "")),
                    buy_price=float(df.iloc[i]["BUY_PRICE_NUM"]),
                    current_price=float(df.iloc[i]["PRICE_NUM"]),
                    current_price_ex_vat=float(
                        df.iloc[i]["PRICE_NUM"] / (1 + VAT_RATE)
                    ),
                    current_coverage_pct=round(
                        float(base_coverage.iloc[i]) * 100, 2
                    ),
                    suggested_price=float(
                        row.get("NEW_PRICE", "0")
                        .replace(".", "")
                        .replace(",", ".")
                        if isinstance(row.get("NEW_PRICE"), str)
                        else 0.0
                    ),
                    suggested_price_ex_vat=float(
                        row.get("NEW_PRICE_EX_VAT", "0")
                        .replace(".", "")
                        .replace(",", ".")
                        if isinstance(row.get("NEW_PRICE_EX_VAT"), str)
                        else 0.0
                    ),
                    suggested_coverage_pct=round(float(new_cov_vals.iloc[i]), 2),
                    needs_adjustment=bool(adjusted_mask[i]),
                    variant_id=str(row.get("VARIANT_ID", "")),
                    variant_types=str(row.get("VARIANT_TYPES", "")),
                    ean=str(df.iloc[i].get("EAN", "")),
                )
            )

        return OptimizeResponse(summary=summary, rows=rows)

    except HTTPException:
        raise
    except DanDomainAPIError as exc:
        logger.warning("DanDomain API error during optimisation: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("Validation error during optimisation: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error during optimisation")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error: {type(exc).__name__}",
        ) from exc


# ---------------------------------------------------------------------------
# Barcode PDF export format routing
# ---------------------------------------------------------------------------

#: Valid barcode export format identifiers.
BARCODE_EXPORT_FORMATS: tuple[str, ...] = ("standard", "zd421_label", "fast_scan")


class BarcodePdfRow(BaseModel):
    """Single row for barcode PDF generation."""

    model_config = {"populate_by_name": True}

    SKU: str = ""
    Product_Number: str = Field("", alias="Product Number")
    Title: str = ""
    Variant_Name: str = Field("", alias="Variant Name")
    Amount: int = 1
    EAN: str = ""


class BarcodePdfRequest(BaseModel):
    """Parameters for barcode PDF generation."""

    export_format: str = Field(
        default="standard",
        description=(
            "Layout variant: 'standard' (A4 2-column grid), "
            "'zd421_label' (50 mm × 100 mm single-label pages), "
            "or 'fast_scan' (compact A4 grid for rapid scanning)."
        ),
    )
    rows: list[BarcodePdfRow] = Field(
        ...,
        description="Export rows with product/barcode data.",
    )
