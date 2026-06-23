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

        # Angle (unknown values shown as '?')
        angle_str = f"{br.angle_deg:.1f}°" if br.angle_deg is not None else "?°"
        msp.add_text(
            angle_str,
            dxfattribs={"layer": "ANNOTATIONS", "height": 4.0},
        ).set_placement((line_x, ann_y_base + row_gap), align=TextEntityAlignment.CENTER)

        # Radius
        radius_str = f"R{br.radius_mm:.1f}" if br.radius_mm is not None else "R?"
        msp.add_text(
            radius_str,
            dxfattribs={"layer": "ANNOTATIONS", "height": 4.0},
        ).set_placement((line_x, ann_y_base + row_gap * 2), align=TextEntityAlignment.CENTER)

        # Bend allowance
        ba_str = f"BA={br.bend_allowance_mm:.2f}" if br.bend_allowance_mm is not None else "BA=?"
        msp.add_text(
            ba_str,
            dxfattribs={"layer": "ANNOTATIONS", "height": 3.5},
        ).set_placement((line_x, ann_y_base + row_gap * 3), align=TextEntityAlignment.CENTER)

        # Direction indicator
        arrow_char = {"UP": "^", "DOWN": "v"}.get(br.direction, "")
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
    k = part.bends[0].k_factor if part.bends else None
    entries = [
        (0, "PART NO:", part.part_id),
        (1, "MATERIAL:", part.material or part.material_code or "N/A"),
        (2, "THICKNESS:", f"{part.thickness_mm:.2f} mm" if part.thickness_mm else "N/A"),
        (3, "K-FACTOR:", f"{k:.2f}" if k is not None else "N/A"),
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
        angle_str = f"{br.angle_deg:.1f}deg" if br.angle_deg is not None else "?deg"
        radius_str = f"R{br.radius_mm:.1f}" if br.radius_mm is not None else "R?"
        ba_str = f"BA={br.bend_allowance_mm:.2f}" if br.bend_allowance_mm is not None else "BA=?"
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 24, angle_str)
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 33, radius_str)
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 42, ba_str)
        c.drawCentredString(bx_pt, y0 + profile_h_pt + 51, br.direction)
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
    k = part.bends[0].k_factor if part.bends else None
    today = date.today().strftime("%Y-%m-%d")
    note_lines = [
        f"Part No: {part.part_id}",
        f"Material: {part.material or part.material_code or 'N/A'}",
        f"Thickness: {part.thickness_mm:.2f} mm" if part.thickness_mm else "Thickness: N/A",
        f"K-Factor: {k:.2f}" if k is not None else "K-Factor: N/A",
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

    bb = part.bounding_box
    L = float(bb["L"]) if bb and bb.get("L") is not None else None
    W = float(bb["W"]) if bb and bb.get("W") is not None else None

    if not part.has_bends or part.bend_count == 0:
        # Flat cut only
        dxf_path = os.path.join(output_dir, f"{safe_id}_flatcut.dxf")
        doc = ezdxf.new("R2010")
        doc.units = units.MM
        msp = doc.modelspace()
        _setup_bend_layers(doc)
        if L is not None and W is not None:
            msp.add_lwpolyline(
                [(0, 0), (L, 0), (L, W), (0, W), (0, 0)],
                dxfattribs={"layer": "PROFILE", "lineweight": 50},
            )
            msp.add_text(
                f"FLAT CUT — NO BENDS\n{part.part_id}",
                dxfattribs={"layer": "ANNOTATIONS", "height": 10.0},
            ).set_placement((L / 2, W / 2), align=TextEntityAlignment.MIDDLE_CENTER)
        else:
            msp.add_text(
                f"FLAT CUT — NO BENDS\n{part.part_id}\nGEOMETRY UNAVAILABLE",
                dxfattribs={"layer": "ANNOTATIONS", "height": 10.0},
            ).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)
        doc.saveas(dxf_path)
        logger.info("Flat cut DXF saved (no bends): %s", dxf_path)
        return dxf_path, None

    # --- Bent part ---
    thickness = part.thickness_mm
    k_factor = part.bends[0].k_factor if part.bends else None

    # Bend allowances (may contain None when bend geometry is unknown).
    bend_allowances = [b.bend_allowance_mm for b in part.bends]
    ba_known = all(ba is not None for ba in bend_allowances)

    # Reconstruct straight segments from real BendRecord data when present.
    segments: list[float] = []
    if part.bends and all(b.segment_before_mm is not None for b in part.bends) \
            and all(b.segment_after_mm is not None for b in part.bends):
        segments.append(part.bends[0].segment_before_mm)
        for b in part.bends:
            segments.append(b.segment_after_mm)
        segments_approx = False
    elif L is not None and ba_known:
        # Approximate: distribute the developed length evenly (annotated).
        total_ba = sum(bend_allowances)
        num_segments = part.bend_count + 1
        segment_length = max(10.0, (L - total_ba) / num_segments)
        segments = [segment_length] * num_segments
        segments_approx = True
    else:
        # Not enough information to build a flat blank — emit honest note only.
        dxf_path = os.path.join(output_dir, f"{safe_id}_bending.dxf")
        doc = ezdxf.new("R2010")
        doc.units = units.MM
        msp = doc.modelspace()
        _setup_bend_layers(doc)
        msp.add_text(
            f"BENDING DRAWING — {part.part_id}\n"
            f"{part.bend_count} bend(s) detected, but bend geometry "
            f"(angle/radius/segments) is unavailable.\n"
            f"Refer to the source CAD / part drawing.",
            dxfattribs={"layer": "ANNOTATIONS", "height": 8.0},
        ).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)
        doc.saveas(dxf_path)
        logger.info("Bending DXF saved (geometry unavailable): %s", dxf_path)
        return dxf_path, None

    blank_length = compute_flat_blank_length(segments, [ba or 0.0 for ba in bend_allowances])

    # Compute bend positions (cumulative from left)
    bend_positions: list[float] = []
    cursor = 0.0
    for i, br in enumerate(part.bends):
        cursor += segments[i]
        bend_positions.append(cursor)
        cursor += (br.bend_allowance_mm or 0.0)

    # Profile display height
    profile_h = min(W, 80.0) if W is not None else 80.0

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
    blank_label = f"BLANK LENGTH = {blank_length:.2f} mm"
    if segments_approx:
        blank_label += " (APPROX — segment split estimated from overall length)"
    msp.add_text(
        blank_label,
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
