import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from azure.identity import DefaultAzureCredential
from azure.cosmos import CosmosClient

# ---- Configuration ----
COSMOS_ENDPOINT = "https://cosmos-sentinel-triage.documents.azure.com:443/"
COSMOS_DATABASE = "TriageDB"
COSMOS_CONTAINER = "Incidents"

st.set_page_config(page_title="Triage Console", layout="wide", initial_sidebar_state="collapsed")

# ---- Styling ----
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');

.stApp {
    background-color: #0A0C10;
    color: #E8E6DF;
    font-family: 'IBM Plex Mono', monospace;
}

h1, h2, h3 {
    font-family: 'IBM Plex Mono', monospace !important;
    font-weight: 700 !important;
    color: #F0EEE6 !important;
    letter-spacing: -0.02em;
}

h1 {
    font-size: 1.15rem !important;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    margin-bottom: 0 !important;
}

.console-header {
    display: flex;
    align-items: baseline;
    gap: 10px;
    border-bottom: 1px solid #242833;
    padding-bottom: 10px;
    margin-bottom: 24px;
}
.console-header .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #4ADE80;
    box-shadow: 0 0 6px #4ADE80;
    display: inline-block;
}
.console-path {
    font-size: 0.78rem;
    color: #6B7280;
}

.eyebrow {
    font-size: 0.68rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #6B7280;
    margin-bottom: 6px;
    border-left: 2px solid #D9A441;
    padding-left: 8px;
}

/* Status ticker — replaces card grid */
.ticker {
    display: flex;
    border: 1px solid #242833;
    border-radius: 4px;
    overflow: hidden;
    font-size: 0.82rem;
}
.ticker-cell {
    flex: 1;
    padding: 10px 16px;
    border-right: 1px solid #242833;
}
.ticker-cell:last-child { border-right: none; }
.ticker-cell .t-label {
    color: #6B7280;
    font-size: 0.65rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}
