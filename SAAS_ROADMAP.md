# SB-Optima — SaaS Migration Roadmap

> Last updated: 2026-04-05 — Phases 0–10 complete. Phases 11–13 added for
> production-readiness (frontend resilience, testing/CI, polish/performance).

This roadmap migrates **SB-Optima / Coverage Optimizer** from a single-tenant
Streamlit + FastAPI tool into a **multi-tenant, paid SaaS web application**
for DanDomain price optimisation.

---

## How to run tasks with Claude (critical rules)

1. **One task per message** — Claude must complete exactly one task, then stop.
2. Claude must end with: **"Say 'Next' to continue."**
3. **Checkbox rule** — Mark `[x]` when (a) the user says "Mark Task X.Y
   complete", or (b) an automated codebase audit proves all acceptance criteria
   are met with evidence. Never mark a checkbox without proof.
4. **Security** — never log secrets; redact credentials in errors; encrypt at rest.
5. **HostedShop authority** — the single source of truth for the SOAP API is
   `data/hostedshop_docs/hostedshop_api_docs_full.md`. Do not infer
   undocumented endpoints.

---

## Architectural decisions (locked)

| Concern | Decision |
|---|---|
| **API server** | FastAPI (keep — `backend/main.py`) |
| **Database** | Postgres + SQLAlchemy + Alembic migrations |
| **Cache / queue** | Redis |
| **Background jobs** | **Arq** (lightweight, async-native; revisit Celery only if needed) |
| **Frontend (interim)** | Keep Streamlit as thin HTTP client during migration |
| **Frontend (target)** | Next.js / React (deferred to Phase 8) |
| **Auth** | Custom JWT + password + email-verify (or Clerk/Auth0 — decide in ADR) |
| **Billing** | Stripe subscriptions (deferred to Phase 7) |
| **Hosting** | Production domains: `sboptima.dk` / `sboptima.com` (DNS/SSL deferred) |
| **Secret encryption** | Fernet envelope (master key from env); document KMS upgrade path |

### Core principles

1. **Backend is the sole gateway to HostedShop SOAP** — the UI never makes
   direct SOAP calls.
2. **No credentials in request bodies** once the credential vault exists —
   backend reads from the vault, scoped by `tenant_id`.
3. **Preserve existing apply safety model** (guardrails, idempotency, audit)
   while migrating storage from files to Postgres.
4. **Incremental migration** — each phase must leave existing tests green and
   the Streamlit UI functional.

---

# Section A — Current State (as-is)

## Components

| Layer | Module(s) | Responsibility |
|---|---|---|
| **Streamlit UI** | `app.py`, `ui/pages/price_optimizer.py`, `ui/backend_url.py`, `ui/styles.py` | Dashboard, Price Optimizer page (fetch/optimize/push-to-shop), invoice/supplier matching, barcode PDF export, risk analysis view |
| **FastAPI backend** | `backend/main.py`, `backend/optimizer_api.py`, `backend/apply_prices_api.py`, `backend/apply_real_api.py`, `backend/brands_api.py`, `backend/catalog_api.py`, `backend/apply_constants.py` | REST API: `/optimize`, `/brands`, `/catalog/products`, `/apply-prices/dry-run`, `/apply-prices/apply`, `/apply-prices/status`, `/health`, `/test-connection` |
| **Domain layer** | `domain/pricing.py`, `domain/product_loader.py`, `domain/risk_analysis.py`, `domain/invoice_ean.py`, `domain/supplier.py` | Pure business logic: pricing/coverage computation, product loading + variant expansion, risk analysis, invoice parsing, supplier parsing, EAN/SKU matching, LLM-assisted column mapping |
| **SOAP client** | `dandomain_api.py` | `DanDomainClient` — read (`Product_GetAll`, `get_all_brands`, `Product_GetVariantsByItemNumber`) + write (`Product_Update`, `Product_UpdateVariant` via `update_prices_batch`). HTTPS, retry, credential scrubbing. |
| **Push safety** | `push_safety.py` | `build_push_updates()` — explicit-selection gate + computed-diff gate for the Streamlit direct-push path |
| **Tests** | `tests/` (1303 tests as of 2026-04-05) | Coverage of backend endpoints, domain logic, apply guardrails, push safety, invoice/supplier matching, auth, RBAC, billing, quotas, observability, security hardening, retention, admin API |
| **Persistence** | `data/apply_batches/{batch_id}.json`, `data/apply_batches/{batch_id}.applied`, `data/apply_audit.log` | File-based batch manifests, idempotency markers, JSONL audit log |

## Data flow

```
Streamlit UI (app.py)
  Sidebar: credentials, backend URL, dry-run toggle, site_id
     |
     | HTTP (all interactions)
     v
  FastAPI Backend
  (/optimize, /brands, /test-connection,
   /catalog/products, /apply-prices/*, ...)
     |
     v
  domain/ (pricing, product_loader, ...)
     |
     v
  DanDomainClient (read + write via backend only:
   Product_GetAll, get_all_brands,
   GetVariantsByItemNumber, update_prices_batch)
     |                  |
     v                  v
  HostedShop SOAP    data/apply_batches/
  (external)         data/apply_audit.log
```

## Read vs write boundaries

| Path | Type | Module |
|---|---|---|
| `GET /health` | Read | `backend/main.py` |
| `GET /brands` | Read | `backend/brands_api.py` |
| `POST /optimize` | Read | `backend/optimizer_api.py` |
| `POST /catalog/products` | Read | `backend/catalog_api.py` |
| `POST /apply-prices/dry-run` | Read (persists manifest file) | `backend/apply_prices_api.py` |
| `GET /apply-prices/batch/{id}` | Read | `backend/apply_prices_api.py` |
| `POST /apply-prices/apply` | **Write** (guarded) | `backend/apply_real_api.py` |

> **Resolved (Task 1.1 + 1.2)**: The two-write-path risk is eliminated.
> All reads and writes go through backend endpoints.

## Safety guardrails (already implemented)

| Guardrail | Location |
|---|---|
| Environment gating (`SB_OPTIMA_ENABLE_APPLY`) | `backend/apply_real_api.py:is_apply_enabled()` |
| `confirm: true` required | `backend/apply_real_api.py:157` |
| Batch-level max rows (100) | `backend/apply_real_api.py:MAX_APPLY_ROWS` |
| Per-row: positive finite price | `backend/apply_real_api.py:_validate_row` |
| Per-row: abs(change_pct) <= 30% | `backend/apply_real_api.py:MAX_CHANGE_PCT` |
| Per-row: no selling below buy_price | `backend/apply_real_api.py:_validate_row` |
| Idempotency (`.applied` marker) | `backend/apply_real_api.py:202` |
| Audit log (JSONL) | `backend/apply_real_api.py:262-274` to `data/apply_audit.log` |
| UUID path-traversal prevention | `backend/apply_constants.py:UUID_RE` |
| Push safety: selection + diff gates | `push_safety.py:build_push_updates` |
| `/health` liveness probe | `backend/main.py:71` |

## Environment variables (current)

| Variable | Purpose | Where read |
|---|---|---|
| `SB_OPTIMA_ENABLE_APPLY` | Gate real-apply endpoint | `backend/apply_real_api.py` |
| `SB_OPTIMA_BACKEND_URL` | Default backend URL in Streamlit | `app.py` |
| `DANDOMAIN_API_USERNAME` | Optional credential seed | `app.py` |
| `DANDOMAIN_API_PASSWORD` | Optional credential seed | `app.py` |
| `OPENAI_API_KEY` | LLM fallback for invoice/supplier parsing | `domain/invoice_ean.py`, `domain/supplier.py` |
| `OPENAI_BASE_URL` | Optional LLM base URL override | `domain/invoice_ean.py` |

## Known issues for SaaS readiness

1. **Two write paths** (see above).
2. **No caching** — every `/optimize` and `/catalog/products` call creates a fresh
   `DanDomainClient` and fetches all products from SOAP. `enrich_variants()` in
   `catalog_api.py` can make N+1 SOAP calls (`Product_GetVariantsByItemNumber`
   per product).
3. **Credentials in request payloads** — `OptimizeRequest`, `CatalogRequest`,
   `ApplyRequest`, `GET /brands` all require `api_username`/`api_password`.
4. **Long-running requests** — optimisation + variant enrichment can block HTTP
   requests for minutes, causing timeouts.
5. **File-based persistence** — batch manifests and audit log live in
   `data/apply_batches/` and `data/apply_audit.log`; not multi-tenant-safe.
6. **LLM keys** — `OPENAI_API_KEY` is a single server-level env var; no
   per-tenant cost attribution.
7. **No auth / multi-tenancy** — all endpoints are open.

---

# Section B — Target SaaS Architecture

```
                    +----------------------------+
                    |   Web UI (Next.js)         |  <-- sboptima.dk / sboptima.com
                    |   (Phase 8 — deferred)     |
                    +------------+---------------+
                                 | HTTPS (JSON)
          +----------------------+----------------------+
          |                      |                      |
          |   +-----------------v--------------------+  |
          |   |       FastAPI API Server             |  |
          |   |  (existing backend/ — keep in place) |  |
          |   |  * Auth middleware (JWT)              |  |
          |   |  * Tenant scoping                    |  |
          |   |  * RBAC (viewer/operator/admin)      |  |
          |   |  * Job submission -> Arq             |  |
          |   |  * Credential vault reads            |  |
          |   +------+----------+---------+----------+  |
          |          |          |         |             |
          |          v          v         v             |
          |    +----------+ +-------+ +--------------+ |
          |    | Postgres | | Redis | | Arq Worker   | |
          |    |          | |       | | (async jobs) | |
          |    | * tenants| | * job | | * optimize   | |
          |    | * users  | |   q's | | * apply      | |
          |    | * creds  | | * TTL | | * enrichment | |
          |    | * batches| |  cache| |              | |
          |    | * audit  | |       | |              | |
          |    +----------+ +-------+ +------+-------+ |
          |                                  |         |
          |                     +------------v-------+ |
          |                     |  DanDomainClient   | |
          |                     |  (dandomain_api.py)| |
          |                     |  <-- sole SOAP gate| |
          |                     +------------+-------+ |
          +------------------------------+-------------+
                                         | HTTPS/SOAP
                                +---------v----------+
                                |  HostedShop SOAP   |
                                |  (external)        |
                                +--------------------+
```

