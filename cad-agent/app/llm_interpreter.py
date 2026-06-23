"""
LLM enrichment module — uses DeepSeek (via the OpenAI-compatible API) to enrich
raw part metadata.

Two-pass enrichment:
  1. Standard pass — classify part and suggest material from CAD metadata.
  2. Refinement pass — triggered when confidence < LLM_CONFIDENCE_THRESHOLD.
     Optionally includes Tavily web search results for material/part context.

Honesty rules enforced here:
  - Material is only assigned when the model is reasonably confident or a
    filename code provides a hint; otherwise it stays None and is flagged.
  - Bends come from geometry only — never padded to match an LLM claim.
  - Mass is only computed when both a real volume and a known density exist.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from app.config import get_settings
from app.models import BendRecord, PartRecord
from app.cad_parser import detect_sheet_metal, extract_bends_from_geometry
from app.web_search import (
    build_material_search_query,
    format_search_results,
    search_engineering_context,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a manufacturing engineering assistant for sheet-metal and machined "
    "components (vehicle bodies, brackets, panels, enclosures). You are given "
    "objective CAD metadata for ONE part. Classify the part and, ONLY when the "
    "evidence justifies it, suggest a likely material from the provided list.\n\n"
    "Rules you MUST follow:\n"
    "- Do NOT assume an 'armoured vehicle' or any specific end-use unless the "
    "metadata clearly indicates it.\n"
    "- If you cannot determine the material with reasonable confidence, return "
    "material_code = null and set a low confidence. Never guess a material just "
    "to fill the field.\n"
    "- If a filename-derived material/thickness hint is provided, you may use it, "
    "but mark material_inferred = true.\n"
    "- Base part_type on the geometry (a thin flat profile is SHEET_METAL).\n"
    "- Do not invent bends; bend detection is handled separately from geometry.\n"
    "Respond ONLY with valid JSON matching the schema."
)

RETRY_SYSTEM_PROMPT = (
    "You are a senior manufacturing engineer reviewing a low-confidence CAD part "
    "classification. Your first-pass analysis was uncertain. Re-evaluate using "
    "the additional context provided (including any web search excerpts).\n\n"
    "Rules you MUST follow:\n"
    "- Choose a material_code ONLY from the provided materials list when evidence "
    "supports it. If still uncertain, return material_code = null.\n"
    "- Raise confidence ONLY when you have concrete evidence (filename code, "
    "geometry type, thickness, or a credible web source).\n"
    "- Do not invent bends or dimensions.\n"
    "- If web search results are irrelevant, ignore them and say so in notes.\n"
    "Respond ONLY with valid JSON matching the schema."
)

USER_PROMPT_TEMPLATE = """Part metadata (objective, from the CAD file):
{part_metadata_json}

Filename-derived hints (may be null):
{filename_hints_json}

Available materials (code: name):
{materials_list}

Respond with JSON only:
{{
  "part_name": "human-readable name",
  "part_type": "SHEET_METAL|SOLID|ASSEMBLY|UNKNOWN",
  "material_code": "one of the material codes, or null if unsure",
  "material": "material name, or null",
  "material_inferred": true,
  "notes": "concise manufacturing notes, or null",
  "confidence": 0.0
}}"""

RETRY_USER_PROMPT_TEMPLATE = """This part received a LOW-CONFIDENCE first-pass classification.
Re-evaluate carefully using all evidence below.

Part metadata (objective, from the CAD file):
{part_metadata_json}

Filename-derived hints (may be null):
{filename_hints_json}

First-pass LLM result (low confidence — verify or correct):
{first_pass_json}

Web search context (may be empty):
{web_search_context}

Available materials (code: name):
{materials_list}

