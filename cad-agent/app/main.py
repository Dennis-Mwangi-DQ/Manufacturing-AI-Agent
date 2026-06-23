"""
Streamlit UI for the Engineering CAD AI Agent.
Calls FastAPI endpoints to process CAD files and display results.
"""
from __future__ import annotations

import io
import os
import sys
import time
from datetime import date
from pathlib import Path

# Streamlit runs this file directly; ensure project root is on sys.path.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd
import requests
import streamlit as st

from app.config import get_settings

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_settings = get_settings()
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

SUPPORTED_FORMATS = [".stp", ".step", ".igs", ".iges", ".dxf"]
MAX_FILE_MB = _settings.MAX_FILE_SIZE_MB

# ---------------------------------------------------------------------------
# Demo mode synthetic data
# ---------------------------------------------------------------------------

DEMO_RESULT = {
    "session_id": "demo-00000000-0000-0000-0000-000000000001",
    "status": "SUCCESS",
    "parts_extracted": 12,
    "bom_lines": 12,
    "dxf_files_generated": 7,
    "bending_drawings_generated": 5,
    "assembly_drawings_generated": 1,
    "processing_time_seconds": 4.2,
    "warnings": [
        "3 parts have low-confidence classifications. Human review recommended.",
        "2 parts have inferred materials. Verify before manufacturing.",
    ],
    "errors": [],
    "download_url": "#demo",
    "summary_report": "# CAD Agent Demo Run\n\nThis is a simulated result for demonstration purposes.",
}

DEMO_BOM = [
    {"SR NO.": 1, "IMAGE": "", "SS ENGRAVING NAME": "Q1-AV001", "Revision": 1,
     "DXF File Name": "B4_Q1-AV001-DXF-1.DXF", "Material": "BALLISTIC STEEL", "Hardness": "500",
     "Thickness (mm)": 8, "Quantity": 1, "ASSY": "12500", "SCOPE OF WORK": "L+B",
     "Notes": "", "Flags": "BENDS:2"},
    {"SR NO.": 2, "IMAGE": "", "SS ENGRAVING NAME": "Q1-AV002", "Revision": 1,
     "DXF File Name": "B4_Q1-AV002-DXF-1.DXF", "Material": "BALLISTIC STEEL (INFERRED)", "Hardness": "500",
     "Thickness (mm)": 6, "Quantity": 1, "ASSY": "12500", "SCOPE OF WORK": "L",
     "Notes": "LOW CONFIDENCE", "Flags": "LOW_CONFIDENCE;INFERRED"},
]


def _load_bom_preview(result: dict, is_demo: bool) -> pd.DataFrame | None:
    """Build a BOM dataframe from the API response or /bom endpoint."""
    if is_demo:
        return pd.DataFrame(DEMO_BOM)

    rows = result.get("bom_preview")
    if rows:
        return pd.DataFrame(rows)

    session_id = result.get("session_id")
    if not session_id:
        return None

    try:
        response = requests.get(f"{API_BASE_URL}/bom/{session_id}", timeout=15)
        if response.status_code == 200:
            return pd.DataFrame(response.json().get("rows", []))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Engineering CAD AI Agent",
    page_icon="🛡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("CAD Agent")
    st.caption("Armoured Vehicle Manufacturing — Prototype v1.0")

    st.divider()

    # API status
    api_key_set = bool(_settings.DEEPSEEK_API_KEY)
    if api_key_set:
        st.success("DeepSeek API: Connected", icon="✅")
        st.caption(f"Model: `{_settings.DEEPSEEK_MODEL}`")
        st.caption(f"Refinement threshold: `{_settings.LLM_CONFIDENCE_THRESHOLD}`")
    else:
        st.error("DeepSeek API: Not configured", icon="❌")
        st.caption("Set DEEPSEEK_API_KEY in your .env file to enable LLM enrichment.")

    web_search_ready = bool(_settings.TAVILY_API_KEY) and _settings.ENABLE_WEB_SEARCH
    if web_search_ready:
        st.success("Web search (Tavily): Enabled", icon="✅")
    elif _settings.ENABLE_WEB_SEARCH:
        st.warning("Web search: Disabled (no TAVILY_API_KEY)", icon="⚠️")
    else:
        st.info("Web search: Off (ENABLE_WEB_SEARCH=false)", icon="ℹ️")

    st.divider()

    st.subheader("Supported Formats")
    st.markdown(
        """
- **STEP** (.stp, .step) — full 3D assembly
- **IGES** (.igs, .iges) — 3D interchange
- **DXF** (.dxf) — 2D profiles / flat patterns

**Max file size:** 50 MB
**Max parts:** 200 per assembly
"""
    )

    st.divider()

    demo_mode = st.checkbox("Demo Mode", value=False, help="Run with synthetic data — no real CAD file needed.")

    st.divider()

    st.caption("Note: All outputs require human review before use in manufacturing.")

