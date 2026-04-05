# Repo Overview — SB‑Optima / Coverage Optimizer

## 1. Purpose

This repository contains **SB‑Optima / Coverage Optimizer**, a toolset for:

- Optimizing product prices for a DanDomain shop based on margin / coverage rate.
- Providing a **Streamlit UI** for analysts/operators.
- Exposing a **FastAPI backend** for optimization, dry‑run “apply” previews, and guarded real apply.
- Enforcing **strict safety & audit guardrails** around price changes.

High‑level flow:

1. User uploads or fetches product data.
2. Domain logic computes coverage/margins and suggested new prices.
3. User reviews results (including risk views).
4. User optionally performs a **dry‑run** to persist a batch manifest.
5. Later, a guarded **real apply** endpoint can write changes to the shop, with multiple safety layers.

---

## 2. Top‑Level Layout

At the repo root:

- `app.py` — Streamlit app entrypoint.
- `backend/` — FastAPI backend (API endpoints).
- `domain/` — Core domain logic: pricing, product loading, invoices, risk analysis, suppliers.
- `ui/` — Shared UI helpers for Streamlit (styles, backend URL handling, pages).
- `tests/` — Test suite for backend, domain, and safety logic.
- `data/` — Local data files:
  - HostedShop / currency‑converter docs (single source of truth for that API).
  - `data/apply_batches/` — stored dry‑run manifests (see roadmap tasks 2.5.x).
  - `data/apply_audit.log` — JSONL audit log for real applies.
- `CLAUDE.md` — Agent authority/policy instructions.
- `ROADMAP.md` — Single checklist for backend + Streamlit roadmap (one task per message).
- `SAAS_ROADMAP.md` — Longer‑term SaaS multi‑tenant roadmap.
- `requirements.txt` — Python dependencies.

---

## 3. FastAPI Backend (`backend/`)

The backend is the **API layer** used by Streamlit and any other clients.

Key modules:

- `backend/main.py`
  - FastAPI app creation and router wiring.
  - Mounts sub‑routers such as:
    - Brands / shop connectivity.
    - Optimization.
    - Dry‑run apply.
    - Real apply.
    - Health/config endpoints (see roadmap Stage 4.3).
  - Central place to look for:
    - CORS settings.
    - Root routes.
    - Dependency injection (if any).

- `backend/brands_api.py`
  - Endpoints to:
    - Check DanDomain connectivity.
    - Fetch brands / catalog metadata for the UI.
  - Uses `dandomain_api.py` or related helpers to talk to the external DanDomain API.
  - Often involved in **“backend URL connectivity”** checks from the Streamlit UI (see roadmap Task 1.2 and 4.3).

- `backend/optimizer_api.py`
  - Endpoints for **price optimization**:
    - Accepts optimization parameters (filters, beautify digit, min coverage rate, etc.).
    - Uses `domain/pricing.py`, `domain/product_loader.py`, and related helpers.
    - Returns:
      - Optimized product rows.
      - Per‑row coverage and price change details.
      - Summaries for UI tables (top increases/decreases, etc.).

- `backend/apply_constants.py`
  - Centralized **guardrail constants** for apply operations:
    - Max rows per batch.
    - Max allowed percentage price change.
    - Minimum coverage / margin.
    - Any other hard‑coded bounds shared between dry‑run and real apply logic.

- `backend/apply_prices_api.py`
  - **Dry‑run apply** endpoint(s), e.g. `POST /apply-prices/dry-run`.
  - Responsibilities (see ROADMAP Stage 2.1 and 2.5.1):
    - Accept optimization payload and optional filters.
    - Compute change set (which rows would change, from which price to which).
    - Persist a **batch manifest** to `data/apply_batches/{batch_id}.json` with:
      - `batch_id` (UUID4).
      - `created_at` (UTC ISO).
      - `optimize_payload`.
      - `product_numbers` list or `null`.
      - `changes`.
      - `summary`.
    - Return at least `{batch_id, changes, summary}` to the caller.
    - Provide `GET /apply-prices/batch/{batch_id}`:
      - Strict UUID validation.
      - Load and return manifest JSON.
      - 404 if missing.

