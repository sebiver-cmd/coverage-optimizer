# Product Coverage Rate Optimizer 🚀

This is a Streamlit web app designed to process product CSV files, calculate coverage rates (profit margins), and automatically adjust sales prices to ensure at least a 50% margin. It also "beautifies" the final prices so they end in a 9 (e.g., converting 856 to 859).

## Features
- Calculates `PRICE_EX_VAT` assuming a 25% Danish VAT.
- Calculates current `COVERAGE_RATE`.
- Identifies products with < 50% margin and adjusts their prices up.
- Beautifies prices to end in 9 without dropping the margin.
- Exports a clean, formatted CSV ready for upload.

## How to Run Locally
1. Clone this repository.
2. Install the requirements: `pip install -r requirements.txt`
3. Run the app: `streamlit run app.py`