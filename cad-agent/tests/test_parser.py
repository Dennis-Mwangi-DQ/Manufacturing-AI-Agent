"""
Tests for the CAD parser — focused on the no-fabrication guarantee and real
DXF flat-pattern extraction.
"""
import tempfile
from pathlib import Path

import pytest

from app.cad_parser import (
    detect_format,
    parse_cad_file,
    parse_filename_metadata,
)
from app.config import get_settings
from app.llm_interpreter import _enrich_single_part_metadata_only

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DXF = (
    REPO_ROOT
    / "sample_data"
    / "SW CAD Template"
    / "T1B6-12500-DXF-1"
    / "12C500-RADIATOR MOUNT"
    / "M4_Q1-T1B6-12C501-DXF-1.DXF"
)


def _load_materials():
    import json
    mat_path = Path(__file__).resolve().parents[1] / "data" / "materials.json"
    with open(mat_path) as f:
        return json.load(f)["materials"]


# ---------------------------------------------------------------------------
# Format detection — must fail loudly, never fabricate
# ---------------------------------------------------------------------------

def test_detect_format_dxf():
    assert detect_format("part.dxf") == "DXF"
    assert detect_format("part.STEP") == "STEP"


def test_detect_format_solidworks_native_raises():
    with pytest.raises(ValueError) as exc:
        detect_format("assembly.SLDASM")
    assert "SolidWorks" in str(exc.value)


def test_detect_format_unknown_raises():
    with pytest.raises(ValueError):
        detect_format("model.xyz")


def test_step_without_pythonocc_fails_loudly():
    """A STEP upload must error clearly (never fabricate) when OCC is missing."""
    try:
        import OCC.Core  # type: ignore  # noqa: F401
        has_occ = True
    except ImportError:
        has_occ = False

    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        tmp.write(b"ISO-10303-21;\n")  # minimal, not valid -> must not fabricate
        tmp_path = tmp.name

    with pytest.raises((RuntimeError, ValueError)):
        parse_cad_file(tmp_path)
    # If OCC is missing the error is the explicit install message.
    if not has_occ:
        try:
            parse_cad_file(tmp_path)
        except RuntimeError as exc:
            assert "pythonocc-core" in str(exc)


# ---------------------------------------------------------------------------
# Filename metadata (client convention)
# ---------------------------------------------------------------------------

def test_parse_filename_metadata_basic():
    meta = parse_filename_metadata("M4_Q1-T1B6-12C501-DXF-1.DXF")
    assert meta["material_letter"] == "M"
    assert meta["thickness_mm"] == 4.0
    assert meta["quantity"] == 1
    assert meta["part_number"] == "T1B6-12C501"
    assert meta["engraving_name"] == "Q1-12C501"
    assert meta["revision"] == 1
    assert meta["sub_assembly_code"] == "12C500"
    assert meta["dxf_file_name"] == "M4_Q1-T1B6-12C501-DXF-1.DXF"


def test_parse_filename_metadata_assy_from_project_path():
    path = "/sample/T1B6-12500-DXF-1/12C500/M4_Q1-T1B6-12C501-DXF-1.DXF"
    meta = parse_filename_metadata(path)
    assert meta["assy_code"] == "12500"


def test_parse_filename_metadata_decimal_thickness():
    meta = parse_filename_metadata("B6.5_Q2-T1B6-12A511-DXF-1.DXF")
    assert meta["thickness_mm"] == 6.5
    assert meta["quantity"] == 2


def test_parse_filename_metadata_unstructured():
    meta = parse_filename_metadata("531_CAD_Drawing_Moore_Industries.dxf")
    # No client code present -> no fabricated thickness.
    assert meta["thickness_mm"] is None


# ---------------------------------------------------------------------------
# Real DXF flat-pattern extraction
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not SAMPLE_DXF.exists(), reason="sample DXF not available")
def test_real_dxf_single_part_not_split_by_layer():
    parts = parse_cad_file(str(SAMPLE_DXF))
    # One flat-pattern file == one part (the old bug produced one per layer).
    assert len(parts) == 1


@pytest.mark.skipif(not SAMPLE_DXF.exists(), reason="sample DXF not available")
def test_real_dxf_uses_filename_and_real_geometry():
    part = parse_cad_file(str(SAMPLE_DXF))[0]
    assert part["part_id"] == "T1B6-12C501"
    assert part["thickness_mm"] == 4.0
    assert part["thickness_source"] == "filename"
    assert part["quantity"] == 1
    bb = part["bounding_box"]
    assert bb["L"] > 100 and bb["W"] > 100  # real measured extents
    # No fabricated armour material/synthetic notes.
    assert "FALLBACK" not in (part.get("notes") or "")


@pytest.mark.skipif(not SAMPLE_DXF.exists(), reason="sample DXF not available")
def test_real_dxf_enrichment_is_honest():
    raw = parse_cad_file(str(SAMPLE_DXF))[0]
    materials = _load_materials()
    rec = _enrich_single_part_metadata_only(raw, materials, get_settings())
    assert rec.part_type == "SHEET_METAL"
    # Mass is only present when volume & density are both known.
    if rec.volume_mm3 is None:
        assert rec.mass_kg is None
    # Material is either unknown or an explicitly-inferred real grade.
    if rec.material_code is not None:
        assert rec.material_code in {m["code"] for m in materials}


@pytest.mark.skipif(not SAMPLE_DXF.exists(), reason="sample DXF not available")
def test_parse_cad_file_uses_original_filename_on_temp_path(tmp_path):
    """Uploads are saved as temp names; metadata must come from original_filename."""
    import shutil

    tmp_dxf = tmp_path / "tmpljmz7h67.dxf"
    shutil.copy(SAMPLE_DXF, tmp_dxf)
    part = parse_cad_file(
        str(tmp_dxf),
        original_filename="M4_Q1-T1B6-12C501-DXF-1.DXF",
    )[0]
    assert part["part_id"] == "T1B6-12C501"
    assert part["thickness_mm"] == 4.0
    assert part["thickness_source"] == "filename"
    assert part["quantity"] == 1
