"""
BOM generator — creates Excel (.xlsx) and CSV outputs from enriched PartRecord list.
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.models import PartRecord

logger = logging.getLogger(__name__)

# Colour constants
HEADER_BG = "1F4E79"    # Dark blue
HEADER_FG = "FFFFFF"    # White
LOW_CONF_BG = "FFFF00"  # Yellow
INFERRED_BG = "FFF2CC"  # Light yellow/amber

COLUMNS = [
    "Item No.",
    "Part Number",
    "Part Name/Description",
    "Quantity",
    "UoM",
    "Material",
    "Mass (kg)",
    "Parent Assembly",
    "Level",
    "Notes",
    "Flags",
]


def _flags(part: PartRecord) -> str:
    flags = []
    if part.low_confidence:
        flags.append("LOW_CONFIDENCE")
    if part.material_inferred:
        flags.append("INFERRED")
    if part.has_bends:
        flags.append(f"BENDS:{part.bend_count}")
    return "; ".join(flags)


def _material_cell(part: PartRecord) -> str:
    mat = part.material or (part.material_code or "")
    if part.material_inferred and mat:
        mat = f"{mat} (INFERRED)"
    return mat


def _sorted_parts(parts: list[PartRecord]) -> list[PartRecord]:
    return sorted(parts, key=lambda p: (p.bom_level, p.part_id))


def _build_rows(parts: list[PartRecord]) -> list[list]:
    sorted_p = _sorted_parts(parts)
    rows = []
    for idx, part in enumerate(sorted_p, start=1):
        rows.append([
            idx,
            part.part_id,
            part.part_name,
            part.quantity,
            "EA",
            _material_cell(part),
            round(part.mass_kg, 4) if part.mass_kg is not None else "",
            part.parent_assembly or "",
            part.bom_level,
            part.notes or "",
            _flags(part),
        ])
    return rows


def generate_bom(parts: list[PartRecord], session_id: str, output_dir: str) -> tuple[str, str]:
    """
    Build BOM from enriched PartRecord list.
    Returns (xlsx_path, csv_path).
    """
    os.makedirs(output_dir, exist_ok=True)
    xlsx_path = os.path.join(output_dir, "BOM.xlsx")
    csv_path = os.path.join(output_dir, "BOM.csv")

    rows = _build_rows(parts)

    # ---- Excel ----
    wb = Workbook()
    ws = wb.active
    ws.title = "BOM"

    # Header row
    header_fill = PatternFill(start_color=HEADER_BG, end_color=HEADER_BG, fill_type="solid")
    header_font = Font(bold=True, color=HEADER_FG, size=10)
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for col_idx, col_name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align

    ws.row_dimensions[1].height = 30

    # Data rows
    low_conf_fill = PatternFill(start_color=LOW_CONF_BG, end_color=LOW_CONF_BG, fill_type="solid")
    normal_font = Font(size=10)
    sorted_parts = _sorted_parts(parts)

    for row_idx, (row_data, part) in enumerate(zip(rows, sorted_parts), start=2):
        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = normal_font
            cell.alignment = Alignment(vertical="center")
            if part.low_confidence:
                cell.fill = low_conf_fill

    # Totals row
    total_row = len(rows) + 2
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True, size=10)
    ws.cell(row=total_row, column=4, value=sum(p.quantity for p in parts)).font = Font(bold=True, size=10)
    total_mass = sum(p.mass_kg for p in parts if p.mass_kg is not None)
    ws.cell(row=total_row, column=7, value=round(total_mass, 3)).font = Font(bold=True, size=10)
    ws.cell(row=total_row, column=3, value=f"{len(parts)} parts total").font = Font(italic=True, size=10)

    # Auto-fit columns
    col_widths = [len(c) + 2 for c in COLUMNS]
    for row_data in rows:
        for i, val in enumerate(row_data):
            col_widths[i] = min(50, max(col_widths[i], len(str(val)) + 2))
    for i, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    # Freeze top row
    ws.freeze_panes = "A2"

    wb.save(xlsx_path)
    logger.info("BOM Excel saved to %s", xlsx_path)

    # ---- CSV ----
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    logger.info("BOM CSV saved to %s", csv_path)

    return xlsx_path, csv_path
