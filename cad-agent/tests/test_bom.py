"""
Tests for BOM generation.
"""
import os
import tempfile

import pandas as pd
import pytest

from app.models import PartRecord
from app.bom_generator import generate_bom


def make_test_parts():
    return [
        PartRecord(
            part_id="AV-001",
            part_name="Hull Panel",
            part_type="SHEET_METAL",
            quantity=2,
            material="ARMOX 500T",
            material_code="ARMOX-500T",
            mass_kg=24.3,
            bom_level=1,
            has_bends=True,
            bend_count=2,
        ),
        PartRecord(
            part_id="AV-002",
            part_name="Mounting Bracket",
            part_type="SOLID",
            quantity=4,
            material=None,
            material_inferred=True,
            low_confidence=True,
            mass_kg=1.2,
            bom_level=2,
        ),
    ]


def test_bom_generates_files():
    parts = make_test_parts()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path, csv_path = generate_bom(parts, "test-session-001", tmpdir)
        assert os.path.exists(xlsx_path)
        assert os.path.exists(csv_path)


def test_bom_row_count():
    parts = make_test_parts()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path, csv_path = generate_bom(parts, "test-session-002", tmpdir)
        df = pd.read_csv(csv_path)
        assert len(df) == 2


def test_bom_item_numbers_sequential():
    parts = make_test_parts()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path, csv_path = generate_bom(parts, "test-session-003", tmpdir)
        df = pd.read_csv(csv_path)
        assert list(df["Item No."]) == [1, 2]


def test_bom_flags_low_confidence():
    parts = make_test_parts()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path, csv_path = generate_bom(parts, "test-session-004", tmpdir)
        df = pd.read_csv(csv_path)
        flags_col = df["Flags"].fillna("").tolist()
        # AV-002 is low_confidence + material_inferred
        av002_flags = flags_col[1]
        assert "LOW_CONFIDENCE" in av002_flags
        assert "INFERRED" in av002_flags


def test_bom_inferred_material_label():
    parts = make_test_parts()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path, csv_path = generate_bom(parts, "test-session-005", tmpdir)
        df = pd.read_csv(csv_path)
        # AV-002 has no material but is_inferred — cell should be empty or "(INFERRED)"
        # Since material is None, cell will be empty
        mat_val = str(df.loc[df["Part Number"] == "AV-002", "Material"].values[0])
        # Either empty string or contains INFERRED
        assert mat_val == "" or "INFERRED" in mat_val or mat_val == "nan"


def test_bom_xlsx_exists_and_valid():
    import openpyxl
    parts = make_test_parts()
    with tempfile.TemporaryDirectory() as tmpdir:
        xlsx_path, _ = generate_bom(parts, "test-session-006", tmpdir)
        wb = openpyxl.load_workbook(xlsx_path)
        assert "BOM" in wb.sheetnames
        ws = wb["BOM"]
        # Header row
        assert ws.cell(row=1, column=1).value == "Item No."
