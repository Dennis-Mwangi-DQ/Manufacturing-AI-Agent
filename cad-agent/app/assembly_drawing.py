"""
2D schematic assembly drawing generator — produces DXF + PDF on A1 sheet.
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

# A1 sheet dimensions in mm
A1_W = 841.0
A1_H = 594.0

# Colour map per BOM level (DXF color indices)
LEVEL_COLORS = [7, 1, 2, 3, 4, 5, 6, 8, 9, 10]


def _setup_assembly_layers(doc) -> None:
    layer_defs = [
        ("PARTS",       7, 35, "CONTINUOUS"),
        ("BALLOONS",    5, 18, "CONTINUOUS"),
        ("DIMENSIONS",  2, 18, "CONTINUOUS"),
        ("ANNOTATIONS", 3, 18, "CONTINUOUS"),
        ("TITLE_BLOCK", 7, 35, "CONTINUOUS"),
        ("PARTS_LIST",  7, 25, "CONTINUOUS"),
    ]
    for name, color, lw, lt in layer_defs:
        if name not in doc.layers:
            layer = doc.layers.add(name)
            layer.dxf.color = color
            layer.dxf.lineweight = lw


def _auto_layout(parts: list[PartRecord], area_w: float, area_h: float, max_parts: int = 20) -> list[dict]:
    """
    Arrange up to max_parts parts on a grid within (area_w × area_h).
    Returns list of dicts: {part, cx, cy, draw_w, draw_h, item_no}
    """
    displayed = parts[:max_parts]
    n = len(displayed)
    if n == 0:
        return []

    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cell_w = area_w / cols
    cell_h = area_h / rows

    layout = []
    for idx, part in enumerate(displayed):
        col = idx % cols
        row = idx // cols
        cx = (col + 0.5) * cell_w
        cy = area_h - (row + 0.5) * cell_h  # top-to-bottom

        # Scale part bounding box to fit within 70% of cell
        bb = part.bounding_box or {"L": 100.0, "W": 80.0, "H": 10.0}
        pL = float(bb.get("L", 100.0))
        pW = float(bb.get("W", 80.0))
        max_draw = min(cell_w, cell_h) * 0.7
        scale = max_draw / max(pL, pW, 1.0)
        draw_w = pL * scale
        draw_h = pW * scale

        layout.append({
            "part": part,
            "cx": cx,
            "cy": cy,
            "draw_w": draw_w,
            "draw_h": draw_h,
            "item_no": idx + 1,
        })
    return layout


def _draw_assembly_title_block(msp, session_id: str) -> None:
    """Draw title block in bottom-right of A1 sheet."""
    tb_w, tb_h = 200.0, 60.0
    x0 = A1_W - tb_w - 10
    y0 = 10.0
    today = date.today().strftime("%Y-%m-%d")
    short_id = session_id[:12] if len(session_id) > 12 else session_id

    attribs = {"layer": "TITLE_BLOCK", "lineweight": 35}
    text_h = {"layer": "TITLE_BLOCK", "height": 3.0}

    msp.add_lwpolyline(
        [(x0, y0), (x0 + tb_w, y0), (x0 + tb_w, y0 + tb_h), (x0, y0 + tb_h), (x0, y0)],
        dxfattribs=attribs,
    )
    row_h = tb_h / 4
    for i in range(1, 4):
        msp.add_line((x0, y0 + i * row_h), (x0 + tb_w, y0 + i * row_h), dxfattribs=attribs)
    msp.add_line((x0 + tb_w / 2, y0), (x0 + tb_w / 2, y0 + tb_h), dxfattribs=attribs)

    entries = [
        (0, "ASSEMBLY NO:", short_id),
        (1, "DATE:", today),
        (2, "SCALE:", "1:50"),
        (3, "REV:", "A"),
    ]
    for row, label, value in entries:
        ty = y0 + (row + 0.5) * row_h
        msp.add_text(label, dxfattribs=text_h).set_placement(
            (x0 + 2, ty), align=TextEntityAlignment.LEFT
        )
        msp.add_text(value, dxfattribs={**text_h, "height": 3.5}).set_placement(
            (x0 + tb_w / 2 + 2, ty), align=TextEntityAlignment.LEFT
        )


def _draw_parts_list_table(msp, parts: list[PartRecord], x0: float, y0: float) -> None:
    """Draw top-10-by-mass parts list table."""
    top_parts = sorted(
        [p for p in parts if p.mass_kg is not None],
        key=lambda p: p.mass_kg or 0,
        reverse=True,
    )[:10]

    if not top_parts:
        top_parts = parts[:10]

    headers = ["Item", "Part No.", "Description", "Qty", "Material"]
    col_widths = [15.0, 35.0, 60.0, 12.0, 50.0]
    row_h = 7.0
    table_w = sum(col_widths)
    table_h = (len(top_parts) + 1) * row_h

    attribs = {"layer": "PARTS_LIST", "lineweight": 18}
    header_text = {"layer": "PARTS_LIST", "height": 3.0}

    # Outer border
    msp.add_lwpolyline(
        [(x0, y0 - table_h), (x0 + table_w, y0 - table_h),
         (x0 + table_w, y0), (x0, y0), (x0, y0 - table_h)],
        dxfattribs=attribs,
    )

    # Header row
    msp.add_line((x0, y0 - row_h), (x0 + table_w, y0 - row_h), dxfattribs=attribs)
    cur_x = x0
    for i, (hdr, cw) in enumerate(zip(headers, col_widths)):
        msp.add_text(hdr, dxfattribs=header_text).set_placement(
            (cur_x + cw / 2, y0 - row_h / 2), align=TextEntityAlignment.MIDDLE_CENTER
        )
        if i < len(headers) - 1:
            msp.add_line((cur_x + cw, y0 - table_h), (cur_x + cw, y0), dxfattribs=attribs)
        cur_x += cw

    # Data rows
    for row_idx, part in enumerate(top_parts):
        ry = y0 - (row_idx + 2) * row_h
        msp.add_line((x0, ry + row_h), (x0 + table_w, ry + row_h), dxfattribs=attribs)

        cur_x = x0
        row_vals = [
            str(row_idx + 1),
            part.part_id,
            part.part_name[:20],
            str(part.quantity),
            (part.material or part.material_code or "N/A")[:18],
        ]
        for val, cw in zip(row_vals, col_widths):
            msp.add_text(val, dxfattribs={**header_text, "height": 2.5}).set_placement(
                (cur_x + 1, ry + row_h / 2), align=TextEntityAlignment.LEFT
            )
            cur_x += cw


def generate_assembly_drawing(
    parts: list[PartRecord],
    session_id: str,
    output_dir: str,
) -> tuple[str, str]:
    """
    Generate assembly drawing as DXF + PDF.
    Returns (dxf_path, pdf_path).
    """
    os.makedirs(output_dir, exist_ok=True)

    dxf_path = os.path.join(output_dir, "Assembly_Drawing.dxf")
    pdf_path = os.path.join(output_dir, "Assembly_Drawing.pdf")

    # ---- DXF ----
    doc = ezdxf.new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()
    _setup_assembly_layers(doc)

    # Drawing border
    msp.add_lwpolyline(
        [(5, 5), (A1_W - 5, 5), (A1_W - 5, A1_H - 5), (5, A1_H - 5), (5, 5)],
        dxfattribs={"layer": "TITLE_BLOCK", "lineweight": 50},
    )

    # Title
    msp.add_text(
        f"ASSEMBLY DRAWING — {session_id[:16]}",
        dxfattribs={"layer": "ANNOTATIONS", "height": 10.0},
    ).set_placement((A1_W / 2, A1_H - 20), align=TextEntityAlignment.MIDDLE_CENTER)

    # Parts layout area: leave right strip for parts list
    layout_w = A1_W * 0.65
    layout_h = A1_H - 80.0  # leave room for title and parts list
    layout_origin_x = 15.0
    layout_origin_y = 50.0

    layout = _auto_layout(parts, layout_w, layout_h)

    for item in layout:
        part = item["part"]
        cx = layout_origin_x + item["cx"]
        cy = layout_origin_y + item["cy"]
        dw = item["draw_w"]
        dh = item["draw_h"]
        item_no = item["item_no"]

        color = LEVEL_COLORS[part.bom_level % len(LEVEL_COLORS)]
        rx0, ry0 = cx - dw / 2, cy - dh / 2

        # Part rectangle
        msp.add_lwpolyline(
            [(rx0, ry0), (rx0 + dw, ry0), (rx0 + dw, ry0 + dh), (rx0, ry0 + dh), (rx0, ry0)],
            dxfattribs={"layer": "PARTS", "lineweight": 25, "color": color},
        )

        # Part number label inside
        msp.add_text(
            part.part_id,
            dxfattribs={"layer": "ANNOTATIONS", "height": min(3.0, dh * 0.2)},
        ).set_placement((cx, cy), align=TextEntityAlignment.MIDDLE_CENTER)

        # Balloon callout
        balloon_cx = cx + dw / 2 + 15
        balloon_cy = cy + dh / 2 + 15
        balloon_r = 6.0

        msp.add_circle(
            center=(balloon_cx, balloon_cy),
            radius=balloon_r,
            dxfattribs={"layer": "BALLOONS", "lineweight": 18},
        )
        msp.add_text(
            str(item_no),
            dxfattribs={"layer": "BALLOONS", "height": 4.0},
        ).set_placement((balloon_cx, balloon_cy), align=TextEntityAlignment.MIDDLE_CENTER)

        # Leader line
        msp.add_line(
            (cx + dw / 2, cy + dh / 2),
            (balloon_cx - balloon_r, balloon_cy),
            dxfattribs={"layer": "BALLOONS", "lineweight": 13},
        )

    # Compute overall assembly extents for dimension line
    if layout:
        all_cx = [layout_origin_x + i["cx"] for i in layout]
        all_cy = [layout_origin_y + i["cy"] for i in layout]
        all_dw = [i["draw_w"] for i in layout]
        all_dh = [i["draw_h"] for i in layout]
        min_x = min(c - d / 2 for c, d in zip(all_cx, all_dw)) - 5
        max_x = max(c + d / 2 for c, d in zip(all_cx, all_dw)) + 5
        min_y = min(c - d / 2 for c, d in zip(all_cy, all_dh)) - 5
        max_y = max(c + d / 2 for c, d in zip(all_cy, all_dh)) + 5
        total_w = max_x - min_x
        total_h = max_y - min_y

        # Overall dimension annotations
        msp.add_text(
            f"OVERALL: {total_w:.0f} × {total_h:.0f} mm",
            dxfattribs={"layer": "DIMENSIONS", "height": 5.0},
        ).set_placement(((min_x + max_x) / 2, min_y - 15), align=TextEntityAlignment.MIDDLE_CENTER)

    # Parts list table
    table_x = layout_w + 20
    table_y = layout_h + layout_origin_y
    _draw_parts_list_table(msp, parts, table_x, table_y)

    # Title block
    _draw_assembly_title_block(msp, session_id)

    doc.saveas(dxf_path)
    logger.info("Assembly DXF saved: %s", dxf_path)

    # ---- PDF ----
    try:
        _generate_assembly_pdf(parts, layout, session_id, layout_origin_x, layout_origin_y, pdf_path)
    except Exception as exc:
        logger.error("Assembly PDF generation failed: %s", exc)
        # Create minimal PDF fallback
        _generate_minimal_assembly_pdf(parts, session_id, pdf_path)

    return dxf_path, pdf_path


def _generate_assembly_pdf(
    parts: list[PartRecord],
    layout: list[dict],
    session_id: str,
    ox: float,
    oy: float,
    pdf_path: str,
) -> None:
    """Render assembly drawing to PDF using reportlab + matplotlib."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A3, landscape
    from reportlab.lib.units import mm

    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_aspect("equal")
    ax.set_facecolor("#f8f8f8")

    cmap = plt.get_cmap("tab20")

    for item in layout:
        part = item["part"]
        cx_mm = ox + item["cx"]
        cy_mm = oy + item["cy"]
        dw = item["draw_w"]
        dh = item["draw_h"]
        item_no = item["item_no"]
        color = cmap(part.bom_level / max(1, max(p.bom_level for p in parts) + 1))

        rect = mpatches.Rectangle(
            (cx_mm - dw / 2, cy_mm - dh / 2), dw, dh,
            linewidth=1, edgecolor="black", facecolor=color, alpha=0.6
        )
        ax.add_patch(rect)
        ax.text(cx_mm, cy_mm, part.part_id[:12], ha="center", va="center", fontsize=5, wrap=True)

        # Balloon
        bcx = cx_mm + dw / 2 + 12
        bcy = cy_mm + dh / 2 + 12
        balloon = mpatches.Circle((bcx, bcy), radius=5, linewidth=1, edgecolor="black", facecolor="white")
        ax.add_patch(balloon)
        ax.text(bcx, bcy, str(item_no), ha="center", va="center", fontsize=6)
        ax.plot([cx_mm + dw / 2, bcx - 5], [cy_mm + dh / 2, bcy], "k-", linewidth=0.5)

    ax.set_xlim(0, A1_W)
    ax.set_ylim(0, A1_H)
    ax.set_title(f"Assembly Drawing — {session_id[:20]}", fontsize=12)
    ax.set_xlabel("mm")
    ax.set_ylabel("mm")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_png = tmp.name
    fig.savefig(tmp_png, dpi=150, bbox_inches="tight")
    plt.close(fig)

    # Embed PNG into PDF
    page_w, page_h = landscape(A3)
    c = rl_canvas.Canvas(pdf_path, pagesize=landscape(A3))
    c.drawImage(tmp_png, 0, 0, width=page_w, height=page_h, preserveAspectRatio=True)

    today = date.today().strftime("%Y-%m-%d")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20, page_h - 20, f"Assembly Drawing — Session: {session_id[:20]}")
    c.setFont("Helvetica", 8)
    c.drawString(20, page_h - 32, f"Date: {today}  |  Scale: 1:50  |  Rev A  |  Parts: {len(parts)}")

    c.save()

    try:
        os.unlink(tmp_png)
    except OSError:
        pass

    logger.info("Assembly PDF saved: %s", pdf_path)


