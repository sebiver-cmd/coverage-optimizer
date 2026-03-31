# SB‑Optima — SaaS Web App Roadmap (multi-tenant, paid access)

This roadmap is for building a **proper web app** (not Streamlit) that supports:
- Multiple DanDomain customers (“tenants”)
- Multiple user accounts per tenant
- Paid access (subscriptions) like Bontii
- Secure storage of credentials/tokens
- Audit trails + safe apply workflow
- Production deployment

This roadmap is written so Claude can work in **one chat** and do **one task per message**, only proceeding when you say “Next”.

---

## How to run tasks with Claude (critical rules)

1) **One task per message**: Claude must complete exactly one task, then stop.
2) Claude must end with: **“Say ‘Next’ to continue.”**
3) Claude should only check tasks in this file when you say: **“Mark Task X.Y complete”**.
4) Security rule: never log secrets; redact credentials; encrypt at rest.

---

# Epic 0 — Decide the target architecture (required first)

## Task 0.1 — Architecture decision record (ADR) for SaaS stack
- [ ] Task 0.1

### Goal
Write an ADR that locks:
- Frontend: (Next.js/React recommended)
- Backend: FastAPI (keep) or move to e.g. Django/Nest (recommend keep FastAPI)
- DB: Postgres
- Background jobs: Celery/RQ/Arq
- Cache/queue: Redis
- Auth: Clerk/Auth0/NextAuth OR custom JWT + password + email verify
- Billing: Stripe subscriptions
- Hosting: Fly.io/Render/AWS (pick one)
- Secret encryption: KMS or app-level envelope encryption

### Claude prompt
```text
Task 0.1 only: create docs/adr/0001-saas-architecture.md proposing a stack for a multi-tenant paid SaaS web app for DanDomain price optimization.
Include: components, data flow, security model, tenant isolation, deployment model, tradeoffs, and why.
Stop after this task.
```

Acceptance: ADR exists and is concrete enough to implement.

---

# Epic 1 — Repository restructure into a real web app (monorepo)

## Task 1.1 — Create monorepo layout (no features yet)
- [ ] Task 1.1

### Target layout (recommended)
- `apps/web/` (Next.js frontend)
- `apps/api/` (FastAPI backend)
- `packages/shared/` (types/DTOs)
- `infra/` (docker compose, migrations, scripts)

### Claude prompt
```text
Task 1.1 only: restructure repo into a monorepo layout:
- move current FastAPI backend into apps/api
- keep current Streamlit UI temporarily under legacy/streamlit (do not delete yet)
- add apps/web skeleton (Next.js) with minimal homepage
- add root README updates explaining dev commands
Ensure existing backend tests still run.
Stop after this task.
```

---

# Epic 2 — Multi-tenancy, auth, and permissions (core SaaS)

## Task 2.1 — Add database + migrations (Postgres)
- [ ] Task 2.1

Claude prompt:
```text
Task 2.1 only: add Postgres persistence to apps/api:
- SQLAlchemy/SQLModel + Alembic
- docker-compose with postgres
- create initial migration scaffolding
- add config via env vars
Stop after this task.
```

## Task 2.2 — Tenant model + user model
- [ ] Task 2.2

Goal tables:
- tenants (id, name, created_at, stripe_customer_id, plan, status)
- users (id, tenant_id, email, password_hash OR external_auth_id, role)
- memberships if you want many-to-many

Claude prompt:
```text
Task 2.2 only: implement Tenant + User schema + migrations + basic CRUD admin endpoints (protected).
Define roles: owner, admin, operator, viewer.
Stop after this task.
```

## Task 2.3 — Authentication (choose one: external provider or JWT)
- [ ] Task 2.3

Claude prompt:
```text
Task 2.3 only: implement auth end-to-end:
- signup/login (or external provider integration)
- session/JWT
- tenant scoping on all API routes
- middleware that sets current_user + current_tenant
Add tests for auth + tenant isolation.
Stop after this task.
```

## Task 2.4 — RBAC authorization middleware
- [ ] Task 2.4

Claude prompt:
```text
Task 2.4 only: implement role-based access control:
- viewer: read-only
- operator: can run optimize and create batches
- admin/owner: can apply prices + manage credentials + billing
Add tests for forbidden/allowed routes per role.
Stop after this task.
```

---

# Epic 3 — Secure DanDomain credential storage (per tenant)

## Task 3.1 — Credential vault model + encryption-at-rest
- [ ] Task 3.1

Goal:
- tenants store DanDomain connection info (username + password OR token) encrypted
- never return raw secrets via API
- rotate credentials

