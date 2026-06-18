"""
CAD geometry extraction module.
Supports STEP, IGES (via pythonocc-core if installed), and DXF (via ezdxf).
Falls back to synthetic demo data if pythonocc-core is unavailable.
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_PARTS = 200

# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(file_path: str) -> str:
    """Return 'STEP', 'IGES', or 'DXF' based on file extension."""
    ext = Path(file_path).suffix.lower()
    if ext in (".stp", ".step"):
        return "STEP"
    if ext in (".igs", ".iges"):
        return "IGES"
    if ext == ".dxf":
        return "DXF"
    raise ValueError(f"Unsupported file format: '{ext}'. Accepted: .stp, .step, .igs, .iges, .dxf")


# ---------------------------------------------------------------------------
# Sheet metal detection helper
# ---------------------------------------------------------------------------

def detect_sheet_metal(part_dict: dict) -> tuple[bool, Optional[float]]:
    """
    Returns (is_sheet_metal, thickness_mm).
    Heuristic: if the minimum dimension of the bounding box is < 30 mm, treat as sheet metal.
    """
    bb = part_dict.get("bounding_box")
    if not bb:
        return False, None
    dims = [bb.get("L", 9999), bb.get("W", 9999), bb.get("H", 9999)]
    min_dim = min(dims)
    if min_dim < 30.0:
        return True, round(min_dim, 3)
    return False, None


# ---------------------------------------------------------------------------
# Bend extraction helper
# ---------------------------------------------------------------------------

def extract_bends_from_geometry(part_dict: dict) -> list[dict]:
    """
    For sheet metal parts, attempt to extract bend features from geometry data.
    Returns list of raw bend dicts: {angle_deg, radius_mm, direction}.
    For DXF parts: looks for arc_entities list in part_dict.
    For STEP/IGES: uses cylindrical_faces list if present.
    Returns empty list when no bends are detectable (LLM will infer later).
    """
    bends: list[dict] = []

    # DXF path: arcs in modelspace
    arcs = part_dict.get("arc_entities", [])
    for i, arc in enumerate(arcs):
        radius = arc.get("radius", 8.0)
        sweep = arc.get("sweep_angle_deg", 90.0)
        if sweep < 5.0:
            continue  # too small to be a structural bend
        bends.append({
            "angle_deg": round(sweep, 2),
            "radius_mm": round(radius, 3),
            "direction": "UP" if i % 2 == 0 else "DOWN",
        })

    # STEP/IGES path: cylindrical face data
    cyl_faces = part_dict.get("cylindrical_faces", [])
    for i, face in enumerate(cyl_faces):
        radius = face.get("radius", 8.0)
        angle = face.get("angle_deg", 90.0)
        if angle < 5.0:
            continue
        bends.append({
            "angle_deg": round(angle, 2),
            "radius_mm": round(radius, 3),
            "direction": "UP" if i % 2 == 0 else "DOWN",
        })

    return bends


# ---------------------------------------------------------------------------
# DXF parsing (pure ezdxf — no fallback needed)
# ---------------------------------------------------------------------------

def _parse_dxf(file_path: str) -> list[dict]:
    """Parse a DXF file and return list of raw part dicts, one per layer group."""
    import ezdxf  # type: ignore

    try:
        doc = ezdxf.readfile(file_path)
    except Exception as exc:
        raise ValueError(f"Failed to read DXF file: {exc}") from exc

    msp = doc.modelspace()

    # Group entities by layer
    layers: dict[str, list] = {}
    for entity in msp:
        layer = entity.dxf.layer if hasattr(entity.dxf, "layer") else "0"
        layers.setdefault(layer, []).append(entity)

    if not layers:
        logger.warning("DXF file has no entities in modelspace.")
        # Return a single dummy part representing the file
        return [_dxf_empty_part(file_path)]

    parts: list[dict] = []
    for layer_name, entities in layers.items():
        part_dict = _build_part_from_dxf_layer(layer_name, entities, file_path)
        parts.append(part_dict)

    return parts


def _dxf_empty_part(file_path: str) -> dict:
    filename = Path(file_path).stem
    return {
        "part_id": f"DXF-001",
        "part_name": filename,
        "file_format": "DXF",
        "shape_type": "UNKNOWN",
        "face_count": 0,
        "surface_area_mm2": 0.0,
        "volume_mm3": 0.0,
        "bounding_box": {"L": 100.0, "W": 100.0, "H": 5.0},
        "thickness_mm": 5.0,
        "parent_assembly": None,
        "bom_level": 0,
        "metadata": {},
        "arc_entities": [],
    }


def _build_part_from_dxf_layer(layer_name: str, entities: list, file_path: str) -> dict:
    """Compute bounding box and characterise a DXF layer as a part."""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    line_count = 0
    arc_count = 0
    circle_count = 0
    text_metadata: dict[str, str] = {}
    arc_entities: list[dict] = []

    for ent in entities:
        etype = ent.dxftype()

        if etype == "LINE":
            line_count += 1
            for pt in [ent.dxf.start, ent.dxf.end]:
                min_x = min(min_x, pt.x)
                min_y = min(min_y, pt.y)
                max_x = max(max_x, pt.x)
                max_y = max(max_y, pt.y)

        elif etype == "ARC":
            arc_count += 1
            cx, cy = ent.dxf.center.x, ent.dxf.center.y
            r = ent.dxf.radius
            min_x = min(min_x, cx - r)
            min_y = min(min_y, cy - r)
            max_x = max(max_x, cx + r)
            max_y = max(max_y, cy + r)
            start_angle = ent.dxf.start_angle
            end_angle = ent.dxf.end_angle
            sweep = end_angle - start_angle
            if sweep < 0:
                sweep += 360.0
            arc_entities.append({"radius": r, "sweep_angle_deg": sweep})

        elif etype == "CIRCLE":
            circle_count += 1
            cx, cy = ent.dxf.center.x, ent.dxf.center.y
            r = ent.dxf.radius
            min_x = min(min_x, cx - r)
            min_y = min(min_y, cy - r)
            max_x = max(max_x, cx + r)
            max_y = max(max_y, cy + r)

        elif etype == "LWPOLYLINE":
            for pt in ent.get_points():
                min_x = min(min_x, pt[0])
                min_y = min(min_y, pt[1])
                max_x = max(max_x, pt[0])
                max_y = max(max_y, pt[1])
            line_count += 1

        elif etype == "POLYLINE":
            for vertex in ent.vertices:
                px, py = vertex.dxf.location.x, vertex.dxf.location.y
                min_x = min(min_x, px)
                min_y = min(min_y, py)
                max_x = max(max_x, px)
                max_y = max(max_y, py)
            line_count += 1

        elif etype in ("TEXT", "MTEXT", "ATTDEF"):
            text_val = getattr(ent.dxf, "text", "") or ""
            if "=" in text_val:
                k, _, v = text_val.partition("=")
                text_metadata[k.strip()] = v.strip()
            elif text_val:
                text_metadata[f"text_{len(text_metadata)}"] = text_val

    # Guard against empty geometry
    if min_x == float("inf"):
        min_x = min_y = 0.0
        max_x = max_y = 100.0

    L = round(max_x - min_x, 3)
    W = round(max_y - min_y, 3)
    # DXF is 2D — assume thin sheet metal; default thickness 6 mm
    H = 6.0

    total_entities = line_count + arc_count + circle_count
    face_count = total_entities  # approximate

    # Estimate surface area from bounding box
    surface_area = L * W

    # Sanitise layer name for part_id
    safe_layer = layer_name.replace(" ", "_").replace("/", "_")[:30]
    part_id = f"DXF-{safe_layer}"
    part_name = text_metadata.get("PART_NO", text_metadata.get("part_no", layer_name))

    return {
        "part_id": part_id,
        "part_name": part_name,
        "file_format": "DXF",
        "shape_type": "FLAT_PROFILE",
        "face_count": face_count,
        "surface_area_mm2": round(surface_area, 3),
        "volume_mm3": round(surface_area * H, 3),
        "bounding_box": {"L": L, "W": W, "H": H},
        "thickness_mm": H,
        "parent_assembly": None,
        "bom_level": 0,
        "metadata": text_metadata,
        "arc_entities": arc_entities,
        "line_count": line_count,
        "arc_count": arc_count,
        "circle_count": circle_count,
    }


# ---------------------------------------------------------------------------
# STEP / IGES parsing via pythonocc-core (with graceful fallback)
# ---------------------------------------------------------------------------

def _parse_step_occ(file_path: str) -> list[dict]:
    """Parse a STEP file using pythonocc-core."""
    from OCC.Core.STEPControl import STEPControl_Reader  # type: ignore
    from OCC.Core.IFSelect import IFSelect_RetDone  # type: ignore
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore
    from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL  # type: ignore
    from OCC.Core.BRep import BRep_Builder  # type: ignore
    from OCC.Core.TopoDS import TopoDS_Compound  # type: ignore
    from OCC.Core.Bnd import Bnd_Box  # type: ignore
    from OCC.Core.BRepBndLib import brepbndlib  # type: ignore
    from OCC.Core.GProp import GProp_GProps  # type: ignore
    from OCC.Core.BRepGProp import brepgprop  # type: ignore

    reader = STEPControl_Reader()
    status = reader.ReadFile(file_path)
    if status != IFSelect_RetDone:
        raise ValueError(f"STEPControl_Reader failed to read '{file_path}' (status={status})")

    reader.TransferRoots()
    shape = reader.OneShape()

    parts: list[dict] = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    idx = 0
    while explorer.More():
        solid = explorer.Current()
        idx += 1
        part_dict = _solid_to_part_dict(solid, idx, file_path, brepbndlib, brepgprop)
        parts.append(part_dict)
        explorer.Next()

    if not parts:
        # Try shells if no solids found
        explorer2 = TopExp_Explorer(shape, TopAbs_SHELL)
        while explorer2.More():
            shell = explorer2.Current()
            idx += 1
            part_dict = _solid_to_part_dict(shell, idx, file_path, brepbndlib, brepgprop)
            parts.append(part_dict)
            explorer2.Next()

    if not parts:
        logger.warning("No solids or shells found in STEP file '%s'. Creating single-part fallback.", file_path)
        parts = [_synthetic_single_part(file_path, "STEP")]

    return parts


def _solid_to_part_dict(shape, idx: int, file_path: str, brepbndlib, brepgprop) -> dict:
    """Convert an OCC TopoDS shape to a raw part dict."""
    from OCC.Core.Bnd import Bnd_Box  # type: ignore
    from OCC.Core.GProp import GProp_GProps  # type: ignore
    from OCC.Core.BRepGProp import brepgprop as _brepgprop  # type: ignore
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore
    from OCC.Core.TopAbs import TopAbs_FACE  # type: ignore

    # Bounding box
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    L = round(abs(xmax - xmin), 3)
    W = round(abs(ymax - ymin), 3)
    H = round(abs(zmax - zmin), 3)

    # Volume
    props = GProp_GProps()
    try:
        _brepgprop.VolumeProperties(shape, props)
        volume = abs(props.Mass())
    except Exception:
        volume = L * W * H

    # Surface area
    try:
        _brepgprop.SurfaceProperties(shape, props)
        surface_area = abs(props.Mass())
    except Exception:
        surface_area = 2 * (L * W + W * H + L * H)

    # Face count
    face_exp = TopExp_Explorer(shape, TopAbs_FACE)
    face_count = 0
    while face_exp.More():
        face_count += 1
        face_exp.Next()

    stem = Path(file_path).stem
    part_id = f"{stem.upper()[:10]}-{idx:03d}"

    return {
        "part_id": part_id,
        "part_name": f"Part_{idx:03d}",
        "file_format": "STEP",
        "shape_type": "SOLID",
        "face_count": face_count,
        "surface_area_mm2": round(surface_area, 3),
        "volume_mm3": round(volume, 3),
        "bounding_box": {"L": L, "W": W, "H": H},
        "thickness_mm": None,
        "parent_assembly": None,
        "bom_level": 0,
        "metadata": {},
        "arc_entities": [],
        "cylindrical_faces": [],
    }


def _parse_iges_occ(file_path: str) -> list[dict]:
    """Parse an IGES file using pythonocc-core."""
    from OCC.Core.IGESControl import IGESControl_Reader  # type: ignore
    from OCC.Core.IFSelect import IFSelect_RetDone  # type: ignore
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore
    from OCC.Core.TopAbs import TopAbs_SOLID  # type: ignore
    from OCC.Core.BRepBndLib import brepbndlib  # type: ignore
    from OCC.Core.BRepGProp import brepgprop  # type: ignore

    reader = IGESControl_Reader()
    status = reader.ReadFile(file_path)
    if status != IFSelect_RetDone:
        raise ValueError(f"IGESControl_Reader failed (status={status})")

    reader.TransferRoots()
    shape = reader.OneShape()

    parts: list[dict] = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    idx = 0
    while explorer.More():
        solid = explorer.Current()
        idx += 1
        part_dict = _solid_to_part_dict(solid, idx, file_path, brepbndlib, brepgprop)
        parts.append(part_dict)
        explorer.Next()

    if not parts:
        parts = [_synthetic_single_part(file_path, "IGES")]

    return parts


# ---------------------------------------------------------------------------
# Fallback synthetic data (when pythonocc-core is unavailable)
# ---------------------------------------------------------------------------

def _generate_fallback_parts(file_path: str, file_format: str) -> list[dict]:
    """
    Generate realistic synthetic part data from filename and file size.
    Used when pythonocc-core is not installed.
    """
    logger.warning(
        "[FALLBACK MODE] pythonocc-core not installed. Generating synthetic part data for '%s'. "
        "Install pythonocc-core via conda for real CAD parsing.",
        file_path,
    )
    file_size_bytes = os.path.getsize(file_path) if os.path.exists(file_path) else 100_000
    stem = Path(file_path).stem

    # Estimate part count from file size (rough heuristic: ~5 KB per part)
    estimated_parts = max(1, min(int(file_size_bytes / 5000), 20))

    parts = []
    armour_part_templates = [
        ("Hull_Front_Panel", 1200, 800, 12, "SHEET_METAL"),
        ("Hull_Rear_Panel", 1100, 750, 12, "SHEET_METAL"),
        ("Hull_Side_Panel_L", 2400, 800, 10, "SHEET_METAL"),
        ("Hull_Side_Panel_R", 2400, 800, 10, "SHEET_METAL"),
        ("Floor_Plate", 2200, 1600, 20, "SHEET_METAL"),
        ("Roof_Panel", 2200, 1500, 8, "SHEET_METAL"),
        ("Door_Panel_L", 800, 1200, 10, "SHEET_METAL"),
        ("Door_Panel_R", 800, 1200, 10, "SHEET_METAL"),
        ("Engine_Mounting_Bracket", 300, 200, 150, "SOLID"),
        ("Suspension_Arm", 450, 80, 80, "SOLID"),
        ("Wheel_Hub", 200, 200, 180, "SOLID"),
        ("Hatch_Assembly", 600, 600, 25, "ASSEMBLY"),
        ("Vision_Block_Frame", 250, 150, 15, "SHEET_METAL"),
        ("Antenna_Mount_Bracket", 180, 120, 8, "SHEET_METAL"),
        ("Towing_Hook_Weldment", 350, 150, 180, "SOLID"),
        ("Armour_Spall_Liner", 1800, 600, 18, "SHEET_METAL"),
        ("Exhaust_Heat_Shield", 600, 400, 3, "SHEET_METAL"),
        ("Air_Filter_Housing", 300, 250, 280, "SOLID"),
        ("Cooling_Fan_Guard", 400, 400, 5, "SHEET_METAL"),
        ("Fuel_Tank_Skid_Plate", 900, 600, 8, "SHEET_METAL"),
    ]

    for i in range(min(estimated_parts, len(armour_part_templates))):
        tpl = armour_part_templates[i]
        name, L, W, H, ptype = tpl
        volume = L * W * H
        surface_area = 2 * (L * W + W * H + L * H)
        is_sm = H < 30
        thickness = H if is_sm else None

        part_id = f"{stem.upper()[:8]}-{i+1:03d}"
        parts.append({
            "part_id": part_id,
            "part_name": name,
            "file_format": file_format,
            "shape_type": ptype,
            "face_count": 6 + (i * 4),
            "surface_area_mm2": round(surface_area, 3),
            "volume_mm3": round(volume, 3),
            "bounding_box": {"L": float(L), "W": float(W), "H": float(H)},
            "thickness_mm": float(thickness) if thickness else None,
            "parent_assembly": None if i < 2 else f"{stem.upper()[:8]}-001" if ptype != "ASSEMBLY" else None,
            "bom_level": 0 if i < 2 else 1,
            "metadata": {},
            "arc_entities": [],
            "cylindrical_faces": [],
            "notes": f"[FALLBACK MODE] Synthetic data — install pythonocc-core for real geometry",
        })

    return parts


def _synthetic_single_part(file_path: str, file_format: str) -> dict:
    stem = Path(file_path).stem
    return {
        "part_id": f"{stem.upper()[:10]}-001",
        "part_name": stem,
        "file_format": file_format,
        "shape_type": "UNKNOWN",
        "face_count": 6,
        "surface_area_mm2": 240000.0,
        "volume_mm3": 960000.0,
        "bounding_box": {"L": 400.0, "W": 300.0, "H": 8.0},
        "thickness_mm": 8.0,
        "parent_assembly": None,
        "bom_level": 0,
        "metadata": {},
        "arc_entities": [],
        "cylindrical_faces": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_cad_file(file_path: str) -> list[dict]:
    """
    Parse a STEP, IGES, or DXF file and return a list of raw part dicts.

    Each dict contains: part_id, part_name, file_format, shape_type,
    face_count, surface_area_mm2, volume_mm3, bounding_box (L/W/H),
    thickness_mm, parent_assembly, bom_level, metadata.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"CAD file not found: '{file_path}'")

    file_format = detect_format(file_path)
    logger.info("Parsing %s file: %s", file_format, file_path)

    if file_format == "DXF":
        raw_parts = _parse_dxf(file_path)
    elif file_format == "STEP":
        try:
            raw_parts = _parse_step_occ(file_path)
        except ImportError:
            raw_parts = _generate_fallback_parts(file_path, "STEP")
        except Exception as exc:
            logger.error("STEP parsing failed: %s. Using fallback.", exc)
            raw_parts = _generate_fallback_parts(file_path, "STEP")
    elif file_format == "IGES":
        try:
            raw_parts = _parse_iges_occ(file_path)
        except ImportError:
            raw_parts = _generate_fallback_parts(file_path, "IGES")
        except Exception as exc:
            logger.error("IGES parsing failed: %s. Using fallback.", exc)
            raw_parts = _generate_fallback_parts(file_path, "IGES")
    else:
        raise ValueError(f"Unsupported format: {file_format}")

    # Enforce 200-part limit
    if len(raw_parts) > MAX_PARTS:
        logger.warning(
            "Assembly has %d parts; truncating to %d. Some parts will be omitted.",
            len(raw_parts),
            MAX_PARTS,
        )
        raw_parts = raw_parts[:MAX_PARTS]

    logger.info("Extracted %d parts from %s", len(raw_parts), file_path)
    return raw_parts
