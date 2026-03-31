"""SB-Optima — Streamlit dashboard shell.

Thin entry-point that configures the page, applies shared styles,
renders the sidebar (settings + navigation), and routes to the
active page module.
"""

import os

import streamlit as st

from domain.pricing import MIN_COVERAGE_RATE, BEAUTIFY_LAST_DIGIT
from ui.styles import DASHBOARD_CSS
from ui.pages import home, price_optimizer, placeholders
from ui.backend_url import normalize_base_url, check_backend_connected

# --- Page Configuration ---
st.set_page_config(
    page_title="SB-Optima",
    layout="wide",
)

# --- Apply shared CSS ---
st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)

# --- Navigation pages ---
_PAGES = [
    "Dashboard",
    "Price Optimizer",
    "Reports",
]

# ---------------------------------------------------------------------------
# Sidebar — settings + navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### SB-Optima")

    # Navigation
    st.caption("NAVIGATION")
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
    st.caption("API CONNECTION")

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
        "Dry-run (simulate only)",
        value=True,
        help="When checked, push-to-shop shows what would be sent but makes no API calls.",
    )

    api_ready = bool(api_username and api_password)

    st.divider()
    st.caption("BACKEND")
    backend_url = st.text_input(
        "Backend URL",
        value=os.environ.get("SB_OPTIMA_BACKEND_URL", "http://127.0.0.1:8000"),
        placeholder="http://127.0.0.1:8000",
        help="URL of the SB-Optima FastAPI backend.",
    )

    # --- Backend connectivity status ---
    _ok, _msg = check_backend_connected(backend_url, api_username, api_password)
    if _ok:
        st.success(_msg)
    else:
        st.error("Backend not reachable")
        st.caption("Try http://127.0.0.1:8000")
        with st.expander("Details"):
            st.code(_msg)

    st.divider()
    _active_beautify = st.session_state.get("_cc_beautify_digit", BEAUTIFY_LAST_DIGIT)
    st.markdown(
        '<div class="version-badge">'
        "SB-Optima v2.0<br>"
        f"Min margin {int(MIN_COVERAGE_RATE * 100)}% · "
        f"Prices end in {_active_beautify}"
        "</div>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Page routing
# ---------------------------------------------------------------------------
if page == "Dashboard":
    home.render(
        api_ready=api_ready,
        api_username=api_username,
        api_password=api_password,
    )

elif page == "Price Optimizer":
    price_optimizer.render(
        api_username=api_username,
        api_password=api_password,
        api_ready=api_ready,
        site_id=site_id,
        dry_run=dry_run,
        backend_url=backend_url,
    )

elif page == "Reports":
    placeholders.render_reports()