Respond with JSON only:
{{
  "part_name": "human-readable name",
  "part_type": "SHEET_METAL|SOLID|ASSEMBLY|UNKNOWN",
  "material_code": "one of the material codes, or null if unsure",
  "material": "material name, or null",
  "material_inferred": true,
  "notes": "concise manufacturing notes explaining your decision, or null",
  "confidence": 0.0
}}"""

# Conservative mapping from filename material letter to a default material code.
# Only includes letters whose meaning is well established for this client's
# exports. Ambiguous letters are intentionally omitted (no guessing).
_FILENAME_LETTER_TO_MATERIAL = {
    "M": "MILD-S275",
}


# ---------------------------------------------------------------------------
# Material helpers
# ---------------------------------------------------------------------------

def _lookup_material(material_code: Optional[str], materials_table: list[dict]) -> Optional[dict]:
    if not material_code:
        return None
    for mat in materials_table:
        if mat.get("code") == material_code:
            return mat
    return None


def _material_density(material_code: Optional[str], materials_table: list[dict]) -> Optional[float]:
    """Return density in g/cm3 for a code, or None when unknown (no default)."""
    mat = _lookup_material(material_code, materials_table)
    if mat and mat.get("density_g_cm3") is not None:
        return float(mat["density_g_cm3"])
    return None


def _material_k_factor(material_code: Optional[str], materials_table: list[dict], default: float) -> float:
    mat = _lookup_material(material_code, materials_table)
    if mat and mat.get("k_factor") is not None:
        return float(mat["k_factor"])
    return default


def _compute_mass(volume_mm3: Optional[float], density_g_cm3: Optional[float]) -> Optional[float]:
    """kg = (mm3 / 1000 -> cm3) * (g/cm3) / 1000. None when inputs unknown."""
    if volume_mm3 is None or volume_mm3 <= 0 or density_g_cm3 is None:
        return None
    return round((volume_mm3 / 1000.0) * density_g_cm3 / 1000.0, 4)


def _resolve_material(
    llm_data: Optional[dict],
    raw: dict,
    materials_table: list[dict],
) -> tuple[Optional[str], Optional[str], bool, Optional[str]]:
    """Resolve (material_code, material_name, inferred, note).

    Priority: a confident LLM choice, then a filename letter hint, else unknown.
    Never invents an arbitrary material.
    """
    # 1. LLM-provided material that exists in the table.
    if llm_data:
        code = llm_data.get("material_code")
        mat = _lookup_material(code, materials_table)
        if mat:
            inferred = bool(llm_data.get("material_inferred", False))
            return mat["code"], mat["name"], inferred, None

    # 2. Filename material letter hint.
    fmeta = raw.get("filename_meta") or {}
    letter = fmeta.get("material_letter")
    code = _FILENAME_LETTER_TO_MATERIAL.get(letter) if letter else None
    mat = _lookup_material(code, materials_table)
    if mat:
        note = (
            f"Material inferred from filename code '{fmeta.get('raw_code')}' "
            "— verify against drawing/specification."
        )
        return mat["code"], mat["name"], True, note

    # 3. Unknown — do not guess.
    return None, None, False, None


def _confidence_from_llm_data(llm_data: Optional[dict]) -> float:
    if not llm_data:
        return 0.0
    try:
        return float(llm_data.get("confidence", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _should_run_second_pass(llm_data: dict, threshold: float) -> bool:
    return _confidence_from_llm_data(llm_data) < threshold


def _pick_better_llm_result(first: dict, second: dict) -> tuple[dict, bool]:
    """
    Choose the better of two LLM responses.

    Prefers higher confidence; on a tie, prefers the response that resolved
  a material_code present in the first result's uncertainty.
    """
    first_conf = _confidence_from_llm_data(first)
    second_conf = _confidence_from_llm_data(second)

    if second_conf > first_conf:
        return second, True
    if second_conf < first_conf:
        return first, False

    # Tie-break: prefer second pass if it found a material the first pass missed.
    if not first.get("material_code") and second.get("material_code"):
        return second, True
    return first, False


# ---------------------------------------------------------------------------
# Bend record construction (geometry-driven, never padded)
# ---------------------------------------------------------------------------

def _build_bend_records(
    part_id: str,
    raw_bends: list[dict],
    thickness_mm: Optional[float],
    k_factor: float,
) -> list[BendRecord]:
    from app.bending_calculator import compute_bend_allowance  # avoid circular import

    records: list[BendRecord] = []
    for i, rb in enumerate(raw_bends):
        angle = rb.get("angle_deg")
        radius = rb.get("radius_mm")
        direction = rb.get("direction") or "UNKNOWN"

        bend_allowance = None
        if angle is not None and radius is not None and thickness_mm is not None:
            bend_allowance = round(
                compute_bend_allowance(angle, radius, thickness_mm, k_factor), 4
            )

        records.append(
            BendRecord(
                bend_id=i + 1,
                part_id=part_id,
                angle_deg=angle,
                radius_mm=radius,
                direction=direction,
                k_factor=k_factor if bend_allowance is not None else None,
                bend_allowance_mm=bend_allowance,
                segment_before_mm=None,
                segment_after_mm=None,
            )
        )
    return records


# ---------------------------------------------------------------------------
# Prompt building / response parsing
# ---------------------------------------------------------------------------

def _part_prompt_context(part: dict) -> tuple[str, str]:
    safe_part = {
        k: v for k, v in part.items()
        if k not in ("bend_lines", "circles", "filename_meta")
    }
    part_json = json.dumps(safe_part, indent=2, default=str)
    hints = part.get("filename_meta") or {}
    hints_json = json.dumps(hints, indent=2, default=str)
    return part_json, hints_json


def build_llm_prompt(part: dict, materials_table: list[dict]) -> str:
    part_json, hints_json = _part_prompt_context(part)
    mat_list = "\n".join(f"{m['code']}: {m['name']}" for m in materials_table)
    return USER_PROMPT_TEMPLATE.format(
        part_metadata_json=part_json,
        filename_hints_json=hints_json,
        materials_list=mat_list,
    )


def build_retry_llm_prompt(
    part: dict,
    materials_table: list[dict],
    first_pass: dict,
    search_results: list[dict],
) -> str:
    part_json, hints_json = _part_prompt_context(part)
    mat_list = "\n".join(f"{m['code']}: {m['name']}" for m in materials_table)
    return RETRY_USER_PROMPT_TEMPLATE.format(
        part_metadata_json=part_json,
        filename_hints_json=hints_json,
        first_pass_json=json.dumps(first_pass, indent=2, default=str),
        web_search_context=format_search_results(search_results),
        materials_list=mat_list,
    )


def _parse_llm_response(text: str) -> Optional[dict]:
    text = (text or "").strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _invoke_llm_for_json(llm, messages: list, part_id: str, label: str) -> Optional[dict]:
    """Call the LLM up to 3 times and return parsed JSON, or None."""
    for attempt in range(3):
        try:
            response = llm.invoke(messages)
            llm_data = _parse_llm_response(response.content)
            if llm_data:
                return llm_data
            logger.warning(
                "LLM returned unparseable JSON for part %s (%s, attempt %d)",
                part_id, label, attempt + 1,
            )
        except Exception as exc:
            logger.warning(
                "LLM call failed for part %s (%s, attempt %d): %s",
                part_id, label, attempt + 1, exc,
            )
    return None


def _run_refinement_pass(
    raw: dict,
    materials_table: list[dict],
    settings,
    llm,
    first_pass: dict,
) -> tuple[Optional[dict], bool, bool]:
    """
    Second LLM pass with optional web search.

    Returns (llm_data, used_second_pass, used_web_search).
    """
    from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

    search_results: list[dict] = []
    used_web_search = False

    if settings.ENABLE_WEB_SEARCH and settings.TAVILY_API_KEY:
        query = build_material_search_query(raw, first_pass)
        search_results = search_engineering_context(
            query,
            settings.TAVILY_API_KEY,
            max_results=3,
        )
        used_web_search = bool(search_results)
        if search_results:
            logger.info(
                "Web search returned %d results for part %s",
                len(search_results),
                raw.get("part_id"),
            )

    retry_prompt = build_retry_llm_prompt(raw, materials_table, first_pass, search_results)
    messages = [
        SystemMessage(content=RETRY_SYSTEM_PROMPT),
        HumanMessage(content=retry_prompt),
    ]
    retry_data = _invoke_llm_for_json(
        llm,
        messages,
        str(raw.get("part_id", "UNKNOWN")),
        "refinement pass",
    )
    if retry_data is None:
        return first_pass, False, used_web_search

    chosen, second_was_better = _pick_better_llm_result(first_pass, retry_data)
    if second_was_better or chosen is retry_data:
        refinement_note = "Refinement pass applied"
        if used_web_search:
            refinement_note += " (web-assisted)"
        existing = chosen.get("notes")
        chosen["notes"] = f"{existing} | {refinement_note}" if existing else refinement_note
        return chosen, True, used_web_search

    return first_pass, True, used_web_search


# ---------------------------------------------------------------------------
# Per-part enrichment
# ---------------------------------------------------------------------------

def _build_part_record(
    raw: dict,
    materials_table: list[dict],
    settings,
    llm_data: Optional[dict],
) -> PartRecord:
    """Construct a PartRecord, merging objective geometry with optional LLM data."""
    is_sm, sm_thickness = detect_sheet_metal(raw)
    raw_bends = extract_bends_from_geometry(raw)

    # Part type: prefer geometry signal, then LLM.
    if is_sm:
        part_type = "SHEET_METAL"
    elif llm_data and llm_data.get("part_type"):
        part_type = llm_data["part_type"]
    else:
        part_type = raw.get("shape_type", "UNKNOWN")

    # Thickness: known geometry/filename value only (never fabricated).
    thickness = raw.get("thickness_mm")
    if thickness is None:
        thickness = sm_thickness

    material_code, material_name, material_inferred, material_note = _resolve_material(
        llm_data, raw, materials_table
    )

    density = _material_density(material_code, materials_table)
    mass = _compute_mass(raw.get("volume_mm3"), density)

    k_factor = _material_k_factor(material_code, materials_table, settings.DEFAULT_K_FACTOR)
    bends = _build_bend_records(raw.get("part_id", "P-001"), raw_bends, thickness, k_factor)

    threshold = settings.LLM_CONFIDENCE_THRESHOLD

    # Confidence handling.
    if llm_data is not None:
        confidence = _confidence_from_llm_data(llm_data)
    else:
        confidence = None
    low_confidence = (confidence is None) or (confidence < threshold)

    # Notes: combine geometry notes, material note, and LLM notes.
    note_parts = []
    if raw.get("notes"):
        note_parts.append(raw["notes"])
    if material_note:
        note_parts.append(material_note)
    if llm_data and llm_data.get("notes"):
        note_parts.append(str(llm_data["notes"]))
    if mass is None and raw.get("volume_mm3"):
        note_parts.append("Mass not computed: material/density unknown.")
    if mass is None and not raw.get("volume_mm3"):
        note_parts.append("Mass not computed: volume unavailable.")
    notes = " | ".join(note_parts) if note_parts else None

    part_name = raw.get("part_name") or raw.get("part_id", "UNKNOWN")
    if llm_data and llm_data.get("part_name"):
        part_name = llm_data["part_name"]

    return PartRecord(
        part_id=raw.get("part_id", "UNKNOWN"),
        part_name=part_name,
        part_type=part_type,
        quantity=int(raw.get("quantity", 1) or 1),
        material=material_name,
        material_code=material_code,
        thickness_mm=thickness,
        thickness_source=raw.get("thickness_source"),
        mass_kg=mass,
        volume_mm3=raw.get("volume_mm3"),
        surface_area_mm2=raw.get("surface_area_mm2"),
        bounding_box=raw.get("bounding_box"),
        parent_assembly=raw.get("parent_assembly"),
        bom_level=raw.get("bom_level", 0),
        source_path=raw.get("source_path"),
        has_bends=len(bends) > 0,
        bend_count=len(bends),
        bends=bends,
        llm_confidence=confidence,
        notes=notes,
        material_inferred=material_inferred,
        low_confidence=low_confidence,
    )


def _enrich_single_part_metadata_only(raw: dict, materials_table: list[dict], settings) -> PartRecord:
    """Enrich a part using geometry + filename only (no LLM)."""
    return _build_part_record(raw, materials_table, settings, llm_data=None)


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------

def enrich_parts(raw_parts: list[dict], materials_table: list[dict]) -> list[PartRecord]:
    """Enrich each raw part. Falls back to metadata-only mode without an LLM."""
    settings = get_settings()

    if not settings.DEEPSEEK_API_KEY:
        logger.warning("DEEPSEEK_API_KEY not set — running in metadata-only mode.")
        return [_enrich_single_part_metadata_only(p, materials_table, settings) for p in raw_parts]

    try:
        from langchain_openai import ChatOpenAI  # type: ignore

        llm = ChatOpenAI(
            model=settings.DEEPSEEK_MODEL,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            temperature=0,
            max_tokens=1024,
        )
    except ImportError as exc:
        logger.warning("LangChain OpenAI not available (%s). Metadata-only mode.", exc)
        return [_enrich_single_part_metadata_only(p, materials_table, settings) for p in raw_parts]

    results: list[PartRecord] = []
    for raw in raw_parts:
        results.append(_enrich_single_part_llm(raw, materials_table, settings, llm))
    return results


def _enrich_single_part_llm(raw: dict, materials_table: list[dict], settings, llm) -> PartRecord:
    """Call DeepSeek for a single part, with a refinement pass when confidence is low."""
    from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

    part_id = str(raw.get("part_id", "UNKNOWN"))
    user_prompt = build_llm_prompt(raw, materials_table)
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    llm_data = _invoke_llm_for_json(llm, messages, part_id, "first pass")

    if llm_data is None:
        logger.warning(
            "All LLM retries exhausted for part %s — using metadata-only result.",
            part_id,
        )
        part = _build_part_record(raw, materials_table, settings, llm_data=None)
        part.notes = (part.notes + " | " if part.notes else "") + "LLM enrichment unavailable"
        part.low_confidence = True
        return part

    if _should_run_second_pass(llm_data, settings.LLM_CONFIDENCE_THRESHOLD):
        logger.info(
            "Low confidence (%.2f) for part %s — running refinement pass",
            _confidence_from_llm_data(llm_data),
            part_id,
        )
        llm_data, _, _ = _run_refinement_pass(raw, materials_table, settings, llm, llm_data)

    return _build_part_record(raw, materials_table, settings, llm_data)
