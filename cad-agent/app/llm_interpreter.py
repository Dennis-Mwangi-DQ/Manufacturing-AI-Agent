"""
LLM enrichment module — uses DeepSeek (via the OpenAI-compatible API) to enrich
raw part metadata.

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

def build_llm_prompt(part: dict, materials_table: list[dict]) -> str:
    safe_part = {
        k: v for k, v in part.items()
        if k not in ("bend_lines", "circles", "filename_meta")
    }
    part_json = json.dumps(safe_part, indent=2, default=str)
    hints = part.get("filename_meta") or {}
    hints_json = json.dumps(hints, indent=2, default=str)
    mat_list = "\n".join(f"{m['code']}: {m['name']}" for m in materials_table)
    return USER_PROMPT_TEMPLATE.format(
        part_metadata_json=part_json,
        filename_hints_json=hints_json,
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

    # Confidence handling.
    if llm_data is not None:
        confidence = float(llm_data.get("confidence", 0.5))
    else:
        confidence = None
    low_confidence = (confidence is None) or (confidence < 0.6)

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
    """Call the LLM for a single part. Retries before falling back to metadata-only."""
    from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

    user_prompt = build_llm_prompt(raw, materials_table)
    messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    llm_data: Optional[dict] = None
    for attempt in range(3):
        try:
            response = llm.invoke(messages)
            llm_data = _parse_llm_response(response.content)
            if llm_data:
                break
            logger.warning(
                "LLM returned unparseable JSON for part %s (attempt %d)",
                raw.get("part_id"), attempt + 1,
            )
        except Exception as exc:
            logger.warning(
                "LLM call failed for part %s (attempt %d): %s",
                raw.get("part_id"), attempt + 1, exc,
            )

    if llm_data is None:
        logger.warning(
            "All LLM retries exhausted for part %s — using metadata-only result.",
            raw.get("part_id"),
        )
        part = _build_part_record(raw, materials_table, settings, llm_data=None)
        part.notes = (part.notes + " | " if part.notes else "") + "LLM enrichment unavailable"
        part.low_confidence = True
        return part

    return _build_part_record(raw, materials_table, settings, llm_data)
