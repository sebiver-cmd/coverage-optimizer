"""Price Optimizer module — analysis, pricing pipeline, and push-to-shop.

Uses the FastAPI backend ``/optimize`` endpoint for computing optimisation
suggestions (read-only).  Push-to-shop behaviour is unchanged.
"""

from __future__ import annotations

import logging
import time

import requests
import streamlit as st
import pandas as pd
import numpy as np

from dandomain_api import DanDomainClient, DanDomainAPIError
from push_safety import build_push_updates
from ui.backend_url import normalize_backend_url, normalize_base_url
from domain.risk_analysis import (
    compute_largest_decreases,
    compute_near_cost_warnings,
    compute_change_histogram,
    NEAR_COST_MARGIN_THRESHOLD,
)

from domain.pricing import (
    VAT_RATE,
    MIN_COVERAGE_RATE,
    REQUIRED_COLUMNS,
    IMPORT_COLUMNS_BASE,
    clean_price,
    format_dk,
    calc_coverage_rate,
    optimize_prices,
)
from domain.supplier import (
    DEFAULT_CURRENCY_RATES,
    ENCODING_OPTIONS,
    parse_supplier_file,
    detect_supplier_columns,
    match_supplier_to_products,
    detect_discount_lines,
)
from domain.invoice_ean import (
    detect_invoice_columns,
    build_ean_export,
    match_invoice_to_products,
    build_export_from_matches,
    generate_barcode_pdf,
)

logger = logging.getLogger(__name__)

# Columns shown in the simplified (default) table view.
_SIMPLE_COLUMNS = [
    'TITLE_DK', 'NUMBER', 'PRODUCER',
    'BUY_PRICE', 'PRICE', 'COVERAGE_RATE_%',
    'NEW_PRICE', 'NEW_COVERAGE_RATE_%',
]

# Default timeout (seconds) for HTTP requests to the FastAPI backend.
_BACKEND_TIMEOUT = 120


# ---------------------------------------------------------------------------
# Backend helpers (read-only HTTP calls to the FastAPI backend)
# ---------------------------------------------------------------------------

# Backward-compatible aliases – all logic lives in ui.backend_url now.
_normalize_backend_url = normalize_backend_url
_normalize_base_url = normalize_base_url


