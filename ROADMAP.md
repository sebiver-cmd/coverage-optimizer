# SB‑Optima / Coverage Optimizer — Full Roadmap (Claude‑driven)

This document is the **single checklist** for the project roadmap. It is designed so you can work with Claude in **one chat thread** and avoid back-and-forth across chats.

## Related roadmap (SaaS)
For the long-term multi-tenant paid web app roadmap, see: `SAAS_ROADMAP.md`.

---

## How to use this roadmap with Claude (critical rules)

### Rule 1 — One task at a time
Claude must work on **exactly ONE task per message**.

- You (human) will say: **“Start Task X.Y”** or **“Next”**.
- Claude must **not** begin the next task automatically.
- Claude should finish the current task, then stop and end with: **“Say ‘Next’ to continue.”**

### Rule 2 — Mark tasks complete only when asked
Claude may summarize progress, but should only change task checkboxes when you explicitly say:
- **“Mark Task X.Y complete”**, or
- **“Update ROADMAP.md checkmarks”**

### Rule 3 — Constraints apply unless a task explicitly overrides them
- Do **not** change optimization logic unless instructed by the task.
- Do **not** touch shop write/push logic until **Stage 3**.
- Prefer shared utilities over duplicated logic.
- Add/update tests for new helpers/endpoints.
- Keep endpoints backwards compatible unless explicitly stated.

---

## Status Legend
- [ ] Not started
- [x] Done (mark only when confirmed)

---

# Stage 0 — Streamlit reliability & consistency (polish)

## Task 0.1 — Centralize backend URL normalization + connectivity check
- [x] Task 0.1

### Notes (what “done” means)
Implemented a shared `ui/backend_url.py` and refactored Streamlit callers to use it; added/updated tests and ensured credentials are passed to `/brands` connectivity check.

---

# Stage 1 — Read‑only review UX (historical)

## Task 1.1 — Result summary tables show titles (Top 5 increases/decreases include TITLE_DK)
- [x] Task 1.1

## Task 1.2 — Backend URL default is 127.0.0.1 + status indicator
- [x] Task 1.2

## Task 1.3 — Disable dashboard fetch UI (legacy fetch button removed/disabled with explanation)
- [x] Task 1.3

---

# Stage 2 — Dry‑run apply (historical)

## Task 2.1 — Backend `POST /apply-prices/dry-run` + Streamlit “Preview apply” UI
- [x] Task 2.1

---

# Stage 2.5 — Batch manifest (still no shop writes)

## Task 2.5.1 — Persist dry‑run manifest + return `batch_id`
- [x] Task 2.5.1

### Goal
Create deterministic “approved change set” so later apply can use exactly what was reviewed.

### Claude prompt (copy/paste)
```text
You are working in repo `sebiver-cmd/coverage-optimizer`.

CRITICAL: Work on exactly ONE task: Task 2.5.1 only. Do not start any other tasks.

Task 2.5.1 — Persist dry-run manifests with batch_id (still no shop writes)

Backend:
1) Extend POST /apply-prices/dry-run:
- Generate batch_id (UUID4 string)
- Persist JSON to data/apply_batches/{batch_id}.json (create dirs)
- Manifest must include:
  - batch_id
  - created_at (UTC ISO)
  - optimize_payload (full)
  - product_numbers (full list or null)
  - changes
  - summary
- Response must include {batch_id, changes, summary}

2) Add GET /apply-prices/batch/{batch_id}:
- Validate batch_id strictly as UUID (prevent path traversal)
- Load and return manifest JSON
- 404 if missing

Streamlit:
- After dry-run preview, display batch_id prominently
- Add optional “Load batch” UI:
  - input batch_id + button
  - call GET /apply-prices/batch/{batch_id}
  - render stored summary + changes table

Tests:
- dry-run returns batch_id and file created
- manifest JSON has required fields
- GET returns same content
- UUID validation (bad IDs rejected)
- 404 missing batch

Safety:
- No shop writes
- No import of write clients or push_safety in this feature

Finish by:
- listing files changed
- listing tests added/updated
- confirming tests pass
Then STOP and say: “Say ‘Next’ to continue.”
```

---

# Stage 3 — Real apply (writes enabled) with strict guardrails

