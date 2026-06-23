"""
Batch / folder runner — traverse a directory recursively, parse every supported
CAD file (one part per file), and build ONE consolidated manufacturing package
(BOM + DXF flats + bending drawings + assembly drawing + ZIP).

Usage:
    python -m app.batch "/path/to/folder"
    python -m app.batch "/path/to/folder" --output ./outputs --name FENDER_VLH
    python -m app.batch "/path/to/folder" --no-llm    # metadata-only, no network

Design: same no-fabrication guarantees as the single-file pipeline. A file that
cannot be parsed is recorded as a warning and skipped; it never injects fake data
into the consolidated BOM.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.models import PartRecord, PipelineResult, SessionLog
from app.cad_parser import parse_cad_file, parse_project_assy_from_path
from app.llm_interpreter import enrich_parts, _enrich_single_part_metadata_only
from app.bom_generator import generate_bom
from app.dxf_generator import generate_dxf_flat
from app.bending_calculator import generate_bending_drawing
from app.assembly_drawing import generate_assembly_drawing
from app.output_packager import package_outputs
from app.pipeline import _load_materials, _log_locally, _log_to_supabase

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".dxf", ".step", ".stp", ".iges", ".igs"}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def find_cad_files(folder: str) -> list[Path]:
    """Return all supported CAD files under `folder`, recursively, sorted."""
    root = Path(folder)
    if not root.exists():
        raise FileNotFoundError(f"Folder not found: '{folder}'")
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: '{folder}'")

    files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files)


# ---------------------------------------------------------------------------
# Parsing + de-duplication
# ---------------------------------------------------------------------------

def _aggregate_parts(raw_parts: list[dict]) -> list[dict]:
    """Merge duplicate part numbers, summing their quantities.

    The same physical part can appear in several sub-folders. We combine them
    into one BOM line and add up the per-file quantities to give a true
    consolidated cut count.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for part in raw_parts:
        pid = part.get("part_id", "UNKNOWN")
        if pid not in by_id:
            by_id[pid] = dict(part)
            by_id[pid]["_source_count"] = 1
            order.append(pid)
        else:
            existing = by_id[pid]
            existing["quantity"] = int(existing.get("quantity", 1) or 1) + int(part.get("quantity", 1) or 1)
            existing["_source_count"] += 1
    return [by_id[pid] for pid in order]


def collect_parts(files: list[Path], root: str) -> tuple[list[dict], list[str]]:
    """Parse every file, tag with its sub-assembly, and aggregate duplicates.

    Returns (aggregated_raw_parts, warnings).
    """
    warnings: list[str] = []
    raw_parts: list[dict] = []
    root_path = Path(root)
    root_assy = parse_project_assy_from_path(str(root_path))

    for f in files:
        try:
            parsed = parse_cad_file(str(f), original_filename=f.name)
        except Exception as exc:
            msg = f"Skipped '{f.relative_to(root_path)}': {exc}"
            logger.warning(msg)
            warnings.append(msg)
            continue

        # Tag each part with the immediate parent folder as its sub-assembly.
        parent = f.parent.name
        for part in parsed:
            part["source_filename"] = f.name
            fmeta = part.setdefault("filename_meta", {})
            if root_assy and not fmeta.get("assy_code"):
                fmeta["assy_code"] = root_assy
            if parent and parent != root_path.name:
                part.setdefault("parent_assembly", parent)
                part["parent_assembly"] = parent
                part["bom_level"] = 1
        raw_parts.extend(parsed)

    aggregated = _aggregate_parts(raw_parts)
    return aggregated, warnings


# ---------------------------------------------------------------------------
# Consolidated package build
# ---------------------------------------------------------------------------