# ---------------------------------------------------------------------------
# Main header
# ---------------------------------------------------------------------------

st.title("Engineering CAD AI Agent")
st.subheader("Armoured Vehicle Manufacturing — Prototype v1.0")
st.markdown(
    "Upload a CAD file to automatically extract a BOM, generate DXF flat drawings, "
    "bending drawings, and an assembly drawing."
)

st.divider()

# ---------------------------------------------------------------------------
# Upload section
# ---------------------------------------------------------------------------

col_upload, col_info = st.columns([2, 1])

with col_upload:
    st.subheader("Upload CAD File")

    uploaded_files = None
    upload_mode = "single"
    if demo_mode:
        st.info("Demo Mode active — click 'Run Demo' to see a sample pipeline result.")
        process_btn = st.button("Run Demo", type="primary", use_container_width=True)
        uploaded_file = None
    else:
        upload_mode = st.radio(
            "Upload mode",
            ["Single file", "Folder / multiple files"],
            horizontal=True,
        )
        if upload_mode == "Single file":
            uploaded_file = st.file_uploader(
                "Choose a CAD file",
                type=["stp", "step", "igs", "iges", "dxf"],
                help=f"Supported formats: STEP, IGES, DXF. Maximum size: {MAX_FILE_MB} MB.",
            )
            process_btn = st.button(
                "Process File",
                type="primary",
                disabled=(uploaded_file is None),
                use_container_width=True,
            )
        else:
            uploaded_file = None
            uploaded_files = st.file_uploader(
                "Select all files in the folder (or upload a .zip of it)",
                type=["stp", "step", "igs", "iges", "dxf", "zip"],
                accept_multiple_files=True,
                help="Open the folder, select all files (Ctrl+A) and drop them here, "
                     "or zip the folder and upload the single .zip.",
            )
            st.caption(
                "All files are consolidated into ONE package. Duplicate part "
                "numbers are merged and quantities summed."
            )
            process_btn = st.button(
                "Process Folder",
                type="primary",
                disabled=(not uploaded_files),
                use_container_width=True,
            )

with col_info:
    st.subheader("Pipeline Steps")
    st.markdown(
        """
1. File validation
2. Session setup
3. CAD geometry extraction
4. LLM part enrichment (DeepSeek — two-pass with optional web search)
5. BOM generation
6. DXF flat drawings
7. Bending drawings
8. Assembly drawing
9. Package outputs
"""
    )

st.divider()

# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

if process_btn:
    if demo_mode:
        # Use synthetic demo data
        with st.spinner("Running demo pipeline..."):
            time.sleep(1.5)
        result = DEMO_RESULT
        st.session_state["result"] = result
        st.session_state["demo"] = True
    elif upload_mode == "Folder / multiple files":
        if not uploaded_files:
            st.error("Please select the folder's files (or a .zip) first.")
        else:
            with st.spinner(f"Processing {len(uploaded_files)} file(s) into one package..."):
                try:
                    multipart = [
                        ("files", (f.name, f.getvalue(), "application/octet-stream"))
                        for f in uploaded_files
                    ]
                    response = requests.post(f"{API_BASE_URL}/upload_batch", files=multipart, timeout=900)

                    if response.status_code == 200:
                        st.session_state["result"] = response.json()
                        st.session_state["demo"] = False
                    else:
                        try:
                            detail = response.json().get("detail", response.text)
                        except Exception:
                            detail = response.text
                        st.error(f"Batch error ({response.status_code}): {detail}")
                        st.session_state.pop("result", None)

                except requests.exceptions.ConnectionError:
                    st.error(
                        "Cannot connect to the CAD Agent API. "
                        "Make sure the FastAPI server is running: `uvicorn app.api:app --reload`"
                    )
                except requests.exceptions.Timeout:
                    st.error("Request timed out. The folder may be too large.")
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")
    else:
        if uploaded_file is None:
            st.error("Please upload a CAD file first.")
        else:
            with st.spinner("Running pipeline... this may take 30–120 seconds for large assemblies."):
                try:
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), "application/octet-stream")}
                    response = requests.post(f"{API_BASE_URL}/upload", files=files, timeout=300)

                    if response.status_code == 200:
                        result = response.json()
                        st.session_state["result"] = result
                        st.session_state["demo"] = False
                    else:
                        try:
                            detail = response.json().get("detail", response.text)
                        except Exception:
                            detail = response.text
                        st.error(f"Pipeline error ({response.status_code}): {detail}")
                        st.session_state.pop("result", None)

                except requests.exceptions.ConnectionError:
                    st.error(
                        "Cannot connect to the CAD Agent API. "
                        "Make sure the FastAPI server is running: `uvicorn app.api:app --reload`"
                    )
                except requests.exceptions.Timeout:
                    st.error("Request timed out. The file may be too large or complex.")
                except Exception as exc:
                    st.error(f"Unexpected error: {exc}")