- `backend/apply_real_api.py`
  - **Real apply** endpoint(s), e.g. `POST /apply-prices/apply`.
  - Responsibilities (see ROADMAP Stage 3 and 4.x):
    - Accept `{batch_id, confirm}` payload.
    - Validate:
      - `batch_id` is a UUID.
      - `confirm` is `true`.
    - Load manifest from `data/apply_batches/{batch_id}.json`.
    - Enforce **hard guardrails** (from `apply_constants.py`):
      - Max rows per batch.
      - Positive/finite new prices.
      - Absolute percentage change within configured bounds.
    - Enforce **per‑row safety** (Stage 4.1):
      - No selling below buy price where available.
      - Resulting coverage not below `MIN_COVERAGE_RATE`.
      - Apply valid rows only; collect invalid rows in `failed`.
    - Handle **idempotency** (Stage 3.1):
      - Create `data/apply_batches/{batch_id}.applied` marker on success.
      - Block double‑apply with 4xx unless explicit override (not implemented in 3.1).
    - Write **audit log** entries to `data/apply_audit.log` (JSONL) with:
      - Timestamp.
      - `batch_id`.
      - `api_username` / actor.
      - `total_rows`, `applied_count`, `failed_count`.
    - Honor **environment gating** (Stage 4.2):
      - Read `SB_OPTIMA_ENABLE_APPLY` from environment.
      - If disabled, return 403/503 with clear message.
    - Response shape includes:
      - `batch_id`.
      - `applied_count`.
      - `failed` list with structured reasons.
      - `started_at`, `finished_at`.

- Health/config endpoints (per ROADMAP 4.3)
  - Implemented in `backend/main.py` or dedicated module.
  - `GET /health` returns JSON:
    - `status: "ok"` when up.
    - `version` (constant, env, or git SHA).
    - `apply_enabled` (from `SB_OPTIMA_ENABLE_APPLY`).
    - `timestamp` (UTC ISO).
  - Streamlit uses this instead of probing `/brands` with fake credentials.

---

## 4. Domain Layer (`domain/`)

Pure business logic; aim to keep this side‑effect‑free where possible.

- `domain/pricing.py`
  - Core **pricing and coverage rate logic**:
    - Calculation of `PRICE_EX_VAT` from gross prices (25% Danish VAT by default).
    - Computation of current coverage/margins.
    - Determining suggested new prices to meet minimum margin (e.g., 50%).
    - “Beautification” of prices (e.g., round/up to end in 9) while respecting margin constraints.
  - Shared between backend and any other callers (UI should go through backend, not call this directly).

- `domain/product_loader.py`
  - Loading and normalizing product data:
    - From CSV files or DanDomain API responses.
    - Parsing numerical fields (prices, costs).
    - Handling missing/invalid rows.
  - Provides data structures/DataFrames used by `pricing.py` and `optimizer_api.py`.

- `domain/risk_analysis.py`
  - Implements **risk view** helpers (Stage 5.1):
    - Compute largest price decreases.
    - Identify near‑cost / low‑margin rows.
    - Prepare histogram/binned statistics for Change %.
  - Designed to be testable independently of Streamlit.

- `domain/invoice_ean.py`
  - Invoice + EAN related logic:
    - Parse invoice PDFs/CSVs (if relevant).
    - Extract and analyze pricing related to invoices.
  - Covered by extensive tests (`tests/test_invoice_ean.py`).

- `domain/supplier.py`
  - Supplier‑related domain logic:
    - Represent suppliers.
    - Load and analyze supplier data (e.g., buy prices, terms).
    - Support coverage/margin analysis per supplier.

---

## 5. Next.js Frontend (`frontend/`)

The Next.js app is the **primary human interface** to the optimizer.

- `frontend/src/app/` — App Router pages:
  - `/login`, `/signup` — Authentication pages.
  - `/dashboard` — Tenant dashboard with plan, usage, credentials.
  - `/optimizer` — Price Optimizer (brand selector, async jobs, results table,
    risk view, dry-run + apply flow, CSV export).
  - `/history` — Jobs, batches, audit event history with filters.
  - `/billing` — Plan info, billing status, Stripe checkout.