### Production domain notes

| Domain | Intended use |
|---|---|
| `sboptima.dk` | Primary web app + API (`api.sboptima.dk`) |
| `sboptima.com` | Redirect to `sboptima.dk` (or English locale later) |

When configuring:
- **CORS allowed origins**: `https://sboptima.dk`, `https://www.sboptima.dk`,
  `https://sboptima.com`, `http://localhost:3000` (dev)
- **Cookie domain**: `.sboptima.dk` (allows `api.sboptima.dk` <-> `sboptima.dk`)
- **CSP / HSTS**: enforce once DNS + SSL are in place (not in scope for code tasks)

---

# Section C — Phased Roadmap

## Phase 0 — Planning & ADR

### Task 0.1 — Architecture Decision Record ✅
- [x] Task 0.1 — Updated: 2026-04-05

**Objective**: Write `docs/adr/0001-saas-architecture.md` locking the stack
choices listed above, with component diagram, data flow, security model, tenant
isolation strategy, and deployment plan.

**Scope**: new file only; no code changes.

**Acceptance criteria**:
- ADR file exists and is concrete enough to hand to a developer.
- Explicitly states: FastAPI, Postgres, Redis, Arq, JWT auth, Stripe (deferred),
  Next.js (deferred), Fernet envelope encryption.
- Documents sboptima.dk / sboptima.com intended use for CORS + cookies.

**Tests**: none (doc only).

**Risks**: bikeshedding on auth provider. Mitigation: default to custom JWT; note
external-provider alternative.

**Evidence**:
- `docs/adr/0001-saas-architecture.md` — Accepted ADR covering FastAPI, PostgreSQL 16 + SQLAlchemy, Redis 7, Arq, custom JWT + bcrypt, Stripe (deferred), Next.js (deferred), Fernet encryption, sboptima.dk/sboptima.com domain plan.

---

## Phase 1 — Safety Prerequisites (consolidate write paths)

### Task 1.1 — Retire Streamlit direct SOAP writes ✅
- [x] Task 1.1 — Updated: 2026-04-05

**Objective**: Remove the direct `DanDomainClient.update_prices_batch()` call
from `ui/pages/price_optimizer.py` and route all writes through the backend
`POST /apply-prices/apply` endpoint.

**Scope**:
- `ui/pages/price_optimizer.py` (lines ~2538-2810): replace `build_push_updates` +
  direct SOAP with HTTP calls to `/apply-prices/dry-run` then `/apply-prices/apply`.
- `push_safety.py`: keep as library (its diff/selection logic is useful); but the
  UI no longer calls `DanDomainClient` directly.
- Possibly `backend/apply_real_api.py`: ensure it handles variant updates
  (`variant_id` routing to `Product_UpdateVariant`) since the Streamlit path
  already does this.

**Acceptance criteria**:
- `grep -rn "DanDomainClient" ui/` returns zero hits (except test helpers).
- All writes to HostedShop go through `POST /apply-prices/apply`.
- Existing Streamlit push-to-shop UX still works (now via backend).
- All 660+ existing tests pass.

**Tests**:
- Update `tests/test_push_safety.py` if any interface changes.
- Add integration test: Streamlit-style push payload via backend apply with mock
  SOAP, verifying audit log written.

**Risks**:
- Backend apply currently doesn't handle variant_id / `Product_UpdateVariant`
  routing (it only sends `product_number` + `new_price`). **Mitigation**: extend
  the backend apply payload/manifest to include `variant_id` and route to the
  correct SOAP method.
- Streamlit dry-run toggle must map to backend dry-run. **Mitigation**: use
  existing `/apply-prices/dry-run` endpoint.

**Evidence**:
- `grep -rn "DanDomainClient" ui/` returns zero hits — confirmed.
- `ui/backend_client.py` — `apply_batch()` calls `POST /apply-prices/apply`; `create_manifest()` calls `POST /apply-prices/create-manifest`.
- `ui/pages/price_optimizer.py` — `_execute_backend_push()` and `_apply_batch()` route all writes through backend HTTP.
- Tests: `tests/test_task_1_1_retire_direct_soap.py`.

### Task 1.2 — Backend becomes sole product fetcher for UI ✅
- [x] Task 1.2 — Updated: 2026-04-05

**Objective**: Remove direct `DanDomainClient` read calls from the Streamlit UI
where feasible (product fetch, brand fetch already use backend; ensure no
remaining direct calls).

**Scope**:
- `ui/pages/price_optimizer.py`: audit all `DanDomainClient` usages (lines 17,
  2646, 2788). Line 2646 is "Test Connection" — route through
  `POST /test-connection`.
- `app.py`: no direct SOAP calls (already clean).

**Acceptance criteria**:
- `from dandomain_api import` does not appear in any `ui/` file.
- Streamlit functions as before, all reads via backend HTTP.

**Tests**: existing tests pass; no new tests needed beyond grep verification.

**Risks**: test-connection latency via backend. **Mitigation**: `/test-connection`
already exists in `backend/main.py`.

**Evidence**:
- `grep -rn "from dandomain_api import" ui/` returns zero hits — confirmed.
- `grep -rn "DanDomainClient" ui/` returns zero hits — confirmed.
- `ui/pages/price_optimizer.py` — `_fetch_brands_from_backend()`, `_fetch_catalog_products()`, `_test_connection_via_backend()` all use backend HTTP.
- Tests: `tests/test_task_1_2_no_ui_soap.py`.

---

## Phase 2 — Foundations (Docker + DB + config)

### Task 2.1 — Docker Compose for local dev ✅
- [x] Task 2.1 — Updated: 2026-04-05

**Objective**: One-command local dev environment with FastAPI, Postgres, and
Redis.

**Scope**:
- New: `infra/docker-compose.yml` (FastAPI, Postgres 16, Redis 7)
- New: `infra/Dockerfile.api` (Python container for backend)
- New: `.env.example` documenting all env vars
- `requirements.txt`: add `psycopg2-binary`, `redis`, `sqlalchemy`, `alembic`,
  `arq`
- `README.md`: update with Docker dev instructions

**Acceptance criteria**:
- `docker compose -f infra/docker-compose.yml up` starts all services.
- `GET /health` returns 200.
- Streamlit can connect to dockerized backend.

**Tests**: smoke test — health endpoint returns 200.

**Risks**: zeep needs outbound HTTPS to fetch WSDL.
**Mitigation**: document that Docker network needs outbound access; tests mock SOAP.

**Evidence**:
- `infra/docker-compose.yml` — Services: `api` (FastAPI, port 8000), `postgres` (PostgreSQL 16, health check), `redis` (Redis 7, health check).
- `infra/Dockerfile.api` — `python:3.12-slim`, non-root user `appuser`, `uvicorn backend.main:app`.
- `.env.example` — 82 documented env vars covering all phases.
- `GET /health` — Returns `{"status": "ok", "db": "ok|error|skipped"}` (backend/main.py).

### Task 2.2 — Postgres + Alembic scaffolding ✅
- [x] Task 2.2 — Updated: 2026-04-05

**Objective**: Database connection and migration framework, ready for models.

**Scope**:
- New: `backend/db.py` (engine, session factory, `get_db` dependency)
- New: `alembic/` directory with `alembic.ini`, `env.py`
- `backend/main.py`: startup event creates engine; optional healthcheck includes
  DB ping
- New env vars: `DATABASE_URL`

