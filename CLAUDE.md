# Copilot Agent Instructions (Authoritative Sources)

## Authority policy (MUST FOLLOW)
- The file `data/hostedshop_docs/hostedshop_api_docs_full.md` is the **single source of truth** for the HostedShop / currency converter API.
- When answering API questions, use **only** that file.
- If something is not explicitly in that file, respond with: **"Not found in the provided documentation."**
- Do not infer missing endpoints/parameters/behavior.

## Repo context (MUST READ AT SESSION START)
- At the beginning of a session, read `docs/REPO_OVERVIEW.md` to build a mental model of this repository.
- Re-use that understanding for all subsequent tasks in the same session instead of re-inferring the structure from scratch.
- Only open additional files as needed for the current task.

## Citation requirement
- For every API claim (endpoint, method, parameter, return type, error), include a short quote or reference the URL heading from the docs dump section you used.
