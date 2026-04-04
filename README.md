# Product Coverage Rate Optimizer 🚀

This is a Streamlit web app designed to process product CSV files, calculate coverage rates (profit margins), and automatically adjust sales prices to ensure at least a 50% margin. It also "beautifies" the final prices so they end in a 9 (e.g., converting 856 to 859).

## Features
- Calculates `PRICE_EX_VAT` assuming a 25% Danish VAT.
- Calculates current `COVERAGE_RATE`.
- Identifies products with < 50% margin and adjusts their prices up.
- Beautifies prices to end in 9 without dropping the margin.
- Exports a clean, formatted CSV ready for upload.

## Write Path — All Writes go through the Backend

Since **SaaS Roadmap Task 1.1**, the Streamlit UI no longer writes directly to
HostedShop / DanDomain via SOAP.  All price-apply writes are routed through the
FastAPI backend's guarded endpoints:

1. **`POST /apply-prices/create-manifest`** — The UI submits its pre-computed
   list of price changes (including `variant_id`, `product_id`, buy-price
   deltas).  The backend persists a batch manifest and returns a `batch_id`.
2. **`POST /apply-prices/apply`** — The UI confirms with `batch_id` +
   credentials.  The backend enforces all guardrails (env gating, per-row
   safety, audit log, idempotency) before writing to the shop.

### Escape hatch (deprecated)

Setting the environment variable `SB_OPTIMA_ALLOW_UI_DIRECT_PUSH=true` re-enables
the legacy direct-SOAP write path in the UI.  **This is deprecated and should
not be used in production.**  The UI will display a warning whenever this flag
is active.

## How to Run Locally
1. Clone this repository.
2. Install the requirements: `pip install -r requirements.txt`
3. Run the app: `streamlit run app.py`