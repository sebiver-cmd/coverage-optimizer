# ADR 0001 — SaaS Architecture for SB-Optima

| Field       | Value                                              |
|-------------|----------------------------------------------------|
| **Status**  | Accepted                                           |
| **Date**    | 2026-04-04                                         |
| **Deciders**| SB-Optima team                                     |
| **Scope**   | Full-stack SaaS migration of Coverage Optimizer    |

## Context

SB-Optima / Coverage Optimizer is currently a single-tenant tool composed of a
Streamlit UI, a FastAPI backend, and domain logic for DanDomain price
optimisation.  The goal is to migrate it to a **multi-tenant, paid SaaS web
application** while preserving all existing safety guardrails (batch limits,
per-row margin checks, idempotency, audit logging, environment gating).

Key problems the migration must solve:

1. **Two write paths** — the Streamlit UI can bypass backend guardrails and write
   directly to HostedShop via SOAP.  This must be consolidated.
2. **No multi-tenancy** — all endpoints are open; no auth, no tenant scoping.
3. **File-based persistence** — batch manifests and audit logs live on the local
   filesystem, which is not safe for multi-tenant operation.
4. **Credentials in request payloads** — API credentials travel in every HTTP
   request body rather than being stored server-side.
5. **Long-running requests** — optimisation can block HTTP for minutes.
6. **No caching** — every request fetches products from SOAP anew.

## Decision

### Stack Choices

| Concern              | Decision                                                      |
|----------------------|---------------------------------------------------------------|
| **API server**       | **FastAPI** — keep existing `backend/main.py`                 |
| **Database**         | **PostgreSQL 16** + **SQLAlchemy** (ORM) + **Alembic** (migrations) |
| **Cache / queue**    | **Redis 7**                                                   |
| **Background jobs**  | **Arq** (lightweight, async-native); revisit Celery only if needed |
| **Frontend (interim)** | Keep **Streamlit** as thin HTTP client during migration     |
| **Frontend (target)** | **Next.js / React** with TypeScript (deferred to Phase 8)   |
| **Authentication**   | **Custom JWT** + bcrypt passwords + email verification        |
| **Billing**          | **Stripe** subscriptions (deferred to Phase 7)               |
| **Secret encryption**| **Fernet** envelope encryption (master key from env var)     |
| **Hosting domains**  | `sboptima.dk` (primary), `sboptima.com` (redirect/English)   |

#### Auth Provider Rationale

Custom JWT is chosen over external providers (Clerk, Auth0) for:

- Full control over user/tenant data model.
- No vendor lock-in or per-MAU cost at early stage.
- Simpler local development and testing.

**Alternative noted**: If scaling to >10 000 users or needing SSO/SAML quickly,
re-evaluate Clerk or Auth0.  The JWT middleware (`backend/auth.py`) is designed
as a pluggable dependency so swapping the provider requires changing only the
token-verification function.

#### Encryption Upgrade Path

Fernet (symmetric, AES-128 + HMAC-SHA256) is adequate for MVP.  The
`tenant_credentials` table will include a `key_version` column to support
future multi-key rotation.  When compliance or scale demands it, upgrade to
AWS KMS / GCP KMS envelope encryption — only `backend/vault.py` changes.

---

## Component Diagram

```
                    ┌───────────────────────────────────┐
                    │   Web UI                          │
                    │   Streamlit (interim)              │
                    │   Next.js  (target — Phase 8)     │
                    │   Domains: sboptima.dk / .com      │
                    └──────────────┬────────────────────┘
                                   │ HTTPS (JSON)
                    ┌──────────────▼────────────────────┐
                    │   FastAPI API Server               │
                    │   backend/main.py                  │
                    │                                    │
                    │   Middleware layers:                │
                    │   ├─ CORS                          │
                    │   ├─ JWT auth (Phase 4)            │
                    │   ├─ RBAC (Phase 4)                │
                    │   ├─ Tenant scoping                │
                    │   └─ Billing gate (Phase 7)        │
                    │                                    │
                    │   Routers:                         │
                    │   ├─ /health                       │
                    │   ├─ /auth/*                       │
                    │   ├─ /brands                       │
                    │   ├─ /optimize                     │
                    │   ├─ /catalog/products             │
                    │   ├─ /apply-prices/*               │
                    │   ├─ /jobs/*                       │
                    │   ├─ /tenant/*                     │
                    │   └─ /test-connection              │
                    └──┬─────────┬──────────┬───────────┘
                       │         │          │
          ┌────────────▼──┐  ┌──▼───────┐ ┌▼──────────────┐
          │  PostgreSQL 16│  │ Redis 7  │ │ Arq Worker    │
          │               │  │          │ │               │
          │  Tables:      │  │ * Job    │ │ * optimize    │
          │  * tenants    │  │   queues │ │ * apply       │
          │  * users      │  │ * TTL    │ │ * enrichment  │
          │  * tenant_    │  │   product│ │               │
          │    credentials│  │   cache  │ │ Reads vault   │
          │  * apply_     │  │          │ │ from Postgres │
          │    batches    │  │          │ │               │
          │  * audit_log  │  │          │ │               │
          │  * usage_     │  │          │ │               │
          │    events     │  │          │ │               │
          └───────────────┘  └──────────┘ └───────┬───────┘
                                                  │
                                       ┌──────────▼────────┐
                                       │ DanDomainClient   │
                                       │ dandomain_api.py  │
                                       │ (sole SOAP gate)  │
                                       └──────────┬────────┘
                                                  │ HTTPS/SOAP
                                       ┌──────────▼────────┐
                                       │ HostedShop SOAP   │
                                       │ (external)        │
                                       └───────────────────┘
```