## Task 3.1 — Apply stored manifest: `POST /apply-prices/apply` (small batches)
- [ ] Task 3.1

### Claude prompt (copy/paste)
```text
You are working in repo `sebiver-cmd/coverage-optimizer`.

CRITICAL: Work on exactly ONE task: Task 3.1 only. Do not start any other tasks.

Task 3.1 — Implement guarded real apply endpoint consuming batch manifests

Prereq: batch manifests exist at data/apply_batches/{batch_id}.json from Task 2.5.1.

Backend: POST /apply-prices/apply
Request:
- batch_id: str (UUID)
- confirm: bool (must be true)

Behavior:
1) Validate batch_id as UUID; reject invalid with 422 or 400
2) Load manifest by batch_id; 404 if missing
3) Hard guardrails (reject entire request with 400 if violated):
   - number of change rows <= 100 (configurable constant is fine)
   - new_price is positive and finite
   - abs(change_pct) <= 30 (constant)
4) Apply writes using the existing single write path (do NOT duplicate SOAP logic).
5) Write an audit log line (jsonl) appended to data/apply_audit.log with:
   - timestamp, batch_id, api_username, total_rows, applied_count, failed_count
6) Prevent double-apply:
   - create data/apply_batches/{batch_id}.applied marker on success
   - if marker exists, block with 409 (or 400) unless explicit override exists (do NOT implement override in this task)

Response:
- batch_id
- applied_count
- failed: list of {NUMBER, reason}
- started_at, finished_at

Streamlit:
- Add “Apply prices” UI shown only when a batch_id exists in session state
- Require user confirmation (type “APPLY” or checkbox)
- Show guardrail summary
- Show results (applied/failed)

Tests:
- confirm=false fails
- >100 rows fails
- out-of-range change_pct fails
- valid manifest triggers write function (mock it)
- double apply blocked

Finish by:
- listing files changed
- listing tests added/updated
- confirming tests pass
Then STOP and say: “Say ‘Next’ to continue.”
```

---

# Stage 4 — Production safety improvements

## Task 4.1 — Per‑row margin/cost guardrails (partial success)
- [ ] Task 4.1

### Goal
Ensure **no individual product** is pushed below cost or below your global minimum coverage rate, while still allowing partial success (apply valid rows, reject unsafe ones with reasons).

### Claude prompt (copy/paste)
```text
You are working in repo `sebiver-cmd/coverage-optimizer`.

CRITICAL: Work on exactly ONE task: Task 4.1 only. Do not start any other tasks.

Task 4.1 — Per-row margin/cost guardrails (partial success)

Context:
- There is already an optimization pipeline and (from Stage 3) an apply endpoint that can write price changes based on a batch manifest.
- We now want an additional layer of safety at APPLY TIME for each individual row.

Requirements:

Backend:
1) Add per-row guardrail checks during apply (in the apply pipeline, not in the optimizer):
   - If a row has buy_price (or equivalent cost field):
     - Block the change if new_price <= buy_price (no selling below buy price).
   - Compute resulting coverage/margin using the same logic as optimization (MIN_COVERAGE_RATE constant).
     - Block the change if resulting coverage < MIN_COVERAGE_RATE.
2) Behavior on violations:
   - Do NOT abort the whole batch just because a few rows fail.
   - Instead:
     - Apply all VALID rows.
     - Collect FAILED rows with structured reasons (e.g., "below_cost", "below_min_coverage").
3) Response shape:
   - Extend the apply response (from Task 3.1) with:
     - `failed: list[{NUMBER, reason, current_price, new_price, buy_price, current_coverage_pct, suggested_coverage_pct}]` (include whatever fields are already available; do not invent data that isn't in manifest).
   - `applied_count` should reflect only successfully applied rows.

4) Audit log:
   - Ensure audit log entry records:
     - number of attempted rows
     - number_applied
     - number_failed
   - Do not log raw credentials or secrets.

UI (Streamlit apply view):
1) Update the Apply results section to:
   - Show a small summary: "Applied X rows, blocked Y rows by safety rules."
   - Show a table of failed rows with reasons (e.g., “Below cost”, “Below minimum coverage”).
2) Keep this read-only and descriptive. Do NOT add overrides in this task.

Tests:
1) Unit/integration tests for apply logic:
   - When all rows safe: all applied, failed empty.
   - When some rows below cost: those rows in failed, others applied.
   - When some rows below MIN_COVERAGE_RATE: those rows in failed, others applied.
   - Mixed case: both cost and coverage violations appear with correct reasons.
2) Ensure audit log test reflects applied vs failed counts.

Finish by:
- listing files changed
- listing tests added/updated
- confirming tests pass

Then STOP and say: “Say ‘Next’ to continue.”
```

