# SB-Optima — DanDomain Price Optimizer 🚀

Multi-tenant SaaS application for DanDomain price optimisation.

## Architecture

| Layer | Technology | Directory |
|---|---|---|
| **Frontend** | Next.js 16 + TypeScript + Tailwind | `frontend/` |
| **Backend** | FastAPI + SQLAlchemy + Alembic | `backend/` |
| **Domain** | Pure Python business logic | `domain/` |
| **Database** | PostgreSQL 16 | via Docker Compose |
| **Cache/Queue** | Redis 7 + Arq | via Docker Compose |
| **Billing** | Stripe subscriptions | `backend/billing_api.py` |

## Features

- **Multi-tenant** with JWT authentication and RBAC (owner/admin/operator/viewer).
- Calculates coverage rates (profit margins) and adjusts sales prices.
- Price beautification (ending in 9/5/0) while respecting margin constraints.
- Encrypted credential vault (Fernet) for DanDomain API keys.
- Async job queue for optimization runs.
- Dry-run preview and guarded real-apply with audit trail.
- Stripe billing integration with plan-based quotas.
- Prometheus metrics, structured JSON logging, request correlation.

## How to Run Locally

### Prerequisites

- [Node.js](https://nodejs.org/) 18+ (for the frontend)
- [Docker](https://docs.docker.com/get-docker/) and
  [Docker Compose](https://docs.docker.com/compose/) (v2+)

### Quick start

```bash
# 1. Create your local env file (edit as needed)
cp .env.example .env

# 2. Start backend services (FastAPI + Postgres + Redis)
docker compose -f infra/docker-compose.yml up --build

# 3. Verify the backend is running
curl http://localhost:8000/health
# → {"status":"ok"}

# 4. In a separate terminal, start the Next.js frontend
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

The frontend reads `NEXT_PUBLIC_API_URL` from `.env.local` (defaults to
`http://localhost:8000`).

### Database migrations (Alembic)

```bash
# Apply all pending migrations (run from repo root)
DATABASE_URL=postgresql+psycopg2://sboptima:sboptima@localhost:5432/sboptima \
  alembic upgrade head

# Create a new auto-generated migration
DATABASE_URL=postgresql+psycopg2://sboptima:sboptima@localhost:5432/sboptima \
  alembic revision --autogenerate -m "describe your change"
```

### Stopping / cleaning up

```bash
docker compose -f infra/docker-compose.yml down
docker compose -f infra/docker-compose.yml down -v   # also remove volumes
```

## Running Tests

```bash
pip install -r requirements.txt
pip install pytest httpx fastapi fpdf2
python -m pytest tests/ -v
```

## Write Path — All Writes go through the Backend

All price-apply writes are routed through the FastAPI backend's guarded endpoints:

1. **`POST /apply-prices/create-manifest`** — submit price changes; backend
   persists a batch manifest and returns a `batch_id`.
2. **`POST /apply-prices/apply`** — confirm with `batch_id`; backend enforces
   guardrails (env gating, per-row safety, audit log, idempotency).

## SOAP rate limiting

All SOAP calls routed through `DanDomainClient` are rate-limited per caller
so that one tenant cannot starve another.

| Env var | Default | Description |
|---|---|---|
| `SOAP_MAX_CONCURRENT` | `3` | Maximum concurrent SOAP calls per caller. |
| `SOAP_CALL_DELAY_S` | `0.2` | Minimum seconds between successive SOAP calls per caller. |
| `SOAP_RATE_LIMIT_PER_S` | `5.0` | Reserved for future token-bucket rate. |