Claude prompt:
```text
Task 3.1 only: implement encrypted credential storage:
- DB table tenant_credentials (tenant_id, encrypted_blob, created_at, updated_at)
- use envelope encryption (Fernet with master key from env is acceptable for MVP; document upgrade path to KMS)
- API endpoints:
  - PUT /tenant/credentials (store)
  - DELETE /tenant/credentials
  - GET /tenant/credentials/status (no secrets)
Add tests ensuring secrets not logged/returned.
Stop after this task.
```

---

# Epic 4 — Billing (Stripe subscriptions) + gating

## Task 4.1 — Stripe customer + subscription integration
- [ ] Task 4.1

Claude prompt:
```text
Task 4.1 only: integrate Stripe billing:
- create checkout session endpoint
- webhook handler to update tenant plan/status
- store stripe_customer_id + subscription status in tenants table
- add billing gating: only active paid tenants can run optimize/apply
Add webhook tests (signature verify mocked).
Stop after this task.
```

---

# Epic 5 — Replace Streamlit with real web UI (Next.js)

## Task 5.1 — Web app scaffold + auth UI + tenant switcher
- [ ] Task 5.1

Claude prompt:
```text
Task 5.1 only: implement apps/web:
- Next.js app router
- login flow UI (matching backend choice)
- tenant dashboard skeleton + navigation
Stop after this task.
```

## Task 5.2 — Price Optimizer UI (web)
- [ ] Task 5.2

Features:
- configure run (filters, beautify digit, price pct)
- fetch brands
- run optimization
- table with filters/search
- top 5 increases/decreases includes product title
- export CSV

Claude prompt:
```text
Task 5.2 only: implement Price Optimizer page in apps/web:
- call API to fetch brands and run optimization
- render results table with filters + top 5 sections
- ensure TITLE_DK (or title) visible
- export CSV
Stop after this task.
```

---

# Epic 6 — Safe apply workflow (SaaS version)

(Use the same concepts you already built: dry-run → batch manifest → guarded apply → audit → verify.)

## Task 6.1 — Persist batches in DB (replace file-based manifests)
- [ ] Task 6.1

Claude prompt:
```text
Task 6.1 only: implement DB persistence for batches:
- tables: optimize_runs, apply_batches, apply_batch_rows, audit_log
- store summary + rows + params
- tenant-scoped
- endpoints: create dry-run batch, list batches, get batch details
Stop after this task.
```

## Task 6.2 — Apply endpoint with guardrails + audit + idempotency
- [ ] Task 6.2

Claude prompt:
```text
Task 6.2 only: implement apply endpoint using stored batch rows:
- hard guardrails (row count, pct bounds, positive finite)
- per-row margin/cost rules
- audit log with who/when
- idempotency: cannot apply same batch twice unless explicit override
Add tests.
Stop after this task.
```

## Task 6.3 — Verification step (re-fetch and confirm)
- [ ] Task 6.3

Claude prompt:
```text
Task 6.3 only: implement post-apply verification:
- after apply, re-fetch affected products and confirm prices match
- store verification result
- show in web UI
Stop after this task.
```

---

# Epic 7 — Background jobs + rate limits + reliability

## Task 7.1 — Job queue for optimize/apply
- [ ] Task 7.1

Claude prompt:
```text
Task 7.1 only: add background jobs (Celery/RQ/Arq):
- optimize runs execute async
- apply executes async
- API returns job_id and UI polls
Stop after this task.
```

---

# Epic 8 — Production deployment

## Task 8.1 — Docker + deployment docs + environment config
- [ ] Task 8.1

Claude prompt:
```text
Task 8.1 only: create production-ready Docker setup:
- apps/web and apps/api containers
- postgres and redis
- environment variable docs
- secure defaults
Stop after this task.
```

## Task 8.2 — Observability (health, logging, metrics)
- [ ] Task 8.2

Claude prompt:
```text
Task 8.2 only: add /health, structured logs, request IDs, and basic metrics.
Stop after this task.
```

---

# Epic 9 — Compliance & security hardening (recommended)

## Task 9.1 — Threat model + security checklist
- [ ] Task 9.1

Claude prompt:
```text
Task 9.1 only: write docs/security/threat-model.md covering tenant isolation, secrets handling, billing abuse, SSRF risks, logging redaction, and API auth.
Stop after this task.
```

---

## Notes on your current codebase
You currently have:
- a FastAPI backend that can fetch brands + optimize
- a Streamlit UI (legacy) that provides UX for optimizing and (in later stages) dry-run/apply
This roadmap migrates that into a multi-tenant paid SaaS.