**Acceptance criteria**:
- `alembic upgrade head` runs clean on empty Postgres.
- `GET /health` optionally reports `db: ok`.
- Existing tests pass (they don't need DB).

**Tests**: test that `get_db` yields a session and rolls back on error.

**Risks**: import-time side effects from SQLAlchemy engine.
**Mitigation**: lazy engine init guarded by `DATABASE_URL` presence.

**Evidence**:
- `backend/db.py` — `init_engine()`, `get_db()` (session dependency), `check_db()` (SELECT 1 health probe). Lazy init via `DATABASE_URL`.
- `alembic/` — `alembic.ini`, `env.py`, 5 migration files in `alembic/versions/`.
- `GET /health` reports `db: ok|error|skipped`.
- Tests: `tests/test_db.py`, `tests/test_migrations_sqlite.py` (schema, linear chain, importable, unique IDs).

### Task 2.3 — Env var convention + config module ✅
- [x] Task 2.3 — Updated: 2026-04-05

**Objective**: Centralise all configuration into one Pydantic `Settings` class.

**Scope**:
- New: `backend/config.py` with `Settings(BaseSettings)`:
  - `DATABASE_URL`, `REDIS_URL`, `SB_OPTIMA_ENABLE_APPLY`, `OPENAI_API_KEY`,
    `ENCRYPTION_KEY`, `JWT_SECRET`, `CORS_ORIGINS`, `SBOPTIMA_ENV` (dev/staging/prod)
- Update: `backend/apply_real_api.py` to read from `Settings` instead of
  raw `os.environ`.
- Update: `.env.example`

**Acceptance criteria**:
- All env vars documented in one place.
- `backend/config.py` used by at least the apply module.

**Tests**: unit test for default values and override.

**Risks**: breaking existing `os.environ` reads.
**Mitigation**: `Settings` reads from env — transparent to existing code.

**Evidence**:
- `backend/config.py` — Pydantic `Settings(BaseSettings)` with `@lru_cache` singleton. 35+ config flags covering all phases.
- `backend/config.py:to_safe_dict()` — Secrets redacted for safe logging.
- `.env.example` — All vars documented.
- Tests: `tests/test_config.py`.

---

## Phase 3 — Background Jobs + Caching (early)

### Task 3.1 — Arq worker + async optimize ✅
- [x] Task 3.1 — Updated: 2026-04-05

**Objective**: Long-running optimisation runs as a background job instead of
blocking HTTP.

**Scope**:
- New: `backend/worker.py` (Arq `WorkerSettings`, `run_optimization_job`)
- New: `backend/jobs_api.py` router:
  - `POST /jobs/optimize` enqueues job, returns `{job_id}`
  - `GET /jobs/{job_id}` returns `{status, result?}`
- `backend/main.py`: wire `jobs_router`
- Keep synchronous `POST /optimize` as-is for backward compat (Streamlit uses it)

**Acceptance criteria**:
- `POST /jobs/optimize` returns job_id within 1s.
- `GET /jobs/{job_id}` returns `pending` then `running` then `completed` with result.
- Result matches synchronous `/optimize` output.

**Tests**:
- Unit: job enqueue returns job_id.
- Unit: poll returns correct status.
- Integration: async result matches sync result (mock SOAP).

**Risks**: Arq requires running worker process.
**Mitigation**: document `arq backend.worker.WorkerSettings` command; Docker Compose
adds worker service.

**Evidence**:
- `backend/worker.py` — Arq `WorkerSettings`, `run_optimization_job`.
- `backend/jobs_api.py` — `POST /jobs/optimize` (operator+), `GET /jobs/{job_id}` (operator+), `GET /jobs/` (viewer+, paginated).
- `backend/repositories/jobs_repo.py` — `create_job()`, `list_jobs()`, `get_job()`, `update_job_status()`.
- Redis key: `sboptima:job:<job_id>`. Config: `JOB_RESULT_TTL_S` (default 3600s).
- DB persistence: `OptimizationJob` rows when `SBOPTIMA_AUTH_REQUIRED=true`.
- Tests: `tests/test_jobs_api.py`, `tests/test_jobs_integration.py`.

### Task 3.2 — Per-tenant product cache (Redis) ✅
- [x] Task 3.2 — Updated: 2026-04-05

**Objective**: Cache product data in Redis to avoid re-fetching from SOAP on
every optimisation call.

**Scope**:
- New: `backend/cache.py` (Redis client, `get_cached_products`,
  `set_cached_products`, TTL config)
- Update: `backend/optimizer_api.py` and `backend/catalog_api.py` to check
  cache before SOAP fetch.
- Cache key: `products:{api_username_hash}:{site_id}` (pre-tenant phase) then
  `products:{tenant_id}:{site_id}` (post-tenant phase).
- TTL: 15 min default, configurable via `PRODUCT_CACHE_TTL_S`.
- Invalidate on apply.

**Acceptance criteria**:
- Second `/optimize` call with same params does not call SOAP.
- Cache is invalidated after `/apply-prices/apply`.
- TTL expiry works.

**Tests**:
- Cache hit returns cached data.
- Cache miss calls SOAP and stores.
- Apply invalidates cache.

**Risks**: stale data after external shop changes.
**Mitigation**: short TTL + manual invalidation endpoint `POST /cache/invalidate`.

**Evidence**:
- `backend/cache.py` — `get_redis()`, `build_caller_key()` (SHA-256 hash, no plaintext creds), `get_cached_products()`, `set_cached_products()`, `invalidate_products_cache()`. Gzipped JSON serialisation.
- Cache keys: `products:{caller_key}:{site_id}`, `products_enriched:{caller_key}:{site_id}`.
- Config: `PRODUCT_CACHE_TTL_S` (default 900s / 15 min), `CACHE_KEY_SALT`, `CACHE_MAX_PAYLOAD_KB` (5120 KB).
- Silent no-op when Redis unavailable.
- Tests: `tests/test_cache.py`.

### Task 3.3 — SOAP rate limiting + variant-enrichment batching ✅
- [x] Task 3.3 — Updated: 2026-04-05

**Objective**: Prevent N+1 variant enrichment from overwhelming HostedShop;
add per-caller rate limiting.

**Scope**:
- `domain/product_loader.py` (`enrich_variants`): add configurable concurrency
  limit and delay between SOAP calls.
- New: `backend/soap_limiter.py` — token-bucket or semaphore limiting SOAP calls
  per tenant.
- New env var: `SOAP_MAX_CONCURRENT` (default 3), `SOAP_CALL_DELAY_S` (default 0.2).

**Acceptance criteria**:
- Variant enrichment with 500 products doesn't exceed configured concurrency.
- Rate limiter is shared across requests for the same tenant.

**Tests**:
- Unit: rate limiter blocks when bucket empty.
- Integration: enrichment respects concurrency limit (mock SOAP with sleep).

**Risks**: increased total enrichment time.
**Mitigation**: cache enriched variants (Redis); only re-enrich on cache miss.

**Evidence**:
- `backend/soap_limiter.py` — `soap_limit(caller_key)` context manager. Threading.Semaphore for bounded concurrency + threading.Lock for inter-call delay.
- Config: `SOAP_MAX_CONCURRENT` (default 3), `SOAP_CALL_DELAY_S` (default 0.2s).
- Records `backend.metrics.record_soap_call()` on every SOAP call.
- Tests: `tests/test_soap_limiter.py`.

---

## Phase 4 — Multi-Tenancy + Auth + RBAC

### Task 4.1 — Tenant + User models ✅
- [x] Task 4.1 — Updated: 2026-04-05

**Objective**: Core DB models for multi-tenancy.

**Scope**:
- New Alembic migration with tables:
  - `tenants` (id UUID, name, created_at, stripe_customer_id nullable, plan, status)
  - `users` (id UUID, tenant_id FK, email unique-per-tenant, password_hash, role, created_at)
- Roles enum: `owner`, `admin`, `operator`, `viewer`
- New: `backend/models.py` (SQLAlchemy models)
- New: `backend/tenant_api.py` (basic CRUD — protected later)

**Acceptance criteria**:
- Migration runs clean.
- Models can be imported and used in tests.

**Tests**: CRUD unit tests for tenant + user creation.

**Risks**: UUID vs integer PK.
**Mitigation**: use UUID (standard for SaaS; avoids enumeration).

**Evidence**:
- `backend/models.py` — `Tenant` (UUID PK, name, created_at, stripe fields, plan, status, daily limits), `User` (UUID PK, tenant_id FK, email, password_hash, role enum, created_at). `Role` enum: owner/admin/operator/viewer. Unique(tenant_id, email).
- `alembic/versions/0001_add_tenants_and_users.py` — Creates `tenants` + `users` tables.
- Tests: `tests/test_tenant_user_models.py`.

### Task 4.2 — Authentication (JWT) ✅
- [x] Task 4.2 — Updated: 2026-04-05

**Objective**: Signup, login, JWT issuance, tenant-scoped middleware.

**Scope**:
- New: `backend/auth.py` (password hashing, JWT encode/decode, `get_current_user`
  dependency)
- New endpoints: `POST /auth/signup`, `POST /auth/login`, `POST /auth/refresh`
- Middleware: extract JWT and set `request.state.user` + `request.state.tenant_id`
- All existing endpoints remain open during this task (RBAC comes next)

**Acceptance criteria**:
- Signup creates user + tenant.
- Login returns JWT.
- Protected route rejects invalid/missing token.

**Tests**:
- Signup + login flow.
- Expired token rejected.
- Wrong tenant data isolated.

**Risks**: password handling.
**Mitigation**: use `passlib[bcrypt]`; never store plaintext.

**Evidence**:
- `backend/auth.py` — `hash_password()` (bcrypt), `verify_password()`, `create_access_token()` (HS256 JWT), `decode_token()`, `get_current_user()` (HTTPBearer dependency), `get_optional_current_user()` (respects `SBOPTIMA_AUTH_REQUIRED`).
- `backend/auth_api.py` — `POST /auth/signup`, `POST /auth/login`, `POST /auth/refresh`, `GET /auth/me`.
- JWT payload: `{sub, tenant_id, role, iat, exp}`.
- Config: `JWT_SECRET`, `JWT_ALGORITHM` (HS256), `JWT_ACCESS_TOKEN_EXP_MINUTES` (60), `SBOPTIMA_AUTH_REQUIRED`.
- Tests: `tests/test_auth.py`.

### Task 4.3 — RBAC middleware ✅
- [x] Task 4.3 — Updated: 2026-04-05

**Objective**: Role-based access control on all API routes.

**Scope**:
- New: `backend/rbac.py` (`require_role(min_role)` dependency)
- Route mapping:
  - `viewer`: `/health`, `/brands`, `/optimize`, `/catalog/products`,
    `/apply-prices/batch/{id}`, `/apply-prices/status`
  - `operator`: all viewer + `/apply-prices/dry-run`, `/jobs/optimize`
  - `admin`/`owner`: all operator + `/apply-prices/apply`, credential mgmt,
    tenant settings
- Apply RBAC to all routers

**Acceptance criteria**:
- Viewer cannot call `/apply-prices/apply` results in 403.
- Operator can create dry-run but cannot apply results in 403.
- Admin can do everything.

**Tests**: parameterised test per role x endpoint.

**Risks**: accidentally locking out existing Streamlit users during migration.
**Mitigation**: add a `SBOPTIMA_AUTH_REQUIRED=false` env var that disables auth
for local dev / migration period.

**Evidence**:
- `backend/rbac.py` — `ROLE_ORDER` (viewer:0, operator:1, admin:2, owner:3), `require_role(min_role)` FastAPI dependency. No-op when `SBOPTIMA_AUTH_REQUIRED=false`.
- Applied to all routers in `backend/main.py`: 15 routers with appropriate role requirements.
- Tests: `tests/test_rbac.py`.

---

## Phase 5 — Credential Vault + Secret Handling

### Task 5.1 — Encrypted credential storage ✅
- [x] Task 5.1 — Updated: 2026-04-05

**Objective**: Store DanDomain SOAP credentials per tenant, encrypted at rest.

**Scope**:
- New migration: `tenant_credentials` (tenant_id FK, encrypted_blob, created_at,
  updated_at)
- New: `backend/vault.py` (Fernet encrypt/decrypt with `ENCRYPTION_KEY` from env)
- New endpoints:
  - `PUT /tenant/credentials` (store — admin/owner only)
  - `DELETE /tenant/credentials`
  - `GET /tenant/credentials/status` (returns `{stored: bool, updated_at}` — no secrets)
- Validate credentials on store (call `/test-connection` internally)

**Acceptance criteria**:
- Credentials stored encrypted in DB.
- `GET /tenant/credentials/status` never returns raw secrets.
- `backend/optimizer_api.py` can retrieve credentials from vault by `tenant_id`.

**Tests**:
- Store + status round-trip.
- Encrypted blob is not plaintext.
- Delete removes credentials.
- Retrieve with wrong tenant returns nothing.

**Risks**: `ENCRYPTION_KEY` rotation.
**Mitigation**: document rotation procedure (re-encrypt all blobs); add
`key_version` column for future multi-key support.

**Evidence**:
- `backend/crypto.py` — `encrypt_str()`, `decrypt_str()` using Fernet.
- `backend/credentials_api.py` — `POST /credentials/` (admin+), `GET /credentials/` (admin+), `DELETE /credentials/{id}` (admin+). Response: `CredentialOut` (metadata only, no secrets).
- `backend/models.py:HostedShopCredential` — `api_username_enc`, `api_password_enc` (Fernet-encrypted), `site_id`, `name`. Unique(tenant_id, name).
- `alembic/versions/0002_add_hostedshop_credentials.py` — Creates `hostedshop_credentials` table.
- Returns 503 when auth disabled. Requires `ENCRYPTION_KEY`.
- Config: `ENCRYPTION_KEY`, `CREDENTIAL_CIPHER` (fernet), `ALLOW_REQUEST_CREDENTIALS_WHEN_AUTHED`.
- Tests: `tests/test_credentials.py`.

### Task 5.2 — Remove credentials from request payloads ✅
- [x] Task 5.2 — Updated: 2026-04-05

**Objective**: Backend reads credentials from vault instead of requiring them in
every request body.

**Scope**:
- `backend/optimizer_api.py`: `OptimizeRequest` drops `api_username`/`api_password`;
  backend resolves from vault using `request.state.tenant_id`.
- `backend/brands_api.py`: same — query params no longer carry credentials.
- `backend/catalog_api.py`: same.
- `backend/apply_real_api.py`: same — `ApplyRequest` drops credentials.
- Add backward-compat: if vault is empty **and** credentials are in payload,
  use payload (migration period only; log deprecation warning).

**Acceptance criteria**:
- Happy path: credentials come from vault.
- Fallback: credentials from payload work with deprecation warning.
- No credentials appear in logs.

**Tests**:
- Vault path works end-to-end (mock SOAP).
- Payload fallback works with warning logged.
- Missing both results in 401.

**Risks**: breaking Streamlit during migration.
**Mitigation**: backward-compat fallback + `SBOPTIMA_AUTH_REQUIRED=false` flag.

**Evidence**:
- `backend/credential_resolver.py` — `resolve_hostedshop_credentials()` resolves from vault (by credential_id) or request payload (fallback with deprecation warning).
- `ui/vault_helpers.py` — `build_optimize_payload()`, `build_catalog_payload()`, `build_apply_payload()`, `build_brands_params()` prefer `credential_id` when vault active; no plaintext credentials included.
- Config: `ALLOW_REQUEST_CREDENTIALS_WHEN_AUTHED` (default false).
- Tests: `tests/test_ui_vault_mode.py`, `tests/test_credentials.py`.

### Task 5.3 — LLM key management ✅
- [x] Task 5.3 — Updated: 2026-04-05

**Objective**: Decide and implement how `OPENAI_API_KEY` is handled in SaaS
context.

**Scope**:
- For MVP: single server-managed key (env var), shared across tenants.
- Add usage tracking: log tenant_id + token count per LLM call in
  `domain/invoice_ean.py` and `domain/supplier.py`.
- `backend/llm_usage.py`: in-memory usage tracking with `tracked_llm_call()`,
  `get_monthly_llm_usage()`, `check_llm_limit()`, and `record_llm_usage()`.
- `OPENAI_MONTHLY_TOKEN_LIMIT` config flag with enforcement (0 = unlimited).
- Admin API tenant detail extended with `llm_usage` field.
- Future: per-tenant key stored in vault (document upgrade path).

**Acceptance criteria**:
- ✅ LLM calls log `{tenant_id, tokens_used, model}` via structured JSON logger.
- ✅ No tenant can see another's LLM usage (tenant-scoped via admin API).
- ✅ `OPENAI_MONTHLY_TOKEN_LIMIT` enforced; calls rejected when exceeded.
- ✅ `_default_llm_call` accepts `tenant_id` keyword param (backward compatible).
- ✅ 10 tests pass covering tracking, isolation, limits, and config.

**Tests**: `tests/test_llm_usage.py` — 10 tests.

**Risks**: cost blowup.
**Mitigation**: `OPENAI_MONTHLY_TOKEN_LIMIT` env var; reject calls above limit.

**Evidence**:
- `backend/llm_usage.py` — `tracked_llm_call()`, `get_monthly_llm_usage()`, `check_llm_limit()`, `record_llm_usage()`, `reset_usage_store()`.
- `backend/config.py` — `openai_monthly_token_limit` (default 0 = unlimited).
- `domain/invoice_ean.py` — `_default_llm_call(prompt, api_key, model, *, tenant_id=None)`, `detect_invoice_columns(*, tenant_id=None)`, `suggest_column_mapping(*, tenant_id=None)`.
- `domain/supplier.py` — `_parse_pdf_line_items_llm(*, tenant_id=None)` with usage logging.
- `backend/admin_api.py` — `TenantDetailResponse.llm_usage` field with per-tenant monthly usage.
- Tests: `tests/test_llm_usage.py` (10 tests: usage tracking, tenant isolation, limits, config, admin endpoint).

---

## Phase 6 — DB Migration of Batches + Audit (preserve guardrails)

### Task 6.1 — Batch manifest + audit tables ✅
- [x] Task 6.1 — Updated: 2026-04-05

**Objective**: Postgres tables for batch manifests and audit, replacing
`data/apply_batches/` and `data/apply_audit.log`.

**Scope**:
- New migration with tables:
  - `apply_batches` (id UUID, tenant_id FK, created_at, optimize_payload JSONB,
    product_numbers JSONB, changes JSONB, summary JSONB, status enum
    [draft/applied/failed])
  - `apply_batch_rows` (optional normalisation — or keep changes as JSONB)
  - `audit_log` (id, tenant_id, user_id, batch_id FK, timestamp, action,
    total_rows, applied_count, skipped_count, failed_count, metadata JSONB)
- Migration script: import existing `data/apply_batches/*.json` and
  `data/apply_audit.log` into new tables (one-time).

**Acceptance criteria**:
- Dry-run creates a DB record (not a file).
- Apply reads from DB, writes audit to DB.
- Existing idempotency (`status != 'applied'`) preserved.
- Migration script imports existing files.

**Tests**:
- All existing `test_apply_prices_dry_run.py` and `test_apply_real.py` tests
  adapted to use DB.
- Migration script test with fixture files.

**Risks**: breaking apply flow.
**Mitigation**: dual-write (file + DB) during transition; remove file path once
verified.

**Evidence**:
- `backend/models.py` — `OptimizationJob`, `ApplyBatch`, `AuditEvent` models with full tenant scoping.
- `backend/repositories/jobs_repo.py` — CRUD for OptimizationJob.
- `backend/repositories/batches_repo.py` — CRUD for ApplyBatch.
- `backend/repositories/audit_repo.py` — Query layer for AuditEvent.
- `alembic/versions/0003_add_jobs_and_batches.py` — Creates `optimization_jobs`, `apply_batches`, `audit_events` tables.
- Dual-write: file + DB when auth enabled; file-only fallback when auth disabled.
- Tests: `tests/test_task_6_1_batches_audit.py`.

### Task 6.2 — Apply endpoint uses DB (guardrails preserved) + Tenant Dashboards ✅
- [x] Task 6.2 — Updated: 2026-04-05

**Objective**: `POST /apply-prices/apply` reads manifest from DB, writes audit
to DB, uses DB status for idempotency. Additionally, tenant dashboard list
endpoints (`GET /jobs`, `GET /apply-prices/batches`, `GET /audit`) and
Streamlit History page added for tenant-scoped job/batch/audit history.

**Scope**:
- `backend/apply_real_api.py`: load from DB instead of file; set `status=applied`.
- `backend/apply_prices_api.py`: persist to DB instead of file.
- `backend/apply_constants.py`: keep guardrail constants; `BATCH_DIR` and
  `AUDIT_LOG` become fallbacks only.

**Acceptance criteria**:
- All guardrails (MAX_APPLY_ROWS, MAX_CHANGE_PCT, below-cost, env gating)
  still enforced — test suite proves it.
- `.applied` marker replaced by DB status column.
- Audit log in DB with tenant_id + user_id.

**Tests**: existing test suite adapted; all pass.

**Risks**: data loss during transition.
**Mitigation**: keep file writes as fallback until stable; document removal.

**Evidence**:
- `backend/apply_prices_api.py` — `POST /apply-prices/dry-run`, `POST /apply-prices/create-manifest`, `GET /apply-prices/batches` (viewer+, paginated, filtered, tenant-scoped), `GET /apply-prices/batch/{batch_id}`.
- `backend/apply_real_api.py` — Updates `ApplyBatch.status` + `ApplyBatch.summary_json` in DB. All guardrails preserved.
- `backend/audit_api.py` — `GET /audit` (viewer+, paginated, filtered, tenant-scoped).
- `backend/jobs_api.py` — `GET /jobs/` (viewer+, paginated, filtered, tenant-scoped).
- `ui/pages/history.py` — Streamlit History page with tabs: jobs, batches, audit events.
- Auth-off: all list endpoints return 503.
- Tests: `tests/test_task_6_2_dashboards.py`, `tests/test_apply_real.py`, `tests/test_apply_prices_dry_run.py`.

---

## Phase 7 — Billing + Plans (Stripe)

### Task 7.1 — Stripe integration ✅
- [x] Task 7.1 — Updated: 2026-04-05

**Objective**: Paid subscriptions with plan-based gating.

**Scope**:
- Stripe checkout session endpoint.
- Webhook handler (subscription created/updated/cancelled).
- `tenants.stripe_customer_id`, `tenants.stripe_subscription_id`, `tenants.billing_status`.
- Billing gate: non-active tenants get 402 on `/optimize`, `/apply-prices/apply`,
  `/jobs/optimize`. Gate skipped when `BILLING_ENABLED=false` or
  `SBOPTIMA_AUTH_REQUIRED=false`.

**Acceptance criteria**:
- ✅ Checkout flow creates Stripe customer.
- ✅ Webhook updates tenant plan/status.
- ✅ Billing gate returns 402 for `billing_status` in (NULL, canceled, past_due, inactive).
- ✅ Billing gate passes `billing_status = "active"` tenants.
- ✅ Gate skipped when `BILLING_ENABLED=false`.
- ✅ Gate skipped when `SBOPTIMA_AUTH_REQUIRED=false`.
- ✅ 32 billing gate tests pass (parameterised by status × endpoint).

**Tests**: `tests/test_billing.py`, `tests/test_ui_billing.py`, `tests/test_billing_gate.py` — 32 gate tests.

**Risks**: webhook reliability.
**Mitigation**: idempotent webhook handler; Stripe event deduplication.

**Evidence**:
- `backend/stripe_billing.py` — `is_billing_configured()`, `create_or_get_customer()`, `create_checkout_session()`, `parse_and_verify_webhook()`, `handle_webhook_event()`, `report_usage_to_stripe()`.
- `backend/billing_api.py` — `POST /billing/checkout` (admin+), `GET /billing/status` (viewer+), `POST /billing/webhook` (signature-verified).
- `backend/billing_gate.py` — `check_billing_gate` FastAPI dependency; returns 402 for inactive billing_status.
- `backend/models.py:Tenant` — `stripe_customer_id`, `stripe_subscription_id`, `billing_status` columns.
- `alembic/versions/0005_add_stripe_fields.py` — Adds Stripe columns.
- Config: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID_PRO`, `STRIPE_PRICE_ID_ENTERPRISE`, `BILLING_ENABLED`.
- UI: `ui/pages/billing.py` — Plan display, billing status, Stripe checkout.
- Endpoints gated: `POST /jobs/optimize` (`jobs_api.py`), `POST /apply-prices/apply` (`apply_real_api.py`), `POST /optimize/` (`optimizer_api.py`).
- Tests: `tests/test_billing.py`, `tests/test_ui_billing.py`, `tests/test_billing_gate.py` (32 tests).

### Task 7.2 — Plans + quotas + usage tracking (Stripe-ready) ✅
- [x] Task 7.2 — Updated: 2026-04-05

**Objective**: Define plan tiers with per-tenant quota enforcement and usage
visibility, ready for Stripe metered billing.

**Scope**:
- `backend/plans.py`: `Plan` dataclass, `PLANS` dict (free/pro/enterprise),
  `get_plan()`, `list_plans()`.
- `backend/plan_api.py`: `GET /plans` (viewer+), `GET /tenant/plan` (viewer+),
  `PUT /tenant/plan` (admin+ with audit).
- `backend/quotas.py`: `check_quota()`, `get_usage()`, `get_limits()`.
  Counts `OptimizationJob` / `ApplyBatch` rows per day. Raises 429
  `QuotaExceeded` when limit hit.
- `backend/usage_api.py`: `GET /usage` (viewer+) returns daily usage vs limits.
- `alembic/versions/0004_add_tenant_limits.py`: `daily_optimize_jobs_limit`,
  `daily_apply_limit`, `daily_optimize_sync_limit` on tenants table.
- `backend/models.py:UsageEvent` — dedicated `usage_events` table.
- `alembic/versions/0006_add_usage_events.py` — creates `usage_events` table.
- `backend/repositories/usage_repo.py` — `emit_usage_event()`, `list_usage_events()`.
- Usage events emitted from `backend/jobs_api.py` (`job.optimize`) and
  `backend/apply_real_api.py` (`batch.apply`) on every persisted operation.
- `backend/stripe_billing.py:report_usage_to_stripe()` — Stripe Billing Meter
  Events API reporting for metered-plan tenants (non-fatal, best-effort).

**Acceptance criteria**:
- ✅ Plans defined with limits (free/pro/enterprise).
- ✅ Quota enforcement works (429 on limit exceed).
- ✅ `GET /usage` returns daily usage vs limits.
- ✅ `usage_events` table exists and records events for every optimize + apply.
- ✅ Stripe usage records reported for metered plans (via `report_usage_to_stripe()`).
- ✅ Usage events are tenant-scoped (no cross-tenant visibility).
- ✅ 17 tests pass covering event emission, listing, tenant isolation, Stripe reporting.

**Tests**: `tests/test_plans.py`, `tests/test_quotas.py`, `tests/test_usage_events.py` — 17 usage event tests.

**Evidence**:
- `backend/plans.py` — `Plan` dataclass, `PLANS` dict (free/pro/enterprise), `get_plan()`, `list_plans()`.
- `backend/plan_api.py` — `GET /plans` (viewer+), `GET /tenant/plan` (viewer+), `PUT /tenant/plan` (admin+).
- `backend/quotas.py` — `check_quota()`, `get_usage()`, `get_limits()`. Raises 429 `QuotaExceeded`.
- `backend/usage_api.py` — `GET /usage` (viewer+) returns daily usage vs limits.
- `alembic/versions/0004_add_tenant_limits.py` — Adds `daily_*_limit` columns to tenants.
- `backend/models.py:UsageEvent` — `id`, `tenant_id` (FK), `event_type`, `created_at`, `meta_json`.
- `alembic/versions/0006_add_usage_events.py` — Creates `usage_events` table.
- `backend/repositories/usage_repo.py` — `emit_usage_event()`, `list_usage_events()` (paginated, filtered, tenant-scoped).
- `backend/jobs_api.py` — Emits `job.optimize` usage event + Stripe reporting in `_persist_job_to_db()`.
- `backend/apply_real_api.py` — Emits `batch.apply` usage event + Stripe reporting in `_persist_apply_batch_to_db()`.
- `backend/stripe_billing.py` — `report_usage_to_stripe()` via Stripe Billing Meter Events API.
- Tests: `tests/test_usage_events.py` (17 tests: emission, listing, pagination, tenant isolation, Stripe reporting).

---

## Phase 8 — Web Frontend (Next.js) + Deprecate Streamlit

### Task 8.1 — Next.js scaffold + auth UI ✅
- [x] Task 8.1 — Updated: 2026-04-05

**Objective**: Minimal Next.js app with login, tenant dashboard, and navigation.

**Scope**:
- New: `frontend/` (Next.js app router, TypeScript)
- Login/signup pages using backend auth endpoints.
- Tenant dashboard skeleton.
- CORS configured for `sboptima.dk` + localhost.

**Acceptance criteria**:
- Login then JWT then dashboard renders.
- Cookie domain works for `.sboptima.dk`.

**Evidence**:
- `frontend/` directory with Next.js 16 + TypeScript + Tailwind CSS.
- `frontend/src/app/login/page.tsx` — login page connected to `POST /auth/login`.
- `frontend/src/app/signup/page.tsx` — signup page connected to `POST /auth/signup`.
- `frontend/src/app/dashboard/page.tsx` — tenant dashboard with plan, usage, credentials.
- `frontend/src/lib/auth-context.tsx` — JWT auth provider with localStorage, auto-refresh.
- `frontend/src/components/Navbar.tsx` — navigation bar with user info.
- `backend/config.py` — CORS dev defaults include `http://localhost:3000`.
- `npm run build` succeeds with all routes compiled.

### Task 8.2 — Price Optimizer page (web) ✅
- [x] Task 8.2 — Updated: 2026-04-05

**Objective**: Port the Streamlit Price Optimizer to Next.js.

**Scope**: brand selector, optimisation trigger (async job), results table with
filters, risk view, dry-run + apply flow, CSV export.

**Acceptance criteria**: feature parity with Streamlit Price Optimizer page.

**Evidence**:
- `frontend/src/app/optimizer/page.tsx` — full Price Optimizer page with:
  - Credential profile selector, brand multi-select filter.
  - Async job trigger via `POST /jobs/optimize` with polling.
  - Results table (All Products, Adjusted Only tabs) with text filter.
  - Risk View (histogram, largest decreases, near-cost warnings).
  - Dry-run preview + "APPLY" confirmation flow.
  - CSV export of optimization results.
- `frontend/src/app/history/page.tsx` — History page (jobs, batches, audit).
- `frontend/src/app/billing/page.tsx` — Billing page (plan info, Stripe checkout).
- `frontend/src/lib/api.ts` — typed API client covering all backend endpoints.

### Task 8.3 — Deprecate Streamlit ✅
- [x] Task 8.3 — Updated: 2026-04-05

**Objective**: Remove Streamlit UI code once Next.js has feature parity.

**Scope**: delete `app.py`, `ui/`, update README.

**Acceptance criteria**: `app.py` and `ui/` removed; all tests pass; README
points to Next.js frontend.

**Evidence**:
- `app.py` removed.
- `ui/` directory removed.
- `.streamlit/` directory removed.
- `streamlit` removed from `requirements.txt`.
- Streamlit-only test files removed: `test_backend_url.py`, `test_ui_vault_mode.py`,
  `test_ui_billing.py`, `test_result_summary.py`, `test_task_1_2_no_ui_soap.py`,
  `test_price_optimizer_backend.py`.
- `TestUINoDirectSoap` class removed from `test_task_1_1_retire_direct_soap.py`.
- README updated to point to Next.js frontend.
- `docs/REPO_OVERVIEW.md` updated with Next.js frontend section.
- All 1227 remaining tests pass.

---

## Phase 9 — Observability + Operational Safety

### Task 9.1 — Observability hardening (request IDs, structured logs, Prometheus) ✅
- [x] Task 9.1 — Updated: 2026-04-05

**Objective**: Production-grade observability with request correlation, structured
JSON logging with sensitive-key redaction, and Prometheus metrics for HTTP,
SOAP, quota, and billing events.

**Scope**:
- `RequestIDMiddleware` — attach or generate `X-Request-ID` (UUID-4) per request;
  echo on response; store in `request.state.request_id`.
- `AccessLogMiddleware` — structured JSON log per request with `method`, `path`,
  `status_code`, `duration_ms`, `request_id`, `tenant_id`, `user_id`; record
  HTTP metrics.
- `JSONFormatter` in `logging_config.py` — single-line JSON logs with contextual
  extras; `redact_dict()` scrubs authorization, api_password, password,
  jwt_secret, encryption_key, stripe_secret_key, stripe_webhook_secret, cookie,
  x-api-key (case-insensitive, nested).
- `setup_logging()` — configure root logger to use `JSONFormatter`.
- Prometheus `/metrics` endpoint — gated by `METRICS_ENABLED` config flag (503
  when disabled); admin-only RBAC when auth enabled. Custom registry with:
  `http_requests_total`, `http_request_duration_seconds`, `soap_calls_total`,
  `quota_exceeded_total`, `billing_webhook_events_total`.
- Helper functions: `record_http_request()`, `record_soap_call()`,
  `record_quota_exceeded()`, `record_billing_webhook()`.

**Acceptance criteria**:
- ✅ Every response includes `X-Request-ID` header (caller-provided or generated).
- ✅ Access log emits structured JSON with tenant/user correlation per request.
- ✅ `redact_dict()` removes all sensitive keys (case-insensitive, nested dicts).
- ✅ `GET /metrics` returns Prometheus exposition format when enabled; 503 when
  disabled; admin-gated when auth enabled.
- ✅ All five Prometheus counters/histograms are populated.
- ✅ 18 tests pass covering request ID, redaction, metrics endpoint, and
  structured logging.

**Tests**: `tests/test_observability.py` — 18 tests.

**Evidence**:
- `backend/middleware/request_id.py` — `RequestIDMiddleware`.
- `backend/middleware/access_log.py` — `AccessLogMiddleware` + `record_http_request` calls.
- `backend/logging_config.py` — `JSONFormatter`, `redact_dict()`, `setup_logging()`.
- `backend/metrics.py` — Prometheus registry, five metrics, `GET /metrics` router, helper functions.
- `backend/config.py` — `metrics_enabled` flag (default `False`).
- Tests: `tests/test_observability.py` (18 tests).

---

### Task 9.2 — Operational safety (admin diagnostics, migration tests, ops docs) ✅
- [x] Task 9.2 — Updated: 2026-04-05

**Objective**: Admin-only diagnostics and tenant-management endpoints for
platform operators; CI-safe migration validation; operational runbooks for
database migrations and backups.

**Scope**:
- `GET /admin/diagnostics` — app version, git SHA, config flags
  (`auth_required`, `billing_enabled`, `metrics_enabled`), DB status + latency,
  row counts (tenants, users, jobs). Admin+ RBAC; 503 when auth disabled.
- `GET /admin/tenants` — paginated list (limit 1–200, offset, optional `plan`
  filter). Returns metadata only (id, name, plan, status, created_at,
  billing_status). Admin+ RBAC; 503 when auth disabled.
- `GET /admin/tenant/{id}` — detail view with limits dict, usage dict,
  user_count, credential_count, billing booleans. Admin+ RBAC; 503 when auth
  disabled. 404 for unknown tenant.
- `tests/test_migrations_sqlite.py` — 5 tests: ORM schema creates all 6 core
  tables, all tables have PKs, Alembic revision chain is linear, all migration
  files importable with valid upgrade/downgrade, revision IDs unique.
- `docs/ops/migrations.md` — Alembic migration discipline: naming convention,
  review checklist, development/staging/production commands, CI safety via
  `test_migrations_sqlite.py`, best practices.
- `docs/ops/backups.md` — PostgreSQL backup/restore procedures: recommended
  cadence (daily + pre-deploy prod, weekly staging), `pg_dump`/`pg_restore`
  commands, post-restore steps, sensitive table inventory, security guidance.

**Acceptance criteria**:
- ✅ `GET /admin/diagnostics` returns health, config flags, DB latency, row
  counts; no secrets leaked.
- ✅ `GET /admin/tenants` supports pagination + plan filter; no PII.
- ✅ `GET /admin/tenant/{id}` returns limits + usage; no secrets; 404 for
  unknown.
- ✅ All admin endpoints return 503 when auth disabled; 403 for viewer/operator.
- ✅ Migration tests validate schema, linearity, importability, uniqueness.
- ✅ Ops docs cover migrations and backups with actionable runbooks.
- ✅ 20 admin API tests + 5 migration tests pass.

**Tests**: `tests/test_admin_api.py` — 20 tests; `tests/test_migrations_sqlite.py` — 5 tests.

**Evidence**:
- `backend/admin_api.py` — `GET /admin/diagnostics`, `GET /admin/tenants`, `GET /admin/tenant/{id}`.
- `backend/main.py` — Admin router mounted at `/admin`.
- `tests/test_admin_api.py` — 20 tests (503, RBAC, diagnostics shape, tenant list, tenant detail).
- `tests/test_migrations_sqlite.py` — 5 tests (schema, linear chain, importable, unique IDs).
- `docs/ops/migrations.md` — Alembic migration discipline and CI safety.
- `docs/ops/backups.md` — PostgreSQL backup/restore procedures.

---

## Phase 10 — Security Hardening + Data Retention

### Task 10.1 — Security hardening (CORS, security headers, request size limits) ✅
- [x] Task 10.1 — Updated: 2026-04-05

**Objective**: Defence-in-depth HTTP hardening — explicit CORS (no wildcards),
standard security response headers, HSTS (opt-in), and request body size
limits to prevent abuse.

**Scope**:
- CORS: explicit origins from `CORS_ALLOWED_ORIGINS` (comma-separated) or
  `CORS_ALLOWED_ORIGIN_REGEX`; no wildcard `*`; dev defaults to
  `http://localhost:8501`.
- `SecurityHeadersMiddleware` — when `SECURITY_HEADERS_ENABLED=true` (default):
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: strict-origin-when-cross-origin`,
  `Content-Security-Policy: default-src 'none'`,
  `Permissions-Policy: camera=(), microphone=(), geolocation=()`.
- `Strict-Transport-Security` header — only when `HSTS_ENABLED=true`:
  `max-age=63072000; includeSubDomains`.
- `RequestSizeLimitMiddleware` — checks `Content-Length` against
  `MAX_REQUEST_BODY_BYTES` (default 1 MB); returns 413 Content Too Large with
  JSON error body on exceed; non-numeric headers pass through.
- Webhook endpoint (`POST /billing/webhook`) does not require JWT but is
  subject to request size limit.

**Acceptance criteria**:
- ✅ Only explicitly configured origins are reflected in CORS `Access-Control-Allow-Origin`; no wildcard.
- ✅ `CORS_ALLOWED_ORIGIN_REGEX` enables dynamic pattern matching.
- ✅ Security headers present on all responses when enabled; absent when disabled.
- ✅ HSTS header present only when `HSTS_ENABLED=true`.
- ✅ Oversized requests receive 413; normal requests pass through.
- ✅ Webhook endpoint is protected by size limit but not by JWT.
- ✅ 13 tests pass covering CORS, headers, size limits, and webhook protections.

**Tests**: `tests/test_security_hardening.py` — 13 tests.

**Evidence**:
- `backend/middleware/security_headers.py` — `SecurityHeadersMiddleware` (6 headers).
- `backend/middleware/request_size_limit.py` — `RequestSizeLimitMiddleware` (413 on exceed).
- `backend/main.py` — CORS configuration with `cors_allowed_origins` / `cors_allowed_origin_regex`.
- `backend/config.py` — `cors_allowed_origins`, `cors_allowed_origin_regex`, `security_headers_enabled` (default `True`), `hsts_enabled` (default `False`), `max_request_body_bytes` (default 1 MB).
- Tests: `tests/test_security_hardening.py` (13 tests: 5 CORS, 4 headers, 2 size limit, 2 webhook).

---

### Task 10.2 — Data retention + tenant export ✅
- [x] Task 10.2 — Updated: 2026-04-05

**Objective**: Automated data retention pruning for old jobs, batches, and audit
events; admin-only tenant data export endpoint with PII/secret redaction and
row caps.

**Scope**:
- `backend/retention.py`:
  - `prune_jobs(db, cutoff_dt)` — delete `optimization_jobs` older than cutoff.
  - `prune_batches(db, cutoff_dt)` — delete `apply_batches` older than cutoff.
  - `prune_audit(db, cutoff_dt)` — delete `audit_events` older than cutoff.
  - `run_retention(db, settings, now_utc)` — orchestrate all three; emit
    `maintenance.retention` audit event; return dict with cutoffs + pruned
    counts. No-op when `RETENTION_ENABLED=false`.
- `scripts/run_retention.py` — CLI entrypoint for scheduled retention
  (`python scripts/run_retention.py`); exits 0 on success/disabled, 1 on error.
- `GET /admin/tenant/{id}/export` — admin+ RBAC; 503 when auth disabled; returns
  tenant data bundle (tenant info, users, jobs, batches, audit events); each
  collection capped at 10,000 rows with truncation flags; redacts
  `password_hash`, `api_username_enc`, `api_password_enc`, `encryption_key`,
  `stripe_secret_key`, `stripe_webhook_secret`; emits `admin.tenant.exported`
  audit event.
- Config: `RETENTION_ENABLED` (default `True`), `RETENTION_JOBS_DAYS` (30),
  `RETENTION_BATCHES_DAYS` (30), `RETENTION_AUDIT_DAYS` (90).

**Acceptance criteria**:
- ✅ `prune_jobs`, `prune_batches`, `prune_audit` delete only records older than
  configured cutoff; leave newer records untouched.
- ✅ `run_retention` orchestrates all three and emits maintenance audit event.
- ✅ Retention is a no-op when `RETENTION_ENABLED=false`.
- ✅ `scripts/run_retention.py` works as standalone CLI.
- ✅ Export endpoint returns tenant data with no secrets/PII; capped at 10k rows.
- ✅ Export is tenant-scoped (no cross-tenant data leakage).
- ✅ Export emits `admin.tenant.exported` audit event.
- ✅ Export returns 503 when auth disabled; 403 for non-admin roles; 404 for
  unknown tenant.
- ✅ 17 tests pass (7 retention + 10 export).

**Tests**: `tests/test_retention_and_export.py` — 17 tests.

**Evidence**:
- `backend/retention.py` — `prune_jobs()`, `prune_batches()`, `prune_audit()`, `run_retention()`.
- `scripts/run_retention.py` — CLI entrypoint for scheduled retention.
- `backend/admin_api.py` — `GET /admin/tenant/{id}/export` (admin+, 503 auth-off, 10k cap, PII redaction, audit event).
- `backend/config.py` — `retention_enabled`, `retention_jobs_days`, `retention_batches_days`, `retention_audit_days`.
- Tests: `tests/test_retention_and_export.py` (17 tests: 7 retention, 10 export).

---

## Phase 11 — Frontend Resilience & Error Handling

### Task 11.1 — React error boundaries + global error/not-found pages
- [x] Task 11.1

**Objective**: Prevent white-screen crashes by adding React error boundaries and
Next.js App Router error/not-found handlers, and fix silent error swallowing on
Dashboard and History pages so users always see feedback when something fails.

**Scope**:
- `frontend/src/app/error.tsx` — global error boundary page (App Router
  convention); renders a user-friendly "Something went wrong" message with a
  "Try again" button that calls `reset()`. Logs error to console.
- `frontend/src/app/not-found.tsx` — custom 404 page with link back to
  dashboard.
- `frontend/src/components/ErrorBoundary.tsx` — reusable React class-component
  error boundary wrapping `children`; used inside `providers.tsx` around
  page content.
- **Fix silent error handling**: Dashboard page (`dashboard/page.tsx`) and
  History page (`history/page.tsx`) currently use `.catch(() => {})`. Replace
  with proper error state: add `error` state variable, display a red banner
  with the error message and a "Retry" button, matching the pattern already
  used in Login/Signup pages.
- Ensure all pages surface API errors visibly.

**Acceptance criteria**:
- [x] `frontend/src/app/error.tsx` exists and renders error UI with reset action.
- [x] `frontend/src/app/not-found.tsx` exists and renders 404 UI.
- [x] `ErrorBoundary` component wraps app content in `providers.tsx`.
- [x] Dashboard page shows red error banner on API failure (not silent).
- [x] History page shows red error banner on API failure (not silent).
- [x] `npm run build` succeeds with zero errors.

---

### Task 11.2 — Accessibility improvements (semantic HTML + ARIA)
- [x] Task 11.2

**Objective**: Bring the frontend to baseline WCAG 2.1 Level A compliance by
adding semantic HTML elements, ARIA labels, and focus management.

**Scope**:
- `Navbar.tsx` — wrap in `<nav aria-label="Main navigation">` element; add
  `aria-current="page"` on active link.
- `PageShell.tsx` — wrap children in `<main>` element.
- All `<table>` elements — add `<caption>` or `aria-label` describing the
  table content.
- All form `<input>` elements — ensure each has an associated `<label>` with
  `htmlFor` (Login, Signup, Optimizer controls, History filters).
- Add `aria-live="polite"` on status/error banners so screen readers announce
  them.
- Add visible focus styles (`focus:ring-2 focus:ring-blue-500`) on all
  interactive elements (buttons, links, inputs).
- Skip-to-content link at top of `layout.tsx`.

**Acceptance criteria**:
- [x] `<nav>` wraps navbar; `<main>` wraps page content.
- [x] All form inputs have associated `<label>` elements.
- [x] All tables have `aria-label` or `<caption>`.
- [x] Status/error banners have `aria-live="polite"`.
- [x] Visible focus indicators on all interactive elements.
- [x] Skip-to-content link present.
- [x] `npm run build` succeeds.

---

## Phase 12 — Frontend Testing & CI/CD Pipeline

### Task 12.1 — Frontend test infrastructure + initial test suite
- [x] Task 12.1

**Objective**: Set up a frontend test framework and write initial unit/component
tests for critical paths (auth, API client, key pages).

**Scope**:
- Install and configure **Vitest** + **React Testing Library** +
  **@testing-library/jest-dom** in `frontend/`.
- `frontend/vitest.config.ts` — configure jsdom environment, path aliases.
- `frontend/src/__tests__/api.test.ts` — unit tests for `api.ts`: verify
  `authHeaders()`, `ApiError` construction, request builder (mock `fetch`).
- `frontend/src/__tests__/auth-context.test.tsx` — test `AuthProvider`:
  initial state, `setToken()`, `logout()`, localStorage interaction.
- `frontend/src/__tests__/login.test.tsx` — render Login page, simulate form
  submission, verify API call, verify error display on failure.
- `frontend/src/__tests__/dashboard.test.tsx` — render Dashboard, verify
  loading state, verify data display after mock API response.
- Add `"test": "vitest run"` script to `frontend/package.json`.
- Target: ≥15 frontend tests passing.

**Acceptance criteria**:
- [x] `cd frontend && npm test` runs Vitest and passes ≥15 tests.
- [x] Tests cover: API client, auth context, login page, dashboard page.
- [x] No new runtime dependencies added (test deps are devDependencies only).
- [x] `npm run build` still succeeds.

---

### Task 12.2 — CI/CD pipeline (GitHub Actions) + frontend Dockerfile
- [x] Task 12.2

**Objective**: Automate build, lint, and test for both backend and frontend on
every push/PR; add a production-ready frontend Docker image.

**Scope**:
- `.github/workflows/ci.yml` — GitHub Actions workflow triggered on
  `push` (main) and `pull_request`:
  - **Backend job**: Python 3.11+, `pip install` deps, `python -m pytest tests/ -v`.
  - **Frontend job**: Node 20+, `npm ci`, `npm run lint`, `npm run build`,
    `npm test`.
  - Both jobs run in parallel.
- `infra/Dockerfile.frontend` — multi-stage Docker build:
  - Stage 1 (builder): `node:20-alpine`, `npm ci`, `npm run build`.
  - Stage 2 (runner): `node:20-alpine`, copy `.next/standalone` + `.next/static`
    + `public/`, expose port 3000, `CMD ["node", "server.js"]`.
  - `next.config.ts` — set `output: "standalone"` for Docker support.
- Update `infra/docker-compose.yml` — add `frontend` service, port 3000,
  depends on `api`.
- Update README.md with updated `docker compose up` instructions that include
  frontend.

**Acceptance criteria**:
- [x] `.github/workflows/ci.yml` exists and defines backend + frontend jobs.
- [x] `infra/Dockerfile.frontend` builds a runnable Next.js image.
- [x] `next.config.ts` has `output: "standalone"`.
- [x] `docker-compose.yml` includes `frontend` service on port 3000.
- [x] README updated with full-stack docker compose instructions.
- [x] CI workflow passes (backend tests + frontend lint/build/test).

---

## Phase 13 — Frontend Polish & Performance

### Task 13.1 — Data caching, loading skeletons, and password validation
- [ ] Task 13.1

**Objective**: Improve UX by adding client-side data caching to avoid redundant
fetches, replace "Loading…" text with skeleton placeholders, and add password
strength validation on the signup form.

**Scope**:
- **Data caching**: Create `frontend/src/lib/use-cached-fetch.ts` — a custom
  React hook that wraps `fetch` calls with a simple in-memory cache
  (Map-based, keyed by URL + token, with configurable TTL; default 60s).
  Apply to Dashboard (plan/usage/credentials), History (jobs/batches/audit),
  and Billing (status/plan) pages to avoid re-fetching on every mount.
- **Loading skeletons**: Create `frontend/src/components/Skeleton.tsx` —
  reusable animated skeleton components (SkeletonCard, SkeletonTable,
  SkeletonText). Replace "Loading…" text on Dashboard, History, Billing,
  and Optimizer pages with appropriate skeleton layouts.
- **Password strength**: On Signup page, add a password strength indicator
  below the password field. Rules: minimum 8 chars (already enforced),
  at least one uppercase, one lowercase, one digit. Display strength as
  "Weak" / "Medium" / "Strong" with color coding. Disable submit when
  strength < Medium.
- **Next.js image config**: Remove `images: { unoptimized: true }` from
  `next.config.ts` (or set to `false`) so Next.js image optimization is
  available when images are added.

**Acceptance criteria**:
- [ ] `use-cached-fetch` hook exists and is used on ≥3 pages.
- [ ] Dashboard, History, Billing show skeleton loaders instead of "Loading…".
- [ ] Signup page shows password strength indicator; submit disabled when weak.
- [ ] `images.unoptimized` removed or set to `false` in `next.config.ts`.
- [ ] `npm run build` succeeds.

---

### Task 13.2 — Table virtualization + billing history
- [ ] Task 13.2

**Objective**: Handle large datasets efficiently with virtualized table
rendering, and surface billing history / invoice data on the Billing page.

**Scope**:
- **Table virtualization**: Install `@tanstack/react-virtual` (or similar
  lightweight virtualizer). Create
  `frontend/src/components/VirtualTable.tsx` — a generic virtualized table
  component that renders only visible rows. Apply to:
  - Optimizer results table (currently sliced to 500 rows; virtualizer
    removes the slice limit).
  - History tables (Jobs, Batches, Audit Events).
  - Risk view tables (largest decreases, near-cost products).
- **Billing history**: Add a "Billing History" section to
  `frontend/src/app/billing/page.tsx`. Backend already tracks
  `usage_events` — add `GET /billing/invoices` endpoint (or reuse Stripe
  API data via backend proxy) that returns recent invoice line items.
  Display in a table with date, description, amount, status columns.
  If Stripe billing is disabled, show "Billing history is not available"
  message.

**Acceptance criteria**:
- [ ] `VirtualTable` component exists and renders only visible rows.
- [ ] Optimizer page uses `VirtualTable` with no 500-row slice limit.
- [ ] History page tables use `VirtualTable`.
- [ ] Billing page shows billing history section (or "not available" when
      Stripe disabled).
- [ ] `npm run build` succeeds.
- [ ] No performance regressions (page load stays under 3s for 1000+ rows).

---

## Audit Summary (2026-04-05)

| Phase | Task | Status | Notes |
|---|---|---|---|
| 0 | 0.1 — ADR | ✅ | `docs/adr/0001-saas-architecture.md` |
| 1 | 1.1 — Retire UI SOAP writes | ✅ | No DanDomainClient in ui/ |
| 1 | 1.2 — Backend sole fetcher | ✅ | No dandomain_api in ui/ |
| 2 | 2.1 — Docker Compose | ✅ | `infra/docker-compose.yml` |
| 2 | 2.2 — Postgres + Alembic | ✅ | `backend/db.py`, 5 migrations |
| 2 | 2.3 — Config module | ✅ | `backend/config.py`, 35+ flags |
| 3 | 3.1 — Arq worker | ✅ | `backend/worker.py`, `jobs_api.py` |
| 3 | 3.2 — Product cache | ✅ | `backend/cache.py`, Redis |
| 3 | 3.3 — SOAP limiter | ✅ | `backend/soap_limiter.py` |
| 4 | 4.1 — Tenant + User models | ✅ | `backend/models.py`, migration 0001 |
| 4 | 4.2 — Auth (JWT) | ✅ | `backend/auth.py`, `auth_api.py` |
| 4 | 4.3 — RBAC | ✅ | `backend/rbac.py` |
| 5 | 5.1 — Credential vault | ✅ | `backend/crypto.py`, `credentials_api.py`, migration 0002 |
| 5 | 5.2 — Remove creds from payloads | ✅ | `backend/credential_resolver.py` |
| 5 | 5.3 — LLM key management | ✅ | `backend/llm_usage.py`, `OPENAI_MONTHLY_TOKEN_LIMIT`, tenant-aware logging (10 tests) |
| 6 | 6.1 — Batch + audit tables | ✅ | Migration 0003, repos |
| 6 | 6.2 — DB apply + dashboards | ✅ | List endpoints, History page |
| 7 | 7.1 — Stripe integration | ✅ | Checkout + webhook + billing gate (`backend/billing_gate.py`, 32 tests) |
| 7 | 7.2 — Plans + quotas + usage | ✅ | Plans + quotas + `usage_events` table + Stripe reporting (17 tests) |
| 8 | 8.1 — Next.js scaffold | ✅ | `frontend/` Next.js 16 + TypeScript + Tailwind, auth pages, dashboard |
| 8 | 8.2 — Price Optimizer (web) | ✅ | Full optimizer page with async jobs, risk view, apply flow, CSV export |
| 8 | 8.3 — Deprecate Streamlit | ✅ | `app.py` + `ui/` removed; 1227 tests pass; README updated |
| 9 | 9.1 — Observability hardening | ✅ | Request IDs, JSON logs, redaction, Prometheus metrics (18 tests) |
| 9 | 9.2 — Operational safety | ✅ | Admin diagnostics/tenants endpoints, migration tests, ops docs (25 tests) |
| 10 | 10.1 — Security hardening | ✅ | CORS, security headers, HSTS, request size limits (13 tests) |
| 10 | 10.2 — Data retention + export | ✅ | Retention pruning, CLI script, tenant export endpoint (17 tests) |
| 11 | 11.1 — Error boundaries + error/404 pages | ⛔ | Error boundaries, global error.tsx/not-found.tsx, fix silent catches |
| 11 | 11.2 — Accessibility (semantic HTML + ARIA) | ⛔ | Nav/main semantics, ARIA labels, focus styles, skip-to-content |
| 12 | 12.1 — Frontend test suite | ⛔ | Vitest + RTL, ≥15 tests (API client, auth, login, dashboard) |
| 12 | 12.2 — CI/CD + frontend Dockerfile | ⛔ | GitHub Actions, Dockerfile.frontend, docker-compose frontend service |
| 13 | 13.1 — Caching, skeletons, password validation | ⛔ | Client-side cache hook, skeleton loaders, password strength, image config |
| 13 | 13.2 — Table virtualization + billing history | ⛔ | VirtualTable component, remove 500-row limit, billing history display |

**Total**: 24 ✅ · 0 🟡 · 6 ⛔

---

# Section D — First 2 Weeks Execution Plan (✅ Complete)

> All initial tasks are complete. This section is preserved for historical reference.

| Order | Task | Status | Definition of Done |
|---|---|---|---|
| 1 | **0.1** — ADR | ✅ | `docs/adr/0001-saas-architecture.md` exists and is reviewed. |
| 2 | **1.1** — Retire Streamlit direct SOAP writes | ✅ | `grep -rn "DanDomainClient" ui/` returns 0 results; all tests pass. |
| 3 | **1.2** — Backend sole fetcher | ✅ | `from dandomain_api import` absent from `ui/`; Streamlit works via backend HTTP. |
| 4 | **2.1** — Docker Compose | ✅ | `docker compose up` starts API + Postgres + Redis; `/health` returns 200. |
| 5 | **2.2** — Postgres + Alembic | ✅ | `alembic upgrade head` succeeds; `GET /health` reports `db: ok`. |

---

# Section E — Non-Goals & Deferred Items

The following are explicitly **out of scope** for current sprints:

- ~~**Next.js frontend** (Phase 8)~~ — ✅ Complete (Phase 8.1–8.3).
- **Monorepo restructure** (`apps/web`, `apps/api`, `packages/shared`) — do NOT
  move files into a monorepo layout. Keep the current flat structure; add
  `frontend/` alongside `backend/` when the time comes.
- **DNS / SSL setup** for sboptima.dk — document intended use; do not configure.
- **KMS-based encryption** — Fernet is acceptable for MVP; document upgrade path.
- **Per-tenant LLM keys** — use server-managed key; add usage tracking first
  (Task 5.3).
- **Invoice/supplier matching migration** — these features keep working as-is via
  Streamlit; no SaaS-specific changes until frontend migration.
- **Variant matching improvements** — paused per project decision.
- **Post-apply verification** (re-fetch and confirm) — nice-to-have; schedule
  after billing gate is in place.

---

# Section F — Next 3 Tasks

> Updated: 2026-04-05 — Production-readiness tasks queued.

### 🔜 Next: Task 11.1 — Error boundaries + error/not-found pages

Add `error.tsx`, `not-found.tsx`, reusable `ErrorBoundary` component, and fix
silent `.catch(() => {})` on Dashboard/History pages to show user-visible error
banners.

### 🔜 Then: Task 11.2 — Accessibility improvements

Semantic HTML (`<nav>`, `<main>`), ARIA labels on tables/forms, `aria-live` on
status banners, visible focus styles, skip-to-content link.

### 🔜 Then: Task 12.1 — Frontend test infrastructure + initial tests

Vitest + React Testing Library setup. ≥15 tests covering API client, auth
context, Login page, Dashboard page.

---

### After that: Tasks 12.2, 13.1, 13.2

CI/CD pipeline (GitHub Actions) + frontend Dockerfile, then client-side caching
/ skeletons / password validation, then table virtualization + billing history.
