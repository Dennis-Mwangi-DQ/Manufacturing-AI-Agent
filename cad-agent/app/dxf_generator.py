"""
DXF flat drawing generator for sheet metal parts.
Uses ezdxf R2010 format for maximum CAD software compatibility.
"""
from __future__ import annotations

import logging
import os
import shutil
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
    mat_val = part.material or part.material_code or "N/A"
    if part.material_inferred and mat_val != "N/A":
        mat_val += " (INFERRED)"
    thk_val = f"{part.thickness_mm:.1f} mm" if part.thickness_mm else "N/A"
    if part.thickness_mm and part.thickness_source == "filename":
        thk_val += " (FILE)"
    fields = [
        (0, "PART NO:", part.part_id),
        (1, "MATERIAL:", mat_val),
        (2, "THICKNESS:", thk_val),
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
    Produce a DXF flat drawing for a sheet metal part.

    If the part originated from a DXF flat pattern, the real source geometry is
    passed through unchanged (the input already IS the flat pattern). Otherwise
    a bounding-box outline is drawn from the real measured extents, clearly
    annotated as an approximation. No holes or bend lines are ever invented.

    Returns the path to the generated/copied DXF.
    """
    os.makedirs(output_dir, exist_ok=True)
    safe_id = part.part_id.replace("/", "_").replace(" ", "_")
    dxf_path = os.path.join(output_dir, f"{safe_id}_flat.dxf")

    # --- Pass through real source DXF geometry when available ---
    src = part.source_path
    if src and src.lower().endswith(".dxf") and os.path.exists(src):
        try:
            shutil.copyfile(src, dxf_path)
            logger.info("DXF flat drawing copied from source: %s", dxf_path)
            return dxf_path
        except OSError as exc:
            logger.warning("Could not copy source DXF (%s); drawing outline instead.", exc)

    # --- Otherwise draw the real bounding-box outline (approximation) ---
    doc = ezdxf.new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()
    _setup_layers(doc)

    bb = part.bounding_box
    if not bb or bb.get("L") is None or bb.get("W") is None:
        # No real extents — emit an honest note rather than a fake rectangle.
        msp.add_text(
            f"{part.part_id}\nGEOMETRY UNAVAILABLE — refer to source CAD file",
            dxfattribs={"layer": "3_ANNOTATIONS", "height": 8.0},
        ).set_placement((0, 0), align=TextEntityAlignment.MIDDLE_CENTER)
        doc.saveas(dxf_path)
        logger.info("DXF flat drawing (no geometry) saved: %s", dxf_path)
        return dxf_path

    L = float(bb.get("L"))
    W = float(bb.get("W"))
    thickness = part.thickness_mm

    x0, y0 = 20.0, 70.0

    # Outer bounding-box rectangle (real overall extents).
    msp.add_lwpolyline(
        [(x0, y0), (x0 + L, y0), (x0 + L, y0 + W), (x0, y0 + W), (x0, y0)],
        dxfattribs={"layer": "0_OUTLINE", "lineweight": 50},
    )

    # Dimensions (real).
    _add_dimension_annotation(msp, x0, y0, L, W, thickness)

    # Approximation notice — the true profile/holes are not reconstructed here.
    msp.add_text(
        "OUTLINE = OVERALL BOUNDING BOX (approx.) — see source CAD for true profile",
        dxfattribs={"layer": "3_ANNOTATIONS", "height": 4.0},
    ).set_placement((x0, y0 - 18), align=TextEntityAlignment.LEFT)

    if part.has_bends and part.bend_count > 0:
        msp.add_text(
            f"BENDS: {part.bend_count} (see bending drawing)",
            dxfattribs={"layer": "3_ANNOTATIONS", "height": 4.0},
        ).set_placement((x0, y0 - 26), align=TextEntityAlignment.LEFT)

    # Part ID annotation
    msp.add_text(
        part.part_id,
        dxfattribs={"layer": "3_ANNOTATIONS", "height": 5.0},
    ).set_placement((x0 + L + 12, y0 + W + 10), align=TextEntityAlignment.LEFT)

    # Material annotation
    mat_str = part.material or part.material_code or "N/A"
    if part.material_inferred and mat_str != "N/A":
        mat_str += " (INFERRED)"
    msp.add_text(
        f"MAT: {mat_str}",
        dxfattribs={"layer": "3_ANNOTATIONS", "height": 3.5},
    ).set_placement((x0 + L + 12, y0 + W + 2), align=TextEntityAlignment.LEFT)

    # Title block
    tb_x = x0 + L - 130.0
    tb_y = y0 - 60.0
    _add_title_block(msp, max(x0, tb_x), tb_y, part)

    doc.saveas(dxf_path)
    logger.info("DXF flat drawing (bbox outline) saved: %s", dxf_path)
    return dxf_path
