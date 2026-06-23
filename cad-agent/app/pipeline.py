"""
Master pipeline orchestrator — runs all 8 steps and returns PipelineResult.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import get_settings
from app.models import PipelineResult, SessionLog, PartRecord
from app.cad_parser import parse_cad_file, detect_format
from app.llm_interpreter import enrich_parts
from app.bom_generator import generate_bom
from app.dxf_generator import generate_dxf_flat
from app.bending_calculator import generate_bending_drawing
from app.assembly_drawing import generate_assembly_drawing
from app.output_packager import package_outputs

logger = logging.getLogger(__name__)


def _load_materials() -> list[dict]:
    """Load materials table from data/materials.json."""
    # Resolve relative to this file's location
    here = Path(__file__).parent
    mat_path = here.parent / "data" / "materials.json"
    if mat_path.exists():
        with open(mat_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("materials", [])
    logger.warning("materials.json not found at %s — using empty materials table.", mat_path)
    return []


def _log_to_supabase(session_log: SessionLog, settings) -> bool:
    """Attempt to insert session log to Supabase. Returns True on success."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        return False
    try:
        from supabase import create_client  # type: ignore
        client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        record = session_log.model_dump()
        client.table("session_logs").insert(record).execute()
        logger.info("Session log inserted to Supabase: %s", session_log.session_id)
        return True
    except ImportError:
        logger.warning("supabase package not available — falling back to local log.")
    except Exception as exc:
        logger.warning("Supabase insert failed (%s) — falling back to local log.", exc)
    return False


def _log_locally(session_log: SessionLog, settings) -> None:
    """Write session log to logs/run_{session_id}.json."""
    logs_dir = Path(settings.OUTPUT_DIR).parent / "logs"
    os.makedirs(logs_dir, exist_ok=True)
    log_path = logs_dir / f"run_{session_log.session_id}.json"
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(session_log.model_dump(), f, indent=2)
    logger.info("Session log written locally: %s", log_path)


