"""
LLM enrichment module — uses DeepSeek via LangChain to enrich raw part metadata.
"""
from __future__ import annotations

import json
import logging
import math
from typing import Optional

from app.config import get_settings
from app.models import BendRecord, PartRecord
from app.cad_parser import detect_sheet_metal, extract_bends_from_geometry

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an engineering assistant specialising in armoured vehicle manufacturing. "
    "Given CAD component metadata, identify: part type, likely material if not specified, "
    "BOM description, whether the part is a sheet metal part (has bends), and any "
    "manufacturing notes. Respond ONLY with valid JSON matching the schema provided."
)

USER_PROMPT_TEMPLATE = """Part metadata:
{part_metadata_json}

Available materials: {materials_list}

Respond with JSON:
{{
  "part_name": "human-readable name",
  "part_type": "SHEET_METAL|SOLID|ASSEMBLY|UNKNOWN",
  "material_code": "one of the material codes or null",
  "material": "material name or null",
  "has_bends": true,
  "bend_count": 0,
  "notes": "manufacturing notes or null",
  "confidence": 0.0,
  "material_inferred": true
}}"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _material_density(material_code: Optional[str], materials_table: list[dict]) -> float:
    """Return density_g_cm3 for a material code, defaulting to 7.85 (steel)."""
    if material_code:
        for mat in materials_table:
            if mat.get("code") == material_code:
                return float(mat.get("density_g_cm3", 7.85))
    return 7.85


def _material_k_factor(material_code: Optional[str], materials_table: list[dict], default: float) -> float:
    """Return k_factor for a material code."""
    if material_code:
        for mat in materials_table:
            if mat.get("code") == material_code:
                kf = mat.get("k_factor")
                if kf is not None:
                    return float(kf)
    return default


def _compute_mass(volume_mm3: Optional[float], density_g_cm3: float) -> Optional[float]:
    if volume_mm3 is None or volume_mm3 <= 0:
        return None
    # volume mm3 → cm3: divide by 1000; then × density → grams; ÷ 1000 → kg
    return round((volume_mm3 / 1000.0) * density_g_cm3 / 1000.0, 4)


def build_llm_prompt(part: dict, materials_table: list[dict]) -> str:
    """Build the user prompt for a single part."""
    # Trim large metadata for token efficiency
    safe_part = {k: v for k, v in part.items() if k not in ("arc_entities", "cylindrical_faces")}
    part_json = json.dumps(safe_part, indent=2)
    mat_list = json.dumps([{"code": m["code"], "name": m["name"]} for m in materials_table], indent=2)
    return USER_PROMPT_TEMPLATE.format(
        part_metadata_json=part_json,
        materials_list=mat_list,
    )


def _parse_llm_response(text: str) -> Optional[dict]:
    """Extract JSON from LLM response text."""
    text = text.strip()
    # Try to find a JSON block
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        return json.loads(text[start:end])
    except json.JSONDecodeError:
        return None


def _enrich_single_part_metadata_only(raw: dict, materials_table: list[dict], settings) -> PartRecord:
    """Enrich a part using geometry heuristics only (no LLM)."""
    is_sm, thickness = detect_sheet_metal(raw)
    raw_bends = extract_bends_from_geometry(raw)

    part_type = raw.get("shape_type", "UNKNOWN")
    if is_sm:
        part_type = "SHEET_METAL"

    density = _material_density(None, materials_table)
    mass = _compute_mass(raw.get("volume_mm3"), density)

    bends = _build_bend_records(raw.get("part_id", "P-001"), raw_bends, thickness or 8.0, settings.DEFAULT_K_FACTOR)

    return PartRecord(
        part_id=raw.get("part_id", "UNKNOWN"),
        part_name=raw.get("part_name", raw.get("part_id", "UNKNOWN")),
        part_type=part_type,
        quantity=1,
        material=None,
        material_code=None,
        thickness_mm=thickness,
        mass_kg=mass,
        volume_mm3=raw.get("volume_mm3"),
        bounding_box=raw.get("bounding_box"),
        parent_assembly=raw.get("parent_assembly"),
        bom_level=raw.get("bom_level", 0),
        has_bends=len(bends) > 0,
        bend_count=len(bends),
        bends=bends,
        llm_confidence=None,
        notes=raw.get("notes"),
        material_inferred=False,
        low_confidence=True,
    )


def _build_bend_records(
    part_id: str,
    raw_bends: list[dict],
    thickness_mm: float,
    default_k_factor: float,
) -> list[BendRecord]:
    """Convert raw bend dicts to BendRecord objects with computed bend allowances."""
    from app.bending_calculator import compute_bend_allowance  # avoid circular import

    records = []
    segment = thickness_mm * 50  # naive segment estimate

    for i, rb in enumerate(raw_bends):
        angle = rb.get("angle_deg", 90.0)
        radius = rb.get("radius_mm", 8.0)
        direction = rb.get("direction", "UP")
        ba = compute_bend_allowance(angle, radius, thickness_mm, default_k_factor)
        records.append(
            BendRecord(
                bend_id=i + 1,
                part_id=part_id,
                angle_deg=angle,
                radius_mm=radius,
                direction=direction,
                k_factor=default_k_factor,
                bend_allowance_mm=round(ba, 4),
                segment_before_mm=round(segment, 3),
                segment_after_mm=round(segment, 3),
            )
        )
    return records


# ---------------------------------------------------------------------------
# Main enrichment function
# ---------------------------------------------------------------------------

def enrich_parts(raw_parts: list[dict], materials_table: list[dict]) -> list[PartRecord]:
    """
    Send each part's metadata to the LLM for enrichment.
    Returns list of PartRecord with LLM-inferred fields filled.
    Batches parts in groups of 10 to stay within token limits.
    Falls back to metadata-only mode if LLM is unavailable.
    """
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
    batch_size = 10

    for batch_start in range(0, len(raw_parts), batch_size):
        batch = raw_parts[batch_start: batch_start + batch_size]
        logger.info("LLM enriching batch %d-%d / %d", batch_start + 1, batch_start + len(batch), len(raw_parts))

        for raw in batch:
            record = _enrich_single_part_llm(raw, materials_table, settings, llm)
            results.append(record)

    return results


def _enrich_single_part_llm(
    raw: dict,
    materials_table: list[dict],
    settings,
    llm,
) -> PartRecord:
    """Call the LLM for a single part. Retries twice before falling back."""
    from langchain_core.messages import HumanMessage, SystemMessage  # type: ignore

    user_prompt = build_llm_prompt(raw, materials_table)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    llm_data: Optional[dict] = None
    for attempt in range(3):
        try:
            response = llm.invoke(messages)
            llm_data = _parse_llm_response(response.content)
            if llm_data:
                break
            logger.warning("LLM returned unparseable JSON for part %s (attempt %d)", raw.get("part_id"), attempt + 1)
        except Exception as exc:
            logger.warning("LLM call failed for part %s (attempt %d): %s", raw.get("part_id"), attempt + 1, exc)

    if llm_data is None:
        logger.warning("All LLM retries exhausted for part %s — using metadata fallback.", raw.get("part_id"))
        part = _enrich_single_part_metadata_only(raw, materials_table, settings)
        part.notes = (part.notes or "") + " | LLM parse failure"
        part.low_confidence = True
        return part

    # Merge LLM fields with raw geometry
    is_sm, thickness = detect_sheet_metal(raw)
    raw_bends = extract_bends_from_geometry(raw)

    part_type = llm_data.get("part_type", "UNKNOWN")
    has_bends = llm_data.get("has_bends", len(raw_bends) > 0)
    bend_count = llm_data.get("bend_count", len(raw_bends))
    confidence = float(llm_data.get("confidence", 0.5))
    material_code = llm_data.get("material_code")
    material_inferred = bool(llm_data.get("material_inferred", False))
    low_confidence = confidence < 0.6

    density = _material_density(material_code, materials_table)
    mass = _compute_mass(raw.get("volume_mm3"), density)

    # Determine thickness
    llm_thickness = thickness  # geometry heuristic
    if raw.get("thickness_mm"):
        llm_thickness = raw["thickness_mm"]

    k_factor = _material_k_factor(material_code, materials_table, settings.DEFAULT_K_FACTOR)

    bends = _build_bend_records(
        raw.get("part_id", "P-001"),
        raw_bends,
        llm_thickness or 8.0,
        k_factor,
    )

    # Pad bend list if LLM says there are more bends than geometry found
    while len(bends) < bend_count:
        extra_idx = len(bends) + 1
        from app.bending_calculator import compute_bend_allowance
        ba = compute_bend_allowance(90.0, 8.0, llm_thickness or 8.0, k_factor)
        bends.append(
            BendRecord(
                bend_id=extra_idx,
                part_id=raw.get("part_id", "P-001"),
                angle_deg=90.0,
                radius_mm=8.0,
                direction="UP" if extra_idx % 2 != 0 else "DOWN",
                k_factor=k_factor,
                bend_allowance_mm=round(ba, 4),
                segment_before_mm=round((llm_thickness or 8.0) * 50, 3),
                segment_after_mm=round((llm_thickness or 8.0) * 50, 3),
            )
        )

    return PartRecord(
        part_id=raw.get("part_id", "UNKNOWN"),
        part_name=llm_data.get("part_name", raw.get("part_name", raw.get("part_id", "UNKNOWN"))),
        part_type=part_type,
        quantity=1,
        material=llm_data.get("material"),
        material_code=material_code,
        thickness_mm=llm_thickness,
        mass_kg=mass,
        volume_mm3=raw.get("volume_mm3"),
        bounding_box=raw.get("bounding_box"),
        parent_assembly=raw.get("parent_assembly"),
        bom_level=raw.get("bom_level", 0),
        has_bends=has_bends,
        bend_count=len(bends),
        bends=bends,
        llm_confidence=confidence,
        notes=llm_data.get("notes"),
        material_inferred=material_inferred,
        low_confidence=low_confidence,
    )
