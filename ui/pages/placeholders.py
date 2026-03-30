"""Placeholder pages for future dashboard modules."""

from __future__ import annotations

import streamlit as st


def render_price_optimizer() -> None:
    """Placeholder for the Price Optimizer module."""
    st.markdown(
        '<h1 class="hero-header">💰 Price Optimizer</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="hero-sub">'
        "Advanced pricing rules, competitor monitoring, and margin "
        "optimisation tools."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown("")
    st.markdown(
        '<div class="info-card">'
        "<h4>🚧 Coming Soon</h4>"
        "<p>This module is under development. It will include:<br>"
        "• Rule-based pricing strategies<br>"
        "• Competitor price tracking<br>"
        "• Automated margin optimisation<br>"
        "• Price elasticity analysis</p>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_reports() -> None:
    """Placeholder for the Reports module."""
    st.markdown(
        '<h1 class="hero-header">📈 Reports</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="hero-sub">'
        "Coverage reports, price change history, and audit logs."
        "</p>",
        unsafe_allow_html=True,
    )
    st.markdown("")
    st.markdown(
        '<div class="info-card">'
        "<h4>🚧 Coming Soon</h4>"
        "<p>This module is under development. It will include:<br>"
        "• Coverage rate reports by brand/category<br>"
        "• Price change history and trends<br>"
        "• Push-to-shop audit trail<br>"
        "• Export to PDF/Excel</p>"
        "</div>",
        unsafe_allow_html=True,
    )
