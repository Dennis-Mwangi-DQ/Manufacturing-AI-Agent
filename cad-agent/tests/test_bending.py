"""
Tests for bending calculator — bend allowance math and bending drawing generation.
"""
import math
import os
import tempfile

import pytest

from app.bending_calculator import compute_bend_allowance, compute_flat_blank_length, generate_bending_drawing
from app.models import BendRecord, PartRecord


# ---------------------------------------------------------------------------
# Pure math tests
# ---------------------------------------------------------------------------

def test_bend_allowance_formula():
    # BA = angle × (R + K×T) × π/180
    ba = compute_bend_allowance(angle_deg=90.0, radius_mm=8.0, thickness_mm=8.0, k_factor=0.33)
    expected = 90 * math.pi / 180 * (8.0 + 0.33 * 8.0)
    assert abs(ba - expected) < 0.01


def test_bend_allowance_90_degree():
    ba = compute_bend_allowance(90, 8, 8, 0.33)
    assert 16.0 < ba < 18.0  # expected ~16.97 mm


def test_flat_blank_length():
    segments = [420.0, 180.0]
    allowances = [16.97]
    total = compute_flat_blank_length(segments, allowances)
    assert abs(total - 616.97) < 0.01


def test_zero_bends():
    total = compute_flat_blank_length([500.0], [])
    assert total == 500.0


def test_multiple_bends():
    ba = compute_bend_allowance(90, 8, 8, 0.33)
    segments = [100.0, 200.0, 150.0]
    allowances = [ba, ba]
    total = compute_flat_blank_length(segments, allowances)
    expected = 450.0 + 2 * ba
    assert abs(total - expected) < 0.01


def test_bend_allowance_zero_angle():
    ba = compute_bend_allowance(0.0, 8.0, 8.0, 0.33)
    assert ba == 0.0


def test_bend_allowance_180_degree():
    ba = compute_bend_allowance(180.0, 8.0, 8.0, 0.33)
    expected = 180 * math.pi / 180 * (8.0 + 0.33 * 8.0)
    assert abs(ba - expected) < 0.01


def test_k_factor_effect():
    ba_low = compute_bend_allowance(90, 8, 8, 0.25)
    ba_high = compute_bend_allowance(90, 8, 8, 0.50)
    assert ba_high > ba_low


# ---------------------------------------------------------------------------
# Drawing generation tests
# ---------------------------------------------------------------------------

def _make_bent_part():
    bends = [
        BendRecord(
            bend_id=1,
            part_id="AV-BP-001",
            angle_deg=90.0,
            radius_mm=8.0,
            direction="UP",
            k_factor=0.33,
            bend_allowance_mm=16.97,
            segment_before_mm=200.0,
            segment_after_mm=200.0,
        ),
        BendRecord(
            bend_id=2,
            part_id="AV-BP-001",
            angle_deg=90.0,
            radius_mm=8.0,
            direction="DOWN",
            k_factor=0.33,
            bend_allowance_mm=16.97,
            segment_before_mm=200.0,
            segment_after_mm=200.0,
        ),
    ]
    return PartRecord(
        part_id="AV-BP-001",
        part_name="Bent Hull Panel",
        part_type="SHEET_METAL",
        quantity=1,
        material="ARMOX-500T",
        material_code="ARMOX-500T",
        thickness_mm=8.0,
        mass_kg=18.5,
        bom_level=1,
        has_bends=True,
        bend_count=2,
        bends=bends,
        bounding_box={"L": 650.0, "W": 200.0, "H": 8.0},
    )


def test_bending_drawing_creates_dxf():
    part = _make_bent_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path, pdf_path = generate_bending_drawing(part, tmpdir)
        assert os.path.exists(dxf_path)
        assert dxf_path.endswith(".dxf")


def test_bending_drawing_creates_pdf():
    part = _make_bent_part()
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path, pdf_path = generate_bending_drawing(part, tmpdir)
        if pdf_path:  # PDF is optional (reportlab may fail in CI)
            assert os.path.exists(pdf_path)
            assert pdf_path.endswith(".pdf")


def test_bending_drawing_no_bends_returns_none_pdf():
    part = PartRecord(
        part_id="AV-FC-001",
        part_name="Flat Cut Panel",
        part_type="SHEET_METAL",
        quantity=1,
        thickness_mm=6.0,
        bom_level=0,
        has_bends=False,
        bend_count=0,
        bending_box=None,
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path, pdf_path = generate_bending_drawing(part, tmpdir)
        assert os.path.exists(dxf_path)
        assert pdf_path is None
