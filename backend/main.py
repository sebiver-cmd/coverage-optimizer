"""FastAPI backend for SB-Optima.

Exposes a REST API that wraps the existing DanDomain SOAP client and
domain logic.  Runs independently of the Streamlit frontend so neither
process interferes with the other.

Start with::

    uvicorn backend.main:app --reload
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from pydantic import BaseModel

from dandomain_api import DanDomainClient, DanDomainAPIError
from backend.optimizer_api import router as optimizer_router
from backend.brands_api import router as brands_router
from backend.apply_prices_api import router as apply_prices_router
from backend.apply_real_api import router as apply_real_router
from backend.catalog_api import router as catalog_router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ConnectionRequest(BaseModel):
    """Credentials required to test the DanDomain SOAP connection."""

    api_username: str
    api_password: str
    site_id: int = 1


class ConnectionResponse(BaseModel):
    """Result of a connection test against the DanDomain API."""

    status: str
    product_count: int | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SB-Optima API",
    description="REST gateway for the SB-Optima price-optimisation platform.",
    version="0.1.0",
)

app.include_router(optimizer_router)
app.include_router(brands_router)
app.include_router(apply_prices_router)
app.include_router(apply_real_router)
app.include_router(catalog_router)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe — returns immediately, no external calls."""
    return {"status": "ok"}


@app.post("/test-connection", response_model=ConnectionResponse)
def test_connection(req: ConnectionRequest) -> ConnectionResponse:
    """Verify DanDomain API credentials.

    Instantiates a :class:`DanDomainClient`, calls
    :meth:`~DanDomainClient.test_connection`, and returns the result.
    """
    try:
        client = DanDomainClient(
            username=req.api_username,
            password=req.api_password,
        )
        result = client.test_connection()
        return ConnectionResponse(
            status=result.get("status", "connected"),
            product_count=result.get("product_count"),
        )
    except DanDomainAPIError as exc:
        logger.warning("DanDomain connection test failed: %s", exc)
        return ConnectionResponse(status="error", error=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during connection test")
        return ConnectionResponse(status="error", error=str(exc))
