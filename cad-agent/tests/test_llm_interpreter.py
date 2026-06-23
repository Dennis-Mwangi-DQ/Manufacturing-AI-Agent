"""
Tests for LLM interpreter refinement pass and web search helpers.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.llm_interpreter import (
    _pick_better_llm_result,
    _should_run_second_pass,
    build_retry_llm_prompt,
    _enrich_single_part_llm,
)
from app.web_search import (
    build_material_search_query,
    format_search_results,
    search_engineering_context,
)


def test_should_run_second_pass_below_threshold():
    assert _should_run_second_pass({"confidence": 0.4}, 0.6) is True
    assert _should_run_second_pass({"confidence": 0.59}, 0.6) is True


def test_should_run_second_pass_at_or_above_threshold():
    assert _should_run_second_pass({"confidence": 0.6}, 0.6) is False
    assert _should_run_second_pass({"confidence": 0.9}, 0.6) is False


def test_pick_better_llm_result_prefers_higher_confidence():
    first = {"confidence": 0.4, "material_code": None}
    second = {"confidence": 0.75, "material_code": "MILD-S275"}
    chosen, used_second = _pick_better_llm_result(first, second)
    assert chosen is second
    assert used_second is True


def test_pick_better_llm_result_tie_breaks_on_material():
    first = {"confidence": 0.5, "material_code": None}
    second = {"confidence": 0.5, "material_code": "DC01"}
    chosen, used_second = _pick_better_llm_result(first, second)
    assert chosen is second
    assert used_second is True


def test_build_material_search_query_includes_part_context():
    raw = {
        "part_id": "T1B6-12C501",
        "part_name": "Radiator Mount",
        "thickness_mm": 4.0,
        "filename_meta": {"raw_code": "M4_Q1-T1B6-12C501-DXF-1", "part_number": "T1B6-12C501"},
    }
    query = build_material_search_query(raw, {"part_type": "SHEET_METAL"})
    assert "Radiator Mount" in query
    assert "T1B6-12C501" in query
    assert "4.0mm" in query
    assert "sheet metal" in query


def test_format_search_results_empty():
    assert format_search_results([]) == "No web search results available."


def test_format_search_results_with_hits():
    text = format_search_results([
        {"title": "S275 steel", "content": "Structural mild steel.", "url": "https://example.com"},
    ])
    assert "S275 steel" in text
    assert "https://example.com" in text


def test_search_engineering_context_no_api_key():
    assert search_engineering_context("test query", "") == []


@patch("httpx.post")
def test_search_engineering_context_parses_response(mock_post):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"title": "ARMOX 500T", "content": "High-hardness armour steel.", "url": "https://a.com"},
        ]
    }
    mock_post.return_value = mock_response

    hits = search_engineering_context("armour steel panel", "test-key", max_results=1)
    assert len(hits) == 1
    assert hits[0]["title"] == "ARMOX 500T"
    mock_post.assert_called_once()


def test_build_retry_llm_prompt_includes_first_pass_and_search():
    raw = {"part_id": "P-001", "part_name": "Bracket"}
    materials = [{"code": "MILD-S275", "name": "Structural mild steel S275"}]
    first_pass = {"confidence": 0.3, "material_code": None, "part_type": "UNKNOWN"}
    search = [{"title": "Bracket steel", "content": "Often S275.", "url": "https://x.com"}]

    prompt = build_retry_llm_prompt(raw, materials, first_pass, search)
    assert "LOW-CONFIDENCE" in prompt
    assert '"confidence": 0.3' in prompt
    assert "Bracket steel" in prompt
    assert "MILD-S275" in prompt


@patch("app.llm_interpreter._run_refinement_pass")
@patch("app.llm_interpreter._invoke_llm_for_json")
def test_enrich_single_part_llm_runs_refinement_on_low_confidence(mock_invoke, mock_refinement):
    mock_invoke.return_value = {
        "part_name": "Panel",
        "part_type": "SHEET_METAL",
        "material_code": None,
        "material": None,
        "material_inferred": False,
        "notes": None,
        "confidence": 0.35,
    }
    mock_refinement.return_value = (
        {
            "part_name": "Panel",
            "part_type": "SHEET_METAL",
            "material_code": "MILD-S275",
            "material": "Structural mild steel S275",
            "material_inferred": True,
            "notes": "Refinement pass applied",
            "confidence": 0.8,
        },
        True,
        True,
    )

    settings = Settings(
        DEEPSEEK_API_KEY="test",
        LLM_CONFIDENCE_THRESHOLD=0.6,
        DEFAULT_K_FACTOR=0.33,
    )
    raw = {
        "part_id": "P-001",
        "part_name": "Panel",
        "bounding_box": {"L": 200, "W": 100, "H": 4},
        "quantity": 1,
    }
    materials = [{"code": "MILD-S275", "name": "Structural mild steel S275", "density_g_cm3": 7.85, "k_factor": 0.38}]

    part = _enrich_single_part_llm(raw, materials, settings, llm=MagicMock())
    mock_refinement.assert_called_once()
    assert part.material_code == "MILD-S275"
    assert part.low_confidence is False


@patch("app.llm_interpreter._run_refinement_pass")
@patch("app.llm_interpreter._invoke_llm_for_json")
def test_enrich_single_part_llm_skips_refinement_on_high_confidence(mock_invoke, mock_refinement):
    mock_invoke.return_value = {
        "part_name": "Panel",
        "part_type": "SHEET_METAL",
        "material_code": "MILD-S275",
        "material": "Structural mild steel S275",
        "material_inferred": False,
        "notes": None,
        "confidence": 0.85,
    }

    settings = Settings(
        DEEPSEEK_API_KEY="test",
        LLM_CONFIDENCE_THRESHOLD=0.6,
        DEFAULT_K_FACTOR=0.33,
    )
    raw = {
        "part_id": "P-001",
        "part_name": "Panel",
        "bounding_box": {"L": 200, "W": 100, "H": 4},
        "quantity": 1,
    }
    materials = [{"code": "MILD-S275", "name": "Structural mild steel S275", "density_g_cm3": 7.85, "k_factor": 0.38}]

    part = _enrich_single_part_llm(raw, materials, settings, llm=MagicMock())
    mock_refinement.assert_not_called()
    assert part.material_code == "MILD-S275"
    assert part.low_confidence is False
