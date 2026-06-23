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
    {"Item No.": 1, "Part Number": "AV-001", "Part Name/Description": "Hull Front Panel",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-500T", "Mass (kg)": 47.3,
     "Parent Assembly": "", "Level": 0, "Notes": "", "Flags": "BENDS:2"},
    {"Item No.": 2, "Part Number": "AV-002", "Part Name/Description": "Hull Rear Panel",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-500T", "Mass (kg)": 43.1,
     "Parent Assembly": "", "Level": 0, "Notes": "", "Flags": "BENDS:2"},
    {"Item No.": 3, "Part Number": "AV-003", "Part Name/Description": "Hull Side Panel L",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-500T (INFERRED)", "Mass (kg)": 88.6,
     "Parent Assembly": "AV-001", "Level": 1, "Notes": "Material inferred", "Flags": "INFERRED;BENDS:3"},
    {"Item No.": 4, "Part Number": "AV-004", "Part Name/Description": "Hull Side Panel R",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-500T (INFERRED)", "Mass (kg)": 88.6,
     "Parent Assembly": "AV-001", "Level": 1, "Notes": "Material inferred", "Flags": "INFERRED;BENDS:3"},
    {"Item No.": 5, "Part Number": "AV-005", "Part Name/Description": "Floor Plate",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-500T", "Mass (kg)": 541.2,
     "Parent Assembly": "", "Level": 0, "Notes": "", "Flags": "BENDS:1"},
    {"Item No.": 6, "Part Number": "AV-006", "Part Name/Description": "Roof Panel",
     "Quantity": 1, "UoM": "EA", "Material": "AL-5083", "Mass (kg)": 136.7,
     "Parent Assembly": "", "Level": 0, "Notes": "", "Flags": "BENDS:2"},
    {"Item No.": 7, "Part Number": "AV-007", "Part Name/Description": "Door Panel L",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-370T", "Mass (kg)": 74.4,
     "Parent Assembly": "", "Level": 0, "Notes": "LOW CONFIDENCE", "Flags": "LOW_CONFIDENCE;BENDS:2"},
    {"Item No.": 8, "Part Number": "AV-008", "Part Name/Description": "Door Panel R",
     "Quantity": 1, "UoM": "EA", "Material": "ARMOX-370T", "Mass (kg)": 74.4,
     "Parent Assembly": "", "Level": 0, "Notes": "LOW CONFIDENCE", "Flags": "LOW_CONFIDENCE;BENDS:2"},
    {"Item No.": 9, "Part Number": "AV-009", "Part Name/Description": "Engine Mounting Bracket",
     "Quantity": 4, "UoM": "EA", "Material": "MILD-S275", "Mass (kg)": 3.4,
     "Parent Assembly": "AV-001", "Level": 2, "Notes": "", "Flags": ""},
    {"Item No.": 10, "Part Number": "AV-010", "Part Name/Description": "Suspension Arm",
     "Quantity": 4, "UoM": "EA", "Material": "MILD-S275", "Mass (kg)": 8.7,
     "Parent Assembly": "AV-001", "Level": 2, "Notes": "", "Flags": ""},
    {"Item No.": 11, "Part Number": "AV-011", "Part Name/Description": "Hatch Assembly",
     "Quantity": 2, "UoM": "EA", "Material": "ARMOX-370T", "Mass (kg)": 28.9,
     "Parent Assembly": "", "Level": 0, "Notes": "", "Flags": "BENDS:1"},
    {"Item No.": 12, "Part Number": "AV-012", "Part Name/Description": "Spall Liner",
     "Quantity": 6, "UoM": "EA", "Material": "UHMWPE", "Mass (kg)": 4.2,
     "Parent Assembly": "", "Level": 1, "Notes": "", "Flags": ""},
]


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
    else:
        st.error("DeepSeek API: Not configured", icon="❌")
        st.caption("Set DEEPSEEK_API_KEY in your .env file to enable LLM enrichment.")

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
4. LLM part enrichment (DeepSeek)
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
        st.session_state["bom_df"] = pd.DataFrame(DEMO_BOM)
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
                        st.session_state.pop("bom_df", None)
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
                        st.session_state.pop("bom_df", None)
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

    st.divider()

    # BOM Preview
    with st.expander("BOM Preview (first 20 rows)", expanded=False):
        bom_df = st.session_state.get("bom_df")

        if bom_df is None and not is_demo:
            # Try to fetch BOM from session files (best effort)
            st.info("BOM preview is available in the downloaded ZIP (BOM.csv).")
        elif bom_df is not None:
            st.dataframe(
                bom_df.head(20),
                use_container_width=True,
                hide_index=True,
            )
        elif is_demo:
            st.dataframe(
                pd.DataFrame(DEMO_BOM).head(20),
                use_container_width=True,
                hide_index=True,
            )

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