# ---------------------------------------------------------------------------
# Results section
# ---------------------------------------------------------------------------

if "result" in st.session_state:
    result = st.session_state["result"]
    is_demo = st.session_state.get("demo", False)

    st.subheader("Pipeline Results")

    status = result.get("status", "UNKNOWN")
    if status == "SUCCESS":
        st.success(f"Pipeline completed successfully in {result.get('processing_time_seconds', 0):.1f}s")
    elif status == "PARTIAL":
        st.warning(f"Pipeline completed with warnings in {result.get('processing_time_seconds', 0):.1f}s")
    else:
        st.error(f"Pipeline failed: {status}")

    # Metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Parts Found", result.get("parts_extracted", 0))
    m2.metric("BOM Lines", result.get("bom_lines", 0))
    m3.metric("DXF Drawings", result.get("dxf_files_generated", 0))
    m4.metric("Bending Drawings", result.get("bending_drawings_generated", 0))

    m5, m6 = st.columns([1, 3])
    m5.metric("Assembly Drawing", "Yes" if result.get("assembly_drawings_generated", 0) > 0 else "No")

    # Warnings
    warnings = result.get("warnings", [])
    if warnings:
        with st.expander(f"Warnings ({len(warnings)})", expanded=True):
            for w in warnings:
                st.warning(w)

    # Errors
    errors = result.get("errors", [])
    if errors:
        with st.expander(f"Errors ({len(errors)})", expanded=True):
            for e in errors:
                st.error(e)

    st.divider()

    # BOM Preview
    bom_df = _load_bom_preview(result, is_demo)
    if bom_df is not None and not bom_df.empty:
        st.subheader("BOM Preview")
        st.caption(f"{len(bom_df)} line(s) — same data as BOM.xlsx / BOM.csv in the download package.")
        st.dataframe(
            bom_df,
            use_container_width=True,
            hide_index=True,
            height=min(700, 38 + 35 * len(bom_df)),
        )
    elif not is_demo and result.get("bom_lines", 0) == 0:
        st.info("No BOM lines were generated for this run.")

    st.divider()

    # Download button
    col_dl, col_sr = st.columns([1, 2])
    with col_dl:
        session_id = result.get("session_id", "")
        if is_demo:
            st.info("Demo mode — download not available for synthetic data.")
        else:
            download_url = f"{API_BASE_URL}/download/{session_id}"
            try:
                dl_response = requests.get(download_url, timeout=30, stream=True)
                if dl_response.status_code == 200:
                    zip_bytes = dl_response.content
                    st.download_button(
                        label="Download Output ZIP",
                        data=zip_bytes,
                        file_name=f"cad_agent_output_{session_id[:8]}.zip",
                        mime="application/zip",
                        use_container_width=True,
                    )
                else:
                    st.warning("Output ZIP not yet available. Try refreshing.")
            except Exception as exc:
                st.warning(f"Could not retrieve download: {exc}")

    with col_sr:
        summary = result.get("summary_report", "")
        if summary:
            with st.expander("Summary Report", expanded=False):
                st.markdown(summary)

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

st.divider()
st.caption(
    "Engineering CAD AI Agent — Prototype v1.0 | "
    f"Built: {date.today().strftime('%Y-%m-%d')} | "
    "For development and evaluation purposes only. "
    "All outputs require qualified engineer review before manufacturing use."
)
