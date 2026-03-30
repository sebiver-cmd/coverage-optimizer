"""Dashboard home page — summary cards and quick actions."""

from __future__ import annotations

import streamlit as st

from domain.pricing import MIN_COVERAGE_RATE, BEAUTIFY_LAST_DIGIT


def render(api_ready: bool) -> None:
    """Render the dashboard landing page."""
    st.markdown(
        '<h1 class="hero-header">📊 Coverage Optimizer Dashboard</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="hero-sub">'
        "Your central hub for product pricing, coverage analysis, and "
        "webshop management. Choose a module from the sidebar to get started."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    # --- Quick-status cards ---
    col1, col2, col3 = st.columns(3)
    with col1:
        status = "✅ Connected" if api_ready else "❌ Not configured"
        st.metric("API Status", status)
    with col2:
        product_count = len(st.session_state.get("_api_raw_df", []))
        st.metric("Products Loaded", f"{product_count:,}" if product_count else "—")
    with col3:
        brand_count = len(st.session_state.get("_api_brand_id_map", {}))
        st.metric("Brands", f"{brand_count:,}" if brand_count else "—")

    st.markdown("")
    st.divider()

    # --- Module cards ---
    st.markdown(
        '<div class="section-header">🧩 Modules</div>',
        unsafe_allow_html=True,
    )

    mc1, mc2, mc3, mc4 = st.columns(4)
    with mc1:
        st.markdown(
            '<div class="dash-card">'
            '<div class="icon">💱</div>'
            "<h3>Coverage Converter</h3>"
            "<p>Import products from the API, calculate coverage rates, "
            "adjust prices, and push updates to your webshop.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            "Open Coverage Converter",
            use_container_width=True,
            key="_nav_converter",
        ):
            st.session_state["_nav_page"] = "💱 Coverage Converter"
            st.rerun()
    with mc2:
        st.markdown(
            '<div class="dash-card disabled">'
            '<div class="icon">💰</div>'
            "<h3>Price Optimizer</h3>"
            "<p>Advanced pricing rules, competitor monitoring, "
            "and margin optimisation.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.button(
            "Coming Soon",
            use_container_width=True,
            disabled=True,
            key="_nav_price_opt",
        )
    with mc3:
        st.markdown(
            '<div class="dash-card disabled">'
            '<div class="icon">🚀</div>'
            "<h3>Push to Shop</h3>"
            "<p>Bulk push price and product updates directly "
            "to your DanDomain webshop.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.button(
            "Coming Soon",
            use_container_width=True,
            disabled=True,
            key="_nav_push",
        )
    with mc4:
        st.markdown(
            '<div class="dash-card disabled">'
            '<div class="icon">📈</div>'
            "<h3>Reports</h3>"
            "<p>Coverage reports, price change history, and "
            "audit logs.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.button(
            "Coming Soon",
            use_container_width=True,
            disabled=True,
            key="_nav_reports",
        )

    st.markdown("")
    st.divider()

    # --- Quick reference ---
    st.markdown(
        '<div class="section-header">📋 Quick Reference</div>',
        unsafe_allow_html=True,
    )
    ref1, ref2, ref3 = st.columns(3)
    with ref1:
        st.markdown(
            '<div class="info-card">'
            "<h4>💰 Price Rules</h4>"
            f"<p>Minimum margin: <strong>{int(MIN_COVERAGE_RATE * 100)}%</strong><br>"
            f"Prices end in: <strong>{BEAUTIFY_LAST_DIGIT}</strong><br>"
            "VAT rate: <strong>25%</strong></p>"
            "</div>",
            unsafe_allow_html=True,
        )
    with ref2:
        st.markdown(
            '<div class="info-card">'
            "<h4>🔌 API Connection</h4>"
            "<p>Uses the HostedShop SOAP API.<br>"
            "Configure credentials in the sidebar.<br>"
            "Supports product fetch, brand filter, and price push.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
    with ref3:
        st.markdown(
            '<div class="info-card">'
            "<h4>🛡️ Safety</h4>"
            "<p>Push-to-shop requires explicit selection.<br>"
            "Only changed prices are sent.<br>"
            "Dry-run mode available for testing.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