def _generate_minimal_assembly_pdf(parts: list[PartRecord], session_id: str, pdf_path: str) -> None:
    """Minimal text-based assembly PDF fallback."""
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.lib.pagesizes import A3, landscape

    page_w, page_h = landscape(A3)
    c = rl_canvas.Canvas(pdf_path, pagesize=landscape(A3))
    today = date.today().strftime("%Y-%m-%d")

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, page_h - 40, f"Assembly Drawing — {session_id[:20]}")
    c.setFont("Helvetica", 10)
    c.drawString(40, page_h - 58, f"Date: {today}  |  Total Parts: {len(parts)}")

    y = page_h - 100
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, f"{'Item':<6} {'Part No':<20} {'Name':<30} {'Qty':<5} {'Material'}")
    y -= 16
    c.setLineWidth(0.5)
    c.line(40, y + 10, page_w - 40, y + 10)

    c.setFont("Helvetica", 8)
    for idx, part in enumerate(parts[:40], start=1):
        mat = part.material or part.material_code or "N/A"
        line = f"{idx:<6} {part.part_id:<20} {part.part_name[:28]:<30} {part.quantity:<5} {mat}"
        c.drawString(40, y, line)
        y -= 12
        if y < 40:
            c.showPage()
            y = page_h - 60
            c.setFont("Helvetica", 8)

    c.save()
    logger.info("Assembly PDF (minimal fallback) saved: %s", pdf_path)
