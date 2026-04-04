"""FastAPI backend for SB-Optima.

Exposes a REST API that wraps the existing DanDomain SOAP client and
domain logic.  Runs independently of the Streamlit frontend so neither
process interferes with the other.

Start with::

    uvicorn backend.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from dandomain_api import DanDomainClient, DanDomainAPIError
from backend.optimizer_api import router as optimizer_router
from backend.brands_api import router as brands_router
from backend.apply_prices_api import router as apply_prices_router
from backend.apply_real_api import router as apply_real_router
from backend.catalog_api import router as catalog_router
from backend.jobs_api import router as jobs_router
from backend.tenant_api import router as tenant_router
from backend.config import get_settings
from backend.db import check_db, init_engine

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
# Lifespan — lazy DB initialisation
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Initialise the database engine and log safe configuration."""
    settings = get_settings()
    logger.info("Settings loaded (env=%s): %s", settings.sboptima_env, settings.to_safe_dict())

    engine = init_engine()
    if engine is not None:
        logger.info("Database engine initialised (pool_pre_ping=True)")
    else:
        logger.info("DATABASE_URL not set — running without a database")
    yield


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SB-Optima API",
    description="REST gateway for the SB-Optima price-optimisation platform.",
    version="0.1.0",
    lifespan=_lifespan,
)

app.include_router(optimizer_router)
app.include_router(brands_router)
app.include_router(apply_prices_router)
app.include_router(apply_real_router)
app.include_router(catalog_router)
app.include_router(jobs_router)
app.include_router(tenant_router)

# -- CORS middleware (only when origins are configured) ------------------
_cors_origins = get_settings().get_cors_origins_list()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health_check() -> dict[str, str]:
    """Liveness probe — returns basic status plus optional DB ping.

    * ``db: skipped`` when ``DATABASE_URL`` is not configured.
    * ``db: ok`` when the database responds to ``SELECT 1``.
    * ``db: error`` on connection failure.

    The endpoint **never** returns a non-200 status due to DB state so that
    container orchestrators can always reach the liveness check.
    """
    return {"status": "ok", "db": check_db()}


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
