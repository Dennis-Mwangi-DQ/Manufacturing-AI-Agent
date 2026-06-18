"""
Output packager — bundles all generated files into a ZIP and writes summary report.
"""
from __future__ import annotations

import json
import logging
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.models import PartRecord, SessionLog

logger = logging.getLogger(__name__)


def _list_output_files(session_dir: str) -> list[str]:
    """Return all file paths inside the session output directory."""
    result = []
    for root, _dirs, files in os.walk(session_dir):
        for f in files:
            result.append(os.path.join(root, f))
    return sorted(result)


def _build_summary_report(
    session_log: SessionLog,
    output_files: list[str],
    session_dir: str,
) -> str:
    """Build summary_report.md content."""
    warnings_text = (
        "\n".join(f"- {w}" for w in session_log.warnings)
        if session_log.warnings
        else "None"
    )

    # Relative file paths for readability
    rel_files = []
    for f in output_files:
        try:
            rel = os.path.relpath(f, session_dir)
        except ValueError:
            rel = f
        rel_files.append(f"- {rel}")

    files_text = "\n".join(rel_files) if rel_files else "None"

    assembly_text = "Yes" if session_log.assembly_drawings_generated > 0 else "No"

    report = f"""# CAD Agent Run Summary
**Session ID:** {session_log.session_id}
**Input File:** {session_log.input_filename}
**Timestamp:** {session_log.timestamp}

## Pipeline Results
- Parts extracted: {session_log.parts_extracted}
- BOM lines: {session_log.bom_lines}
- DXF flat drawings: {session_log.dxf_files_generated}
- Bending drawings: {session_log.bending_drawings_generated}
- Assembly drawings generated: {assembly_text}
- Processing time: {session_log.processing_time_seconds:.2f}s

## Warnings
{warnings_text}

## Output Files
{files_text}
"""
    return report


def package_outputs(
    session_id: str,
    parts: list[PartRecord],
    output_dir: str,
    session_log: Optional[SessionLog] = None,
) -> tuple[str, str]:
    """
    Bundle all files in output_dir/session_id/ into a ZIP.
    Generate summary_report.md.
    Returns (zip_path, summary_report_text).
    """
    session_dir = os.path.join(output_dir, session_id)
    os.makedirs(session_dir, exist_ok=True)

    if session_log is None:
        # Minimal fallback log
        session_log = SessionLog(
            session_id=session_id,
            input_filename="unknown",
            input_format="unknown",
            timestamp=datetime.utcnow().isoformat(),
        )

    # Collect all files
    output_files = _list_output_files(session_dir)

    # Write summary report
    report_text = _build_summary_report(session_log, output_files, session_dir)
    report_path = os.path.join(session_dir, "summary_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Re-collect after writing report
    output_files = _list_output_files(session_dir)

    # Create ZIP
    zip_path = os.path.join(output_dir, f"{session_id}.zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in output_files:
            arcname = os.path.relpath(file_path, session_dir)
            zf.write(file_path, arcname)

    logger.info("Output ZIP created: %s (%d files)", zip_path, len(output_files))
    return zip_path, report_text