.ticker-cell .t-value {
    font-size: 1.5rem;
    font-weight: 700;
    color: #F0EEE6;
}
.ticker-cell.warn .t-value { color: #E8C547; }
.ticker-cell.crit .t-value { color: #E5484D; }
.ticker-cell.good .t-value { color: #4ADE80; }

/* Segmented severity meter — replaces bar chart */
.meter {
    display: flex;
    height: 28px;
    border-radius: 3px;
    overflow: hidden;
    border: 1px solid #242833;
}
.meter-seg { position: relative; }
.meter-legend {
    display: flex;
    gap: 18px;
    margin-top: 8px;
    font-size: 0.72rem;
    color: #9CA3AF;
}
.meter-legend .sw {
    display: inline-block; width: 8px; height: 8px; margin-right: 5px; border-radius: 1px;
}

/* Severity chips */
.chip {
    display: inline-block;
    font-size: 0.68rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 3px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.chip-critical { background: rgba(229,72,77,0.15); color: #F5787C; border: 1px solid rgba(229,72,77,0.4); }
.chip-high     { background: rgba(242,153,74,0.15); color: #F6B074; border: 1px solid rgba(242,153,74,0.4); }
.chip-medium   { background: rgba(232,197,71,0.15); color: #E8C547; border: 1px solid rgba(232,197,71,0.4); }
.chip-low      { background: rgba(74,222,128,0.15); color: #4ADE80; border: 1px solid rgba(74,222,128,0.4); }
.chip-unknown  { background: rgba(107,114,128,0.2); color: #9CA3AF; border: 1px solid rgba(107,114,128,0.4); }

.mono-tag {
    font-size: 0.74rem;
    color: #D9A441;
    background: rgba(217,164,65,0.1);
    padding: 1px 7px;
    border-radius: 3px;
    border: 1px solid rgba(217,164,65,0.25);
}

/* Buttons */
.stButton button {
    font-size: 0.78rem !important;
    border-radius: 3px !important;
    border: 1px solid #242833 !important;
    background: #12151B !important;
    color: #C9CDD6 !important;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
.stButton button:hover {
    border-color: #D9A441 !important;
    color: #D9A441 !important;
}

hr, .stDivider { border-color: #242833 !important; }
section[data-testid="stSidebar"] { display: none; }
[data-testid="stDataFrame"] { display: none; }
</style>
""", unsafe_allow_html=True)


# ---- Cosmos connection ----
@st.cache_resource
def get_container():
    credential = DefaultAzureCredential()
    client = CosmosClient(url=COSMOS_ENDPOINT, credential=credential)
    database = client.get_database_client(COSMOS_DATABASE)
    container = database.get_container_client(COSMOS_CONTAINER)
    return container


def load_records(container):
    return list(container.query_items(query="SELECT * FROM c", enable_cross_partition_query=True))


def update_human_review(container, record, status):
    record["human_review"]["status"] = status
    record["human_review"]["reviewed_by"] = "analyst"
    record["human_review"]["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    container.upsert_item(record)


def severity_chip(sev):
    sev = (sev or "unknown").lower()
    if sev not in ("critical", "high", "medium", "low"):
        sev = "unknown"
    return f'<span class="chip chip-{sev}">{sev}</span>'


# ---- Load ----
container = get_container()
if "records" not in st.session_state:
    st.session_state.records = load_records(container)
records = st.session_state.records

st.markdown(
    '<div class="console-header"><span class="dot"></span>'
    '<h1>Alert Triage Console</h1></div>',
    unsafe_allow_html=True
)

if st.button("↻ refresh"):
    st.session_state.records = load_records(container)
    st.rerun()

if not records:
    st.warning("No records found.")
    st.stop()

rows = []
for r in records:
    rows.append({
        "incident_id": r.get("incident_id"),
        "title": r.get("raw_alert", {}).get("title"),
        "input_severity": r.get("raw_alert", {}).get("severity"),
        "ai_severity": r.get("enrichment", {}).get("severity"),
        "mitre_technique": r.get("enrichment", {}).get("mitre_technique"),
        "confidence": r.get("enrichment", {}).get("confidence"),
        "parse_status": r.get("decision_log", {}).get("parse_status"),
        "review_status": r.get("human_review", {}).get("status"),
    })
df = pd.DataFrame(rows)

# ---- Summary ----
total = len(df)
failure_count = int((df["parse_status"] == "failed").sum())
failure_rate = (failure_count / total * 100) if total else 0
auto_eligible = int((df["ai_severity"].isin(["low", "medium"]) & (df["parse_status"] == "success")).sum())
review_required = total - auto_eligible

failure_class = "crit" if failure_rate > 30 else ("warn" if failure_rate > 10 else "")
st.markdown(f'''
<div class="ticker">
    <div class="ticker-cell"><div class="t-label">Total</div><div class="t-value">{total}</div></div>
    <div class="ticker-cell good"><div class="t-label">Auto-Eligible</div><div class="t-value">{auto_eligible}</div></div>
    <div class="ticker-cell warn"><div class="t-label">Review Req.</div><div class="t-value">{review_required}</div></div>
    <div class="ticker-cell {failure_class}"><div class="t-label">Failure Rate</div><div class="t-value">{failure_rate:.1f}%</div></div>
</div>
''', unsafe_allow_html=True)

st.markdown('<div style="height:28px;"></div>', unsafe_allow_html=True)

with st.expander("Severity Distribution", expanded=False):
    severity_colors = {
        "critical": "#E5484D", "high": "#F2994A", "medium": "#E8C547",
        "low": "#4ADE80", "unknown": "#3A3F4B"
    }
    severity_order = ["critical", "high", "medium", "low", "unknown"]
    counts = df["ai_severity"].fillna("unknown").value_counts()
    counts = counts.reindex(severity_order).dropna()
    total_count = counts.sum()

    segs_html = ""
    legend_html = ""
    for sev in counts.index:
        n = counts[sev]
        pct = (n / total_count * 100) if total_count else 0
        color = severity_colors.get(sev, "#3A3F4B")
        segs_html += f'<div class="meter-seg" style="width:{pct}%; background:{color};" title="{sev}: {n}"></div>'
        legend_html += f'<span><span class="sw" style="background:{color};"></span>{sev} &middot; {n}</span>'

    st.markdown(f'<div class="meter">{segs_html}</div><div class="meter-legend">{legend_html}</div>', unsafe_allow_html=True)

st.divider()

# ---- Filters + table ----
with st.expander("Incident Log", expanded=True):
    fc1, fc2 = st.columns(2)
    with fc1:
        severity_filter = st.multiselect("Severity", sorted(df["ai_severity"].dropna().unique().tolist()))
    with fc2:
        parse_filter = st.multiselect("Parse status", sorted(df["parse_status"].dropna().unique().tolist()))

    filtered_df = df.copy()
    if severity_filter:
        filtered_df = filtered_df[filtered_df["ai_severity"].isin(severity_filter)]
    if parse_filter:
        filtered_df = filtered_df[filtered_df["parse_status"].isin(parse_filter)]

    # ---- Native Streamlit sort state (no page navigation) ----
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "informational": 4, "unknown": 5}
    confidence_rank = {"high": 0, "medium": 1, "low": 2, "unknown": 3}

    columns = [
        ("incident_id", "ID", 1),
        ("title", "Title", 3),
        ("input_severity", "Input Sev.", 1),
        ("ai_severity", "AI Sev.", 1),
        ("mitre_technique", "MITRE", 1),
        ("confidence", "Confidence", 1),
        ("parse_status", "Parse", 1),
        ("review_status", "Review", 1),
    ]
    default_first_dir = {
        "incident_id": "asc", "title": "asc", "input_severity": "desc",
        "ai_severity": "desc", "mitre_technique": "asc", "confidence": "desc",
        "parse_status": "asc", "review_status": "asc",
    }

    if "sort_col" not in st.session_state:
        st.session_state.sort_col = "incident_id"
        st.session_state.sort_dir = "asc"
    if "selected_id" not in st.session_state:
        st.session_state.selected_id = None

    # Apply sort
    col = st.session_state.sort_col
    ascending = (st.session_state.sort_dir == "asc")
    if col == "incident_id":
        sort_key = pd.to_numeric(filtered_df[col], errors="coerce")
    elif col in ("input_severity", "ai_severity"):
        sort_key = filtered_df[col].str.lower().map(severity_rank).fillna(99)
    elif col == "confidence":
        sort_key = filtered_df[col].str.lower().map(confidence_rank).fillna(99)
    else:
        sort_key = filtered_df[col].fillna("").str.lower()
    filtered_df = filtered_df.assign(_sk=sort_key).sort_values("_sk", ascending=ascending, kind="stable").drop(columns="_sk")

    # ---- Header row (sort buttons) ----
    header_cols = st.columns([w for _, _, w in columns])
    for (key, label, _), hc in zip(columns, header_cols):
        arrow = ""
        if st.session_state.sort_col == key:
            arrow = " ▲" if st.session_state.sort_dir == "asc" else " ▼"
        if hc.button(f"{label}{arrow}", key=f"hdr_{key}", use_container_width=True):
            if st.session_state.sort_col == key:
                st.session_state.sort_dir = "desc" if st.session_state.sort_dir == "asc" else "asc"
            else:
                st.session_state.sort_col = key
                st.session_state.sort_dir = default_first_dir[key]
            st.rerun()

    st.markdown('<hr style="margin:4px 0; border-color:#242833;">', unsafe_allow_html=True)

    # ---- Body rows (click ID to select) ----
    for _, row in filtered_df.iterrows():
        row_cols = st.columns([w for _, _, w in columns])
        if row_cols[0].button(str(row["incident_id"]), key=f"row_{row['incident_id']}", use_container_width=True):
            st.session_state.selected_id = row["incident_id"]
            st.rerun()
        row_cols[1].markdown(f'<div style="padding-top:6px;">{row["title"]}</div>', unsafe_allow_html=True)
        row_cols[2].markdown(f'<div style="padding-top:6px;">{row["input_severity"] or "&mdash;"}</div>', unsafe_allow_html=True)
        row_cols[3].markdown(f'<div style="padding-top:4px;">{severity_chip(row["ai_severity"])}</div>', unsafe_allow_html=True)
        row_cols[4].markdown(f'<div style="padding-top:6px;">{row["mitre_technique"] or "&mdash;"}</div>', unsafe_allow_html=True)
        row_cols[5].markdown(f'<div style="padding-top:6px;">{row["confidence"] or "&mdash;"}</div>', unsafe_allow_html=True)
        row_cols[6].markdown(f'<div style="padding-top:6px;">{row["parse_status"] or "&mdash;"}</div>', unsafe_allow_html=True)
        row_cols[7].markdown(f'<div style="padding-top:6px;">{row["review_status"] or "pending"}</div>', unsafe_allow_html=True)

st.divider()

# ---- Detail + review ----
st.subheader("Review")
incident_ids = filtered_df["incident_id"].tolist()
if not incident_ids:
    st.info("No incidents match the current filters.")
    st.stop()

default_index = 0
if st.session_state.selected_id and st.session_state.selected_id in incident_ids:
    default_index = incident_ids.index(st.session_state.selected_id)

selected_id = st.selectbox("Incident", incident_ids, index=default_index)
st.session_state.selected_id = selected_id  # manual picks also update the tracked selection
selected_record = next((r for r in records if r.get("incident_id") == selected_id), None)

if selected_record:
    enrichment = selected_record.get("enrichment", {})
    st.markdown(
        f'{severity_chip(enrichment.get("severity"))} '
        f'<span class="mono-tag">{enrichment.get("mitre_technique", "unknown")}</span> '
        f'<span class="mono-tag">{enrichment.get("technique_name", "")}</span>',
        unsafe_allow_html=True
    )
    st.write("")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div class="eyebrow">Raw Alert</div>', unsafe_allow_html=True)
        st.json(selected_record.get("raw_alert", {}))
    with col2:
        st.markdown('<div class="eyebrow">AI Enrichment</div>', unsafe_allow_html=True)
        st.json(enrichment)

    st.markdown('<div class="eyebrow">Decision Log</div>', unsafe_allow_html=True)
    st.json(selected_record.get("decision_log", {}))

    current_status = selected_record.get("human_review", {}).get("status", "pending")
    st.markdown(f'<div class="eyebrow">Review Status: <span class="mono-tag">{current_status}</span></div>', unsafe_allow_html=True)
    st.write("")

    b1, b2, b3 = st.columns(3)
    if b1.button("Approve", width="stretch"):
        update_human_review(container, selected_record, "approved")
        st.session_state.records = load_records(container)
        st.rerun()
    if b2.button("Reject", width="stretch"):
        update_human_review(container, selected_record, "rejected")
        st.session_state.records = load_records(container)
        st.rerun()
    if b3.button("Needs Review", width="stretch"):
        update_human_review(container, selected_record, "needs_review")
        st.session_state.records = load_records(container)
        st.rerun()