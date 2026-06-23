"""
FastAPI application — 4 endpoints for the CAD Agent.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.pipeline import run_pipeline
from app.bom_generator import bom_preview_rows, COLUMNS

logger = logging.getLogger(__name__)

app = FastAPI(
    title="CAD Agent API",
    version="1.0.0",
    description="Engineering CAD AI Agent — Armoured Vehicle Manufacturing",
)

ALLOWED_EXTENSIONS = {".stp", ".step", ".igs", ".iges", ".dxf"}


def _pipeline_response(result, extra: dict | None = None) -> dict:
    """Build JSON payload for upload endpoints, including BOM preview rows."""
    sl = result.session_log
    payload = {
        "session_id": sl.session_id,
        "status": sl.status,
        "parts_extracted": sl.parts_extracted,
        "bom_lines": sl.bom_lines,
        "dxf_files_generated": sl.dxf_files_generated,
        "bending_drawings_generated": sl.bending_drawings_generated,
        "assembly_drawings_generated": sl.assembly_drawings_generated,
        "processing_time_seconds": sl.processing_time_seconds,
        "warnings": sl.warnings,
        "errors": result.errors,
        "download_url": f"/download/{sl.session_id}",
        "summary_report": result.summary_report,
        "bom_preview": bom_preview_rows(result.parts),
    }
    if extra:
        payload.update(extra)
    return payload


def _validate_session_id(session_id: str) -> None:
    try:
        uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id format.")


def _session_output_dir(session_id: str) -> Path:
    settings = get_settings()
    return Path(os.path.abspath(settings.OUTPUT_DIR)) / session_id


@app.get("/health")
async def health():
    """Simple health check endpoint."""
    return {"status": "ok"}


@app.post("/upload")
async def upload_and_process(file: UploadFile = File(...)):
    """
    Accept a CAD file, run the full pipeline, and return session_id with download URL.
    Validates file size before processing.
    """
    settings = get_settings()

    # Validate filename / extension
    original_filename = file.filename or "upload"
    ext = Path(original_filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type '{ext}'. Accepted: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Save to temp file to check size and pass to pipeline
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            tmp_path = tmp.name
            content = await file.read()

        # Check size
        file_size_mb = len(content) / (1024 * 1024)
        if file_size_mb > settings.MAX_FILE_SIZE_MB:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"File size {file_size_mb:.1f} MB exceeds the maximum allowed "
                    f"{settings.MAX_FILE_SIZE_MB} MB."
                ),
            )

        with open(tmp_path, "wb") as f_out:
            f_out.write(content)

        # Run pipeline
        result = run_pipeline(tmp_path, original_filename)

    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Pipeline error for file '%s': %s", original_filename, exc)
        raise HTTPException(status_code=500, detail=f"Pipeline error: {exc}") from exc
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    session_id = result.session_log.session_id

    return JSONResponse(content=_pipeline_response(result))


@app.post("/upload_batch")
async def upload_batch(files: list[UploadFile] = File(...)):
    """Accept multiple CAD files (or a ZIP of a folder) and build ONE package.

    Saves the uploads into a temp directory (extracting any ZIPs, preserving
    sub-folders as sub-assemblies) and runs the consolidated batch pipeline.
    """
    import zipfile
    from app.batch import run_batch, SUPPORTED_EXTENSIONS

    if not files:
        raise HTTPException(status_code=422, detail="No files were uploaded.")

    work_dir = tempfile.mkdtemp(prefix="cadbatch_")
    saved = 0
    label = "batch"
    try:
        for up in files:
            name = os.path.basename(up.filename or "upload")
            ext = Path(name).suffix.lower()
            content = await up.read()

            if ext == ".zip":
                label = Path(name).stem
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as zt:
                    zt.write(content)
                    zip_tmp = zt.name
                try:
                    with zipfile.ZipFile(zip_tmp) as zf:
                        for member in zf.namelist():
                            # Skip unsafe paths and unsupported types.
                            if member.endswith("/") or os.path.isabs(member) or ".." in member:
                                continue
                            if Path(member).suffix.lower() in SUPPORTED_EXTENSIONS:
                                zf.extract(member, work_dir)
                                saved += 1
                finally:
                    os.unlink(zip_tmp)
            elif ext in SUPPORTED_EXTENSIONS:
                dest = os.path.join(work_dir, name)
                with open(dest, "wb") as f_out:
                    f_out.write(content)
                saved += 1

        if saved == 0:
            raise HTTPException(
                status_code=422,
                detail=f"No supported CAD files found. Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))} (or a .zip of them).",
            )

        if len(files) == 1 and label == "batch":
            label = Path(os.path.basename(files[0].filename or "batch")).stem

        result = run_batch(work_dir, label=label)

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Batch error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Batch error: {exc}") from exc
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    sl = result.session_log
    return JSONResponse(content=_pipeline_response(result, extra={"files_received": saved}))


@app.get("/download/{session_id}")
async def download_outputs(session_id: str):
    """Return the ZIP file for a completed session."""
    settings = get_settings()
    _validate_session_id(session_id)

    zip_path = os.path.join(os.path.abspath(settings.OUTPUT_DIR), f"{session_id}.zip")

    if not os.path.exists(zip_path):
        raise HTTPException(
            status_code=404,
            detail=f"Output ZIP not found for session '{session_id}'. "
                   "The session may not exist or processing may not be complete.",
        )

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"cad_agent_output_{session_id[:8]}.zip",
    )


@app.get("/bom/{session_id}")
async def get_bom_preview(session_id: str):
    """Return BOM rows as JSON for UI preview without downloading the ZIP."""
    import csv

    _validate_session_id(session_id)
    session_dir = _session_output_dir(session_id)
    csv_path = session_dir / "BOM.csv"

    if not csv_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"BOM not found for session '{session_id}'.",
        )

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    return JSONResponse(content={
        "session_id": session_id,
        "columns": COLUMNS,
        "rows": rows,
        "total_rows": len(rows),
    })


@app.get("/status/{session_id}")
async def get_status(session_id: str):
    """Return session log JSON for a given session_id."""
    settings = get_settings()

    _validate_session_id(session_id)

    # Try local log first
    logs_dir = Path(settings.OUTPUT_DIR).parent / "logs"
    log_path = logs_dir / f"run_{session_id}.json"

    if log_path.exists():
        import json
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return JSONResponse(content=data)

    # Check if ZIP exists as a proxy for completion
    zip_path = os.path.join(os.path.abspath(settings.OUTPUT_DIR), f"{session_id}.zip")
    if os.path.exists(zip_path):
        return JSONResponse(content={
            "session_id": session_id,
            "status": "SUCCESS",
            "note": "Log file not found but output ZIP exists.",
        })

    raise HTTPException(status_code=404, detail=f"No session found for id '{session_id}'.")