def run_batch(
    folder: str,
    output_dir: Optional[str] = None,
    label: Optional[str] = None,
    use_llm: bool = True,
) -> PipelineResult:
    """Traverse `folder`, parse all CAD files, and build one output package."""
    settings = get_settings()
    start_time = time.time()
    errors: list[str] = []

    output_dir = os.path.abspath(output_dir or settings.OUTPUT_DIR)
    label = label or Path(folder).name or "batch"

    files = find_cad_files(folder)
    logger.info("Batch: found %d CAD file(s) under '%s'", len(files), folder)

    session_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    session_dir = os.path.join(output_dir, session_id)
    dxf_dir = os.path.join(session_dir, "DXF")
    bending_dir = os.path.join(session_dir, "Bending")
    os.makedirs(dxf_dir, exist_ok=True)
    os.makedirs(bending_dir, exist_ok=True)

    session_log = SessionLog(
        session_id=session_id,
        input_filename=f"{label} (folder, {len(files)} files)",
        input_format="BATCH",
        status="PENDING",
        timestamp=timestamp,
    )

    if not files:
        msg = f"No supported CAD files (.dxf/.step/.iges) found under '{folder}'."
        session_log.status = "FAILED"
        session_log.warnings = [msg]
        session_log.processing_time_seconds = round(time.time() - start_time, 2)
        _log_locally(session_log, settings)
        return PipelineResult(session_log=session_log, errors=[msg])

    # ---- Parse + aggregate ----
    raw_parts, warnings = collect_parts(files, folder)
    session_log.parts_extracted = len(raw_parts)

    if not raw_parts:
        msg = "No parts could be extracted from any file in the folder."
        session_log.status = "FAILED"
        session_log.warnings = warnings + [msg]
        session_log.processing_time_seconds = round(time.time() - start_time, 2)
        _log_locally(session_log, settings)
        return PipelineResult(session_log=session_log, errors=[msg])

    # ---- Enrich ----
    materials_table = _load_materials()
    if use_llm and settings.DEEPSEEK_API_KEY:
        parts: list[PartRecord] = enrich_parts(raw_parts, materials_table)
    else:
        if use_llm and not settings.DEEPSEEK_API_KEY:
            warnings.append("DEEPSEEK_API_KEY not set — metadata-only mode.")
        parts = [_enrich_single_part_metadata_only(p, materials_table, settings) for p in raw_parts]

    low_conf = sum(1 for p in parts if p.low_confidence)
    inferred = sum(1 for p in parts if p.material_inferred)
    if low_conf:
        warnings.append(f"{low_conf} parts have low-confidence classifications. Human review recommended.")
    if inferred:
        warnings.append(f"{inferred} parts have inferred materials. Verify before manufacturing.")

    # ---- BOM ----
    try:
        generate_bom(parts, session_id, session_dir)
        session_log.bom_lines = len(parts)
    except Exception as exc:
        msg = f"BOM generation failed: {exc}"
        logger.error(msg)
        errors.append(msg)

    # ---- DXF flats ----
    sheet_metal = [p for p in parts if p.part_type == "SHEET_METAL"]
    dxf_count = 0
    for part in sheet_metal:
        try:
            generate_dxf_flat(part, dxf_dir)
            dxf_count += 1
        except Exception as exc:
            warnings.append(f"DXF flat failed for {part.part_id}: {exc}")
    session_log.dxf_files_generated = dxf_count

    # ---- Bending ----
    bend_count = 0
    for part in [p for p in sheet_metal if p.has_bends and p.bend_count > 0]:
        try:
            generate_bending_drawing(part, bending_dir)
            bend_count += 1
        except Exception as exc:
            warnings.append(f"Bending drawing failed for {part.part_id}: {exc}")
    session_log.bending_drawings_generated = bend_count

    # ---- Assembly ----
    try:
        generate_assembly_drawing(parts, session_id, session_dir)
        session_log.assembly_drawings_generated = 1
    except Exception as exc:
        msg = f"Assembly drawing failed: {exc}"
        logger.error(msg)
        errors.append(msg)

    # ---- Package ----
    session_log.warnings = warnings
    session_log.processing_time_seconds = round(time.time() - start_time, 2)
    zip_path = None
    summary_report = ""
    try:
        zip_path, summary_report = package_outputs(session_id, parts, output_dir, session_log)
        session_log.output_zip_path = zip_path
    except Exception as exc:
        msg = f"Output packaging failed: {exc}"
        logger.error(msg)
        errors.append(msg)

    session_log.status = "PARTIAL" if errors else "SUCCESS"

    if not _log_to_supabase(session_log, settings):
        _log_locally(session_log, settings)

    logger.info(
        "Batch complete: session=%s, status=%s, files=%d, parts=%d, time=%.2fs",
        session_id, session_log.status, len(files), len(parts), session_log.processing_time_seconds,
    )

    return PipelineResult(
        session_log=session_log,
        parts=parts,
        output_zip_path=zip_path,
        summary_report=summary_report,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Traverse a folder of CAD files and build one consolidated "
                    "manufacturing package (BOM + drawings + ZIP).",
    )
    parser.add_argument("folder", help="Path to the folder to traverse recursively.")
    parser.add_argument("--output", "-o", default=None, help="Output directory (default: ./outputs).")
    parser.add_argument("--name", "-n", default=None, help="Label for this batch (default: folder name).")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM enrichment (metadata-only, no network).")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    result = run_batch(
        folder=args.folder,
        output_dir=args.output,
        label=args.name,
        use_llm=not args.no_llm,
    )
    sl = result.session_log

    print("\n=== Batch Result ===")
    print(f"Status:          {sl.status}")
    print(f"Parts (unique):  {sl.parts_extracted}")
    print(f"BOM lines:       {sl.bom_lines}")
    print(f"DXF flats:       {sl.dxf_files_generated}")
    print(f"Bending drawings:{sl.bending_drawings_generated}")
    print(f"Time:            {sl.processing_time_seconds:.2f}s")
    if result.output_zip_path:
        print(f"Package ZIP:     {result.output_zip_path}")
    if sl.warnings:
        print(f"\nWarnings ({len(sl.warnings)}):")
        for w in sl.warnings:
            print(f"  - {w}")
    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for e in result.errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
