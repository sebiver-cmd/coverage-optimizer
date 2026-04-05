"""History page — tenant-scoped dashboard of jobs, batches, and audit events.

Visible only when a JWT token is present (vault mode).  Calls the
``GET /jobs``, ``GET /apply-prices/batches``, and ``GET /audit``
backend list endpoints via :mod:`ui.vault_helpers`.

Security: never renders secret/credential fields.
"""

from __future__ import annotations

import streamlit as st

from ui.vault_helpers import (
    get_auth_headers,
    list_audit,
    list_batches,
    list_jobs,
)


def render(*, backend_url: str, token: str | None) -> None:
    """Render the History page.  Hidden when *token* is ``None``."""

    if not token:
        st.info("Sign in to view history.")
        return

    st.header("History")

    tab_jobs, tab_batches, tab_audit = st.tabs(
        ["Optimisation Jobs", "Apply Batches", "Audit Events"]
    )

    # ----- Jobs tab --------------------------------------------------------
    with tab_jobs:
        _render_jobs(backend_url, token)

    # ----- Batches tab -----------------------------------------------------
    with tab_batches:
        _render_batches(backend_url, token)

    # ----- Audit tab -------------------------------------------------------
    with tab_audit:
        _render_audit(backend_url, token)


# ---------------------------------------------------------------------------
# Internal renderers
# ---------------------------------------------------------------------------


def _render_jobs(backend_url: str, token: str) -> None:
    col1, col2 = st.columns(2)
    with col1:
        status_filter = st.selectbox(
            "Status filter",
            options=["(all)", "queued", "running", "completed", "failed"],
            key="_hist_job_status",
        )
    with col2:
        limit = st.number_input("Rows", min_value=5, max_value=200, value=50, key="_hist_job_limit")

    data, err = list_jobs(
        backend_url,
        token,
        limit=int(limit),
        status=None if status_filter == "(all)" else status_filter,
    )
    if err:
        st.error(err)
        return
    if data is None:
        return

    st.caption(f"Total: {data.get('total', '?')}")
    items = data.get("items", [])
    if not items:
        st.info("No jobs found.")
        return

    # Build a simple table
    rows = []
    for j in items:
        rows.append(
            {
                "ID": j["id"][:8] + "…",
                "Status": j.get("status", ""),
                "Created": _short_ts(j.get("created_at")),
                "Finished": _short_ts(j.get("finished_at")),
                "Error": (j.get("error") or "")[:80],
            }
        )
    st.dataframe(rows, use_container_width=True)


def _render_batches(backend_url: str, token: str) -> None:
    col1, col2, col3 = st.columns(3)
    with col1:
        status_filter = st.selectbox(
            "Status filter",
            options=["(all)", "created", "running", "completed", "failed"],
            key="_hist_batch_status",
        )
    with col2:
        mode_filter = st.selectbox(
            "Mode filter",
            options=["(all)", "dry_run", "apply", "create_manifest"],
            key="_hist_batch_mode",
        )
    with col3:
        limit = st.number_input("Rows", min_value=5, max_value=200, value=50, key="_hist_batch_limit")

    data, err = list_batches(
        backend_url,
        token,
        limit=int(limit),
        status=None if status_filter == "(all)" else status_filter,
        mode=None if mode_filter == "(all)" else mode_filter,
    )
    if err:
        st.error(err)
        return
    if data is None:
        return

    st.caption(f"Total: {data.get('total', '?')}")
    items = data.get("items", [])
    if not items:
        st.info("No batches found.")
        return

    rows = []
    for b in items:
        rows.append(
            {
                "ID": b["id"][:8] + "…",
                "Mode": b.get("mode", ""),
                "Status": b.get("status", ""),
                "Created": _short_ts(b.get("created_at")),
                "Finished": _short_ts(b.get("finished_at")),
            }
        )
    st.dataframe(rows, use_container_width=True)


def _render_audit(backend_url: str, token: str) -> None:
    col1, col2 = st.columns(2)
    with col1:
        event_filter = st.text_input(
            "Event type (exact match)",
            key="_hist_audit_event_type",
            placeholder="e.g. job.enqueued",
        )
    with col2:
        limit = st.number_input("Rows", min_value=5, max_value=200, value=50, key="_hist_audit_limit")

    data, err = list_audit(
        backend_url,
        token,
        limit=int(limit),
        event_type=event_filter or None,
    )
    if err:
        st.error(err)
        return
    if data is None:
        return

    st.caption(f"Total: {data.get('total', '?')}")
    items = data.get("items", [])
    if not items:
        st.info("No audit events found.")
        return

    rows = []
    for e in items:
        meta = e.get("meta") or {}
        meta_str = ", ".join(f"{k}={v}" for k, v in meta.items())[:120]
        rows.append(
            {
                "ID": e["id"][:8] + "…",
                "Event": e.get("event_type", ""),
                "Created": _short_ts(e.get("created_at")),
                "Meta": meta_str,
            }
        )
    st.dataframe(rows, use_container_width=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _short_ts(iso: str | None) -> str:
    """Shorten an ISO timestamp to ``YYYY-MM-DD HH:MM``."""
    if not iso:
        return ""
    return iso[:16].replace("T", " ")
