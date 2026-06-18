"""
DXF flat drawing generator for sheet metal parts.
Uses ezdxf R2010 format for maximum CAD software compatibility.
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

from app.models import PartRecord

logger = logging.getLogger(__name__)

# Layer definitions: (name, color_index, lineweight_hundredths, linetype)
LAYERS = [
    ("0_OUTLINE",      7,  50, "CONTINUOUS"),   # white/black, 0.50 mm
    ("1_HOLES",        1,  35, "CONTINUOUS"),   # red, 0.35 mm
    ("2_BEND_LINES",   4,  25, "DASHED"),       # cyan, 0.25 mm
    ("3_ANNOTATIONS",  2,  18, "CONTINUOUS"),   # yellow, 0.18 mm
    ("4_TITLE_BLOCK",  3,  25, "CONTINUOUS"),   # green, 0.25 mm
]


def _setup_layers(doc) -> None:
    """Create all required layers in the document."""
    for name, color, lw, linetype in LAYERS:
        if name not in doc.layers:
            layer = doc.layers.add(name)
            layer.dxf.color = color
            layer.dxf.lineweight = lw
            try:
                layer.dxf.linetype = linetype
            except Exception:
                pass  # linetype may not be loaded


def _add_title_block(msp, x_origin: float, y_origin: float, part: PartRecord) -> None:
    """Draw a title block in the bottom-right corner of the drawing."""
    # Title block: 130 wide × 50 tall, positioned at (x_origin, y_origin)
    tb_w, tb_h = 130.0, 50.0
    x0, y0 = x_origin, y_origin

    attribs = {"layer": "4_TITLE_BLOCK", "lineweight": 25}
    text_attribs = {"layer": "4_TITLE_BLOCK", "height": 3.0}

    # Outer border
    msp.add_lwpolyline(
        [(x0, y0), (x0 + tb_w, y0), (x0 + tb_w, y0 + tb_h), (x0, y0 + tb_h), (x0, y0)],
        dxfattribs=attribs,
    )

    # Dividing lines
    row_h = tb_h / 5
    for i in range(1, 5):
        msp.add_line(
            (x0, y0 + i * row_h),
            (x0 + tb_w, y0 + i * row_h),
            dxfattribs=attribs,
        )

    # Left/right column split
    mid = tb_w / 2
    msp.add_line((x0 + mid, y0), (x0 + mid, y0 + tb_h), dxfattribs=attribs)

    # Title block text
    today = date.today().strftime("%Y-%m-%d")
    fields = [
        (0, "PART NO:", part.part_id),
        (1, "MATERIAL:", part.material or (part.material_code or "N/A")),
        (2, "THICKNESS:", f"{part.thickness_mm:.1f} mm" if part.thickness_mm else "N/A"),
        (3, "SCALE:", "1:1"),
        (4, "DATE:", today),
    ]
    for row, label, value in fields:
        ty = y0 + (row + 0.5) * row_h
        msp.add_text(label, dxfattribs={**text_attribs, "height": 2.5}).set_placement(
            (x0 + 2, ty), align=TextEntityAlignment.LEFT
        )
        msp.add_text(value, dxfattribs={**text_attribs, "height": 3.0}).set_placement(
            (x0 + mid + 2, ty), align=TextEntityAlignment.LEFT
        )

    # Rev box in top-right
    msp.add_text("REV A", dxfattribs={**text_attribs, "height": 3.5}).set_placement(
        (x0 + tb_w - 5, y0 + tb_h - 6), align=TextEntityAlignment.RIGHT
    )


def _add_dimension_annotation(msp, x0: float, y0: float, L: float, W: float, thickness: Optional[float]) -> None:
    """Add overall dimension annotations."""

    ann_attribs = {"layer": "3_ANNOTATIONS", "height": 4.0}

    # Width dimension above the part
    msp.add_text(
        f"L = {L:.1f} mm",
        dxfattribs=ann_attribs,
    ).set_placement((x0 + L / 2, y0 + W + 12), align=TextEntityAlignment.CENTER)

    # Height dimension to the right
    msp.add_text(
        f"W = {W:.1f} mm",
        dxfattribs=ann_attribs,
    ).set_placement((x0 + L + 12, y0 + W / 2), align=TextEntityAlignment.LEFT)

    if thickness:
        msp.add_text(
            f"THK = {thickness:.2f} mm",
            dxfattribs={**ann_attribs, "height": 3.5},
        ).set_placement((x0, y0 - 10), align=TextEntityAlignment.LEFT)

    # Part name heading
    msp.add_text(
        f"FLAT PROFILE",
        dxfattribs={**ann_attribs, "height": 6.0, "layer": "3_ANNOTATIONS"},
    ).set_placement((x0, y0 + W + 25), align=TextEntityAlignment.LEFT)


def generate_dxf_flat(part: PartRecord, output_dir: str) -> str:
    """
    Generate one DXF flat drawing for a sheet metal part.
    Returns path to generated DXF.
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_id = part.part_id.replace("/", "_").replace(" ", "_")
    dxf_path = os.path.join(output_dir, f"{safe_id}_flat.dxf")

    doc = ezdxf.new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()

    _setup_layers(doc)

    # Geometry from bounding box
    bb = part.bounding_box or {"L": 500.0, "W": 300.0, "H": 8.0}
    L = float(bb.get("L", 500.0))
    W = float(bb.get("W", 300.0))
    thickness = part.thickness_mm or float(bb.get("H", 8.0))

    # Drawing origin
    x0, y0 = 20.0, 70.0  # leave room for title block below

    # --- 0_OUTLINE: outer rectangle ---
    msp.add_lwpolyline(
        [(x0, y0), (x0 + L, y0), (x0 + L, y0 + W), (x0, y0 + W), (x0, y0)],
        dxfattribs={"layer": "0_OUTLINE", "lineweight": 50},
    )

    # --- 1_HOLES: circular cutouts (heuristic: 1 hole per 10 faces, max 8) ---
    face_count = getattr(part, "face_count", 0) or 0
    num_holes = min(int(face_count / 10), 8)
    hole_radius = min(L, W) * 0.03  # 3% of smallest dimension
    hole_radius = max(5.0, min(hole_radius, 30.0))

    if num_holes > 0:
        cols = max(1, int(math.sqrt(num_holes)))
        rows = math.ceil(num_holes / cols)
        x_spacing = L / (cols + 1)
        y_spacing = W / (rows + 1)
        holes_drawn = 0
        for row in range(rows):
            for col in range(cols):
                if holes_drawn >= num_holes:
                    break
                cx = x0 + (col + 1) * x_spacing
                cy = y0 + (row + 1) * y_spacing
                msp.add_circle(
                    center=(cx, cy),
                    radius=hole_radius,
                    dxfattribs={"layer": "1_HOLES", "lineweight": 35},
                )
                holes_drawn += 1

    # --- 2_BEND_LINES: dashed lines for bends ---
    if part.has_bends and part.bend_count > 0:
        bend_spacing = W / (part.bend_count + 1)
        for i in range(1, part.bend_count + 1):
            by = y0 + i * bend_spacing
            msp.add_line(
                (x0, by),
                (x0 + L, by),
                dxfattribs={"layer": "2_BEND_LINES", "lineweight": 25, "linetype": "DASHED"},
            )
            # Bend label
            msp.add_text(
                f"B{i}",
                dxfattribs={"layer": "3_ANNOTATIONS", "height": 4.0},
            ).set_placement((x0 - 10, by), align=TextEntityAlignment.RIGHT)

    # --- 3_ANNOTATIONS: dimensions ---
    _add_dimension_annotation(msp, x0, y0, L, W, thickness)

    # Part ID annotation
    msp.add_text(
        part.part_id,
        dxfattribs={"layer": "3_ANNOTATIONS", "height": 5.0},
    ).set_placement((x0 + L + 12, y0 + W + 10), align=TextEntityAlignment.LEFT)

    # Material annotation
    mat_str = part.material or part.material_code or "N/A"
    if part.material_inferred:
        mat_str += " (INFERRED)"
    msp.add_text(
        f"MAT: {mat_str}",
        dxfattribs={"layer": "3_ANNOTATIONS", "height": 3.5},
    ).set_placement((x0 + L + 12, y0 + W + 2), align=TextEntityAlignment.LEFT)

    # --- 4_TITLE_BLOCK ---
    tb_x = x0 + L - 130.0
    tb_y = y0 - 60.0
    _add_title_block(msp, max(x0, tb_x), tb_y, part)

    doc.saveas(dxf_path)
    logger.info("DXF flat drawing saved: %s", dxf_path)
    return dxf_path
