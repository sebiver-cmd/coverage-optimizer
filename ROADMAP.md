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
- [ ] Task 2.5.1

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

## Task 4.2 — Environment gating (enable apply only when SB_OPTIMA_ENABLE_APPLY=true)
- [ ] Task 4.2

## Task 4.3 — Add `GET /health` and use it for UI connectivity status
- [ ] Task 4.3

---

# Stage 5 — Optional analyst features (read‑only)

## Task 5.1 — Risk view (largest decreases, near-cost warnings, histogram)
- [ ] Task 5.1

---

## Completed Tasks (auto list)
- [x] 0.1
- [x] 1.1
- [x] 1.2
- [x] 1.3
- [x] 2.1
- [ ] 2.5.1
- [ ] 3.1
- [ ] 4.1
- [ ] 4.2
- [ ] 4.3
- [ ] 5.1
