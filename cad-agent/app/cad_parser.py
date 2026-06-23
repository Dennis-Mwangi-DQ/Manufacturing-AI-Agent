"""
CAD geometry extraction module.

Supports:
  - DXF flat patterns via ezdxf (one file == one part).
  - STEP / IGES via pythonocc-core, when installed.

Design principle: NEVER fabricate data. If a file cannot be truly analysed
(e.g. pythonocc-core is missing, or the format is a native SolidWorks file),
the parser raises a clear, user-facing error instead of inventing parts.
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_PARTS = 200

# Entity types that are annotation/markup, not manufacturable geometry.
_ANNOTATION_ENTITY_TYPES = {
    "TEXT", "MTEXT", "ATTDEF", "ATTRIB", "DIMENSION", "LEADER", "MLEADER",
    "MULTILEADER", "HATCH", "WIPEOUT", "IMAGE",
}

# Layer-name keywords that indicate annotation/drafting layers (case-insensitive
# substring match). Geometry on these layers is ignored for part extraction.
_ANNOTATION_LAYER_KEYWORDS = (
    "dim", "dimension", "note", "annotation", "annot", "text", "title",
    "border", "frame", "schematic", "label", "hatch", "centerline",
    "center line", "centre", "hidden", "construction", "sketch",
)

# Layer-name keywords that indicate a sheet-metal bend line.
_BEND_LAYER_KEYWORDS = ("bend",)


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(file_path: str) -> str:
    """Return 'STEP', 'IGES', or 'DXF' based on file extension.

    Raises a descriptive ValueError for native SolidWorks files and any other
    unsupported extension. We never silently accept a file we cannot parse.
    """
    ext = Path(file_path).suffix.lower()
    if ext in (".stp", ".step"):
        return "STEP"
    if ext in (".igs", ".iges"):
        return "IGES"
    if ext == ".dxf":
        return "DXF"
    if ext in (".sldprt", ".sldasm", ".slddrw"):
        raise ValueError(
            f"Native SolidWorks files ('{ext}') cannot be parsed directly. "
            "Please export the part/assembly to STEP (.step) or a flat-pattern "
            "DXF (.dxf) and upload that instead."
        )
    raise ValueError(
        f"Unsupported file format: '{ext}'. Accepted formats: "
        ".step/.stp, .iges/.igs, .dxf"
    )


# ---------------------------------------------------------------------------
# Filename metadata extraction (client convention)
# ---------------------------------------------------------------------------

def parse_filename_metadata(file_path: str) -> dict:
    """Extract metadata encoded in the export filename.

    The client convention (seen in sample_data) encodes a material/thickness
    code, a quantity, and a part number, e.g.:

        M4_Q1-T1B6-12C501-DXF-1.DXF
          ^   ^   ^------------ part number (T1B6-12C501)
          |   +---------------- quantity (Q1 -> 1)
          +-------------------- material letter (M) + thickness (4 mm)

    Values are best-effort and flagged as filename-derived by the caller. Any
    field that cannot be confidently parsed is returned as None.
    """
    stem = Path(file_path).stem
    meta: dict = {
        "material_letter": None,
        "thickness_mm": None,
        "quantity": None,
        "part_number": None,
        "raw_code": None,
    }

    # Leading material/thickness code, e.g. "M4_", "B6.5_", "M8.5_".
    code_match = re.match(r"^([A-Za-z]+)(\d+(?:\.\d+)?)_", stem)
    if code_match:
        meta["material_letter"] = code_match.group(1).upper()
        meta["raw_code"] = code_match.group(1).upper() + code_match.group(2)
        try:
            thickness = float(code_match.group(2))
            # Sanity range for sheet-metal thickness in mm.
            if 0.1 <= thickness <= 60.0:
                meta["thickness_mm"] = thickness
        except ValueError:
            pass

    # Quantity, e.g. "_Q1-", "Q2".
    qty_match = re.search(r"[_\-]?Q(\d+)\b", stem)
    if qty_match:
        try:
            meta["quantity"] = int(qty_match.group(1))
        except ValueError:
            pass

    # Part number: a code like T1B6-12C501 (letter/number blocks). Prefer a
    # token that contains a hyphen and digits and is not the DXF suffix.
    pn_match = re.search(r"([A-Za-z]\d[A-Za-z]?\d?-\d+[A-Za-z]?\d+)", stem)
    if pn_match:
        meta["part_number"] = pn_match.group(1).upper()

    return meta


# ---------------------------------------------------------------------------
# Sheet metal / bend helpers
# ---------------------------------------------------------------------------

def detect_sheet_metal(part_dict: dict) -> tuple[bool, Optional[float]]:
    """Return (is_sheet_metal, thickness_mm).

    A part is treated as sheet metal when it has a known thickness that is
    small relative to its in-plane size, or when it came from a 2D DXF flat
    pattern. Thickness is only reported when actually known (never invented).
    """
    thickness = part_dict.get("thickness_mm")
    file_format = part_dict.get("file_format")

    if file_format == "DXF":
        # A DXF flat pattern is, by definition, a sheet-metal flat profile.
        return True, thickness

    bb = part_dict.get("bounding_box")
    if not bb:
        return False, thickness

    dims = [bb.get("L"), bb.get("W"), bb.get("H")]
    dims = [d for d in dims if d is not None]
    if not dims:
        return False, thickness
    min_dim = min(dims)
    if min_dim < 30.0:
        return True, round(min_dim, 3)
    return False, thickness


def extract_bends_from_geometry(part_dict: dict) -> list[dict]:
    """Extract bend features that are actually present in the geometry.

    For DXF flat patterns, bends are represented as lines on a bend-named
    layer. We report those bends but do NOT invent angle/radius/direction when
    they are not encoded; unknown values are left as None for honest downstream
    handling.
    """
    bends: list[dict] = []
    for raw in part_dict.get("bend_lines", []):
        bends.append({
            "angle_deg": raw.get("angle_deg"),
            "radius_mm": raw.get("radius_mm"),
            "direction": raw.get("direction", "UNKNOWN"),
        })
    return bends


# ---------------------------------------------------------------------------
# Geometry math
# ---------------------------------------------------------------------------

def _polygon_area(points: list[tuple[float, float]]) -> float:
    """Return the absolute area of a polygon via the shoelace formula."""
    n = len(points)
    if n < 3:
        return 0.0
    area2 = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
    return abs(area2) / 2.0


def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _stitch_loops(
    open_paths: list[list[tuple[float, float]]],
    tol: float,
) -> list[list[tuple[float, float]]]:
    """Stitch disconnected edge segments into closed loops by matching endpoints.

    SolidWorks flat-pattern DXFs draw a profile as many separate LINE/ARC
    entities rather than a single closed polyline. This greedily chains
    segments whose endpoints coincide (within `tol`) until they close, so the
    enclosed area can be measured. Segments that never close are discarded.
    """
    loops: list[list[tuple[float, float]]] = []
    remaining = [list(p) for p in open_paths if len(p) >= 2]

    while remaining:
        chain = remaining.pop(0)
        progress = True
        while progress:
            if len(chain) >= 3 and _dist(chain[0], chain[-1]) <= tol:
                break
            progress = False
            for i, seg in enumerate(remaining):
                s, e = seg[0], seg[-1]
                if _dist(chain[-1], s) <= tol:
                    chain.extend(seg[1:])
                elif _dist(chain[-1], e) <= tol:
                    chain.extend(list(reversed(seg))[1:])
                elif _dist(chain[0], e) <= tol:
                    chain = seg[:-1] + chain
                elif _dist(chain[0], s) <= tol:
                    chain = list(reversed(seg))[:-1] + chain
                else:
                    continue
                remaining.pop(i)
                progress = True
                break
        if len(chain) >= 3 and _dist(chain[0], chain[-1]) <= tol:
            loops.append(chain)

    return loops


def _profile_area_from_geometry(
    closed_loops: list[list[tuple[float, float]]],
    open_paths: list[list[tuple[float, float]]],
    circles: list[dict],
    extent: float,
) -> Optional[float]:
    """Compute net sheet area: outer contour minus interior cut-outs and holes.

    Returns None when no closed boundary can be determined (honest — no guess).
    """
    tol = max(0.05, extent * 0.0005)
    loops = list(closed_loops) + _stitch_loops(open_paths, tol)
    areas = [_polygon_area(lp) for lp in loops]
    areas = [a for a in areas if a > 0]
    if not areas:
        return None

    outer = max(areas)
    inner = sum(a for a in areas if a < outer)  # cut-outs / interior loops
    hole_area = sum(math.pi * c["radius"] ** 2 for c in circles)
    net = outer - inner - hole_area
    if net <= 0:
        return None
    return round(net, 3)


def _entity_points(ent, etype: str) -> Optional[list[tuple[float, float]]]:
    """Return ordered (x, y) points approximating an entity, or None."""
    try:
        if etype == "LINE":
            return [(ent.dxf.start.x, ent.dxf.start.y), (ent.dxf.end.x, ent.dxf.end.y)]
        # ARC / ELLIPSE / SPLINE / LWPOLYLINE support flattening to points.
        pts = [(p.x, p.y) for p in ent.flattening(0.1)]
        return pts if len(pts) >= 2 else None
    except Exception:
        return None


def _layer_is_annotation(layer_name: str) -> bool:
    name = (layer_name or "").lower()
    return any(kw in name for kw in _ANNOTATION_LAYER_KEYWORDS)


def _layer_is_bend(layer_name: str) -> bool:
    name = (layer_name or "").lower()
    return any(kw in name for kw in _BEND_LAYER_KEYWORDS)


def _parse_bend_layer_name(layer_name: str) -> dict:
    """Best-effort parse of bend parameters from a SolidWorks bend layer name.

    SolidWorks flat-pattern DXF exports often name bend layers like
    "Bends - Up" / "Bends - Down" and sometimes embed an angle/radius. Any
    value not present is returned as None (never fabricated).
    """
    name = (layer_name or "").lower()
    direction = "UNKNOWN"
    if "up" in name:
        direction = "UP"
    elif "down" in name:
        direction = "DOWN"

    angle = None
    angle_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:deg|°)", name)
    if angle_match:
        try:
            angle = float(angle_match.group(1))
        except ValueError:
            angle = None

    radius = None
    radius_match = re.search(r"r\s*(\d+(?:\.\d+)?)", name)
    if radius_match:
        try:
            radius = float(radius_match.group(1))
        except ValueError:
            radius = None

    return {"angle_deg": angle, "radius_mm": radius, "direction": direction}


# ---------------------------------------------------------------------------
# DXF parsing (one file == one part)
# ---------------------------------------------------------------------------

def _parse_dxf(file_path: str) -> list[dict]:
    """Parse a DXF flat pattern and return a single part dict.

    Annotation/drafting layers and text/dimension entities are excluded from
    the manufacturable profile. Geometry is read for the real bounding box,
    profile area, holes and bend lines.
    """
    import ezdxf  # type: ignore

    try:
        doc = ezdxf.readfile(file_path)
    except Exception as exc:
        raise ValueError(f"Failed to read DXF file: {exc}") from exc

    msp = doc.modelspace()

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    geometry_entities = 0
    closed_polylines: list[list[tuple[float, float]]] = []
    open_paths: list[list[tuple[float, float]]] = []
    circles: list[dict] = []
    bend_lines: list[dict] = []
    text_metadata: dict[str, str] = {}

    def _grow(x: float, y: float) -> None:
        nonlocal min_x, min_y, max_x, max_y
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)

    for ent in msp:
        etype = ent.dxftype()
        layer = getattr(ent.dxf, "layer", "0")

        # Collect text key/value metadata regardless of layer.
        if etype in ("TEXT", "MTEXT", "ATTDEF", "ATTRIB"):
            text_val = getattr(ent.dxf, "text", "") or ""
            if "=" in text_val:
                k, _, v = text_val.partition("=")
                text_metadata[k.strip()] = v.strip()
            continue

        # Skip annotation entity types and annotation layers for geometry.
        if etype in _ANNOTATION_ENTITY_TYPES:
            continue
        if _layer_is_annotation(layer):
            continue

        # Bend lines: capture separately, do not count as profile geometry.
        if _layer_is_bend(layer) and etype in ("LINE", "LWPOLYLINE", "POLYLINE"):
            bend_lines.append(_parse_bend_layer_name(layer))
            continue

        if etype == "LINE":
            geometry_entities += 1
            pts = [(ent.dxf.start.x, ent.dxf.start.y), (ent.dxf.end.x, ent.dxf.end.y)]
            for x, y in pts:
                _grow(x, y)
            open_paths.append(pts)

        elif etype == "ARC":
            geometry_entities += 1
            cx, cy = ent.dxf.center.x, ent.dxf.center.y
            r = ent.dxf.radius
            _grow(cx - r, cy - r)
            _grow(cx + r, cy + r)
            pts = _entity_points(ent, "ARC")
            if pts:
                open_paths.append(pts)

        elif etype == "CIRCLE":
            geometry_entities += 1
            cx, cy = ent.dxf.center.x, ent.dxf.center.y
            r = ent.dxf.radius
            _grow(cx - r, cy - r)
            _grow(cx + r, cy + r)
            circles.append({"cx": cx, "cy": cy, "radius": r})

        elif etype == "LWPOLYLINE":
            geometry_entities += 1
            pts = _entity_points(ent, "LWPOLYLINE") or [(p[0], p[1]) for p in ent.get_points()]
            for x, y in pts:
                _grow(x, y)
            if getattr(ent, "closed", False) and len(pts) >= 3:
                closed_polylines.append(pts)
            else:
                open_paths.append(pts)

        elif etype == "POLYLINE":
            geometry_entities += 1
            pts = []
            for vertex in ent.vertices:
                px, py = vertex.dxf.location.x, vertex.dxf.location.y
                pts.append((px, py))
                _grow(px, py)
            if getattr(ent, "is_closed", False) and len(pts) >= 3:
                closed_polylines.append(pts)
            elif len(pts) >= 2:
                open_paths.append(pts)

        elif etype in ("SPLINE", "ELLIPSE"):
            geometry_entities += 1
            pts = _entity_points(ent, etype)
            if pts:
                for x, y in pts:
                    _grow(x, y)
                open_paths.append(pts)

    # Fall back to header extents if no geometry was collected from entities.
    if min_x == float("inf"):
        try:
            ext_min = doc.header.get("$EXTMIN")
            ext_max = doc.header.get("$EXTMAX")
            if ext_min and ext_max:
                min_x, min_y = ext_min[0], ext_min[1]
                max_x, max_y = ext_max[0], ext_max[1]
        except Exception:
            pass

    filename_meta = parse_filename_metadata(file_path)
    stem = Path(file_path).stem

    if min_x == float("inf") or geometry_entities == 0:
        # No manufacturable geometry could be read. Report honestly rather
        # than inventing a default box.
        logger.warning("DXF '%s' contains no manufacturable geometry.", file_path)
        return [{
            "part_id": filename_meta.get("part_number") or stem,
            "part_name": filename_meta.get("part_number") or stem,
            "file_format": "DXF",
            "shape_type": "UNKNOWN",
            "geometry_entities": 0,
            "surface_area_mm2": None,
            "volume_mm3": None,
            "bounding_box": None,
            "thickness_mm": filename_meta.get("thickness_mm"),
            "thickness_source": "filename" if filename_meta.get("thickness_mm") else None,
            "quantity": filename_meta.get("quantity") or 1,
            "parent_assembly": None,
            "bom_level": 0,
            "source_path": file_path,
            "metadata": text_metadata,
            "filename_meta": filename_meta,
            "bend_lines": [],
            "notes": "No manufacturable geometry detected in DXF. Manual review required.",
        }]

    L = round(max_x - min_x, 3)
    W = round(max_y - min_y, 3)

    # Real profile area: stitch loose LINE/ARC segments into closed loops, take
    # the outer contour and subtract interior cut-outs and circular holes.
    extent = max(L, W)
    profile_area = _profile_area_from_geometry(
        closed_polylines, open_paths, circles, extent
    )

    thickness = filename_meta.get("thickness_mm")
    thickness_source = "filename" if thickness is not None else None

    volume = None
    if profile_area is not None and thickness is not None:
        volume = round(profile_area * thickness, 3)

    part_id = filename_meta.get("part_number") or stem
    part_name = (
        text_metadata.get("PART_NO")
        or text_metadata.get("part_no")
        or filename_meta.get("part_number")
        or stem
    )

    notes_bits = []
    if thickness_source == "filename":
        notes_bits.append(
            f"Thickness {thickness} mm derived from filename code "
            f"'{filename_meta.get('raw_code')}' — verify against drawing."
        )
    if profile_area is None:
        notes_bits.append("Profile area unavailable (no closed contour found).")
    if bend_lines:
        notes_bits.append(f"{len(bend_lines)} bend line(s) detected on bend layer(s).")

    return [{
        "part_id": part_id,
        "part_name": part_name,
        "file_format": "DXF",
        "shape_type": "FLAT_PROFILE",
        "geometry_entities": geometry_entities,
        "surface_area_mm2": profile_area,
        "volume_mm3": volume,
        "bounding_box": {"L": L, "W": W, "H": thickness},
        "thickness_mm": thickness,
        "thickness_source": thickness_source,
        "quantity": filename_meta.get("quantity") or 1,
        "parent_assembly": None,
        "bom_level": 0,
        "source_path": file_path,
        "metadata": text_metadata,
        "filename_meta": filename_meta,
        "hole_count": len(circles),
        "circles": circles,
        "bend_lines": bend_lines,
        "notes": " ".join(notes_bits) if notes_bits else None,
    }]


# ---------------------------------------------------------------------------
# STEP / IGES parsing via pythonocc-core (REAL parsing only — no fabrication)
# ---------------------------------------------------------------------------

_OCC_MISSING_MSG = (
    "STEP/IGES parsing requires the 'pythonocc-core' package, which is not "
    "installed. Install it via conda ('conda install -c conda-forge "
    "pythonocc-core') or run the provided Docker image, or export the model "
    "to a flat-pattern DXF (.dxf) and upload that instead."
)


def _require_occ() -> None:
    """Raise a clear error if pythonocc-core is unavailable."""
    try:
        import OCC.Core  # type: ignore  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(_OCC_MISSING_MSG) from exc


def _parse_step_occ(file_path: str) -> list[dict]:
    """Parse a STEP file using pythonocc-core."""
    _require_occ()
    from OCC.Core.STEPControl import STEPControl_Reader  # type: ignore
    from OCC.Core.IFSelect import IFSelect_RetDone  # type: ignore
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore
    from OCC.Core.TopAbs import TopAbs_SOLID, TopAbs_SHELL  # type: ignore
    from OCC.Core.BRepBndLib import brepbndlib  # type: ignore
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
        parts.append(_solid_to_part_dict(solid, idx, file_path, brepbndlib, brepgprop, "STEP"))
        explorer.Next()

    if not parts:
        explorer2 = TopExp_Explorer(shape, TopAbs_SHELL)
        while explorer2.More():
            shell = explorer2.Current()
            idx += 1
            parts.append(_solid_to_part_dict(shell, idx, file_path, brepbndlib, brepgprop, "STEP"))
            explorer2.Next()

    if not parts:
        raise ValueError(
            f"No solids or shells found in STEP file '{Path(file_path).name}'. "
            "The file may be empty, surface-only, or corrupt."
        )

    return parts


def _parse_iges_occ(file_path: str) -> list[dict]:
    """Parse an IGES file using pythonocc-core."""
    _require_occ()
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
        parts.append(_solid_to_part_dict(solid, idx, file_path, brepbndlib, brepgprop, "IGES"))
        explorer.Next()

    if not parts:
        raise ValueError(
            f"No solids found in IGES file '{Path(file_path).name}'. "
            "The file may be empty, surface-only, or corrupt."
        )

    return parts


def _solid_to_part_dict(shape, idx: int, file_path: str, brepbndlib, brepgprop, file_format: str) -> dict:
    """Convert an OCC TopoDS shape to a raw part dict using real measurements."""
    from OCC.Core.Bnd import Bnd_Box  # type: ignore
    from OCC.Core.GProp import GProp_GProps  # type: ignore
    from OCC.Core.TopExp import TopExp_Explorer  # type: ignore
    from OCC.Core.TopAbs import TopAbs_FACE  # type: ignore

    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    L = round(abs(xmax - xmin), 3)
    W = round(abs(ymax - ymin), 3)
    H = round(abs(zmax - zmin), 3)

    props = GProp_GProps()
    try:
        brepgprop.VolumeProperties(shape, props)
        volume = abs(props.Mass())
    except Exception:
        volume = None

    props2 = GProp_GProps()
    try:
        brepgprop.SurfaceProperties(shape, props2)
        surface_area = abs(props2.Mass())
    except Exception:
        surface_area = None

    face_exp = TopExp_Explorer(shape, TopAbs_FACE)
    face_count = 0
    while face_exp.More():
        face_count += 1
        face_exp.Next()

    stem = Path(file_path).stem
    filename_meta = parse_filename_metadata(file_path)
    part_id = filename_meta.get("part_number") or f"{stem.upper()[:10]}-{idx:03d}"

    return {
        "part_id": part_id,
        "part_name": filename_meta.get("part_number") or f"{stem}_{idx:03d}",
        "file_format": file_format,
        "shape_type": "SOLID",
        "geometry_entities": face_count,
        "surface_area_mm2": round(surface_area, 3) if surface_area is not None else None,
        "volume_mm3": round(volume, 3) if volume is not None else None,
        "bounding_box": {"L": L, "W": W, "H": H},
        "thickness_mm": None,
        "thickness_source": None,
        "quantity": filename_meta.get("quantity") or 1,
        "parent_assembly": None,
        "bom_level": 0,
        "source_path": file_path,
        "metadata": {},
        "filename_meta": filename_meta,
        "bend_lines": [],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_cad_file(file_path: str) -> list[dict]:
    """Parse a STEP, IGES, or DXF file and return a list of raw part dicts.

    Raises a clear error when the file cannot be truly parsed. Never returns
    fabricated/synthetic parts.
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"CAD file not found: '{file_path}'")

    file_format = detect_format(file_path)
    logger.info("Parsing %s file: %s", file_format, file_path)

    if file_format == "DXF":
        raw_parts = _parse_dxf(file_path)
    elif file_format == "STEP":
        raw_parts = _parse_step_occ(file_path)
    elif file_format == "IGES":
        raw_parts = _parse_iges_occ(file_path)
    else:  # pragma: no cover - detect_format guards this
        raise ValueError(f"Unsupported format: {file_format}")

    if len(raw_parts) > MAX_PARTS:
        logger.warning(
            "Assembly has %d parts; truncating to %d. Some parts will be omitted.",
            len(raw_parts),
            MAX_PARTS,
        )
        raw_parts = raw_parts[:MAX_PARTS]

    logger.info("Extracted %d parts from %s", len(raw_parts), file_path)
    return raw_parts