- `frontend/src/lib/api.ts` — API client for all backend communication.
- `frontend/src/lib/auth-context.tsx` — JWT auth context/provider.
- `frontend/src/components/` — Shared components (Navbar, RequireAuth, PageShell).

---

## 6. Tests (`tests/`)

Tests are critical for safety and regression protection. Key test modules:

- `tests/test_price_optimizer_backend.py`
  - End‑to‑end behavior for optimization backend.
  - Ensures:
    - Correct calculation of coverage/margins.
    - Respect for minimum coverage rate.
    - Correct beautification behavior.

- `tests/test_apply_prices_dry_run.py`
  - Tests for the **dry‑run** pipeline:
    - `POST /apply-prices/dry-run` behavior.
    - Manifest persistence under `data/apply_batches/`.
    - Response shape (`batch_id`, `changes`, `summary`).
    - `GET /apply-prices/batch/{batch_id}` behavior.

- `tests/test_apply_real.py`
  - Tests for **real apply**:
    - Guardrails (max rows, change percentage limits).
    - Per‑row safety checks (below cost, below min coverage).
    - Audit log behavior.
    - Idempotency / `.applied` markers.
    - Environment gating via `SB_OPTIMA_ENABLE_APPLY`.

- `tests/test_backend_url.py`
  - Behavior of `ui/backend_url.py`:
    - URL normalization.
    - Connectivity check logic.
    - Backend status indicator.

- `tests/test_brands_api.py`
  - Brand/shop connectivity endpoints:
    - Success and failure modes.
    - Handling of credentials / errors.

- `tests/test_invoice_ean.py`, `tests/test_supplier.py`
  - Domain logic for invoices and suppliers.

- `tests/test_result_summary.py`, `tests/test_risk_analysis.py`
  - Summary and risk analysis helpers:
    - Top increases/decreases tables.
    - Risk metrics and histogram helpers.

- `tests/test_push_safety.py`
  - Tests for `push_safety.py`:
    - Safety rules for writing to DanDomain.
    - Ensures no unsafe pushes slip through.

---

## 7. Safety & Operational Guardrails

When extending or modifying functionality, keep these principles:

- **No direct shop writes from UI or domain**:
  - All writes must go through guarded backend endpoints (see `apply_real_api.py` and `push_safety.py`).
- **Guardrails before writes**:
  - Max batch size.
  - Bounds on percentage changes.
  - No selling below buy price when known.
  - No coverage below configured minimum.
- **Audit and idempotency**:
  - Every real apply must be auditable via `data/apply_audit.log`.
  - A batch must not be applied twice without explicit override logic.
- **Environment gating**:
  - Applying prices requires `SB_OPTIMA_ENABLE_APPLY=true` (or equivalent).
  - Lower environments should default to “apply disabled”.
- **HostedShop / currency API**:
  - The single source of truth is `data/hostedshop_docs/hostedshop_api_docs_full.md`.
  - Do **not** infer undocumented endpoints or behavior.

---

## 8. How Agents (Claude / Opus / Copilot) Should Use This Repo

When an AI agent starts a session in this repo, it should:

1. **Read `CLAUDE.md` first** to understand authority and constraints.
2. **Read this `docs/REPO_OVERVIEW.md`** to build a mental model of:
   - Overall purpose.
   - Backend vs domain vs UI responsibilities.
   - Safety constraints and where they live.
3. **Reuse this mental model** for subsequent tasks in the same session:
   - Do not re‑summarize the whole repo each time.
   - Only open additional files when needed for the current task.
4. **Respect guardrails and tests**:
   - Prefer changing or extending existing helpers instead of duplicating logic.
   - Add or update tests whenever changing pricing, safety, or apply logic.

This overview is intentionally high‑level and stable so agents can quickly orient themselves and then work efficiently on specific tasks using `ROADMAP.md` and `SAAS_ROADMAP.md`.