---

## Data Flow

### Read path (product fetch / optimisation)

```
Client → FastAPI (JWT auth + tenant scope)
  → Check Redis cache (key: products:{tenant_id}:{site_id})
    → Cache HIT  → return cached products
    → Cache MISS → DanDomainClient.Product_GetAll()
                 → Store in Redis (TTL 15 min)
                 → domain/pricing.py compute
                 → Return result
```

### Write path (apply prices)

```
Client → POST /apply-prices/dry-run (operator+)
  → Backend computes change set via domain/pricing.py
  → Persist batch manifest to Postgres (apply_batches table)
  → Return {batch_id, changes, summary}

Client → POST /apply-prices/apply (admin+)
  → Load manifest from Postgres
  → Enforce guardrails (apply_constants.py):
      * MAX_APPLY_ROWS (100)
      * MAX_CHANGE_PCT (30%)
      * Positive finite prices
      * No selling below buy price
      * Min coverage rate check
      * Environment gating (SB_OPTIMA_ENABLE_APPLY)
      * confirm: true required
      * Idempotency (status != 'applied')
  → DanDomainClient.update_prices_batch()
  → Set batch status = 'applied' in Postgres
  → Write audit_log row (tenant_id, user_id, batch_id, counts)
  → Invalidate Redis product cache for tenant
  → Return {batch_id, applied_count, failed, timestamps}
```

### Background job path (async optimisation)

```
Client → POST /jobs/optimize (operator+)
  → Enqueue Arq job (Redis)
  → Return {job_id} immediately

Arq Worker picks up job:
  → Read credentials from vault (Postgres)
  → Fetch products (cache-first, then SOAP)
  → Run domain/pricing.py
  → Store result in Redis (keyed by job_id, TTL 1 hr)

Client → GET /jobs/{job_id}
  → Return {status: pending|running|completed, result?}
```

---

## Security Model

### Authentication

- **Signup**: `POST /auth/signup` → creates `tenant` + `user` (role: owner)
  → returns JWT.
- **Login**: `POST /auth/login` → verifies bcrypt hash → returns JWT
  (access + refresh tokens).
- **Token refresh**: `POST /auth/refresh` → validates refresh token → returns
  new access token.
- **JWT payload**: `{sub: user_id, tenant_id, role, exp, iat}`.
- **JWT secret**: `JWT_SECRET` env var; RS256 upgrade path noted.

### RBAC (Role-Based Access Control)

| Role       | Permissions                                                    |
|------------|----------------------------------------------------------------|
| `viewer`   | `/health`, `/brands`, `/optimize`, `/catalog/products`, batch read, status |
| `operator` | All viewer + `/apply-prices/dry-run`, `/jobs/optimize`        |
| `admin`    | All operator + `/apply-prices/apply`, credential mgmt, tenant settings |
| `owner`    | All admin + billing, user management, delete tenant           |

Implementation: `require_role(min_role)` FastAPI dependency in `backend/rbac.py`.

### Migration Period Auth Bypass

`SBOPTIMA_AUTH_REQUIRED=false` env var disables JWT/RBAC enforcement for
local development and during the migration period while Streamlit is still
the primary UI.

### Credential Handling

- DanDomain SOAP credentials stored in `tenant_credentials` table, encrypted
  with Fernet (`ENCRYPTION_KEY` env var).
- Backend reads credentials from vault by `tenant_id`; credentials never appear
  in request payloads (after Phase 5).
- Backward compatibility: during migration, credentials in request bodies still
  accepted with a deprecation warning logged.
- `GET /tenant/credentials/status` returns `{stored: bool, updated_at}` — never
  raw secrets.

### Secrets Management

