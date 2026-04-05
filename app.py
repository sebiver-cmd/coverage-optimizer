"""SB-Optima — Streamlit dashboard shell.

Thin entry-point that configures the page, applies shared styles,
renders the sidebar (settings + navigation), and routes to the
active page module.
"""

import os

import streamlit as st

from domain.pricing import MIN_COVERAGE_RATE, BEAUTIFY_LAST_DIGIT
from ui.styles import DASHBOARD_CSS
from ui.pages import home, price_optimizer, history, billing
from ui.backend_url import normalize_base_url, check_backend_connected
from ui.vault_helpers import (
    login as vault_login,
    signup as vault_signup,
    list_credentials,
    create_credential,
    delete_credential,
    decode_token_role,
    get_billing_status,
)

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
    "History",
    "Billing",
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

    # ------------------------------------------------------------------
    # Backend URL
    # ------------------------------------------------------------------
    st.divider()
    st.caption("BACKEND")
    backend_url = st.text_input(
        "Backend URL",
        value=os.environ.get("SB_OPTIMA_BACKEND_URL", "http://127.0.0.1:8000"),
        placeholder="http://127.0.0.1:8000",
        help="URL of the SB-Optima FastAPI backend.",
    )

    # --- Backend connectivity status ---
    _ok, _msg = check_backend_connected(backend_url)
    if _ok:
        st.success(_msg)
    else:
        st.error("Backend not reachable")
        st.caption("Try http://127.0.0.1:8000")
        with st.expander("Details"):
            st.code(_msg)

    # ------------------------------------------------------------------
    # Auth section (sign in / sign up / sign out)
    # ------------------------------------------------------------------
    st.divider()
    st.caption("AUTHENTICATION")

    token: str | None = st.session_state.get("token")
    credential_id: str | None = st.session_state.get("credential_id")
    user_role: str | None = decode_token_role(token)

    if token:
        st.info("Signed in (vault mode)")
        if st.button("Sign out", use_container_width=True):
            for _k in ("token", "credential_id", "_vault_creds"):
                st.session_state.pop(_k, None)
            st.rerun()
    else:
        _auth_tab_login, _auth_tab_signup = st.tabs(["Sign in", "Create tenant"])

        with _auth_tab_login:
            _login_email = st.text_input("Email", key="_login_email", placeholder="user@example.com")
            _login_pw = st.text_input("Password", key="_login_pw", type="password")
            if st.button("Sign in", use_container_width=True, key="_btn_login"):
                if _login_email and _login_pw:
                    _tok, _err = vault_login(backend_url, _login_email, _login_pw)
                    if _tok:
                        st.session_state["token"] = _tok
                        st.rerun()
                    else:
                        st.error(_err or "Login failed")
                else:
                    st.warning("Enter email and password.")

        with _auth_tab_signup:
            _su_tenant = st.text_input("Tenant name", key="_su_tenant", placeholder="My Shop")
            _su_email = st.text_input("Email", key="_su_email", placeholder="user@example.com")
            _su_pw = st.text_input("Password", key="_su_pw", type="password")
            if st.button("Create tenant", use_container_width=True, key="_btn_signup"):
                if _su_tenant and _su_email and _su_pw:
                    _tok, _err = vault_signup(backend_url, _su_tenant, _su_email, _su_pw)
                    if _tok:
                        st.session_state["token"] = _tok
                        st.rerun()
                    else:
                        st.error(_err or "Signup failed")
                else:
                    st.warning("Fill in all fields.")

    # ------------------------------------------------------------------
    # Credential profile management (when signed in)
    # ------------------------------------------------------------------
    if token:
        st.divider()
        st.caption("CREDENTIAL PROFILE")

        # Fetch/cache credential list
        if "_vault_creds" not in st.session_state:
            _creds, _cerr = list_credentials(backend_url, token)
            if _creds is not None:
                st.session_state["_vault_creds"] = _creds
            else:
                st.session_state["_vault_creds"] = []
                if _cerr:
                    st.warning(_cerr)

        _vault_creds: list[dict] = st.session_state.get("_vault_creds", [])

        if _vault_creds:
            _cred_options = {
                str(c["id"]): f"{c['name']} (site {c['site_id']})"
                for c in _vault_creds
            }
            _selected_cred = st.selectbox(
                "Select profile",
                options=list(_cred_options.keys()),
                format_func=lambda k: _cred_options[k],
                key="_sel_cred",
            )
            st.session_state["credential_id"] = _selected_cred
            credential_id = _selected_cred

            # Delete button
            _confirm_del = st.checkbox("Confirm delete", key="_confirm_del_cred")
            if st.button(
                "Delete selected",
                disabled=not _confirm_del,
                use_container_width=True,
                key="_btn_del_cred",
            ):
                _ok_d, _derr = delete_credential(backend_url, token, _selected_cred)
                if _ok_d:
                    st.session_state.pop("_vault_creds", None)
                    st.session_state.pop("credential_id", None)
                    st.rerun()
                else:
                    st.error(_derr or "Delete failed")
        else:
            st.info("No credential profiles yet.")

        # Create new profile
        with st.expander("Create new profile"):
            _new_name = st.text_input("Name", key="_new_cred_name", placeholder="Main shop")
            _new_site = st.number_input("Site ID", key="_new_cred_site", min_value=1, value=1)
            _new_user = st.text_input("API username", key="_new_cred_user")
            _new_pass = st.text_input("API password", key="_new_cred_pass", type="password")
            if st.button("Save profile", use_container_width=True, key="_btn_create_cred"):
                if _new_name and _new_site and _new_user and _new_pass:
                    _cresult, _cerr2 = create_credential(
                        backend_url, token, _new_name, str(_new_site), _new_user, _new_pass,
                    )
                    if _cresult:
                        st.session_state.pop("_vault_creds", None)
                        st.success("Profile created.")
                        st.rerun()
                    else:
                        st.error(_cerr2 or "Creation failed")
                else:
                    st.warning("Fill in all fields.")

    # ------------------------------------------------------------------
    # Plan & Limits (when signed in)
    # ------------------------------------------------------------------
    if token:
        st.divider()
        st.caption("PLAN & LIMITS")

        # Detect whether Stripe billing is enabled on the backend
        _billing_data, _billing_err = get_billing_status(backend_url, token)
        _billing_enabled = _billing_err != "billing_not_enabled"

        try:
            import requests as _rq

            _plan_resp = _rq.get(
                f"{normalize_base_url(backend_url)}/tenant/plan",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if _plan_resp.status_code == 200:
                _plan_data = _plan_resp.json()
                _current_plan = _plan_data.get("plan") or "—"
                st.metric("Current plan", _current_plan.capitalize() if _current_plan != "—" else "—")

                _eff = _plan_data.get("effective_limits", {})
                _limit_lines = []
                for _lk, _lv in _eff.items():
                    _limit_lines.append(f"- **{_lk}**: {_lv if _lv is not None else '∞'}")
                if _limit_lines:
                    st.markdown("**Daily limits:**\n" + "\n".join(_limit_lines))

                # Manual plan change — hidden when billing is enabled,
                # shown only in dev mode (SBOPTIMA_ENV=dev) otherwise.
                _show_manual = False
                if not _billing_enabled:
                    _env = os.environ.get("SBOPTIMA_ENV", "").lower()
                    if _env == "dev":
                        _show_manual = True

                if _show_manual:
                    _plans_resp = _rq.get(
                        f"{normalize_base_url(backend_url)}/plans",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10,
                    )
                    if _plans_resp.status_code == 200:
                        _avail_plans = _plans_resp.json().get("plans", [])
                        _plan_names = [p["name"] for p in _avail_plans]
                        _cur_idx = 0
                        if _plan_data.get("plan") in _plan_names:
                            _cur_idx = _plan_names.index(_plan_data["plan"])
                        _chosen = st.selectbox(
                            "Change plan (dev mode)",
                            options=_plan_names,
                            index=_cur_idx,
                            key="_plan_select",
                        )
                        if st.button("Update plan", key="_btn_update_plan", use_container_width=True):
                            _up_resp = _rq.put(
                                f"{normalize_base_url(backend_url)}/tenant/plan",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"plan": _chosen},
                                timeout=10,
                            )
                            if _up_resp.status_code == 200:
                                st.success(f"Plan updated to **{_chosen}**.")
                                st.rerun()
                            else:
                                _err_detail = _up_resp.json().get("detail", "Update failed")
                                st.error(_err_detail)
            else:
                st.caption("Plan info unavailable.")
        except Exception:
            st.caption("Could not load plan info.")

    # ------------------------------------------------------------------
    # Legacy API credentials (when NOT signed in)
    # ------------------------------------------------------------------
    if not token:
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
    else:
        # In vault mode, credentials come from the vault — set empty
        api_username = ""
        api_password = ""
        # Site ID from selected credential profile
        _sel_cred_data = next(
            (c for c in _vault_creds if str(c["id"]) == credential_id),
            None,
        ) if credential_id else None
        site_id = int(_sel_cred_data["site_id"]) if _sel_cred_data else 1

    dry_run = st.checkbox(
        "Dry-run (simulate only)",
        value=True,
        help="When checked, push-to-shop shows what would be sent but makes no API calls.",
    )

    api_ready = bool(token and credential_id) or bool(api_username and api_password)

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
        token=token,
        credential_id=credential_id,
    )

elif page == "History":
    history.render(
        backend_url=backend_url,
        token=token,
    )

elif page == "Billing":
    billing.render(
        backend_url=backend_url,
        token=token,
        user_role=user_role,
    )
