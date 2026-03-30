"""Coverage Optimizer — Streamlit dashboard shell.

Thin entry-point that configures the page, applies shared styles,
renders the sidebar (settings + navigation), and routes to the
active page module.
"""

import os

import streamlit as st

from domain.pricing import MIN_COVERAGE_RATE, BEAUTIFY_LAST_DIGIT
from domain.supplier import ENCODING_OPTIONS
from ui.styles import DASHBOARD_CSS
from ui.pages import home, coverage_converter, placeholders

# --- Page Configuration ---
st.set_page_config(
    page_title="Coverage Optimizer",
    page_icon="📊",
    layout="wide",
)

# --- Apply shared CSS ---
st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

# --- Navigation pages ---
_PAGES = [
    "🏠 Dashboard",
    "💱 Coverage Converter",
    "💰 Price Optimizer",
    "📈 Reports",
]

# ---------------------------------------------------------------------------
# Sidebar — settings + navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 📊 Coverage Optimizer")

    # Navigation
    st.caption("🧭 NAVIGATION")
    # Allow home.py cards to set the page via session state
    _default_idx = 0
    if "_nav_page" in st.session_state:
        try:
            _default_idx = _PAGES.index(st.session_state["_nav_page"])
        except ValueError:
            _default_idx = 0
    page = st.radio(
        "Go to",
        _PAGES,
        index=_default_idx,
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("📁 ENCODING")
    encoding_label = st.selectbox(
        "CSV file encoding",
        options=list(ENCODING_OPTIONS.keys()),
        index=0,
        help=(
            "Choose 'Auto-detect' to let the app guess the encoding, "
            "or pick a specific one if Danish characters (Æ, Ø, Å) look wrong."
        ),
    )
    selected_encoding = ENCODING_OPTIONS[encoding_label]

    st.divider()
    st.caption("💰 PRICE RULES")
    price_pct = st.number_input(
        "Adjust Sales Price (%)",
        min_value=-50.0,
        max_value=200.0,
        value=0.0,
        step=0.5,
        help=(
            "Increase or decrease all sales prices by this percentage "
            "before recalculating coverage rates."
        ),
    )
    include_buy_price = st.checkbox(
        "Include BUY_PRICE in import file",
        value=False,
        help="When checked, the import-ready CSV will contain the BUY_PRICE column.",
    )

    st.divider()
    st.caption("🔌 API CONNECTION")

    try:
        _dd_secrets = st.secrets.get("dandomain", {})
    except Exception:
        _dd_secrets = {}

    api_username = st.text_input(
        "API username",
        value=_dd_secrets.get("username", os.environ.get("DANDOMAIN_API_USERNAME", "")),
        placeholder="api-user@example.com",
    )
    api_password = st.text_input(
        "API password",
        value=_dd_secrets.get("password", os.environ.get("DANDOMAIN_API_PASSWORD", "")),
        type="password",
    )
    site_id = st.number_input(
        "Site ID",
        min_value=1,
        max_value=100,
        value=_dd_secrets.get("site_id", 1),
        help="Language / site ID in your webshop (default: 1).",
    )
    dry_run = st.checkbox(
        "🧪 Dry-run (simulate only)",
        value=True,
        help="When checked, push-to-shop shows what would be sent but makes no API calls.",
    )

    api_ready = bool(api_username and api_password)

    st.divider()
    st.markdown(
        '<div class="version-badge">'
        "Coverage Optimizer v2.0<br>"
        f"Min margin {int(MIN_COVERAGE_RATE * 100)}% · "
        f"Prices end in {BEAUTIFY_LAST_DIGIT}"
        "</div>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Page routing
# ---------------------------------------------------------------------------
if page == "🏠 Dashboard":
    home.render(api_ready=api_ready)

elif page == "💱 Coverage Converter":
    coverage_converter.render(
        api_username=api_username,
        api_password=api_password,
        api_ready=api_ready,
        site_id=site_id,
        dry_run=dry_run,
        price_pct=price_pct,
        include_buy_price=include_buy_price,
        selected_encoding=selected_encoding,
    )

elif page == "💰 Price Optimizer":
    placeholders.render_price_optimizer()

elif page == "📈 Reports":
    placeholders.render_reports()