| Secret             | Storage                          | Access                    |
|--------------------|----------------------------------|---------------------------|
| `JWT_SECRET`       | Environment variable             | FastAPI auth middleware    |
| `ENCRYPTION_KEY`   | Environment variable             | `backend/vault.py`        |
| `DATABASE_URL`     | Environment variable             | `backend/db.py`           |
| `REDIS_URL`        | Environment variable             | `backend/cache.py`, Arq   |
| SOAP credentials   | Postgres (Fernet-encrypted)      | `backend/vault.py`        |
| `OPENAI_API_KEY`   | Environment variable (shared)    | `domain/invoice_ean.py`, `domain/supplier.py` |
| Stripe keys        | Environment variable (Phase 7)   | `backend/billing.py`      |

**No secrets in logs**: all logging must redact credentials.  The existing
`DanDomainClient` already scrubs credentials from error messages.

---

## Tenant Isolation Strategy

### Database Isolation

- **Shared database, tenant-scoped rows** (not schema-per-tenant).
- Every tenant-owned table includes a `tenant_id` column (UUID, FK to `tenants`).
- All queries are scoped by `tenant_id` from `request.state.tenant_id` (set by
  JWT middleware).
- SQLAlchemy event hooks or query filters enforce tenant scoping at the ORM
  level to prevent cross-tenant data leaks.

### Cache Isolation

- Redis cache keys are prefixed with `tenant_id`:
  `products:{tenant_id}:{site_id}`, `job:{tenant_id}:{job_id}`.
- No tenant can read or invalidate another tenant's cache.

### SOAP Isolation

- Each tenant has its own DanDomain credentials (stored in vault).
- `DanDomainClient` is instantiated per-request with tenant-specific credentials.
- SOAP rate limiting is per-tenant (token bucket in `backend/soap_limiter.py`).

### LLM Isolation

- MVP: shared `OPENAI_API_KEY` (server-level env var).
- Usage tracked per tenant: `{tenant_id, tokens_used, model}` logged on every
  LLM call.
- Future: per-tenant keys stored in vault; `OPENAI_MONTHLY_TOKEN_LIMIT` env var
  for cost control.

---

## Database Schema (Target)

```sql
-- Phase 4
CREATE TABLE tenants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    stripe_customer_id TEXT,               -- Phase 7
    plan            TEXT DEFAULT 'free',   -- Phase 7
    plan_status     TEXT DEFAULT 'active', -- Phase 7
    status          TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    email           TEXT NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, email)
);

-- Phase 5
CREATE TABLE tenant_credentials (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id) UNIQUE,
    encrypted_blob  BYTEA NOT NULL,
    key_version     INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 6
CREATE TABLE apply_batches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    optimize_payload JSONB,
    product_numbers JSONB,
    changes         JSONB NOT NULL,
    summary         JSONB,
    status          TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'applied', 'failed'))
);

CREATE TABLE audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    user_id         UUID REFERENCES users(id),
    batch_id        UUID REFERENCES apply_batches(id),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    action          TEXT NOT NULL,
    total_rows      INTEGER,
    applied_count   INTEGER,
    skipped_count   INTEGER,
    failed_count    INTEGER,
    metadata        JSONB
);

-- Phase 7
CREATE TABLE usage_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES tenants(id),
    event_type      TEXT NOT NULL,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB
);
```

---

## CORS and Cookie Configuration

### Production Domains

| Domain            | Purpose                                           |
|-------------------|---------------------------------------------------|
| `sboptima.dk`     | Primary web app + API (`api.sboptima.dk`)         |
| `sboptima.com`    | Redirect to `sboptima.dk` (or English locale)     |

### CORS Allowed Origins

```python
CORS_ORIGINS = [
    "https://sboptima.dk",
    "https://www.sboptima.dk",
    "https://sboptima.com",
    "https://www.sboptima.com",
    "http://localhost:3000",       # Next.js dev
    "http://localhost:8501",       # Streamlit dev
]
```

### Cookie Configuration

- **Cookie domain**: `.sboptima.dk` — allows cookies to be shared between
  `sboptima.dk` and `api.sboptima.dk`.
- **Secure flag**: `True` in production (HTTPS only).
- **HttpOnly**: `True` for refresh tokens.
- **SameSite**: `Lax` (allows top-level navigation).
- **CSP / HSTS**: enforce once DNS + SSL are configured (not in code scope).

---

## Deployment Plan

### Local Development

```
docker compose -f infra/docker-compose.yml up
```

Services:
- **api**: FastAPI on port 8000 (Python container, `infra/Dockerfile.api`)
- **postgres**: PostgreSQL 16 on port 5432
- **redis**: Redis 7 on port 6379
- **worker**: Arq worker (same Python image, different entrypoint)

Streamlit runs outside Docker for rapid iteration:
```
streamlit run app.py
```

