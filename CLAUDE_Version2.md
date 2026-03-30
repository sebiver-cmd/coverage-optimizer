# Claude Agent Instructions (Authoritative Sources)

## Authority policy (MUST FOLLOW)
- The file `docs/hostedshop/hostedshop_api_docs_full.md` is the **single source of truth** for the HostedShop / currency converter API.
- When answering API questions, use **only** that file.
- If something is not explicitly in that file, respond with: **"Not found in the provided documentation."**
- Do not infer missing endpoints/parameters/behavior.

## Citation requirement
- For every API claim (endpoint, method, parameter, return type, error), include a short quote or reference the URL heading from the docs dump section you used.