"""Read-only price-optimisation endpoint.

Fetches products from the DanDomain SOAP API, runs the pricing pipeline
(coverage-rate analysis, minimum-margin adjustment, beautification, and
percentage rules), and returns suggested price changes **without writing
anything back to the webshop**.
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
    api_products_to_dataframe,
    _build_brand_id_map,
    optimize_prices,
    calc_coverage_rate,
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

    # Filters
    brand_ids: Optional[list[int]] = Field(
        default=None,
        description=(
            "Filter to specific brand / producer IDs. "
            "When null or empty, all brands are included."
        ),
    )
    only_online: bool = Field(
        default=True,
        description="When true, only products with an active / online status are included.",
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


class OptimizeSummary(BaseModel):
    """Aggregate statistics for the optimisation run."""

    total_products: int
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

    1. Connects to the DanDomain SOAP API.
    2. Fetches all products (``get_products_batch``).
    3. Resolves brand names (``get_all_brands``).
    4. Converts raw products to a DataFrame.
    5. Applies optional brand / online filters.
    6. Runs ``optimize_prices`` with the caller's settings.
    7. Returns suggested prices — **no data is written** to the webshop.
    """

    try:
        with DanDomainClient(
            username=payload.api_username,
            password=payload.api_password,
        ) as client:
            raw_products = client.get_products_batch()

            # Resolve brand / producer names
            producer_ids: list[int] = []
            for p in raw_products:
                pid = p.get("ProducerId")
                if pid is not None:
                    try:
                        producer_ids.append(int(pid))
                    except (ValueError, TypeError):
                        pass

            brands_map = client.get_all_brands(producer_ids=producer_ids)

        # Hydrate Producer on each product using the brands map
        if brands_map:
            for p in raw_products:
                pid = p.get("ProducerId")
                if pid is not None:
                    try:
                        brand_name = brands_map.get(int(pid))
                        if brand_name:
                            p["Producer"] = brand_name
                    except (ValueError, TypeError):
                        pass

        if not raw_products:
            raise HTTPException(status_code=404, detail="No products found in the webshop.")

        # Build DataFrame (same helper the UI uses)
        df = api_products_to_dataframe(raw_products)
        df = df.sort_values("PRODUCER", key=lambda s: s.str.lower()).reset_index(drop=True)

        # --- Apply filters ---
        if payload.only_online and "ONLINE" in df.columns:
            df = df[df["ONLINE"]].reset_index(drop=True)

        if payload.brand_ids:
            df = df[df["PRODUCER_ID"].isin(payload.brand_ids)].reset_index(drop=True)

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
