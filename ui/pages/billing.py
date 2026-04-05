"""Billing page — view plan, billing status, and initiate Stripe checkout.

Visible only when a JWT token is present (vault mode).  Calls the
``GET /tenant/plan``, ``GET /billing/status``, and ``POST /billing/checkout``
backend endpoints via :mod:`ui.vault_helpers`.

Security:
- Stripe secret keys are **never** displayed.
- Only admin/owner roles can initiate a checkout.
- When billing is not configured (503), a clear message is shown.
"""

from __future__ import annotations

import streamlit as st

from ui.vault_helpers import (
    build_checkout_payload,
    can_manage_billing,
    create_checkout,
    get_billing_status,
    get_tenant_plan,
)


def render(*, backend_url: str, token: str | None, user_role: str | None) -> None:
    """Render the Billing / Settings page."""

    if not token:
        st.info("Sign in to view billing information.")
        return

    st.header("Billing & Plan")

    # ------------------------------------------------------------------
    # 1. Fetch plan info
    # ------------------------------------------------------------------
    plan_data, plan_err = get_tenant_plan(backend_url, token)

    if plan_err:
        st.warning(f"Could not load plan info: {plan_err}")
    elif plan_data:
        col1, col2 = st.columns(2)
        with col1:
            current_plan = plan_data.get("plan") or "—"
            st.metric("Current plan", current_plan.capitalize() if current_plan != "—" else "—")
        with col2:
            eff = plan_data.get("effective_limits", {})
            limit_lines = []
            for lk, lv in eff.items():
                limit_lines.append(f"- **{lk}**: {lv if lv is not None else '∞'}")
            if limit_lines:
                st.markdown("**Daily limits**\n" + "\n".join(limit_lines))

    # ------------------------------------------------------------------
    # 2. Fetch billing status
    # ------------------------------------------------------------------
    billing_data, billing_err = get_billing_status(backend_url, token)
    billing_enabled = billing_err != "billing_not_enabled"

    st.subheader("Billing status")

    if not billing_enabled:
        st.info("⚠️ Billing is not enabled on this server. Stripe checkout is unavailable.")
    elif billing_err:
        st.warning(f"Could not load billing status: {billing_err}")
    elif billing_data:
        bcol1, bcol2, bcol3 = st.columns(3)
        with bcol1:
            st.metric("Billing status", billing_data.get("billing_status") or "—")
        with bcol2:
            has_customer = bool(billing_data.get("stripe_customer_id"))
            st.metric("Stripe customer", "✅ Linked" if has_customer else "❌ Not linked")
        with bcol3:
            has_sub = bool(billing_data.get("stripe_subscription_id"))
            st.metric("Subscription", "✅ Active" if has_sub else "❌ None")

    # ------------------------------------------------------------------
    # 3. Upgrade flow (admin / owner only)
    # ------------------------------------------------------------------
    if billing_enabled:
        st.subheader("Upgrade plan")

        if not can_manage_billing(user_role):
            st.info("Only admin or owner users can change the billing plan.")
        else:
            upgrade_plans = ["pro", "enterprise"]
            chosen_plan = st.selectbox(
                "Select plan",
                options=upgrade_plans,
                format_func=lambda p: p.capitalize(),
                key="_billing_plan_select",
            )

            if st.button("Upgrade via Stripe", key="_btn_stripe_checkout", use_container_width=True):
                # Build URLs — use a placeholder that works for local Streamlit
                success_url = f"{backend_url.rstrip('/')}/billing/success?session_id={{CHECKOUT_SESSION_ID}}"
                cancel_url = f"{backend_url.rstrip('/')}/billing/cancel"

                with st.spinner("Creating checkout session…"):
                    checkout_url, checkout_err = create_checkout(
                        backend_url,
                        token,
                        chosen_plan,
                        success_url,
                        cancel_url,
                    )

                if checkout_err:
                    st.error(checkout_err)
                elif checkout_url:
                    st.success("Checkout session created!")
                    st.link_button(
                        "🔗 Open Stripe Checkout",
                        url=checkout_url,
                        use_container_width=True,
                    )
                    st.caption(
                        "Click the button above to complete your upgrade on Stripe. "
                        "Your plan will be updated automatically once payment is confirmed."
                    )
