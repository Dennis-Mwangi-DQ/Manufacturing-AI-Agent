"""
Bending calculator — bend allowance math and bending drawing generation (DXF + PDF).
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date
from typing import Optional

import ezdxf
from ezdxf import units
from ezdxf.enums import TextEntityAlignment

from app.models import BendRecord, PartRecord

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bend mathematics
# ---------------------------------------------------------------------------

def compute_bend_allowance(
    angle_deg: float,
    radius_mm: float,
    thickness_mm: float,
    k_factor: float,
) -> float:
    """
    Compute bend allowance.
    BA = angle_deg × (π/180) × (radius_mm + k_factor × thickness_mm)
    """
    return angle_deg * (math.pi / 180.0) * (radius_mm + k_factor * thickness_mm)


def compute_flat_blank_length(segments: list[float], bend_allowances: list[float]) -> float:
    """
    Compute total flat blank length.
    = sum(straight segments) + sum(bend allowances)
    """
    return sum(segments) + sum(bend_allowances)


# ---------------------------------------------------------------------------
# Bending DXF helpers
# ---------------------------------------------------------------------------

def _setup_bend_layers(doc) -> None:
    layer_defs = [
        ("PROFILE",      7,  50, "CONTINUOUS"),
        ("BEND_LINES",   4,  25, "DASHED"),
        ("ANNOTATIONS",  2,  18, "CONTINUOUS"),
        ("TITLE_BLOCK",  3,  25, "CONTINUOUS"),
    ]
    for name, color, lw, lt in layer_defs:
        if name not in doc.layers:
            layer = doc.layers.add(name)
            layer.dxf.color = color
            layer.dxf.lineweight = lw
            try:
                layer.dxf.linetype = lt
            except Exception:
                pass


def _draw_flat_profile_with_bends(
    msp,
    x0: float,
    y0: float,
    blank_length: float,
    profile_height: float,
    bend_positions: list[float],
    bend_records: list[BendRecord],
) -> None:
    """Draw unfolded flat profile and annotate bend lines."""
    # Outer profile rectangle
    msp.add_lwpolyline(
        [
            (x0, y0),
            (x0 + blank_length, y0),
            (x0 + blank_length, y0 + profile_height),
            (x0, y0 + profile_height),
            (x0, y0),
        ],
        dxfattribs={"layer": "PROFILE", "lineweight": 50},
    )

    # Bend lines
    for i, (bx, br) in enumerate(zip(bend_positions, bend_records)):
        line_x = x0 + bx
        # Dashed bend line
        msp.add_line(
            (line_x, y0 - 10),
            (line_x, y0 + profile_height + 10),
            dxfattribs={"layer": "BEND_LINES", "lineweight": 25, "linetype": "DASHED"},
        )

        ann_y_base = y0 + profile_height + 15
        row_gap = 8.0

        # Bend ID
        msp.add_text(
            f"B{br.bend_id}",
            dxfattribs={"layer": "ANNOTATIONS", "height": 5.0},
        ).set_placement((line_x, ann_y_base), align=TextEntityAlignment.CENTER)

        # Angle
        msp.add_text(
            f"{br.angle_deg:.1f}°",
            dxfattribs={"layer": "ANNOTATIONS", "height": 4.0},
        ).set_placement((line_x, ann_y_base + row_gap), align=TextEntityAlignment.CENTER)

        # Radius
        msp.add_text(
            f"R{br.radius_mm:.1f}",
            dxfattribs={"layer": "ANNOTATIONS", "height": 4.0},
        ).set_placement((line_x, ann_y_base + row_gap * 2), align=TextEntityAlignment.CENTER)

        # Bend allowance
        msp.add_text(
            f"BA={br.bend_allowance_mm:.2f}",
            dxfattribs={"layer": "ANNOTATIONS", "height": 3.5},
        ).set_placement((line_x, ann_y_base + row_gap * 3), align=TextEntityAlignment.CENTER)

        # Direction arrow indicator
        arrow_char = "▲" if br.direction == "UP" else "▼"  # ▲ or ▼
        msp.add_text(
            arrow_char + br.direction,
            dxfattribs={"layer": "ANNOTATIONS", "height": 4.0},
        ).set_placement((line_x, ann_y_base + row_gap * 4), align=TextEntityAlignment.CENTER)


def _draw_bending_title_block(
    msp,
    x0: float,
    y0: float,
    part: PartRecord,
    blank_length: float,
) -> None:
    """Draw notes block for bending drawing."""
    tb_w, tb_h = 140.0, 60.0
    attribs = {"layer": "TITLE_BLOCK", "lineweight": 25}
    text_h = {"layer": "TITLE_BLOCK", "height": 3.0}

    msp.add_lwpolyline(
        [(x0, y0), (x0 + tb_w, y0), (x0 + tb_w, y0 + tb_h), (x0, y0 + tb_h), (x0, y0)],
        dxfattribs=attribs,
    )

    row_h = tb_h / 6
    mid = tb_w / 2

    for i in range(1, 6):
        msp.add_line((x0, y0 + i * row_h), (x0 + tb_w, y0 + i * row_h), dxfattribs=attribs)
    msp.add_line((x0 + mid, y0), (x0 + mid, y0 + tb_h), dxfattribs=attribs)

    today = date.today().strftime("%Y-%m-%d")
    k = part.bends[0].k_factor if part.bends else 0.33
    entries = [
        (0, "PART NO:", part.part_id),
        (1, "MATERIAL:", part.material or part.material_code or "N/A"),
        (2, "THICKNESS:", f"{part.thickness_mm:.2f} mm" if part.thickness_mm else "N/A"),
        (3, "K-FACTOR:", f"{k:.2f}"),
        (4, "BLANK LENGTH:", f"{blank_length:.2f} mm"),
        (5, "DATE:", today),
    ]
    for row, label, value in entries:
        ty = y0 + (row + 0.5) * row_h
        msp.add_text(label, dxfattribs=text_h).set_placement(
            (x0 + 2, ty), align=TextEntityAlignment.LEFT
        )
        msp.add_text(value, dxfattribs={**text_h, "height": 3.5}).set_placement(
            (x0 + mid + 2, ty), align=TextEntityAlignment.LEFT
        )


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def _generate_bending_pdf(
    part: PartRecord,
    blank_length: float,
    bend_positions: list[float],
    pdf_path: str,
) -> None:
    """Generate a bending drawing PDF using reportlab."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.units import mm
    from reportlab.lib.pagesizes import A3, landscape

    page_w, page_h = landscape(A3)
    c = rl_canvas.Canvas(pdf_path, pagesize=landscape(A3))

    # Scale: fit blank_length into ~500pt available width
    margin = 50
    available_w = page_w - 2 * margin
    scale = min(1.0, available_w / (blank_length * mm)) if blank_length > 0 else 1.0
    profile_h_pt = 60 * mm * scale
    blank_pt = blank_length * mm * scale

    x0 = margin
    y0 = page_h / 2 - profile_h_pt / 2

    # Draw flat profile
    c.setLineWidth(2)
    c.rect(x0, y0, blank_pt, profile_h_pt)

    # Bend lines
    c.setDash([6, 3], 0)
    c.setLineWidth(1)
    for bx, br in zip(bend_positions, part.bends):
        bx_pt = x0 + bx * mm * scale
        c.line(bx_pt, y0 - 10, bx_pt, y0 + profile_h_pt + 10)
        # Annotation
        c.setDash([], 0)
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 14, f"B{br.bend_id}")
        c.setFont("Helvetica", 7)
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 24, f"{br.angle_deg:.1f}°")
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 33, f"R{br.radius_mm:.1f}")
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 42, f"BA={br.bend_allowance_mm:.2f}")
        arrow = "▲" if br.direction == "UP" else "▼"
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 51, arrow + br.direction)
        c.setDash([6, 3], 0)

    c.setDash([], 0)

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin, page_h - margin, f"BENDING DRAWING — {part.part_id}")
    c.setFont("Helvetica", 10)
    c.drawString(margin, page_h - margin - 18, part.part_name)

    # Notes block
    notes_x = page_w - margin - 200
    notes_y = margin
    k = part.bends[0].k_factor if part.bends else 0.33
    today = date.today().strftime("%Y-%m-%d")
    note_lines = [
        f"Part No: {part.part_id}",
        f"Material: {part.material or part.material_code or 'N/A'}",
        f"Thickness: {part.thickness_mm:.2f} mm" if part.thickness_mm else "Thickness: N/A",
        f"K-Factor: {k:.2f}",
        f"Total Blank Length: {blank_length:.2f} mm",
        f"Bends: {part.bend_count}",
        f"Date: {today}",
        "Rev: A",
    ]
    c.setLineWidth(1)
    box_h = len(note_lines) * 14 + 10
    c.rect(notes_x - 5, notes_y - 5, 210, box_h)
    c.setFont("Helvetica", 8)
    for i, line in enumerate(note_lines):
        c.drawString(notes_x, notes_y + (len(note_lines) - 1 - i) * 14, line)

    # Blank length annotation
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(x0 + blank_pt / 2, y0 - 20, f"BLANK LENGTH = {blank_length:.2f} mm")

    c.save()
    logger.info("Bending PDF saved: %s", pdf_path)


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def generate_bending_drawing(part: PartRecord, output_dir: str) -> tuple[str, Optional[str]]:
    """
    Generate bending drawing as DXF + PDF.
    Returns (dxf_path, pdf_path).
    If no bends, returns (flat_dxf_path, None).
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_id = part.part_id.replace("/", "_").replace(" ", "_")

    if not part.has_bends or part.bend_count == 0:
        # Flat cut only
        dxf_path = os.path.join(output_dir, f"{safe_id}_flatcut.dxf")
        doc = ezdxf.new("R2010")
        doc.units = units.MM
        msp = doc.modelspace()
        _setup_bend_layers(doc)
        bb = part.bounding_box or {"L": 500.0, "W": 300.0, "H": 8.0}
        L = float(bb.get("L", 500.0))
        W = float(bb.get("W", 300.0))
        msp.add_lwpolyline(
            [(0, 0), (L, 0), (L, W), (0, W), (0, 0)],
            dxfattribs={"layer": "PROFILE", "lineweight": 50},
        )
        msp.add_text(
            f"FLAT CUT — NO BENDS\n{part.part_id}",
            dxfattribs={"layer": "ANNOTATIONS", "height": 10.0},
        ).set_placement((L / 2, W / 2), align=TextEntityAlignment.MIDDLE_CENTER)
        doc.saveas(dxf_path)
        logger.info("Flat cut DXF saved (no bends): %s", dxf_path)
        return dxf_path, None

    # Calculate blank length from bend records
    thickness = part.thickness_mm or 8.0
    k_factor = part.bends[0].k_factor if part.bends else 0.33

    bend_allowances = [b.bend_allowance_mm for b in part.bends]

    # Distribute segments evenly
    bb = part.bounding_box or {"L": 600.0, "W": 300.0, "H": thickness}
    total_raw_length = float(bb.get("L", 600.0))
    total_ba = sum(bend_allowances)
    num_segments = part.bend_count + 1
    segment_length = max(10.0, (total_raw_length - total_ba) / num_segments)
    segments = [segment_length] * num_segments
    blank_length = compute_flat_blank_length(segments, bend_allowances)

    # Compute bend positions (cumulative from left)
    bend_positions: list[float] = []
    cursor = 0.0
    for i, br in enumerate(part.bends):
        cursor += segments[i]
        bend_positions.append(cursor)
        cursor += br.bend_allowance_mm

    # Profile display height = min(W, 80)
    profile_h = min(float(bb.get("W", 80.0)), 80.0)

    # --- DXF ---
    dxf_path = os.path.join(output_dir, f"{safe_id}_bending.dxf")
    doc = ezdxf.new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()
    _setup_bend_layers(doc)

    x0, y0 = 20.0, 80.0
    _draw_flat_profile_with_bends(msp, x0, y0, blank_length, profile_h, bend_positions, part.bends)

    # Title block
    _draw_bending_title_block(msp, x0, y0 - 80.0, part, blank_length)

    # Blank length annotation
    msp.add_text(
        f"BLANK LENGTH = {blank_length:.2f} mm",
        dxfattribs={"layer": "ANNOTATIONS", "height": 6.0},
    ).set_placement((x0 + blank_length / 2, y0 - 15), align=TextEntityAlignment.CENTER)

    doc.saveas(dxf_path)
    logger.info("Bending DXF saved: %s", dxf_path)

    # --- PDF ---
    pdf_path = os.path.join(output_dir, f"{safe_id}_bending.pdf")
    try:
        _generate_bending_pdf(part, blank_length, bend_positions, pdf_path)
    except Exception as exc:
        logger.error("Bending PDF generation failed for %s: %s", part.part_id, exc)
        pdf_path = None

    return dxf_path, pdf_path