def _fetch_brands_from_backend(
    backend_url: str,
    api_username: str,
    api_password: str,
) -> list[dict]:
    """Fetch brands from ``GET /brands`` on the FastAPI backend.

    Returns a list of ``{"id": int, "name": str}`` dicts, or an empty
    list on error.
    """
    base = _normalize_base_url(backend_url)
    url = f"{base}/brands"
    try:
        resp = requests.get(
            url,
            params={
                "api_username": api_username,
                "api_password": api_password,
            },
            timeout=_BACKEND_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        logger.error("Backend request to %s failed", url, exc_info=True)
        return []


def _run_backend_optimization(
    backend_url: str,
    api_username: str,
    api_password: str,
    site_id: int,
    brand_ids: list[int] | None,
    include_offline: bool,
    include_variants: bool,
    price_pct: float,
    beautify_digit: int,
) -> dict | None:
    """Call ``POST /optimize/`` on the FastAPI backend.

    Returns the parsed JSON response (with ``summary`` and ``rows`` keys),
    or *None* on error.  Errors are reported via :func:`st.error`.
    """
    payload: dict = {
        "api_username": api_username,
        "api_password": api_password,
        "site_id": site_id,
        "price_pct": price_pct,
        "beautify_digit": beautify_digit,
        "include_offline": include_offline,
        "include_variants": include_variants,
    }
    if brand_ids:
        payload["brand_ids"] = brand_ids

    base = _normalize_base_url(backend_url)
    url = f"{base}/optimize/"

    try:
        resp = requests.post(
            url,
            json=payload,
            timeout=_BACKEND_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        logger.error("Backend request to %s failed", url, exc_info=True)
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:200] if exc.response is not None else ""
        st.error(f"Backend optimisation failed ({exc.response.status_code}): {detail}")
        return None
    except requests.ConnectionError:
        logger.error("Backend request to %s failed", url, exc_info=True)
        st.error(
            "Could not connect to the backend.  "
            "Make sure the FastAPI server is running and the Backend URL is correct."
        )
        return None
    except requests.RequestException:
        logger.error("Backend request to %s failed", url, exc_info=True)
        st.error(
            "Backend request failed.  "
            "Please check the Backend URL and server logs for details."
        )
        return None


def _build_dataframes_from_response(
    data: dict,
) -> tuple[pd.DataFrame, int, np.ndarray, pd.DataFrame, pd.DataFrame]:
    """Convert the ``/optimize`` JSON response into DataFrames.

    Returns
    -------
    final_df
        Display DataFrame matching the ``EXPORT_COLUMNS`` layout produced
        by :func:`optimize_prices`, with ``PRODUCER`` inserted after
        ``NUMBER``.
    adjusted_count
        Number of products flagged as needing adjustment.
    adjusted_mask
        Boolean :class:`numpy.ndarray` (one entry per row).
    import_df
        Import-ready subset of adjusted rows.
    raw_df
        Reconstructed "raw" DataFrame with numeric columns
        (``BUY_PRICE_NUM``, ``PRICE_NUM``, etc.) suitable for local
        re-computation when the user edits ``BUY_PRICE``.
    """
    rows = data["rows"]
    summary = data["summary"]

    # --- Raw DataFrame (numeric columns for local re-computation) ---
    raw_records = []
    for r in rows:
        raw_records.append({
            "PRODUCT_ID": r["product_id"],
            "TITLE_DK": r["title"],
            "NUMBER": r["item_number"],
            "PRODUCER": r["producer"],
            "BUY_PRICE": format_dk(r["buy_price"]),
            "BUY_PRICE_NUM": r["buy_price"],
            "PRICE": format_dk(r["current_price"]),
            "PRICE_NUM": r["current_price"],
            "VARIANT_ID": r["variant_id"],
            "VARIANT_TYPES": r["variant_types"],
            "EAN": r.get("ean", ""),
        })
    raw_df = pd.DataFrame(raw_records)

    # --- Display DataFrame (formatted strings) ---
    final_records = []
    for r in rows:
        final_records.append({
            "PRODUCT_ID": r["product_id"],
            "TITLE_DK": r["title"],
            "NUMBER": r["item_number"],
            "PRODUCER": r["producer"],
            "BUY_PRICE": format_dk(r["buy_price"]),
            "PRICE_EX_VAT": format_dk(r["current_price_ex_vat"]),
            "PRICE": format_dk(r["current_price"]),
            "COVERAGE_RATE_%": (
                str(round(r["current_coverage_pct"], 2))
                .replace(".", ",") + "%"
            ),
            "VARIANT_ID": r["variant_id"],
            "VARIANT_TYPES": r["variant_types"],
            "NEW_PRICE_EX_VAT": format_dk(r["suggested_price_ex_vat"]),
            "NEW_PRICE": format_dk(r["suggested_price"]),
            "NEW_COVERAGE_RATE_%": (
                str(round(r["suggested_coverage_pct"], 2))
                .replace(".", ",") + "%"
            ),
        })
    final_df = pd.DataFrame(final_records)

    # --- Adjusted mask & count ---
    adjusted_mask = np.array([r["needs_adjustment"] for r in rows])
    adjusted_count = int(summary["adjusted_count"])

    # --- Import DataFrame ---
    import_records = []
    for r in rows:
        if r["needs_adjustment"]:
            import_records.append({
                "PRODUCT_ID": r["product_id"],
                "TITLE_DK": r["title"],
                "NUMBER": r["item_number"],
                "BUY_PRICE": format_dk(r["buy_price"]),
                "PRICE": format_dk(r["suggested_price"]),
                "VARIANT_ID": r["variant_id"],
                "VARIANT_TYPES": r["variant_types"],
            })
    if import_records:
        import_df = pd.DataFrame(import_records)
    else:
        import_df = pd.DataFrame(
            columns=REQUIRED_COLUMNS,
        )

    return final_df, adjusted_count, adjusted_mask, import_df, raw_df


def _run_dry_run_preview(
    backend_url: str,
    optimize_params: dict,
    product_numbers: list[str] | None = None,
) -> dict | None:
    """Call ``POST /apply-prices/dry-run`` on the FastAPI backend.

    Returns the parsed JSON response (with ``changes`` and ``summary``
    keys), or *None* on error.  Errors are reported via :func:`st.error`.
    """
    base = _normalize_base_url(backend_url)
    url = f"{base}/apply-prices/dry-run"

    body: dict = {
        "optimize_payload": {
            "api_username": optimize_params["api_username"],
            "api_password": optimize_params["api_password"],
            "site_id": optimize_params.get("site_id", 1),
            "price_pct": optimize_params.get("price_pct", 0.0),
            "beautify_digit": optimize_params.get("beautify_digit", 9),
            "include_offline": optimize_params.get("include_offline", False),
            "include_variants": optimize_params.get("include_variants", True),
        },
    }
    if optimize_params.get("brand_ids"):
        body["optimize_payload"]["brand_ids"] = optimize_params["brand_ids"]
    if product_numbers is not None:
        body["product_numbers"] = product_numbers

    try:
        resp = requests.post(url, json=body, timeout=_BACKEND_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:200] if exc.response is not None else ""
        st.error(f"Dry-run request failed ({exc.response.status_code}): {detail}")
        return None
    except requests.ConnectionError:
        st.error(
            "Could not connect to the backend.  "
            "Make sure the FastAPI server is running and the Backend URL is correct."
        )
        return None
    except requests.RequestException:
        st.error(
            "Dry-run request failed.  "
            "Please check the Backend URL and server logs for details."
        )
        return None


def render(
    api_username: str,
    api_password: str,
    api_ready: bool,
    site_id: int,
    dry_run: bool,
    backend_url: str = "http://127.0.0.1:8000",
) -> None:
    """Render the full Price Optimizer page.

    Fetches brands and runs the pricing optimisation via the FastAPI
    backend (``GET /brands``, ``POST /optimize``).  Push-to-shop
    behaviour is unchanged.
    """

    st.markdown(
        '<h1 class="hero-header">Price Optimizer</h1>',
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<p class="hero-sub">'
        f"Analyse product coverage rates and automatically adjust prices "
        f"to at least a "
        f"<strong>{int(MIN_COVERAGE_RATE * 100)}%</strong> profit margin. "
        f"Variant-aware — handles products with multiple variants correctly."
        f"</p>",
        unsafe_allow_html=True,
    )

    # --- Price Rules (local to Price Optimizer) ---
    with st.expander("Price Rules", expanded=False):
        pr_col1, pr_col2, pr_col3 = st.columns(3)
        with pr_col1:
            price_pct = st.number_input(
                "Adjust Sales Price (%)",
                min_value=-50.0,
                max_value=200.0,
                value=0.0,
                step=0.5,
                help=(
                    "Increase or decrease all new sales prices by this "
                    "percentage after coverage-based adjustment and "
                    "beautification."
                ),
                key="_cc_price_pct",
            )
        with pr_col2:
            beautify_options = {9: "End in 9", 0: "End in 0", 5: "End in 5"}
            beautify_digit = st.selectbox(
                "Beautifier ending",
                options=list(beautify_options.keys()),
                format_func=lambda d: beautify_options[d],
                index=0,
                help="Round adjusted prices up to the nearest integer ending in this digit.",
                key="_cc_beautify_digit",
            )
        with pr_col3:
            include_buy_price = st.checkbox(
                "Include BUY_PRICE in import file",
                value=False,
                help="When checked, the import-ready CSV will contain the BUY_PRICE column.",
                key="_cc_include_bp",
            )

    if not api_ready:
        st.markdown(
            '<div class="info-card">'
            "<h4>API Not Connected</h4>"
            "<p>Configure your DanDomain API credentials in the sidebar "
            "to enable price optimisation.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    # --- Fetch brands from backend (cached in session state) ---
    if "_backend_brands" not in st.session_state:
        with st.spinner("Loading brands from backend..."):
            brands = _fetch_brands_from_backend(
                backend_url, api_username, api_password,
            )
            st.session_state["_backend_brands"] = brands

    brands = st.session_state.get("_backend_brands", [])

    # --- Filters ---
    selected_brands, include_offline = _render_filters(brands)

    # --- Run Optimisation button ---
    if st.button("Run Optimisation", type="primary", use_container_width=True):
        with st.spinner("Running optimisation via backend..."):
            response = _run_backend_optimization(
                backend_url=backend_url,
                api_username=api_username,
                api_password=api_password,
                site_id=site_id,
                brand_ids=selected_brands,
                include_offline=include_offline,
                include_variants=True,
                price_pct=price_pct,
                beautify_digit=beautify_digit,
            )
            if response is not None:
                (
                    final_df, adjusted_count, adjusted_mask,
                    import_df, raw_df,
                ) = _build_dataframes_from_response(response)
                st.session_state["_opt_response"] = response
                st.session_state["_opt_final_df"] = final_df
                st.session_state["_opt_adjusted_count"] = adjusted_count
                st.session_state["_opt_adjusted_mask"] = adjusted_mask
                st.session_state["_opt_import_df"] = import_df
                st.session_state["_opt_raw_df"] = raw_df
                st.session_state["_opt_params"] = {
                    "api_username": api_username,
                    "api_password": api_password,
                    "site_id": site_id,
                    "price_pct": price_pct,
                    "beautify_digit": beautify_digit,
                    "include_offline": include_offline,
                    "include_variants": True,
                    "brand_ids": selected_brands,
                }
                # Clear stale data-editor edits from previous runs
                for k in ("_ed_all", "_ed_adj", "_ed_imp"):
                    st.session_state.pop(k, None)

    # --- Display cached results ---
    if "_opt_raw_df" in st.session_state:
        _render_analysis(
            st.session_state["_opt_raw_df"],
            api_username,
            api_password,
            api_ready,
            site_id,
            dry_run,
            price_pct,
            include_buy_price,
            beautify_digit,
            backend_url=backend_url,
        )
    elif "_opt_response" not in st.session_state:
        st.markdown(
            '<div class="info-card">'
            "<h4>Ready to Optimise</h4>"
            "<p>Adjust filters and price rules above, then click "
            "<strong>Run Optimisation</strong> to fetch products from "
            "the backend and compute suggested prices.</p>"
            "</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Filter controls
# ---------------------------------------------------------------------------

def _render_filters(
    brands: list[dict],
) -> tuple[list[int] | None, bool]:
    """Render filter controls and return the selected filter values.

    Parameters
    ----------
    brands
        Brand list from ``GET /brands`` (each item has ``id`` and ``name``).

    Returns
    -------
    brand_ids
        Selected brand IDs, or *None* when no filter is active.
    include_offline
        Whether to include offline / inactive products.
    """
    brand_options = [b["id"] for b in brands]
    brand_map = {b["id"]: b["name"] for b in brands}

    def _brand_label(pid: int) -> str:
        return brand_map.get(pid, f"Unknown ({pid})")

    api_filter_col1, api_filter_col2 = st.columns(2)
    with api_filter_col1:
        selected_brands = st.multiselect(
            "Filter by brand / producer",
            options=brand_options,
            format_func=_brand_label,
            default=[],
            key="_brand_filter",
            placeholder=(
                "All brands (no filter)"
                if brand_options
                else "No brands available"
            ),
            disabled=not brand_options,
            help=(
                "Select one or more brands to include. "
                "Leave empty to include all brands."
            ),
        )
    with api_filter_col2:
        only_online = st.checkbox(
            "Only active (online) products",
            value=True,
            help="When checked, only products marked as 'online' are shown.",
        )

    return selected_brands or None, not only_online


# ---------------------------------------------------------------------------
# Dry-Run Preview
# ---------------------------------------------------------------------------

def _apply_batch(
    backend_url: str,
    batch_id: str,
    api_username: str,
    api_password: str,
    site_id: int = 1,
) -> dict | None:
    """Call ``POST /apply-prices/apply`` on the FastAPI backend.

    Returns the response dict on success, or *None* on error.
    """
    base = _normalize_base_url(backend_url)
    url = f"{base}/apply-prices/apply"
    body = {
        "batch_id": batch_id,
        "confirm": True,
        "api_username": api_username,
        "api_password": api_password,
        "site_id": site_id,
    }
    try:
        resp = requests.post(url, json=body, timeout=_BACKEND_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:200] if exc.response is not None else ""
        st.error(f"Apply failed ({exc.response.status_code}): {detail}")
        return None
    except requests.ConnectionError:
        st.error(
            "Could not connect to the backend.  "
            "Make sure the FastAPI server is running and the Backend URL is correct."
        )
        return None
    except requests.RequestException:
        st.error(
            "Apply failed.  "
            "Please check the Backend URL and server logs for details."
        )
        return None


def _fetch_batch(backend_url: str, batch_id: str) -> dict | None:
    """Call ``GET /apply-prices/batch/{batch_id}`` on the FastAPI backend.

    Returns the manifest dict, or *None* on error.
    """
    base = _normalize_base_url(backend_url)
    url = f"{base}/apply-prices/batch/{batch_id}"
    try:
        resp = requests.get(url, timeout=_BACKEND_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = exc.response.json().get("detail", "")
        except Exception:
            detail = exc.response.text[:200] if exc.response is not None else ""
        st.error(f"Load batch failed ({exc.response.status_code}): {detail}")
        return None
    except requests.ConnectionError:
        st.error(
            "Could not connect to the backend.  "
            "Make sure the FastAPI server is running and the Backend URL is correct."
        )
        return None
    except requests.RequestException:
        st.error(
            "Load batch failed.  "
            "Please check the Backend URL and server logs for details."
        )
        return None


def _check_apply_enabled(backend_url: str) -> bool:
    """Check whether the backend has apply enabled via environment gating.

    Calls ``GET /apply-prices/status`` and returns the ``enabled`` flag.
    Returns *False* on any error (connection failure, unexpected response).
    """
    base = _normalize_base_url(backend_url)
    url = f"{base}/apply-prices/status"
    try:
        resp = requests.get(url, timeout=_BACKEND_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("enabled", False)
    except Exception:
        return False


def _render_dry_run_preview(
    display_all: pd.DataFrame,
    backend_url: str,
) -> None:
    """Render the dry-run apply preview inside the All Products tab.

    Reads the optimisation parameters from session state, calls the
    ``POST /apply-prices/dry-run`` endpoint, and displays the change
    set with a CSV download button.
    """
    opt_params = st.session_state.get("_opt_params")
    if opt_params is None:
        return

    st.markdown("---")
    st.markdown("#### Preview apply (dry-run)")

    # Extract product numbers from the currently displayed table
    if "NUMBER" not in display_all.columns:
        return
    product_numbers = display_all["NUMBER"].dropna().astype(str).tolist()
    product_count = len(product_numbers)

    # Guardrail for large product sets
    large_set = product_count > 500
    confirmed = True
    if large_set:
        st.warning(
            f"The current view contains {product_count:,} products. "
            "Generating a dry-run preview for this many products may "
            "take a while."
        )
        confirmed = st.checkbox(
            "I understand, proceed with dry-run preview",
            value=False,
            key="_dryrun_large_confirm",
        )

    if st.button(
        "Preview apply (dry-run)",
        disabled=large_set and not confirmed,
        key="_btn_dryrun_preview",
    ):
        with st.spinner("Generating dry-run change set..."):
            result = _run_dry_run_preview(
                backend_url=backend_url,
                optimize_params=opt_params,
                product_numbers=product_numbers,
            )
            if result is not None:
                st.session_state["_dryrun_result"] = result

    # Display cached dry-run results
    dryrun = st.session_state.get("_dryrun_result")
    if dryrun is not None:
        # Show batch_id prominently
        batch_id = dryrun.get("batch_id")
        if batch_id:
            st.success(f"Batch ID: {batch_id}")

        summary = dryrun["summary"]

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Total", f"{summary['total']:,}")
        s2.metric("Increases", f"{summary['increases']:,}")
        s3.metric("Decreases", f"{summary['decreases']:,}")
        s4.metric("Unchanged", f"{summary['unchanged']:,}")

        changes = dryrun["changes"]
        if changes:
            changes_df = pd.DataFrame(changes)
            st.dataframe(
                changes_df,
                use_container_width=True,
                hide_index=True,
            )

            # CSV download
            csv_data = changes_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download CSV",
                data=csv_data,
                file_name="dry_run_changes.csv",
                mime="text/csv",
                key="_btn_dryrun_csv",
            )
        else:
            st.info("No changes in the dry-run result.")

    # Load batch UI
    st.markdown("---")
    st.markdown("#### Load batch")
    load_col1, load_col2 = st.columns([3, 1])
    with load_col1:
        batch_input = st.text_input(
            "Batch ID",
            placeholder="Enter a batch UUID to load",
            key="_batch_id_input",
        )
    with load_col2:
        st.markdown("<br>", unsafe_allow_html=True)
        load_clicked = st.button("Load batch", key="_btn_load_batch")

    if load_clicked and batch_input and batch_input.strip():
        with st.spinner("Loading batch..."):
            loaded = _fetch_batch(backend_url, batch_input.strip())
            if loaded is not None:
                st.session_state["_loaded_batch"] = loaded

    loaded_batch = st.session_state.get("_loaded_batch")
    if loaded_batch is not None:
        st.success(f"Loaded batch: {loaded_batch.get('batch_id', 'N/A')}")

        lb_summary = loaded_batch.get("summary", {})
        lb1, lb2, lb3, lb4 = st.columns(4)
        lb1.metric("Total", f"{lb_summary.get('total', 0):,}")
        lb2.metric("Increases", f"{lb_summary.get('increases', 0):,}")
        lb3.metric("Decreases", f"{lb_summary.get('decreases', 0):,}")
        lb4.metric("Unchanged", f"{lb_summary.get('unchanged', 0):,}")

        lb_changes = loaded_batch.get("changes", [])
        if lb_changes:
            st.dataframe(
                pd.DataFrame(lb_changes),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("No changes in this batch.")

    # --- Apply prices section ---
    # Determine the active batch_id from dry-run or loaded batch.
    active_batch_id = None
    active_changes = None
    if dryrun is not None and dryrun.get("batch_id"):
        active_batch_id = dryrun["batch_id"]
        active_changes = dryrun.get("changes", [])
    elif loaded_batch is not None and loaded_batch.get("batch_id"):
        active_batch_id = loaded_batch["batch_id"]
        active_changes = loaded_batch.get("changes", [])

    if active_batch_id and active_changes:
        _render_apply_section(
            backend_url=backend_url,
            batch_id=active_batch_id,
            changes=active_changes,
        )


def _render_apply_section(
    backend_url: str,
    batch_id: str,
    changes: list[dict],
) -> None:
    """Render the apply-prices UI with confirmation and guardrail summary.

    Shown only when a valid ``batch_id`` exists (from a dry-run or
    loaded batch).  Requires the user to type ``APPLY`` to confirm.

    If the backend reports that apply is disabled (environment gate),
    a warning is shown instead of the confirmation controls.
    """
    st.markdown("---")
    st.markdown("#### Apply prices")

    # Environment gate check
    if not _check_apply_enabled(backend_url):
        st.warning(
            "Apply is disabled on the backend.  "
            "Set SB_OPTIMA_ENABLE_APPLY=true on the server to enable."
        )
        return

    # Guardrail summary
    total_rows = len(changes)
    max_abs_pct = max(
        (abs(c.get("change_pct", 0)) for c in changes), default=0
    )
    non_positive = sum(
        1 for c in changes
        if not isinstance(c.get("new_price"), (int, float))
        or c.get("new_price", 0) <= 0
    )
    below_cost = sum(
        1 for c in changes
        if isinstance(c.get("buy_price"), (int, float))
        and c["buy_price"] > 0
        and isinstance(c.get("new_price"), (int, float))
        and c["new_price"] <= c["buy_price"]
    )
    over_pct = sum(
        1 for c in changes
        if abs(c.get("change_pct", 0)) > 30
    )

    # Per-row issues that will be skipped (not batch-level rejects)
    skippable = non_positive + below_cost + over_pct

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Rows", f"{total_rows:,}", help="Max 100 per batch")
    g2.metric(
        "Max change %",
        f"{max_abs_pct:.1f}%",
        help="Rows with abs(change_pct) > 30% will be skipped",
    )
    g3.metric(
        "Below cost",
        f"{below_cost}",
        help="Rows where new_price <= buy_price (will be skipped)",
    )
    g4.metric(
        "Will skip",
        f"{skippable}",
        help="Total rows that will be skipped by per-row guardrails",
    )

    # Batch-level hard reject (>100 rows)
    if total_rows > 100:
        st.error(f"Batch has {total_rows} rows (max 100). Apply will be rejected.")
        return

    # Per-row soft guardrail warnings
    if skippable > 0:
        st.warning(
            f"{skippable} row(s) will be skipped due to per-row guardrails "
            f"({non_positive} invalid price, {over_pct} over change %, "
            f"{below_cost} below cost). "
            f"{total_rows - skippable} row(s) will be applied."
        )

    # Confirmation input
    confirm_text = st.text_input(
        'Type "APPLY" to confirm',
        placeholder="APPLY",
        key="_apply_confirm_text",
    )
    confirmed = confirm_text.strip() == "APPLY"

    opt_params = st.session_state.get("_opt_params", {})

    if st.button(
        "Apply prices",
        disabled=not confirmed,
        type="primary",
        key="_btn_apply_prices",
    ):
        with st.spinner("Applying prices to webshop..."):
            result = _apply_batch(
                backend_url=backend_url,
                batch_id=batch_id,
                api_username=opt_params.get("api_username", ""),
                api_password=opt_params.get("api_password", ""),
                site_id=opt_params.get("site_id", 1),
            )
            if result is not None:
                st.session_state["_apply_result"] = result

    # Display apply result
    apply_result = st.session_state.get("_apply_result")
    if apply_result is not None:
        applied = apply_result.get("applied_count", 0)
        skipped_list = apply_result.get("skipped", [])
        failed_list = apply_result.get("failed", [])

        if not skipped_list and not failed_list:
            st.success(
                f"Applied {applied} price(s) successfully."
            )
        else:
            st.info(
                f"Applied {applied}, "
                f"skipped {len(skipped_list)}, "
                f"failed {len(failed_list)}."
            )

        if skipped_list:
            st.markdown("**Skipped rows** (per-row guardrails)")
            st.dataframe(
                pd.DataFrame(skipped_list),
                use_container_width=True,
                hide_index=True,
            )

        if failed_list:
            st.markdown("**Failed rows** (write errors)")
            st.dataframe(
                pd.DataFrame(failed_list),
                use_container_width=True,
                hide_index=True,
            )

        r1, r2 = st.columns(2)
        r1.caption(f"Started: {apply_result.get('started_at', 'N/A')}")
        r2.caption(f"Finished: {apply_result.get('finished_at', 'N/A')}")


# ---------------------------------------------------------------------------
# Result Summary Panel
# ---------------------------------------------------------------------------

def _render_result_summary(final_df: pd.DataFrame) -> None:
    """Render a read-only summary panel of the optimisation result.

    Displays high-level metrics (total products, changed products,
    average increase/decrease percentages) and top-5 tables for the
    largest price increases and decreases.

    Parameters
    ----------
    final_df
        The display DataFrame produced by :func:`_build_dataframes_from_response`
        (or local re-computation).  Must contain at least ``NUMBER``,
        ``PRICE`` and ``NEW_PRICE`` columns (Danish-formatted strings).
    """
    if final_df.empty:
        return

    # --- Parse formatted prices to numeric values ---
    old_prices = final_df["PRICE"].apply(clean_price)
    new_prices = final_df["NEW_PRICE"].apply(clean_price)

    # Percentage change per product (guard against zero old price)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct_change = np.where(
            old_prices != 0,
            (new_prices - old_prices) / old_prices * 100,
            0.0,
        )

    total_products = len(final_df)
    changed_mask = old_prices != new_prices
    changed_products = int(changed_mask.sum())

    increased_mask = new_prices > old_prices
    decreased_mask = new_prices < old_prices

    avg_increase_pct = (
        float(pct_change[increased_mask].mean())
        if increased_mask.any()
        else 0.0
    )
    avg_decrease_pct = (
        float(pct_change[decreased_mask].mean())
        if decreased_mask.any()
        else 0.0
    )

    # --- Metric row ---
    with st.container():
        st.markdown("#### Result Summary")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total Products", f"{total_products:,}")
        m2.metric("Changed Products", f"{changed_products:,}")
        m3.metric(
            "Avg Increase",
            f"{avg_increase_pct:+.2f}%" if increased_mask.any() else "N/A",
        )
        m4.metric(
            "Avg Decrease",
            f"{avg_decrease_pct:+.2f}%" if decreased_mask.any() else "N/A",
        )

    # --- Top 5 increases / decreases ---
    detail_cols = {
        "NUMBER": final_df["NUMBER"].values,
    }
    # Include product title when available.
    if "TITLE_DK" in final_df.columns:
        detail_cols["TITLE_DK"] = final_df["TITLE_DK"].values
    detail_cols["Old Price"] = old_prices.values
    detail_cols["New Price"] = new_prices.values
    detail_cols["Change %"] = np.round(pct_change, 2)
    detail_df = pd.DataFrame(detail_cols)

    col_left, col_right = st.columns(2)

    with col_left:
        st.markdown("**Top 5 Price Increases**")
        if increased_mask.any():
            top_inc = (
                detail_df[increased_mask.values]
                .nlargest(5, "Change %")
                .reset_index(drop=True)
            )
            st.dataframe(top_inc, use_container_width=True, hide_index=True)
        else:
            st.caption("No price increases detected.")

    with col_right:
        st.markdown("**Top 5 Price Decreases**")
        if decreased_mask.any():
            top_dec = (
                detail_df[decreased_mask.values]
                .nsmallest(5, "Change %")
                .reset_index(drop=True)
            )
            st.dataframe(top_dec, use_container_width=True, hide_index=True)
        else:
            st.caption("No price decreases detected.")

    st.markdown("---")


def _render_risk_view(
    final_df: pd.DataFrame,
    buy_prices: pd.Series,
) -> None:
    """Render a risk-analysis panel below the result summary.

    Shows three sections inside a collapsible expander:

    1. **Largest price decreases** — products with the biggest drop.
    2. **Near-cost warnings** — products whose new price is dangerously
       close to buy/cost price (low margin).
    3. **Price-change histogram** — distribution of percentage changes.

    Parameters
    ----------
    final_df
        Display DataFrame with Danish-formatted ``PRICE`` and ``NEW_PRICE``
        columns.
    buy_prices
        Numeric buy-price series aligned with *final_df* rows.
    """
    if final_df.empty:
        return

    with st.expander("Risk Analysis", expanded=False):
        # --- 1. Largest decreases ---
        st.markdown("**Largest Price Decreases**")
        decreases_df = compute_largest_decreases(final_df, top_n=10)
        if decreases_df.empty:
            st.caption("No price decreases detected.")
        else:
            st.dataframe(decreases_df, use_container_width=True, hide_index=True)

        st.markdown("")

        # --- 2. Near-cost warnings ---
        st.markdown("**Near-Cost Warnings**")
        st.caption(
            f"Products where the new price margin is below "
            f"{int(NEAR_COST_MARGIN_THRESHOLD * 100)}%."
        )
        near_cost_df = compute_near_cost_warnings(final_df, buy_prices)
        if near_cost_df.empty:
            st.caption("No near-cost warnings.")
        else:
            st.dataframe(near_cost_df, use_container_width=True, hide_index=True)

        st.markdown("")

        # --- 3. Price-change histogram ---
        st.markdown("**Price Change Distribution**")
        labels, counts = compute_change_histogram(final_df)
        if labels:
            chart_df = pd.DataFrame({"Change %": labels, "Products": counts})
            st.bar_chart(chart_df, x="Change %", y="Products")
        else:
            st.caption("No data for histogram.")

    st.markdown("---")


# ---------------------------------------------------------------------------
# Analysis, Data Tabs, Downloads, Push
# ---------------------------------------------------------------------------

def _render_analysis(
    parsed_df: pd.DataFrame,
    api_username: str,
    api_password: str,
    api_ready: bool,
    site_id: int,
    dry_run: bool,
    price_pct: float,
    include_buy_price: bool,
    beautify_digit: int,
    backend_url: str = "http://127.0.0.1:8000",
) -> None:
    """Render analysis results, data tabs, downloads, and push-to-shop.

    When the user has not edited any ``BUY_PRICE`` values, the
    pre-computed results from the backend (stored in session state by
    :func:`render`) are used directly.  If ``BUY_PRICE`` edits are
    detected, :func:`optimize_prices` is called locally so that the
    modified cost prices are reflected immediately.
    """
    # --- Apply persisted BUY_PRICE edits from data-editor state ---
    work_df = parsed_df.copy()
    has_buy_price_edits = False
    for key in ("_ed_all", "_ed_adj", "_ed_imp"):
        for row_str, changes in (
            st.session_state.get(key, {}).get("edited_rows", {}).items()
        ):
            if "BUY_PRICE" not in changes:
                continue
            has_buy_price_edits = True
            row_idx = int(row_str)
            if key == "_ed_adj" and "_adj_index_map" in st.session_state:
                idx_map = st.session_state["_adj_index_map"]
                if row_idx < len(idx_map):
                    row_idx = idx_map[row_idx]
                else:
                    continue
            if key == "_ed_imp" and "_imp_index_map" in st.session_state:
                idx_map = st.session_state["_imp_index_map"]
                if row_idx < len(idx_map):
                    row_idx = idx_map[row_idx]
                else:
                    continue
            if row_idx in work_df.index:
                work_df.at[row_idx, 'BUY_PRICE_NUM'] = changes["BUY_PRICE"]

    if has_buy_price_edits:
        # Local re-computation with edited buy prices
        final_df, adjusted_count, adjusted_mask, import_df = optimize_prices(
            work_df, price_pct,
            original_buy_prices=parsed_df['BUY_PRICE_NUM'],
            beautify_digit=beautify_digit,
        )
        # Include PRODUCER column when available
        if 'PRODUCER' in work_df.columns and 'PRODUCER' not in final_df.columns:
            _pos = final_df.columns.get_loc('NUMBER') + 1
            final_df.insert(_pos, 'PRODUCER', work_df['PRODUCER'].values)
    else:
        # Use pre-computed backend results (already has PRODUCER)
        final_df = st.session_state["_opt_final_df"]
        adjusted_count = st.session_state["_opt_adjusted_count"]
        adjusted_mask = st.session_state["_opt_adjusted_mask"]
        import_df = st.session_state["_opt_import_df"]

    # --- Summary Metrics ---
    total = len(final_df)
    unchanged = total - adjusted_count
    st.markdown("")
    mcol1, mcol2, mcol3, mcol4, mcol5 = st.columns(5)
    mcol1.metric("Total Products", f"{total:,}")
    mcol2.metric("Prices Adjusted", f"{adjusted_count:,}")
    mcol3.metric("Unchanged", f"{unchanged:,}")
    adj_pct = (adjusted_count / total * 100) if total else 0
    mcol4.metric("Adjusted %", f"{adj_pct:.1f}%" if total else "—")

    if total:
        base_ex_vat = parsed_df['PRICE_NUM'] / (1 + VAT_RATE)
        base_coverage = calc_coverage_rate(
            parsed_df.assign(PRICE_EX_VAT_NUM=base_ex_vat),
            'PRICE_EX_VAT_NUM', 'BUY_PRICE_NUM',
        )
        base_avg = (base_coverage * 100).mean()
        cov_vals = (
            final_df['NEW_COVERAGE_RATE_%']
            .str.replace('%', '', regex=False)
            .str.replace(',', '.', regex=False)
            .astype(float)
        )
        adj_avg = float(cov_vals.mean())
        delta = adj_avg - base_avg
        mcol5.metric(
            "Avg Coverage",
            f"{adj_avg:.1f}%",
            delta=f"{delta:+.1f}%" if abs(delta) > 0.01 else None,
        )
    st.markdown("")

    # --- Result Summary Panel ---
    _render_result_summary(final_df)

    # --- Risk Analysis Panel ---
    _render_risk_view(final_df, work_df["BUY_PRICE_NUM"])

    # --- Data Tabs ---
    _buy_price_col = "BUY_PRICE"
    _display_all = final_df.copy()
    _display_all[_buy_price_col] = work_df['BUY_PRICE_NUM']

    _disabled_cols = [
        c for c in _display_all.columns if c != _buy_price_col
    ]
    _col_config = {
        _buy_price_col: st.column_config.NumberColumn(
            "BUY_PRICE",
            help="Cost price \u2013 edit to match current supplier price",
            format="%.2f",
            min_value=0.0,
        ),
    }

    # --- Advanced columns toggle ---
    show_advanced = st.checkbox(
        "Advanced columns",
        value=False,
        help="Show all columns including IDs, ex-VAT prices, and variant details.",
        key="_show_advanced_cols",
    )

    # Determine which columns to display
    if show_advanced:
        _visible_cols = list(_display_all.columns)
    else:
        _visible_cols = [
            c for c in _SIMPLE_COLUMNS if c in _display_all.columns
        ]
        # Always include BUY_PRICE even if not in the simple list
        if _buy_price_col not in _visible_cols:
            _visible_cols.insert(0, _buy_price_col)

    tab_all, tab_adjusted, tab_import, tab_supplier, tab_ean = st.tabs([
        "All Products",
        "Adjusted Only",
        "Import Preview",
        "Supplier Match",
        "EAN Barcode Export",
    ])

    with tab_all:
        st.data_editor(
            _display_all[_visible_cols],
            disabled=[c for c in _visible_cols if c != _buy_price_col],
            column_config=_col_config,
            use_container_width=True,
            hide_index=True,
            key="_ed_all",
        )

        # --- Dry-run preview section ---
        _render_dry_run_preview(_display_all, backend_url)

    with tab_adjusted:
        _adj_full = _display_all[adjusted_mask]
        _display_adj = _adj_full[_visible_cols].reset_index(drop=True)
        st.session_state["_adj_index_map"] = list(
            _display_all.index[adjusted_mask]
        )
        if _display_adj.empty:
            st.info("All products already meet the minimum margin \u2013 no adjustments needed.")
        else:
            st.data_editor(
                _display_adj,
                disabled=[c for c in _visible_cols if c != _buy_price_col],
                column_config=_col_config,
                use_container_width=True,
                hide_index=True,
                key="_ed_adj",
            )

    with tab_import:
        if import_df.empty:
            st.info("No products needed adjustment \u2013 nothing to import.")
        else:
            adjusted_full = _display_all[adjusted_mask]
            st.session_state["_imp_index_map"] = list(
                _display_all.index[adjusted_mask]
            )
            st.markdown(
                f"**{adjusted_count}** product"
                f"{'s' if adjusted_count != 1 else ''} "
                "will be included in the import file. "
                "Variant ID and Variant Types are included to ensure "
                "the correct product/variant is targeted."
            )
            preview_df = pd.DataFrame({
                'Product ID': adjusted_full['PRODUCT_ID'].values,
                'Title': adjusted_full['TITLE_DK'].values,
                'Number': adjusted_full['NUMBER'].values,
                'BUY_PRICE': adjusted_full[_buy_price_col].values,
                'Variant ID': adjusted_full['VARIANT_ID'].values,
                'Variant Types': adjusted_full['VARIANT_TYPES'].values,
                'Old Price': adjusted_full['PRICE'].values,
                'New Price': adjusted_full['NEW_PRICE'].values,
                'Old Coverage': adjusted_full['COVERAGE_RATE_%'].values,
                'New Coverage': adjusted_full['NEW_COVERAGE_RATE_%'].values,
            })
            st.data_editor(
                preview_df,
                disabled=[
                    c for c in preview_df.columns if c != _buy_price_col
                ],
                use_container_width=True,
                hide_index=True,
                key="_ed_imp",
                column_config={
                    'Product ID': st.column_config.TextColumn(width='small'),
                    'Title': st.column_config.TextColumn(width='medium'),
                    'Number': st.column_config.TextColumn(width='small'),
                    _buy_price_col: st.column_config.NumberColumn(
                        "BUY_PRICE",
                        help="Cost price \u2013 edit to match current supplier price",
                        format="%.2f",
                        min_value=0.0,
                    ),
                    'Variant ID': st.column_config.TextColumn(width='small'),
                    'Variant Types': st.column_config.TextColumn(width='small'),
                    'Old Price': st.column_config.TextColumn(
                        'Old Price', width='small',
                    ),
                    'New Price': st.column_config.TextColumn(
                        'New Price', width='small',
                    ),
                    'Old Coverage': st.column_config.TextColumn(
                        'Old Coverage', width='small',
                    ),
                    'New Coverage': st.column_config.TextColumn(
                        'New Coverage', width='small',
                    ),
                },
            )

    # --- Supplier Match Tab ---
    with tab_supplier:
        _render_supplier_match(work_df)

    # --- EAN Barcode Export Tab ---
    with tab_ean:
        _render_ean_barcode_export(work_df)

    # --- Downloads ---
    _render_downloads(final_df, import_df, adjusted_count, include_buy_price)

    # --- Push to Shop ---
    if not import_df.empty:
        _render_push_to_shop(
            final_df,
            adjusted_mask,
            parsed_df,
            work_df,
            api_username,
            api_password,
            api_ready,
            site_id,
            dry_run,
        )


# ---------------------------------------------------------------------------
# Supplier Match
# ---------------------------------------------------------------------------

def _render_supplier_match(work_df: pd.DataFrame) -> None:
    """Render the supplier file match tab."""
    st.markdown(
        "Upload a **supplier price list** (CSV or PDF) to automatically "
        "match SKUs and update cost prices. Supports fuzzy SKU matching, "
        "multiple currencies, and discount detection."
    )

    sup_file = st.file_uploader(
        "Upload Supplier Price List",
        type=['csv', 'pdf'],
        key="_supplier_file",
        label_visibility="collapsed",
    )

    # Encoding selection under Advanced (auto-detect by default)
    with st.expander("Advanced: File Encoding", expanded=False):
        encoding_label = st.selectbox(
            "CSV file encoding",
            options=list(ENCODING_OPTIONS.keys()),
            index=0,
            help=(
                "Choose 'Auto-detect' to let the app guess the encoding, "
                "or pick a specific one if Danish characters look wrong."
            ),
            key="_supplier_encoding",
        )
    selected_encoding = ENCODING_OPTIONS[encoding_label]

    if sup_file is not None:
        try:
            sup_bytes = sup_file.getvalue()
            sup_df = parse_supplier_file(
                sup_bytes, sup_file.name, selected_encoding,
            )
        except Exception as exc:
            st.error(f"Failed to parse supplier file: {exc}")
            sup_df = None

        if sup_df is not None and not sup_df.empty:
            detected = detect_supplier_columns(sup_df)

            col_names = ['(none)'] + list(sup_df.columns)
            scol1, scol2, scol3, scol4 = st.columns(4)
            with scol1:
                sku_idx = (
                    col_names.index(detected['sku'])
                    if detected['sku'] in col_names else 0
                )
                sup_sku_col = st.selectbox(
                    "SKU column", col_names, index=sku_idx,
                    help="Column containing the product SKU / article number.",
                )
            with scol2:
                price_idx = (
                    col_names.index(detected['price'])
                    if detected['price'] in col_names else 0
                )
                sup_price_col = st.selectbox(
                    "Price column", col_names, index=price_idx,
                    help="Column containing the unit cost price.",
                )
            with scol3:
                disc_idx = (
                    col_names.index(detected['discount'])
                    if detected['discount'] in col_names else 0
                )
                sup_disc_col = st.selectbox(
                    "Discount column", col_names, index=disc_idx,
                    help="Column containing discount percentages (optional).",
                )
            with scol4:
                desc_idx = (
                    col_names.index(detected['description'])
                    if detected['description'] in col_names else 0
                )
                sup_desc_col = st.selectbox(
                    "Name / Designation column", col_names,
                    index=desc_idx,
                    help=(
                        "Column with the product name or designation "
                        "(optional, improves matching accuracy)."
                    ),
                )

            cur_col1, cur_col2, cur_col3 = st.columns(3)
            with cur_col1:
                det_currency = 'EUR'
                if detected['currency'] and detected['currency'] in sup_df.columns:
                    cur_vals = sup_df[detected['currency']].dropna()
                    if not cur_vals.empty:
                        first_val = str(cur_vals.iloc[0]).upper().strip()
                        if first_val in DEFAULT_CURRENCY_RATES:
                            det_currency = first_val
                currency_list = list(DEFAULT_CURRENCY_RATES.keys())
                sup_currency = st.selectbox(
                    "Source currency",
                    currency_list,
                    index=currency_list.index(det_currency),
                    help="Currency of the prices in the supplier file.",
                )
            with cur_col2:
                default_rate = DEFAULT_CURRENCY_RATES.get(sup_currency, 1.0)
                exchange_rate = st.number_input(
                    f"Rate → DKK",
                    min_value=0.001,
                    max_value=9999.0,
                    value=default_rate,
                    step=0.01,
                    format="%.4f",
                    help=(
                        f"Exchange rate from {sup_currency} to DKK. "
                        "Adjust if the default rate is outdated."
                    ),
                )
            with cur_col3:
                match_threshold = st.slider(
                    "Match threshold",
                    min_value=50,
                    max_value=100,
                    value=70,
                    help=(
                        "Minimum similarity score (%) for fuzzy SKU "
                        "matching. Lower = more matches but less precise."
                    ),
                )

            if sup_sku_col != '(none)' and sup_price_col != '(none)':
                supplier_skus = (
                    sup_df[sup_sku_col].dropna()
                    .astype(str).str.strip()
                    .loc[lambda s: s != '']
                )

                # Build augmented product-SKU pool with composite
                # keys (NUMBER + VARIANT_TYPES) so that supplier
                # SKUs containing variant info can match directly —
                # same approach used by the EAN matching flow.
                composite_lookup: dict[str, tuple[str, str]] = {}
                augmented_skus: list[str] = []
                title_lookup: dict[str, str] = {}

                _nums = (
                    work_df['NUMBER'].fillna('').astype(str)
                    .str.strip()
                )
                _vtypes = (
                    work_df['VARIANT_TYPES'].fillna('').astype(str)
                    .str.strip()
                    if 'VARIANT_TYPES' in work_df.columns
                    else pd.Series('', index=work_df.index)
                )
                _titles = (
                    work_df['TITLE_DK'].fillna('').astype(str)
                    .str.strip()
                    if 'TITLE_DK' in work_df.columns
                    else pd.Series('', index=work_df.index)
                )
                for num, vtype, title in zip(
                    _nums, _vtypes, _titles
                ):
                    if not num:
                        continue
                    if num not in composite_lookup:
                        composite_lookup[num] = (num, '')
                        augmented_skus.append(num)
                        if title:
                            title_lookup[num] = title
                    if vtype:
                        composite = f"{num} {vtype}"
                        if composite not in composite_lookup:
                            composite_lookup[composite] = (num, vtype)
                            augmented_skus.append(composite)

                # Build name mappings for enhanced matching
                supplier_names = None
                if sup_desc_col != '(none)':
                    supplier_names = dict(zip(
                        sup_df[sup_sku_col].astype(str).str.strip(),
                        sup_df[sup_desc_col].astype(str).str.strip(),
                    ))
                product_names = (
                    title_lookup if title_lookup else None
                )

                matches = match_supplier_to_products(
                    supplier_skus.tolist(),
                    augmented_skus,
                    threshold=match_threshold,
                    supplier_names=supplier_names,
                    product_names=product_names,
                )

                # Resolve augmented keys back to plain NUMBERs
                # so that downstream code can look up products.
                for sup_sku, mentry in matches.items():
                    mk = mentry['sku']
                    if mk is not None and mk in composite_lookup:
                        mentry['sku'] = composite_lookup[mk][0]
                    new_alts = []
                    for alt_key, alt_score in mentry['alternatives']:
                        num = composite_lookup.get(
                            alt_key, (alt_key, '')
                        )[0]
                        new_alts.append((num, alt_score))
                    mentry['alternatives'] = new_alts

                disc_col_name = (
                    sup_disc_col if sup_disc_col != '(none)' else None
                )
                disc_lines = detect_discount_lines(sup_df, disc_col_name)

                if disc_lines:
                    with st.expander(
                        f"{len(disc_lines)} discount line(s) detected",
                        expanded=False,
                    ):
                        disc_df = pd.DataFrame(disc_lines)
                        st.dataframe(
                            disc_df, use_container_width=True,
                            hide_index=True,
                        )

                # Build variant lookup for display in suggestions
                _variant_lookup: dict[str, list[str]] = {}
                if 'VARIANT_TYPES' in work_df.columns:
                    for _n, _vt in zip(_nums, _vtypes):
                        if _n and _vt:
                            _variant_lookup.setdefault(
                                _n, []
                            ).append(_vt)

                # Split into auto-matched and unmatched
                auto_matches = {
                    k: v for k, v in matches.items()
                    if v['sku'] is not None
                }
                unmatched = {
                    k: v for k, v in matches.items()
                    if v['sku'] is None and v['alternatives']
                }

                # Manual selection for unmatched SKUs
                manual_matches: dict = {}
                if unmatched:
                    with st.expander(
                        f"\u26a0\ufe0f {len(unmatched)} unmatched SKU(s) "
                        "\u2014 select matches",
                        expanded=True,
                    ):
                        st.caption(
                            "These supplier SKUs could not be "
                            "automatically matched. Pick the correct "
                            "product from the suggestions, or skip."
                        )
                        for sup_sku, mdata in unmatched.items():
                            alts = mdata['alternatives']
                            options = ['(skip \u2014 no match)']
                            for alt_sku, alt_score in alts:
                                lbl = alt_sku
                                if (product_names
                                        and alt_sku in product_names):
                                    lbl += (
                                        f" \u2014 "
                                        f"{product_names[alt_sku]}"
                                    )
                                if alt_sku in _variant_lookup:
                                    lbl += (
                                        " ["
                                        + ", ".join(
                                            _variant_lookup[alt_sku]
                                        )
                                        + "]"
                                    )
                                lbl += f" ({alt_score}%)"
                                options.append(lbl)

                            sup_label = f"\U0001f50d {sup_sku}"
                            if (supplier_names
                                    and sup_sku in supplier_names):
                                sup_label += (
                                    f" \u2014 "
                                    f"{supplier_names[sup_sku][:60]}"
                                )

                            sel = st.selectbox(
                                sup_label, options,
                                key=f"_manual_match_{sup_sku}",
                            )
                            if sel != '(skip \u2014 no match)':
                                idx = options.index(sel) - 1
                                alt_sku, alt_score = alts[idx]
                                manual_matches[sup_sku] = {
                                    'sku': alt_sku,
                                    'score': alt_score,
                                    'alternatives': alts,
                                }

                all_matches = {**auto_matches, **manual_matches}

                if not all_matches:
                    st.warning(
                        "No SKU matches found. Try lowering the "
                        "match threshold or checking the SKU column."
                    )
                else:
                    desc_col_name = (
                        sup_desc_col
                        if sup_desc_col != '(none)' else None
                    )
                    match_rows = _build_match_rows(
                        all_matches, sup_df, sup_sku_col,
                        sup_price_col, disc_lines, exchange_rate,
                        sup_currency, work_df,
                        sup_desc_col=desc_col_name,
                    )

                    if match_rows:
                        match_result_df = pd.DataFrame(match_rows)
                        display_cols = [
                            c for c in match_result_df.columns
                            if not c.startswith('_')
                        ]
                        st.markdown(
                            f"**{len(match_rows)}** SKU match"
                            f"{'es' if len(match_rows) != 1 else ''} "
                            f"found"
                        )
                        st.dataframe(
                            match_result_df[display_cols],
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                'Score': st.column_config.ProgressColumn(
                                    'Match %',
                                    min_value=0,
                                    max_value=100,
                                    format="%d%%",
                                ),
                                'Diff': st.column_config.NumberColumn(
                                    'Diff (DKK)',
                                    format="%.2f",
                                ),
                            },
                        )

                        if st.button(
                            "Update Cost Prices from Supplier",
                            type="primary",
                            use_container_width=True,
                            key="_apply_supplier",
                        ):
                            _apply_supplier_prices(match_rows, work_df)
            else:
                st.info(
                    "Select the **SKU** and **Price** columns above "
                    "to start matching."
                )
    else:
        st.info(
            "Upload a supplier price list (CSV or PDF) to match "
            "SKUs and update cost prices automatically."
        )


def _build_match_rows(
    matches, sup_df, sup_sku_col, sup_price_col,
    disc_lines, exchange_rate, sup_currency, work_df,
    sup_desc_col=None,
):
    """Build the match result rows for supplier matching."""
    match_rows = []
    for sup_sku, match_data in matches.items():
        prod_sku = match_data['sku']
        score = match_data['score']
        if prod_sku is None:
            continue
        sup_row = sup_df.loc[
            sup_df[sup_sku_col].astype(str).str.strip() == sup_sku
        ]
        if sup_row.empty:
            continue
        raw_price = str(sup_row.iloc[0][sup_price_col])
        price_val = clean_price(raw_price)

        row_idx = sup_row.index[0]
        disc_pct = 0.0
        for d in disc_lines:
            if d['row'] == row_idx:
                disc_pct = d['discount_pct']
                break
        if disc_pct > 0:
            price_val = price_val * (1 - disc_pct / 100)

        price_dkk = price_val * exchange_rate

        prod_mask = (
            work_df['NUMBER'].astype(str).str.strip() == prod_sku
        )
        current_cost = 0.0
        if prod_mask.any():
            current_cost = work_df.loc[
                prod_mask, 'BUY_PRICE_NUM'
            ].iloc[0]

        sup_name = ''
        if sup_desc_col and sup_desc_col in sup_row.columns:
            sup_name = str(sup_row.iloc[0][sup_desc_col]).strip()

        prod_name = ''
        prod_price_row = work_df.loc[prod_mask]
        variant_name = ''
        if not prod_price_row.empty:
            if 'TITLE_DK' in prod_price_row.columns:
                prod_name = str(
                    prod_price_row['TITLE_DK'].iloc[0]
                ).strip()
            variant_name = str(
                prod_price_row['VARIANT_TYPES'].iloc[0]
            ).strip() if 'VARIANT_TYPES' in prod_price_row.columns else ''
            sell_ex_vat = (
                prod_price_row['PRICE_NUM'].iloc[0] / (1 + VAT_RATE)
            )
            old_cov = (
                ((sell_ex_vat - current_cost) / sell_ex_vat * 100)
                if sell_ex_vat > 0 else 0.0
            )
            new_cov = (
                ((sell_ex_vat - price_dkk) / sell_ex_vat * 100)
                if sell_ex_vat > 0 else 0.0
            )
        else:
            old_cov = 0.0
            new_cov = 0.0

        match_rows.append({
            'Supplier SKU': sup_sku,
            'Supplier Name': sup_name,
            'Product SKU': prod_sku,
            'Product Name': prod_name,
            'Variant Name': variant_name,
            'Score': score,
            f'Price ({sup_currency})': round(price_val, 2),
            'Discount %': disc_pct if disc_pct > 0 else '',
            'Price (DKK)': round(price_dkk, 2),
            'Current Cost': round(current_cost, 2),
            'Diff': round(price_dkk - current_cost, 2),
            'Old Coverage %': round(old_cov, 1),
            'New Coverage %': round(new_cov, 1),
            '_prod_sku': prod_sku,
            '_new_cost': price_dkk,
        })

    return match_rows


def _apply_supplier_prices(match_rows, work_df):
    """Apply supplier prices to the work DataFrame."""
    updated = 0
    for row in match_rows:
        prod_sku = row['_prod_sku']
        new_cost = row['_new_cost']
        mask = (
            work_df['NUMBER'].astype(str).str.strip() == prod_sku
        )
        if mask.any():
            work_df.loc[mask, 'BUY_PRICE_NUM'] = new_cost
            updated += mask.sum()

    if updated:
        if "_ed_all" not in st.session_state:
            st.session_state["_ed_all"] = {"edited_rows": {}}
        elif "edited_rows" not in st.session_state["_ed_all"]:
            st.session_state["_ed_all"]["edited_rows"] = {}
        edits = st.session_state["_ed_all"]["edited_rows"]
        for row in match_rows:
            prod_sku = row['_prod_sku']
            mask = (
                work_df['NUMBER'].astype(str).str.strip() == prod_sku
            )
            for idx in work_df.index[mask]:
                edits[str(idx)] = {"BUY_PRICE": row['_new_cost']}
        st.success(
            f"Updated cost prices for {updated} product row(s). "
            f"Changes are reflected in all tabs."
        )
        st.rerun()
    else:
        st.warning("No products were updated.")


# ---------------------------------------------------------------------------
# EAN Barcode Export
# ---------------------------------------------------------------------------

def _render_ean_barcode_export(work_df: pd.DataFrame) -> None:
    """Render the EAN barcode export tab.

    Allows the user to upload an invoice (CSV / PDF), match its lines
    to the product catalogue, and download a scannable document with
    SKU, product number, title, variant name, quantity, and EAN barcode.
    """
    st.markdown(
        "Upload an **invoice** (CSV or PDF) to match its lines to "
        "products and generate a scannable EAN barcode document. "
        "The output includes SKU, product number, title, variant name, "
        "quantity, and EAN barcode — ready for scanning."
    )

    if 'EAN' not in work_df.columns:
        st.warning(
            "EAN data is not available. Re-run optimisation to fetch "
            "EAN codes from the shop API."
        )
        return

    inv_file = st.file_uploader(
        "Upload Invoice",
        type=['csv', 'pdf'],
        key="_ean_invoice_file",
        label_visibility="collapsed",
    )

    with st.expander("Advanced: File Encoding", expanded=False):
        enc_label = st.selectbox(
            "Invoice file encoding",
            options=list(ENCODING_OPTIONS.keys()),
            index=0,
            help=(
                "Choose 'Auto-detect' to let the app guess the encoding, "
                "or pick a specific one if characters look wrong."
            ),
            key="_ean_encoding",
        )
    selected_encoding = ENCODING_OPTIONS[enc_label]

    if inv_file is not None:
        try:
            inv_bytes = inv_file.getvalue()
            inv_df = parse_supplier_file(
                inv_bytes, inv_file.name, selected_encoding,
            )
        except Exception as exc:
            st.error(f"Failed to parse invoice file: {exc}")
            inv_df = None

        if inv_df is not None and not inv_df.empty:
            detected = detect_invoice_columns(inv_df)

            col_names = ['(none)'] + list(inv_df.columns)
            ecol1, ecol2, ecol3, ecol4 = st.columns(4)
            with ecol1:
                sku_idx = (
                    col_names.index(detected['sku'])
                    if detected['sku'] in col_names else 0
                )
                inv_sku_col = st.selectbox(
                    "SKU / Article column",
                    col_names,
                    index=sku_idx,
                    help="Column with the product SKU or article number.",
                    key="_ean_sku_col",
                )
            with ecol2:
                qty_idx = (
                    col_names.index(detected['qty'])
                    if detected['qty'] in col_names else 0
                )
                inv_qty_col = st.selectbox(
                    "Quantity / Amount column",
                    col_names,
                    index=qty_idx,
                    help=(
                        "Column with the quantity or amount. "
                        "Select '(none)' to default all quantities to 1."
                    ),
                    key="_ean_qty_col",
                )
            with ecol3:
                desc_idx = (
                    col_names.index(detected['description'])
                    if detected.get('description') in col_names else 0
                )
                inv_desc_col = st.selectbox(
                    "Name / Designation column",
                    col_names,
                    index=desc_idx,
                    help=(
                        "Column with the product name or description. "
                        "Used for variant narrowing and matching quality."
                    ),
                    key="_ean_desc_col",
                )
            with ecol4:
                ean_threshold = st.slider(
                    "Match threshold",
                    min_value=50,
                    max_value=100,
                    value=70,
                    help=(
                        "Minimum similarity score (%) for fuzzy SKU "
                        "matching. Lower = more matches but less precise."
                    ),
                    key="_ean_threshold",
                )

            if inv_sku_col != '(none)':
                qty_col = (
                    inv_qty_col if inv_qty_col != '(none)' else None
                )
                desc_col = (
                    inv_desc_col if inv_desc_col != '(none)' else None
                )

                # Step 1: run matching
                mdata = match_invoice_to_products(
                    products_df=work_df,
                    invoice_df=inv_df,
                    invoice_sku_col=inv_sku_col,
                    invoice_qty_col=qty_col,
                    threshold=ean_threshold,
                    invoice_desc_col=desc_col,
                )

                raw_matches = mdata['matches']
                title_lookup = mdata['title_lookup']
                ean_composite = mdata['composite_lookup']

                # Build invoice-side name map for display
                inv_names: dict[str, str] = {}
                if desc_col and desc_col in inv_df.columns:
                    for idx in inv_df.index:
                        raw_sku = inv_df.at[idx, inv_sku_col]
                        if pd.isna(raw_sku):
                            continue
                        k = str(raw_sku).strip()
                        if k and k not in inv_names:
                            inv_names[k] = str(
                                inv_df.at[idx, desc_col] or ''
                            ).strip()

                # Split into auto-matched and unmatched
                auto_matched = {
                    k: v for k, v in raw_matches.items()
                    if v['sku'] is not None
                }
                unmatched = {
                    k: v for k, v in raw_matches.items()
                    if v['sku'] is None and v['alternatives']
                }

                # Step 2: manual matching for unmatched items
                manual_overrides: dict[str, str] = {}
                if unmatched:
                    with st.expander(
                        f"\u26a0\ufe0f {len(unmatched)} unmatched "
                        f"invoice line(s) \u2014 select matches",
                        expanded=True,
                    ):
                        st.caption(
                            "These invoice SKUs could not be "
                            "automatically matched. Pick the correct "
                            "product from the suggestions, or skip."
                        )
                        for inv_sku, md in unmatched.items():
                            alts = md['alternatives']
                            options = ['(skip \u2014 no match)']
                            for alt_sku, alt_score in alts:
                                lbl = alt_sku
                                # Show product title
                                _num, _vt = ean_composite.get(
                                    alt_sku, (alt_sku, '')
                                )
                                if _num in title_lookup:
                                    lbl += (
                                        f" \u2014 "
                                        f"{title_lookup[_num]}"
                                    )
                                # Show variant name when present
                                if _vt:
                                    lbl += f" [{_vt}]"
                                lbl += f" ({alt_score}%)"
                                options.append(lbl)

                            inv_label = f"\U0001f50d {inv_sku}"
                            if inv_sku in inv_names and inv_names[inv_sku]:
                                inv_label += (
                                    f" \u2014 "
                                    f"{inv_names[inv_sku][:60]}"
                                )

                            sel = st.selectbox(
                                inv_label, options,
                                key=f"_ean_manual_{inv_sku}",
                            )
                            if sel != '(skip \u2014 no match)':
                                idx = options.index(sel) - 1
                                alt_sku, _ = alts[idx]
                                manual_overrides[inv_sku] = alt_sku

                # Step 3: build the export with manual overrides
                export_df = build_export_from_matches(
                    work_df, mdata,
                    manual_overrides=manual_overrides or None,
                )

                matched_count = len(auto_matched) + len(manual_overrides)
                skipped = len(unmatched) - len(manual_overrides)

                if export_df.empty:
                    st.warning(
                        "No matches found. Try lowering the match "
                        "threshold or checking the SKU column."
                    )
                else:
                    status_parts = [
                        f"**{len(export_df)}** matched line"
                        f"{'s' if len(export_df) != 1 else ''}"
                    ]
                    if manual_overrides:
                        status_parts.append(
                            f"({len(manual_overrides)} manual)"
                        )
                    if skipped > 0:
                        status_parts.append(
                            f"\u2014 {skipped} skipped"
                        )
                    st.markdown(" ".join(status_parts))
                    st.dataframe(
                        export_df,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            'Match %': st.column_config.ProgressColumn(
                                'Match %',
                                min_value=0,
                                max_value=100,
                                format="%d%%",
                            ),
                            'Amount': st.column_config.NumberColumn(
                                'Amount',
                                format="%.0f",
                            ),
                        },
                    )

                    csv_data = (
                        "\ufeff"
                        + export_df.to_csv(sep=';', index=False)
                    )

                    dl_col1, dl_col2 = st.columns(2)
                    with dl_col1:
                        pdf_bytes = generate_barcode_pdf(export_df)
                        st.download_button(
                            label="Download Barcode PDF",
                            data=pdf_bytes,
                            file_name="ean_barcode_export.pdf",
                            mime="application/pdf",
                            use_container_width=True,
                            key="_ean_download_pdf",
                        )
                    with dl_col2:
                        st.download_button(
                            label="Download Data (CSV)",
                            data=csv_data.encode('utf-8'),
                            file_name="ean_barcode_export.csv",
                            mime="text/csv; charset=utf-8",
                            use_container_width=True,
                            key="_ean_download",
                        )
            else:
                st.info(
                    "Select the **SKU / Article** column above to "
                    "start matching."
                )
    else:
        st.info(
            "Upload an invoice file (CSV or PDF) to match products "
            "and generate a scannable EAN barcode document."
        )


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def _render_downloads(final_df, import_df, adjusted_count, include_buy_price):
    """Render the download section."""
    st.markdown(
        '<div class="section-header" style="margin-top:1rem;">'
        'Download Reports</div>',
        unsafe_allow_html=True,
    )
    dl_col1, dl_col2 = st.columns(2, gap="small")

    csv_preview = "\ufeff" + "PRODUCTS\n" + final_df.to_csv(sep=';', index=False)
    with dl_col1:
        st.download_button(
            label="Preview \u2013 Full Report CSV",
            data=csv_preview.encode('utf-8'),
            file_name="preview_products.csv",
            mime="text/csv; charset=utf-8",
        )

    import_cols = IMPORT_COLUMNS_BASE.copy()
    if include_buy_price:
        import_cols.insert(3, 'BUY_PRICE')

    if import_df.empty:
        with dl_col2:
            st.download_button(
                label="Import-Ready CSV",
                data="",
                file_name="import_products.csv",
                mime="text/csv; charset=utf-8",
                disabled=True,
            )
            st.caption("No products needed adjustment.")
    else:
        csv_import = (
            "\ufeff" + "PRODUCTS\n"
            + import_df[import_cols].to_csv(sep=';', index=False)
        )
        with dl_col2:
            st.download_button(
                label="Import-Ready CSV",
                data=csv_import.encode('utf-8'),
                file_name="import_products.csv",
                mime="text/csv; charset=utf-8",
            )


# ---------------------------------------------------------------------------
# Push to Shop
# ---------------------------------------------------------------------------

def _render_push_to_shop(
    final_df,
    adjusted_mask,
    parsed_df,
    work_df,
    api_username,
    api_password,
    api_ready,
    site_id,
    dry_run,
):
    """Render the push-to-shop section with safety gates."""
    st.divider()
    st.markdown(
        '<div class="section-header">Push to Shop</div>',
        unsafe_allow_html=True,
    )

    if not api_ready:
        st.markdown(
            '<div class="info-card">'
            "<h4>API Not Connected</h4>"
            "<p>Configure your DanDomain API credentials in the sidebar "
            "to enable direct price updates to your live webshop.</p>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    all_potential_updates = build_push_updates(
        final_df,
        adjusted_mask,
        parsed_df['BUY_PRICE_NUM'],
        work_df['BUY_PRICE_NUM'],
        selected_indices=None,
    )

    if not all_potential_updates:
        st.info(
            "No products have actual price or cost "
            "changes to push."
        )
        return

    mode_pill = (
        '<span class="status-pill dry">DRY-RUN</span>'
        if dry_run
        else '<span class="status-pill live">LIVE</span>'
    )

    # --- Product selection table ---
    sel_rows: list[dict] = []
    for u in all_potential_updates:
        changes: list[str] = []
        if "new_price" in u:
            changes.append(
                f"Price: {format_dk(u['old_price'])} → "
                f"{format_dk(u['new_price'])}"
            )
        if "buy_price" in u:
            old_bp = u.get("old_buy_price")
            if old_bp is not None:
                changes.append(
                    f"BuyPrice: {format_dk(old_bp)} → "
                    f"{format_dk(u['buy_price'])}"
                )
            else:
                changes.append(
                    f"BuyPrice: → {format_dk(u['buy_price'])}"
                )
        sel_rows.append({
            "Push": True,
            "Product ID": u["product_id"],
            "Number": u["product_number"],
            "Title": u.get("title", ""),
            "Variant ID": u.get("variant_id", ""),
            "Changes": " · ".join(changes),
            "Endpoint": u.get("endpoint", ""),
        })

    sel_df = pd.DataFrame(sel_rows)
    edited_sel = st.data_editor(
        sel_df,
        disabled=[c for c in sel_df.columns if c != "Push"],
        column_config={
            "Push": st.column_config.CheckboxColumn(
                "Push", default=True,
            ),
            "Product ID": st.column_config.TextColumn(width="small"),
            "Number": st.column_config.TextColumn(width="small"),
            "Title": st.column_config.TextColumn(width="medium"),
            "Variant ID": st.column_config.TextColumn(width="small"),
            "Changes": st.column_config.TextColumn(width="large"),
            "Endpoint": st.column_config.TextColumn(width="medium"),
        },
        use_container_width=True,
        hide_index=True,
        key="_push_sel",
    )

    push_flags = edited_sel["Push"].tolist()
    if len(push_flags) != len(all_potential_updates):
        st.error("Selection state mismatch — please re-run the page.")
        selected_updates = []
    else:
        selected_updates = [
            u for u, sel in zip(all_potential_updates, push_flags) if sel
        ]

    n_selected = len(selected_updates)
    n_total = len(all_potential_updates)
    n_variants = sum(1 for u in selected_updates if u.get("variant_id"))
    n_base = n_selected - n_variants
    endpoints_summary: list[str] = []
    if n_base > 0:
        endpoints_summary.append(f"Product_Update × {n_base}")
    if n_variants > 0:
        endpoints_summary.append(f"Product_UpdateVariant × {n_variants}")

    st.markdown(
        f"{mode_pill} &nbsp; "
        f"**{n_selected}** / {n_total} products selected "
        f"&nbsp;·&nbsp; Endpoints: "
        f"{', '.join(endpoints_summary) if endpoints_summary else '—'}",
        unsafe_allow_html=True,
    )

    if n_selected == 0:
        st.info("Select at least one product to push.")
    else:
        test_col, push_col = st.columns(2)
        with test_col:
            if st.button(
                "Test Connection",
                use_container_width=True,
            ):
                try:
                    with DanDomainClient(
                        api_username, api_password,
                    ) as client:
                        info = client.test_connection()
                    st.success(
                        f"Connected! Product count: "
                        f"{info.get('product_count', 'N/A')}"
                    )
                except (
                    DanDomainAPIError, ValueError, AttributeError,
                ) as exc:
                    st.error(f"Connection failed: {exc}")

        with push_col:
            push_clicked = st.button(
                "Simulate Push" if dry_run else "Push Prices Now",
                type="primary" if not dry_run else "secondary",
                use_container_width=True,
                disabled=st.session_state.get("_push_running", False),
            )

        if push_clicked:
            if dry_run:
                _handle_dry_run(selected_updates, n_selected)
            else:
                st.session_state["_push_pending"] = True
                st.session_state["_push_updates"] = selected_updates

        # Handle pending live-push confirmation
        if (
            st.session_state.get("_push_pending")
            and not dry_run
            and not st.session_state.get("_push_running")
        ):
            _handle_live_push_confirmation(
                api_username, api_password, site_id,
            )


def _handle_dry_run(selected_updates, n_selected):
    """Display dry-run results."""
    st.info(
        f"**Dry-run**: {n_selected} product(s) would be updated. "
        "Disable dry-run in the sidebar to push for real."
    )
    dry_data: list[dict] = []
    for u in selected_updates:
        r: dict = {
            "Product ID": u["product_id"],
            "Number": u["product_number"],
            "Title": u.get("title", ""),
            "Variant ID": u.get("variant_id", ""),
        }
        if "new_price" in u:
            r["Old Price"] = u.get("old_price", "")
            r["New Price"] = u["new_price"]
        if "buy_price" in u:
            r["Old Buy Price"] = u.get("old_buy_price", "")
            r["New Buy Price"] = u["buy_price"]
        r["Endpoint"] = u.get("endpoint", "")
        dry_data.append(r)
    st.dataframe(
        pd.DataFrame(dry_data),
        use_container_width=True,
        hide_index=True,
    )


def _handle_live_push_confirmation(api_username, api_password, site_id):
    """Handle the two-step live push confirmation."""
    pending_updates = st.session_state.get("_push_updates", [])
    n_pend = len(pending_updates)
    n_pend_var = sum(1 for u in pending_updates if u.get("variant_id"))
    n_pend_base = n_pend - n_pend_var
    ep_list: list[str] = []
    if n_pend_base > 0:
        ep_list.append(f"Product_Update × {n_pend_base}")
    if n_pend_var > 0:
        ep_list.append(f"Product_UpdateVariant × {n_pend_var}")

    st.markdown(
        '<div class="confirm-banner">'
        "<strong>Confirm Live Push</strong><br>"
        f"<strong>{n_pend}</strong> selected product(s) "
        "with verified changes<br>"
        f"Endpoints: <strong>{', '.join(ep_list)}</strong><br>"
        "This action cannot be undone automatically."
        "</div>",
        unsafe_allow_html=True,
    )

    confirm_col, cancel_col = st.columns(2)
    with confirm_col:
        confirmed = st.button(
            "Confirm and Push",
            type="primary",
            use_container_width=True,
        )
    with cancel_col:
        cancelled = st.button(
            "Cancel",
            use_container_width=True,
        )

    if cancelled:
        st.session_state.pop("_push_pending", None)
        st.session_state.pop("_push_updates", None)
        st.info("Push cancelled.")

    if confirmed:
        _execute_live_push(
            pending_updates, api_username, api_password, site_id,
        )


def _execute_live_push(pending_updates, api_username, api_password, site_id):
    """Execute the actual live push to the shop."""
    st.session_state["_push_running"] = True
    st.session_state.pop("_push_pending", None)

    progress_bar = st.progress(0, text="Pushing prices…")
    log_entries: list[dict] = []

    def on_progress(idx, total, pnum, ok, err):
        progress_bar.progress(
            idx / total,
            text=f"Updating {idx}/{total}: {pnum}",
        )
        entry = {
            "product_number": pnum,
            "status": "OK" if ok else "FAILED",
            "error": err,
            "timestamp": time.strftime("%H:%M:%S"),
        }
        if idx - 1 < len(pending_updates):
            u = pending_updates[idx - 1]
            entry["product_id"] = u.get("product_id", "")
            entry["variant_id"] = u.get("variant_id", "")
            entry["variant_types"] = u.get("variant_types", "")
        log_entries.append(entry)

    try:
        with DanDomainClient(api_username, api_password) as client:
            results = client.update_prices_batch(
                pending_updates,
                site_id=site_id,
                progress_callback=on_progress,
            )

        progress_bar.progress(1.0, text="Done!")

        res_c1, res_c2 = st.columns(2)
        res_c1.metric("Succeeded", results["success"])
        res_c2.metric("Failed", results["failed"])

        if results["errors"]:
            with st.expander("Errors", expanded=True):
                st.dataframe(
                    pd.DataFrame(results["errors"]),
                    use_container_width=True,
                    hide_index=True,
                )

    except (DanDomainAPIError, ValueError, AttributeError) as exc:
        st.error(f"Push failed: {exc}")
    finally:
        st.session_state.pop("_push_running", None)
        st.session_state.pop("_push_updates", None)

    if log_entries:
        log_df = pd.DataFrame(log_entries)
        log_csv = log_df.to_csv(index=False)
        st.download_button(
            label="Download Audit Log",
            data=log_csv.encode("utf-8"),
            file_name="api_push_log.csv",
            mime="text/csv",
        )