def run_pipeline(file_path: str, original_filename: str) -> PipelineResult:
    """
    Execute the full 8-step CAD agent pipeline.

    Steps:
      1. File validation
      2. Session setup
      3. CAD parsing
      4. LLM enrichment
      5. BOM generation
      6. DXF flat drawings
      7. Bending drawings
      8. Assembly drawing
      9. Package outputs
     10. Log results
    """
    settings = get_settings()
    start_time = time.time()
    errors: list[str] = []
    warnings: list[str] = []

    # ----------------------------------------------------------------
    # Step 1: File validation
    # ----------------------------------------------------------------
    if not os.path.exists(file_path):
        raise ValueError(f"File not found: '{file_path}'")

    try:
        file_format = detect_format(file_path)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if file_size_mb > settings.MAX_FILE_SIZE_MB:
        raise ValueError(
            f"File size {file_size_mb:.1f} MB exceeds maximum allowed {settings.MAX_FILE_SIZE_MB} MB."
        )

    # ----------------------------------------------------------------
    # Step 2: Session setup
    # ----------------------------------------------------------------
    session_id = str(uuid.uuid4())
    timestamp = datetime.utcnow().isoformat() + "Z"
    output_dir = os.path.abspath(settings.OUTPUT_DIR)
    session_dir = os.path.join(output_dir, session_id)
    os.makedirs(session_dir, exist_ok=True)

    # Subdirectories
    dxf_dir = os.path.join(session_dir, "DXF")
    bending_dir = os.path.join(session_dir, "Bending")
    os.makedirs(dxf_dir, exist_ok=True)
    os.makedirs(bending_dir, exist_ok=True)

    session_log = SessionLog(
        session_id=session_id,
        input_filename=original_filename,
        input_format=file_format,
        status="PENDING",
        timestamp=timestamp,
    )

    logger.info("Pipeline started: session=%s, file=%s, format=%s", session_id, original_filename, file_format)

    # ----------------------------------------------------------------
    # Step 3: CAD parsing
    # ----------------------------------------------------------------
    raw_parts: list[dict] = []
    try:
        raw_parts = parse_cad_file(file_path, original_filename=original_filename)
        for raw in raw_parts:
            raw["source_filename"] = original_filename
        session_log.parts_extracted = len(raw_parts)
        if len(raw_parts) == 200:
            w = "Assembly truncated to 200 parts. Some components were omitted."
            warnings.append(w)
            logger.warning(w)
    except Exception as exc:
        msg = f"CAD parsing failed: {exc}"
        logger.error(msg)
        session_log.status = "FAILED"
        session_log.warnings = warnings
        session_log.processing_time_seconds = time.time() - start_time
        return PipelineResult(
            session_log=session_log,
            errors=[msg],
        )

    if not raw_parts:
        msg = "No parts could be extracted from the CAD file."
        warnings.append(msg)

    # ----------------------------------------------------------------
    # Step 4: LLM enrichment
    # ----------------------------------------------------------------
    materials_table = _load_materials()
    parts: list[PartRecord] = []

    try:
        parts = enrich_parts(raw_parts, materials_table)
        if not settings.DEEPSEEK_API_KEY:
            warnings.append("DEEPSEEK_API_KEY not configured — LLM enrichment skipped (metadata-only mode).")
    except Exception as exc:
        msg = f"LLM enrichment failed: {exc}. Falling back to metadata-only."
        logger.error(msg)
        warnings.append(msg)
        # Fallback: create minimal PartRecords from raw data
        for raw in raw_parts:
            parts.append(PartRecord(
                part_id=raw.get("part_id", "UNKNOWN"),
                part_name=raw.get("part_name", "UNKNOWN"),
                part_type=raw.get("shape_type", "UNKNOWN"),
                volume_mm3=raw.get("volume_mm3"),
                bounding_box=raw.get("bounding_box"),
                bom_level=raw.get("bom_level", 0),
                parent_assembly=raw.get("parent_assembly"),
                low_confidence=True,
                notes="LLM enrichment failed",
            ))

    # Flag low-confidence and inferred materials in warnings
    low_conf_count = sum(1 for p in parts if p.low_confidence)
    inferred_count = sum(1 for p in parts if p.material_inferred)
    if low_conf_count > 0:
        warnings.append(f"{low_conf_count} parts have low-confidence classifications. Human review recommended.")
    if inferred_count > 0:
        warnings.append(f"{inferred_count} parts have inferred materials. Verify before manufacturing.")

    # ----------------------------------------------------------------
    # Step 5: BOM generation
    # ----------------------------------------------------------------
    try:
        xlsx_path, csv_path = generate_bom(parts, session_id, session_dir)
        session_log.bom_lines = len(parts)
    except Exception as exc:
        msg = f"BOM generation failed: {exc}"
        logger.error(msg)
        errors.append(msg)
        warnings.append(msg)

    # ----------------------------------------------------------------
    # Step 6: DXF flat drawings
    # ----------------------------------------------------------------
    sheet_metal_parts = [p for p in parts if p.part_type == "SHEET_METAL"]
    dxf_count = 0
    for part in sheet_metal_parts:
        try:
            generate_dxf_flat(part, dxf_dir)
            dxf_count += 1
        except Exception as exc:
            msg = f"DXF flat drawing failed for {part.part_id}: {exc}"
            logger.warning(msg)
            warnings.append(msg)
    session_log.dxf_files_generated = dxf_count

    # ----------------------------------------------------------------
    # Step 7: Bending drawings
    # ----------------------------------------------------------------
    bend_parts = [p for p in sheet_metal_parts if p.has_bends and p.bend_count > 0]
    bending_count = 0
    for part in bend_parts:
        try:
            dxf_p, pdf_p = generate_bending_drawing(part, bending_dir)
            bending_count += 1
        except Exception as exc:
            msg = f"Bending drawing failed for {part.part_id}: {exc}"
            logger.warning(msg)
            warnings.append(msg)
    session_log.bending_drawings_generated = bending_count

    # ----------------------------------------------------------------
    # Step 8: Assembly drawing
    # ----------------------------------------------------------------
    asm_dxf = None
    asm_pdf = None
    if parts:
        try:
            asm_dxf, asm_pdf = generate_assembly_drawing(parts, session_id, session_dir)
            session_log.assembly_drawings_generated = 1
        except Exception as exc:
            msg = f"Assembly drawing failed: {exc}"
            logger.error(msg)
            errors.append(msg)
            warnings.append(msg)
    else:
        warnings.append("No parts available for assembly drawing.")

    # ----------------------------------------------------------------
    # Step 9: Package outputs
    # ----------------------------------------------------------------
    zip_path = None
    summary_report = ""
    session_log.warnings = warnings
    session_log.processing_time_seconds = round(time.time() - start_time, 2)

    try:
        zip_path, summary_report = package_outputs(session_id, parts, output_dir, session_log)
        session_log.output_zip_path = zip_path
    except Exception as exc:
        msg = f"Output packaging failed: {exc}"
        logger.error(msg)
        errors.append(msg)

    # ----------------------------------------------------------------
    # Step 10: Logging
    # ----------------------------------------------------------------
    # Honest status: no real parts == failure (never report SUCCESS on nothing).
    if not parts:
        session_log.status = "FAILED"
        if not errors:
            errors.append("No parts could be extracted from the CAD file.")
    elif errors:
        session_log.status = "PARTIAL"
    else:
        session_log.status = "SUCCESS"

    logged = _log_to_supabase(session_log, settings)
    if not logged:
        _log_locally(session_log, settings)

    logger.info(
        "Pipeline complete: session=%s, status=%s, time=%.2fs, parts=%d",
        session_id,
        session_log.status,
        session_log.processing_time_seconds,
        len(parts),
    )

    return PipelineResult(
        session_log=session_log,
        parts=parts,
        output_zip_path=zip_path,
        summary_report=summary_report,
        errors=errors,
    )
