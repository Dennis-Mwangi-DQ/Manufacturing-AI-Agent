"""
Tests for DXF flat drawing generation.
"""
import os
import tempfile

import ezdxf
import pytest

from app.models import PartRecord
from app.dxf_generator import generate_dxf_flat


def make_sheet_metal_part():
    return PartRecord(
        part_id="AV-SM-001",
        part_name="Side Panel",
        part_type="SHEET_METAL",
        quantity=1,
        material="ARMOX 500T",
        material_code="ARMOX-500T",
        thickness_mm=8.0,
        mass_kg=24.3,
        bom_level=1,
        has_bends=True,
        bend_count=2,
        bounding_box={"L": 1200, "W": 650, "H": 8},
    )


def test_dxf_file_created():
    part = make_sheet_metal_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = generate_dxf_flat(part, tmpdir)
        assert os.path.exists(dxf_path)
        assert dxf_path.endswith(".dxf")


def test_dxf_has_required_layers():
    part = make_sheet_metal_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = generate_dxf_flat(part, tmpdir)
        doc = ezdxf.readfile(dxf_path)
        layer_names = [layer.dxf.name for layer in doc.layers]
        assert "0_OUTLINE" in layer_names
        assert "1_HOLES" in layer_names
        assert "2_BEND_LINES" in layer_names
        assert "3_ANNOTATIONS" in layer_names
        assert "4_TITLE_BLOCK" in layer_names


def test_dxf_opens_without_error():
    part = make_sheet_metal_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = generate_dxf_flat(part, tmpdir)
        doc = ezdxf.readfile(dxf_path)
        assert doc is not None


def test_dxf_filename_contains_part_id():
    part = make_sheet_metal_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = generate_dxf_flat(part, tmpdir)
        filename = os.path.basename(dxf_path)
        assert "AV-SM-001" in filename


def test_dxf_no_bends_still_generates():
    part = PartRecord(
        part_id="AV-SM-002",
        part_name="Floor Plate",
        part_type="SHEET_METAL",
        quantity=1,
        thickness_mm=20.0,
        bom_level=0,
        has_bends=False,
        bend_count=0,
        bounding_box={"L": 2000, "W": 1500, "H": 20},
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = generate_dxf_flat(part, tmpdir)
        assert os.path.exists(dxf_path)
        doc = ezdxf.readfile(dxf_path)
        assert doc is not None


def test_dxf_modelspace_has_entities():
    part = make_sheet_metal_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = generate_dxf_flat(part, tmpdir)
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        entities = list(msp)
        assert len(entities) > 0
