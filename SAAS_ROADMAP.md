# SB-Optima — SaaS Migration Roadmap

> Last updated: 2026-04-04 — aligned with actual codebase scan.

This roadmap migrates **SB-Optima / Coverage Optimizer** from a single-tenant
Streamlit + FastAPI tool into a **multi-tenant, paid SaaS web application**
for DanDomain price optimisation.

---

## How to run tasks with Claude (critical rules)

1. **One task per message** — Claude must complete exactly one task, then stop.
2. Claude must end with: **"Say 'Next' to continue."**
3. Only mark checkboxes when you say: **"Mark Task X.Y complete"**.
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
| **Tests** | `tests/` (660+ tests) | Coverage of backend endpoints, domain logic, apply guardrails, push safety, invoice/supplier matching |
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

### Task 0.1 — Architecture Decision Record
- [x] Task 0.1

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

---

## Phase 1 — Safety Prerequisites (consolidate write paths)

### Task 1.1 — Retire Streamlit direct SOAP writes
- [x] Task 1.1

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

### Task 1.2 — Backend becomes sole product fetcher for UI
- [x] Task 1.2

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

---

## Phase 2 — Foundations (Docker + DB + config)

### Task 2.1 — Docker Compose for local dev
- [x] Task 2.1

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

### Task 2.2 — Postgres + Alembic scaffolding
- [x] Task 2.2

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

### Task 2.3 — Env var convention + config module
- [ ] Task 2.3

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

---

## Phase 3 — Background Jobs + Caching (early)

### Task 3.1 — Arq worker + async optimize
- [ ] Task 3.1

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

### Task 3.2 — Per-tenant product cache (Redis)
- [ ] Task 3.2

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

### Task 3.3 — SOAP rate limiting + variant-enrichment batching
- [ ] Task 3.3

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

---

## Phase 4 — Multi-Tenancy + Auth + RBAC

### Task 4.1 — Tenant + User models
- [ ] Task 4.1

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

### Task 4.2 — Authentication (JWT)
- [ ] Task 4.2

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

### Task 4.3 — RBAC middleware
- [ ] Task 4.3

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

---

## Phase 5 — Credential Vault + Secret Handling

### Task 5.1 — Encrypted credential storage
- [ ] Task 5.1

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

### Task 5.2 — Remove credentials from request payloads
- [ ] Task 5.2

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

### Task 5.3 — LLM key management
- [ ] Task 5.3

**Objective**: Decide and implement how `OPENAI_API_KEY` is handled in SaaS
context.

**Scope**:
- For MVP: single server-managed key (env var), shared across tenants.
- Add usage tracking: log tenant_id + token count per LLM call in
  `domain/invoice_ean.py` and `domain/supplier.py`.
- Future: per-tenant key stored in vault (document upgrade path).

**Acceptance criteria**:
- LLM calls log `{tenant_id, tokens_used, model}`.
- No tenant can see another's LLM usage.

**Tests**: unit test that LLM usage logging captures tenant_id.

**Risks**: cost blowup.
**Mitigation**: add `OPENAI_MONTHLY_TOKEN_LIMIT` env var; reject calls above limit.

---

## Phase 6 — DB Migration of Batches + Audit (preserve guardrails)

### Task 6.1 — Batch manifest + audit tables
- [ ] Task 6.1

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

### Task 6.2 — Apply endpoint uses DB (guardrails preserved)
- [ ] Task 6.2

**Objective**: `POST /apply-prices/apply` reads manifest from DB, writes audit
to DB, uses DB status for idempotency.

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

---

## Phase 7 — Billing + Plans (Stripe)

### Task 7.1 — Stripe integration
- [ ] Task 7.1

**Objective**: Paid subscriptions with plan-based gating.

**Scope**:
- Stripe checkout session endpoint.
- Webhook handler (subscription created/updated/cancelled).
- `tenants.stripe_customer_id`, `tenants.plan`, `tenants.plan_status`.
- Billing gate middleware: non-active tenants get 402 on `/optimize` and `/apply`.

**Acceptance criteria**:
- Checkout flow creates Stripe customer.
- Webhook updates tenant status.
- Expired subscription blocks optimise/apply.