---

## Task 4.2 — Environment gating (enable apply only when SB_OPTIMA_ENABLE_APPLY=true)
- [ ] Task 4.2

### Goal
Prevent accidental real applies in environments where they shouldn’t happen (e.g., local dev, demo). Only allow the apply endpoint when a specific environment flag is enabled.

### Claude prompt (copy/paste)
```text
You are working in repo `sebiver-cmd/coverage-optimizer`.

CRITICAL: Work on exactly ONE task: Task 4.2 only. Do not start any other tasks.

Task 4.2 — Environment gating for apply (SB_OPTIMA_ENABLE_APPLY=true)

Requirements:

Backend:
1) Add an environment configuration flag:
   - Name: SB_OPTIMA_ENABLE_APPLY
   - Values:
     - absent or "false"/"0" => apply is DISABLED
     - "true"/"1" => apply is ENABLED
   - Parse this once at startup or via a small config helper.

2) Update the real apply endpoint (from Task 3.1):
   - If apply is DISABLED:
     - Return 403 Forbidden (or 503 with clear message) for any request.
     - Message should clearly say apply is disabled and controlled by SB_OPTIMA_ENABLE_APPLY.
   - If ENABLED:
     - Proceed with existing validations/guardrails from earlier tasks.

3) Do NOT weaken any existing guardrails. This is an additional outer gate.

UI (Streamlit):
1) When loading the app, determine whether apply is enabled:
   - Prefer to use a lightweight backend endpoint or configuration response if such exists.
   - If there is no config endpoint yet, temporarily:
     - Call the apply endpoint with a harmless probe OR add a small `/config` or `/health` endpoint (if not already implemented) that exposes an `apply_enabled` flag.
   - In this task, you may add a minimal `/config` or enhance `/health` ONLY for this flag if needed, but do not add lots of extra fields (those belong to Task 4.3).

2) In the Apply UI:
   - If apply is disabled:
     - Hide or disable the Apply button.
     - Show an info/warning message like: "Apply to shop is disabled in this environment. Set SB_OPTIMA_ENABLE_APPLY=true to enable."
   - If enabled:
     - Show the normal Apply UI.

Tests:
1) Backend tests:
   - With SB_OPTIMA_ENABLE_APPLY not set or set to false:
     - Apply endpoint returns 403/503 with clear message.
   - With SB_OPTIMA_ENABLE_APPLY=true:
     - Apply endpoint passes through to existing logic (mock downstream behavior).
2) UI tests (if you have any for Streamlit; otherwise rely on manual testing notes):
   - At least verify the new flag-handling helpers in isolation if they exist.

Finish by:
- listing files changed
- listing tests added/updated
- confirming tests pass

Then STOP and say: “Say ‘Next’ to continue.”
```

---

## Task 4.3 — Add `GET /health` and use it for UI connectivity status
- [ ] Task 4.3

### Goal
Provide a standard health endpoint that tells the UI and ops whether the backend is up, what version is running, and whether apply is enabled, and use it instead of hitting /brands for health.

