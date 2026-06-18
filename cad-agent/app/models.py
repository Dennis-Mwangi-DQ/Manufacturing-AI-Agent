"""
Pydantic v2 data models for the CAD Agent pipeline.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class BendRecord(BaseModel):
    bend_id: int
    part_id: str
    angle_deg: float
    radius_mm: float
    direction: str  # "UP" or "DOWN"
    k_factor: float
    bend_allowance_mm: float
    segment_before_mm: float
    segment_after_mm: float


class PartRecord(BaseModel):
    part_id: str
    part_name: str
    part_type: str  # "SHEET_METAL", "SOLID", "ASSEMBLY", "UNKNOWN"
    quantity: int = 1
    material: Optional[str] = None
    material_code: Optional[str] = None
    thickness_mm: Optional[float] = None
    mass_kg: Optional[float] = None
    volume_mm3: Optional[float] = None
    bounding_box: Optional[dict] = None  # {"L": float, "W": float, "H": float}
    parent_assembly: Optional[str] = None
    bom_level: int = 0
    has_bends: bool = False
    bend_count: int = 0
    bends: list[BendRecord] = Field(default_factory=list)
    llm_confidence: Optional[float] = None
    notes: Optional[str] = None
    material_inferred: bool = False
    low_confidence: bool = False


class SessionLog(BaseModel):
    session_id: str
    input_filename: str
    input_format: str
    parts_extracted: int = 0
    bom_lines: int = 0
    dxf_files_generated: int = 0
    bending_drawings_generated: int = 0
    assembly_drawings_generated: int = 0
    processing_time_seconds: float = 0
    warnings: list[str] = Field(default_factory=list)
    status: str = "PENDING"  # PENDING, SUCCESS, PARTIAL, FAILED
    timestamp: str = ""
    output_zip_path: Optional[str] = None


class PipelineResult(BaseModel):
    session_log: SessionLog
    parts: list[PartRecord] = Field(default_factory=list)
    output_zip_path: Optional[str] = None
    summary_report: str = ""
    errors: list[str] = Field(default_factory=list)