**Tests**: webhook signature verify (mocked); billing gate test.

**Risks**: webhook reliability.
**Mitigation**: idempotent webhook handler; Stripe event deduplication.

### Task 7.2 — Usage metering (optional)
- [ ] Task 7.2

**Objective**: Track optimisation runs and applies per tenant for usage-based
billing.

**Scope**:
- `usage_events` table (tenant_id, event_type, timestamp, metadata).
- Report to Stripe usage records (if metered plan).

**Acceptance criteria**: usage events recorded for every optimize + apply call.

**Tests**: unit test for event recording.

---

## Phase 8 — Web Frontend (Next.js) + Deprecate Streamlit

### Task 8.1 — Next.js scaffold + auth UI
- [ ] Task 8.1

**Objective**: Minimal Next.js app with login, tenant dashboard, and navigation.

**Scope**:
- New: `frontend/` (Next.js app router, TypeScript)
- Login/signup pages using backend auth endpoints.
- Tenant dashboard skeleton.
- CORS configured for `sboptima.dk` + localhost.

**Acceptance criteria**:
- Login then JWT then dashboard renders.
- Cookie domain works for `.sboptima.dk`.

### Task 8.2 — Price Optimizer page (web)
- [ ] Task 8.2

**Objective**: Port the Streamlit Price Optimizer to Next.js.

**Scope**: brand selector, optimisation trigger (async job), results table with
filters, risk view, dry-run + apply flow, CSV export.

**Acceptance criteria**: feature parity with Streamlit Price Optimizer page.

### Task 8.3 — Deprecate Streamlit
- [ ] Task 8.3

**Objective**: Remove Streamlit UI code once Next.js has feature parity.

**Scope**: delete `app.py`, `ui/`, update README.

**Acceptance criteria**: `app.py` and `ui/` removed; all tests pass; README
points to Next.js frontend.

---

# Section D — First 2 Weeks Execution Plan

| Order | Task | Why first | Definition of Done |
|---|---|---|---|
| 1 | **0.1** — ADR | Locks all tech decisions so subsequent tasks don't conflict. | `docs/adr/0001-saas-architecture.md` exists and is reviewed. |
| 2 | **1.1** — Retire Streamlit direct SOAP writes | Eliminates the highest-risk safety gap before any SaaS work. | `grep -rn "DanDomainClient" ui/` returns 0 results (excl. tests); all tests pass. |
| 3 | **1.2** — Backend sole fetcher | Completes the "backend-as-gateway" principle. | `from dandomain_api import` absent from `ui/`; Streamlit works via backend HTTP. |
| 4 | **2.1** — Docker Compose | Enables consistent local dev and prepares for Postgres/Redis. | `docker compose up` starts API + Postgres + Redis; `/health` returns 200. |
| 5 | **2.2** — Postgres + Alembic | DB ready for models; no tables yet but migration framework in place. | `alembic upgrade head` succeeds; `GET /health` reports `db: ok`. |

---

# Section E — Non-Goals & Deferred Items

The following are explicitly **out of scope for the first month**:

- **Next.js frontend** (Phase 8) — Streamlit remains the UI during migration.
- **Stripe billing** (Phase 7) — no payment gates until auth + vault are stable.
- **Monorepo restructure** (`apps/web`, `apps/api`, `packages/shared`) — do NOT
  move files into a monorepo layout. Keep the current flat structure; add
  `frontend/` alongside `backend/` when the time comes.
- **DNS / SSL setup** for sboptima.dk — document intended use; do not configure.
- **KMS-based encryption** — Fernet is acceptable for MVP; document upgrade path.
- **Per-tenant LLM keys** — use server-managed key with usage tracking for now.
- **Invoice/supplier matching migration** — these features keep working as-is via
  Streamlit; no SaaS-specific changes until frontend migration.
- **Variant matching improvements** — paused per project decision.
- **CI/CD pipeline** — defer until Docker Compose is stable.
- **Post-apply verification** (re-fetch and confirm) — nice-to-have; schedule
  after Phase 6.