### Claude prompt (copy/paste)
```text
You are working in repo `sebiver-cmd/coverage-optimizer`.

CRITICAL: Work on exactly ONE task: Task 4.3 only. Do not start any other tasks.

Task 4.3 — Health endpoint + Streamlit connectivity via /health

Requirements:

Backend:
1) Add a GET /health endpoint (e.g., in backend/main.py or a dedicated module):
   - Returns JSON like:
     {
       "status": "ok",
       "version": "<app_version or git SHA if available>",
       "apply_enabled": <bool>,    // from SB_OPTIMA_ENABLE_APPLY
       "timestamp": "<UTC ISO8601>"
     }
   - status should be "ok" if the app is up and able to serve requests.
   - version can be:
     - a hard-coded __version__ constant, or
     - derived from package metadata or env var (keep it simple for now).

2) Do NOT perform slow or external checks in /health (no DanDomain calls, no DB migrations, etc.). It should be fast and reliable.

3) Optionally, return a distinct status if a critical dependency is clearly misconfigured (e.g., missing required env vars), but do not over-engineer for this task.

Streamlit:
1) Update the “Backend connection” / connectivity check logic to use /health:
   - Call GET {backend_url}/health with a short timeout.
   - If status == "ok":
     - show success indicator, optionally with version + apply_enabled info.
   - If request fails or status != "ok":
     - show error with a short, helpful message and (optionally) a collapsed technical detail area.

2) Remove or deprecate the existing “probe /brands with fake credentials” style connectivity check, since /health is cheaper and more appropriate.

Tests:
1) Backend:
   - Test /health returns 200 and JSON with required keys.
   - Test apply_enabled reflects SB_OPTIMA_ENABLE_APPLY flag correctly (true/false).
2) Basic integration/regression:
   - Ensure /brands and /optimize behavior is unchanged by this feature.

Finish by:
- listing files changed
- listing tests added/updated
- confirming tests pass

Then STOP and say: “Say ‘Next’ to continue.”
```

---
---

# Stage 5 — Optional analyst features (read‑only)

## Task 5.1 — Risk view (largest decreases, near-cost warnings, histogram)
- [ ] Task 5.1

### Goal
Give analysts a “risk” lens on an optimization run: highlight large decreases, near-cost prices, and the distribution of percentage changes. This is read-only analysis on the optimization results.

### Claude prompt (copy/paste)
```text
You are working in repo `sebiver-cmd/coverage-optimizer`.

CRITICAL: Work on exactly ONE task: Task 5.1 only. Do not start any other tasks.

Task 5.1 — Risk view (largest decreases, near-cost warnings, histogram)

Requirements:

Backend:
- No new backend endpoints are strictly required for this task.
- You may add small pure helper functions in the UI layer or a shared analysis module to calculate risk metrics from the optimization DataFrame/JSON.

UI (Streamlit Price Optimizer page):
1) Add a “Risk view” section/tab associated with the current optimization results.
2) Include at least three elements:

   a) Largest decreases:
      - Table: top N (e.g., 20) rows with the biggest negative percentage change in price (Change %).
      - Include key columns: NUMBER, TITLE/TITLE_DK, current_price, new_price, change_pct, buy_price (if available).
      - Sort descending by absolute negative change (largest drop first).

   b) Near-cost / low-margin warnings:
      - Table or callout listing rows where:
        - new_price is within a small threshold above buy_price (e.g., <= 5% margin) OR
        - resulting coverage/margin is close to MIN_COVERAGE_RATE (e.g., within 2–3 percentage points).
      - Thresholds can be constants in code; document them and make them easy to adjust.

   c) Histogram of Change %:
      - A simple histogram or binned bar chart of change_pct across all affected rows.
      - Use Streamlit’s built-in charting (e.g., st.bar_chart / altair) and avoid heavy dependencies.

3) Read-only:
   - Do NOT add any buttons that trigger writes or applies.
   - This is purely investigative/analytical UI.

Implementation guidance:
- Consider adding small helper functions:
  - compute_largest_decreases(df, n=20)
  - compute_near_cost_rows(df, margin_threshold, coverage_slack)
  - compute_change_pct_histogram(df, bins=20)
- Keep helpers testable and independent of Streamlit.

Tests:
1) Unit tests for helper functions:
   - largest_decreases returns correct rows & sort order.
   - near_cost_rows flags entries correctly for varied buy_price/margin scenarios.
   - histogram bin logic yields predictable bin counts for toy data.
2) No backend changes should be required; verify existing tests still pass.

Finish by:
- listing files changed
- listing tests added/updated
- confirming tests pass

Then STOP and say: “Say ‘Next’ to continue.”
```

---

## Completed Tasks (auto list)
- [x] 0.1
- [x] 1.1
- [x] 1.2
- [x] 1.3
- [x] 2.1
- [x] 2.5.1
- [ ] 3.1
- [ ] 4.1
- [ ] 4.2
- [ ] 4.3
- [ ] 5.1
