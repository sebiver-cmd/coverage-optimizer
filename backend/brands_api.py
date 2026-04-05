"""Read-only brand-listing endpoint.

Returns the list of brands (producers) available in the DanDomain webshop,
each with its ``ProducerId`` and display name.  The IDs correspond to the
``PRODUCER_ID`` column used for filtering in the ``/optimize`` endpoint and
the Price Optimizer UI.

No data is written to the webshop — this endpoint is strictly read-only.
"""

from __future__ import annotations

import logging
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from dandomain_api import DanDomainClient, DanDomainAPIError
from backend.rbac import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class BrandItem(BaseModel):
    """A single brand / producer entry."""

    id: int
    name: str


class SortOrder(str, Enum):
    """Allowed sort directions for the brand listing."""

    asc = "asc"
    desc = "desc"


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["brands"], dependencies=[Depends(require_role("viewer"))])


@router.get("/brands", response_model=list[BrandItem])
def list_brands(
    api_username: str = Query(..., description="DanDomain API employee username (email)."),
    api_password: str = Query(..., description="DanDomain API employee password."),
    sort: SortOrder = Query(
        default=SortOrder.asc,
        description="Sort brands by name: 'asc' (default) or 'desc'.",
    ),
) -> list[BrandItem]:
    """Return all brands (producers) from the DanDomain webshop.

    Each entry contains the ``ProducerId`` (``id``) and its display name
    (``name``).  These are the same integer IDs accepted by the
    ``brand_ids`` filter in ``POST /optimize``.
    """
    try:
        with DanDomainClient(
            username=api_username,
            password=api_password,
        ) as client:
            brands_map: dict[int, str] = client.get_all_brands()

    except DanDomainAPIError as exc:
        logger.warning("DanDomain API error while fetching brands: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        logger.warning("Validation error while fetching brands: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected error while fetching brands")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error: {type(exc).__name__}",
        ) from exc

    items = [BrandItem(id=pid, name=name) for pid, name in brands_map.items()]

    reverse = sort == SortOrder.desc
    items.sort(key=lambda b: b.name.lower(), reverse=reverse)

    return items
