"""Shared CSS styles for the Streamlit dashboard."""

DASHBOARD_CSS = """
<style>
/* ---- Global ---- */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI',
                 Roboto, sans-serif;
}

/* ---- Sidebar ---- */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #f5f5f5 0%, #e0e0e0 100%);
}
section[data-testid="stSidebar"] * {
    color: #37474f !important;
}
section[data-testid="stSidebar"] .stSelectbox label,
section[data-testid="stSidebar"] .stNumberInput label,
section[data-testid="stSidebar"] .stCheckbox label,
section[data-testid="stSidebar"] .stTextInput label {
    font-weight: 600;
    font-size: 0.82rem;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: #455a64 !important;
}
section[data-testid="stSidebar"] .stCaption {
    color: #546e7a !important;
    font-weight: 700;
    letter-spacing: 0.08em;
}

/* ---- Metrics cards ---- */
div[data-testid="stMetric"] {
    background: #ffffff;
    border: 1px solid #cfd8dc;
    border-radius: 16px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.02);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
}
div[data-testid="stMetric"]:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
div[data-testid="stMetric"] label {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
    color: #607d8b !important;
}
div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
    font-weight: 700;
    font-size: 2rem;
    color: #37474f !important;
}

/* ---- Buttons ---- */
.stButton > button {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    letter-spacing: 0.02em;
    padding: 0.6rem 1.25rem;
    transition: all 0.2s cubic-bezier(.4,0,.2,1);
    border: 1px solid transparent;
}
.stButton > button:hover {
    transform: translateY(-1px);
    box-shadow: 0 6px 20px rgba(0,0,0,0.12);
}
.stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #78909c 0%, #546e7a 100%);
    color: #fff !important;
}

/* ---- Download buttons ---- */
.stDownloadButton > button {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    background: #f5f5f5;
    border: 1px solid #cfd8dc;
    color: #37474f !important;
    transition: all 0.15s ease;
}
.stDownloadButton > button:hover {
    background: #eceff1;
    border-color: #b0bec5;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}
/* ---- Download section compact layout ---- */
.download-row {
    display: flex;
    gap: 0.75rem;
    align-items: stretch;
    margin-top: 0.25rem;
}
.download-row .stDownloadButton { flex: 1; }
/* ---- Supplier match score badge ---- */
.match-score {
    display: inline-block;
    padding: 0.15rem 0.5rem;
    border-radius: 12px;
    font-size: 0.75rem;
    font-weight: 600;
}
.match-score.high   { background: #c8e6c9; color: #1b5e20; }
.match-score.medium { background: #fff9c4; color: #f57f17; }
.match-score.low    { background: #ffcdd2; color: #b71c1c; }

/* ---- Expanders ---- */
.streamlit-expanderHeader {
    font-weight: 600;
    font-size: 0.95rem;
    color: #37474f;
    border-radius: 12px;
}

/* ---- Tabs ---- */
.stTabs [data-baseweb="tab-list"] {
    gap: 0.25rem;
    background: #eceff1;
    border-radius: 12px;
    padding: 0.25rem;
}
.stTabs [data-baseweb="tab"] {
    border-radius: 10px;
    font-weight: 600;
    font-size: 0.88rem;
    padding: 0.5rem 1.5rem;
    color: #607d8b;
    transition: all 0.15s ease;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: #ffffff !important;
    color: #37474f !important;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

/* ---- Dataframes ---- */
.stDataFrame {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid #cfd8dc;
}

/* ---- Dividers ---- */
hr {
    border: none;
    border-top: 1px solid #cfd8dc;
    margin: 2rem 0;
}

/* ---- File uploader ---- */
section[data-testid="stFileUploader"] {
    border: 2px dashed #b0bec5;
    border-radius: 16px;
    padding: 1rem;
    background: #f5f5f5;
    transition: all 0.2s ease;
}
section[data-testid="stFileUploader"]:hover {
    border-color: #78909c;
    background: #eceff1;
}

/* ---- Section headers ---- */
.section-header {
    font-size: 1.15rem;
    font-weight: 700;
    color: #37474f;
    margin-bottom: 0.75rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

/* ---- Confirmation banner ---- */
.confirm-banner {
    background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
    border: 1px solid #f59e0b;
    border-left: 4px solid #f59e0b;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
}
.confirm-banner strong { color: #92400e; }

/* ---- Status pill ---- */
.status-pill {
    display: inline-block;
    padding: 0.2rem 0.75rem;
    border-radius: 20px;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.03em;
}
.status-pill.live { background: #fee2e2; color: #991b1b; }
.status-pill.dry  { background: #eceff1; color: #37474f; }

/* ---- Hero header ---- */
.hero-header {
    margin-bottom: 0.25rem;
    font-size: 2rem;
    font-weight: 800;
    color: #37474f;
    letter-spacing: -0.02em;
}
.hero-sub {
    color: #607d8b;
    margin-top: 0;
    font-size: 1.05rem;
    line-height: 1.5;
    max-width: 700px;
}
.hero-sub strong { color: #455a64; }

/* ---- Info cards ---- */
.info-card {
    background: #f5f5f5;
    border: 1px solid #cfd8dc;
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 0.75rem;
}
.info-card h4 {
    margin: 0 0 0.4rem 0;
    color: #37474f;
    font-size: 0.95rem;
}
.info-card p {
    margin: 0;
    color: #607d8b;
    font-size: 0.88rem;
    line-height: 1.5;
}

/* ---- Sidebar version badge ---- */
.version-badge {
    text-align: center;
    opacity: 0.7;
    font-size: 0.72rem;
    padding: 0.5rem;
    border-radius: 8px;
    background: rgba(0,0,0,0.05);
}

/* ---- Nav item styling ---- */
.nav-item {
    padding: 0.4rem 0.75rem;
    border-radius: 10px;
    margin-bottom: 0.25rem;
    font-weight: 600;
    font-size: 0.9rem;
}
.nav-item.active {
    background: rgba(0,0,0,0.08);
}

/* ---- Dashboard cards ---- */
.dash-card {
    background: #ffffff;
    border: 1px solid #cfd8dc;
    border-radius: 16px;
    padding: 1.5rem;
    text-align: center;
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    cursor: default;
}
.dash-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
.dash-card h3 {
    margin: 0 0 0.4rem 0;
    color: #37474f;
    font-size: 1rem;
}
.dash-card p {
    margin: 0;
    color: #607d8b;
    font-size: 0.82rem;
    line-height: 1.4;
}
.dash-card.disabled {
    opacity: 0.5;
}
</style>
"""