### Environment Variables

All documented in `.env.example`:

| Variable                    | Required | Default          | Description                           |
|-----------------------------|----------|------------------|---------------------------------------|
| `DATABASE_URL`              | Yes      | —                | PostgreSQL connection string          |
| `REDIS_URL`                 | Yes      | —                | Redis connection string               |
| `JWT_SECRET`                | Yes      | —                | Secret for JWT signing                |
| `ENCRYPTION_KEY`            | Yes      | —                | Fernet key for credential encryption  |
| `SB_OPTIMA_ENABLE_APPLY`   | No       | `false`          | Gate for real-apply endpoint          |
| `SBOPTIMA_AUTH_REQUIRED`    | No       | `true`           | Disable auth for local dev            |
| `SBOPTIMA_ENV`              | No       | `dev`            | Environment name (dev/staging/prod)   |
| `OPENAI_API_KEY`            | No       | —                | LLM key for invoice/supplier parsing  |
| `OPENAI_BASE_URL`           | No       | —                | Optional LLM base URL override        |
| `CORS_ORIGINS`              | No       | see above        | Comma-separated allowed origins       |
| `SOAP_MAX_CONCURRENT`       | No       | `3`              | Max concurrent SOAP calls per tenant  |
| `SOAP_CALL_DELAY_S`         | No       | `0.2`            | Delay between SOAP calls              |
| `PRODUCT_CACHE_TTL_S`       | No       | `900`            | Product cache TTL in seconds          |
| `OPENAI_MONTHLY_TOKEN_LIMIT`| No      | —                | Per-tenant LLM token budget           |
| `STRIPE_SECRET_KEY`         | No       | —                | Phase 7                               |
| `STRIPE_WEBHOOK_SECRET`     | No       | —                | Phase 7                               |

### Staging / Production

Deployment target TBD (likely container-based: AWS ECS, Railway, or Fly.io).
Key requirements:

- Managed PostgreSQL (e.g., AWS RDS, Supabase, Neon).
- Managed Redis (e.g., AWS ElastiCache, Upstash).
- Outbound HTTPS access for HostedShop SOAP WSDL + calls.
- Environment variables injected via secret manager (not `.env` files).
- Health check on `GET /health` for load balancer probes.

---

## Migration Strategy

The migration is **incremental** — each phase leaves existing tests green and
the Streamlit UI functional:

1. **Phase 0**: This ADR — locks decisions.
2. **Phase 1**: Consolidate write paths (backend is sole SOAP gateway).
3. **Phase 2**: Docker Compose + Postgres + Alembic + config module.
4. **Phase 3**: Background jobs (Arq) + Redis caching + SOAP rate limiting.
5. **Phase 4**: Multi-tenancy (tenant/user models, JWT auth, RBAC).
6. **Phase 5**: Credential vault (Fernet encryption, remove creds from payloads).
7. **Phase 6**: DB migration of batches + audit (preserve all guardrails).
8. **Phase 7**: Stripe billing + usage metering.
9. **Phase 8**: Next.js frontend + deprecate Streamlit.

### Non-Goals (First Month)

- No monorepo restructure — keep flat layout; add `frontend/` alongside
  `backend/` when needed.
- No DNS/SSL configuration for sboptima.dk.
- No KMS-based encryption (Fernet is sufficient for MVP).
- No per-tenant LLM keys.
- No CI/CD pipeline (defer until Docker Compose is stable).

---

## Consequences

### Positive

- Clear separation: backend is sole gateway to HostedShop SOAP.
- All existing safety guardrails preserved through every migration phase.
- Incremental approach minimises risk of breaking changes.
- Standard stack (FastAPI + Postgres + Redis) with good ecosystem support.
- Arq is lightweight and async-native, fitting the existing FastAPI codebase.

### Negative

- Custom JWT adds auth maintenance burden vs. hosted provider.
- Fernet encryption requires careful key management (rotation procedure needed).
- Shared-database multi-tenancy requires disciplined tenant scoping in every
  query.

### Risks and Mitigations

| Risk                                    | Mitigation                                    |
|-----------------------------------------|-----------------------------------------------|
| Auth provider bikeshedding              | Default to custom JWT; note alternative       |
| Breaking Streamlit during migration     | `SBOPTIMA_AUTH_REQUIRED=false` bypass flag     |
| Cross-tenant data leaks                 | ORM-level tenant scoping + integration tests  |
| SOAP WSDL fetch in Docker              | Document outbound HTTPS requirement; tests mock SOAP |
| Credential key rotation                 | `key_version` column; document rotation procedure |
| Long-running HTTP requests              | Arq background jobs (Phase 3)                 |
| Stale product cache                     | Short TTL (15 min) + invalidation on apply    |
