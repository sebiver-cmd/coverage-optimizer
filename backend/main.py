"""FastAPI backend for SB-Optima.

Exposes a REST API that wraps the existing DanDomain SOAP client and
domain logic.

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
from backend.auth_api import router as auth_router
from backend.credentials_api import router as credentials_router
from backend.audit_api import router as audit_router
from backend.usage_api import router as usage_router
from backend.plan_api import router as plan_router
from backend.billing_api import router as billing_router
from backend.admin_api import router as admin_router
from backend.config import get_settings
from backend.db import check_db, init_engine
from backend.logging_config import setup_logging
from backend.metrics import get_metrics_router
from backend.middleware.request_id import RequestIDMiddleware
from backend.middleware.access_log import AccessLogMiddleware
from backend.middleware.security_headers import SecurityHeadersMiddleware
from backend.middleware.request_size_limit import RequestSizeLimitMiddleware


# Activate structured JSON logging as early as possible.
setup_logging()

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
app.include_router(auth_router)
app.include_router(credentials_router)
app.include_router(audit_router)
app.include_router(usage_router)
app.include_router(plan_router)
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(get_metrics_router())

# -- Observability middleware (Task 9.1) --------------------------------
# Order matters: RequestIDMiddleware must wrap AccessLogMiddleware so that
# the request_id is available when the access log line is emitted.
# Starlette processes middleware in reverse registration order (last added
# is outermost), so we add innermost first, outermost last.
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)

# -- Security middleware (Task 10.1) ------------------------------------
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

# -- CORS middleware (Task 10.1 – explicit, no wildcard) ----------------
_settings = get_settings()
_cors_origins = _settings.get_cors_origins_list()
_cors_regex = _settings.cors_allowed_origin_regex or None

if _cors_origins or _cors_regex:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_origin_regex=_cors_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/")
def root() -> dict[str, str]:
    """Public root endpoint — returns basic API identity.

    No authentication required.  Useful as a quick smoke-test to confirm
    the service is reachable (e.g. ``curl http://localhost:8000/``).
    """
    return {
        "name": app.title,
        "version": app.version,
        "docs": "/docs",
        "health": "/healthz",
    }


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Unauthenticated liveness probe for container orchestrators.

    Used by Docker / Kubernetes healthchecks to determine if the process
    is alive.  No database check — intentionally minimal so it responds
    even if Postgres is temporarily unreachable.
    """
    return {"status": "ok"}


@app.get("/health")
def health_check() -> dict[str, str]:
    """Readiness probe — returns basic status plus optional DB ping.

    No authentication required so that external monitors and simple ``curl``
    checks can verify service readiness without a token.

    * ``db: skipped`` when ``DATABASE_URL`` is not configured.
    * ``db: ok`` when the database responds to ``SELECT 1``.
    * ``db: error`` on connection failure.

    The endpoint **never** returns a non-200 status due to DB state so that
    container orchestrators can always reach the readiness check.
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
