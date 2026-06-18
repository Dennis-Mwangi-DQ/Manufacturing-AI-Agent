# CAD Agent — Prototype Handoff Document

| Field | Value |
|---|---|
| Stage completed | Deep Prototype — Specific |
| Spec | `CAD_Agent_DeepPrototype_Spec_2 1.md` |
| Date | 2026-06-09 |
| Test result | 23/23 passed |
| Next action | Lead approval → `plan-feature (Build)` |

---

## What was built

An end-to-end 8-step AI pipeline that accepts CAD files (STEP, IGES, DXF) and produces a manufacturing package:

| Output | Format | Status |
|---|---|---|
| Bill of Materials | Excel (.xlsx) + CSV | ✅ Full implementation |
| DXF flat drawings | .dxf per sheet metal part | ✅ Full implementation |
| Assembly drawing | .dxf + .pdf | ✅ Full implementation |
| Bending drawings | .dxf + .pdf per bent part | ✅ Full implementation |
| Output package | .zip + summary_report.md | ✅ Full implementation |
| Session logging | Supabase (optional) / local JSON | ✅ Full implementation |

The Streamlit UI and FastAPI backend are running. Demo Mode (sidebar toggle) demonstrates the full workflow without a real CAD file or API keys.

---

## Key architectural decisions

### 1. Streamlit UI (not React)

Spec section 5 listed React; section 2.2 and the folder structure (section 9) listed Streamlit. **Chose Streamlit** for the prototype because:
- All Python stack — no npm/Node setup required
- Hot-reload friendly for demo iteration
- React is the right call for the Build phase (spec section 5 note: "Next.js added in build phase")

**Build phase action:** Replace Streamlit with a Next.js/React frontend. FastAPI backend (`app/api.py`) is already designed as the API layer — no changes needed there.

### 2. Sequential pipeline, not async

The pipeline runs steps 1–8 synchronously in a single request. For a prototype this is fine (demo runs in <10s on DXF files). For production with 50 MB STEP files this will timeout.

**Build phase action:** Move processing to a background task queue (Celery + Redis, or FastAPI `BackgroundTasks` with a polling endpoint). The `/status/{session_id}` endpoint is already designed to support this pattern.

### 3. pythonocc-core fallback mode

pythonocc-core requires `conda install -c conda-forge pythonocc-core` — not installable via pip. Rather than blocking the prototype on this dependency, a graceful fallback was implemented: if the import fails, the parser generates 20 realistic synthetic armour parts and flags them `[FALLBACK MODE]`. DXF parsing (ezdxf, pure pip) works fully without conda.

**Build phase action:** Set up a conda environment (or Docker image with pythonocc-core) for real STEP/IGES parsing. The hook point is `app/cad_parser.py` — the `_parse_step_iges()` function. Replace the fallback branch with the real pythonocc implementation.

### 4. LLM: Claude Sonnet (claude-sonnet-4-6)

Primary LLM is Claude Sonnet via `langchain-anthropic`. GPT-4o is listed as fallback but not wired — the spec notes "API key swap" as sufficient. Temperature is set to 0 for deterministic JSON output.

**Build phase action:** Add OpenAI fallback by checking `OPENAI_API_KEY` when Anthropic fails. Add confidence threshold filtering (spec section 8.3: confidence < 0.6 → flag LOW_CONFIDENCE). Currently threshold flag is applied post-response; Build phase should route low-confidence results to a second LLM call with a more explicit prompt.

### 5. Material lookup: JSON file, not vector DB

The spec listed pgvector for fuzzy material matching. For the prototype, exact JSON lookup is used (`data/materials.json`, 9 materials). This is fast and works for the defined material codes.

**Build phase action:** Load material descriptions into Supabase pgvector. Use embedding similarity search for unknown part names (e.g. "blast-resistant panel" → "ARMOX-500T"). The `app/llm_interpreter.py::_find_material()` function is the hook point.

---

## Files for Build phase reference

| File | What to extend |
|---|---|
| `app/cad_parser.py` | Replace `_parse_step_iges()` fallback with pythonocc-core implementation |
| `app/llm_interpreter.py` | Add GPT-4o fallback; add pgvector material lookup; add confidence threshold retry |
| `app/pipeline.py` | Move to async/background task; add retry logic per spec section 8.2 |
| `app/api.py` | Add async job endpoints; add auth (API key header for prototype → RBAC for Build) |
| `app/main.py` | Replace with Next.js/React frontend |
| `data/materials.json` | Extend to full client material library; migrate to Supabase |
| `tests/` | Expand to full TC-01→TC-10 test matrix against reference BOMs |

---

## Prototype limitations carried to Build

| Limitation | Build phase mitigation |
|---|---|
| No springback compensation | Add press-brake tooling offset table per material and thickness |
| 2D assembly drawings (bounding-box projection) | Integrate pythonocc projection for true 3D→2D views |
| Max 200 parts | Remove hard limit once async processing is in place |
| English metadata only | Add pre-processing translation step (DeepL API or Claude) before LLM pass |
| No GD&T output | Add GD&T annotation layer to DXF generator (spec §15) |
| No weld symbols | Add weld symbol entity to assembly drawing generator |
| No ERP/PLM integration | SAP/SolidWorks PDM connector in Build phase |

---

## Test evidence

Run date: 2026-06-09 | Python 3.13.7 | pytest 9.0.3

```
tests/test_bending.py::test_bend_allowance_formula              PASSED
tests/test_bending.py::test_bend_allowance_90_degree            PASSED
tests/test_bending.py::test_flat_blank_length                   PASSED
tests/test_bending.py::test_zero_bends                          PASSED
tests/test_bending.py::test_multiple_bends                      PASSED
tests/test_bending.py::test_bend_allowance_zero_angle           PASSED
tests/test_bending.py::test_bend_allowance_180_degree           PASSED
tests/test_bending.py::test_k_factor_effect                     PASSED
tests/test_bending.py::test_bending_drawing_creates_dxf         PASSED
tests/test_bending.py::test_bending_drawing_creates_pdf         PASSED
tests/test_bending.py::test_bending_drawing_no_bends_returns_none_pdf  PASSED
tests/test_bom.py::test_bom_generates_files                     PASSED
tests/test_bom.py::test_bom_row_count                           PASSED
tests/test_bom.py::test_bom_item_numbers_sequential             PASSED
tests/test_bom.py::test_bom_flags_low_confidence                PASSED
tests/test_bom.py::test_bom_inferred_material_label             PASSED
tests/test_bom.py::test_bom_xlsx_exists_and_valid               PASSED
tests/test_dxf.py::test_dxf_file_created                        PASSED
tests/test_dxf.py::test_dxf_has_required_layers                 PASSED
tests/test_dxf.py::test_dxf_opens_without_error                 PASSED
tests/test_dxf.py::test_dxf_filename_contains_part_id           PASSED
tests/test_dxf.py::test_dxf_no_bends_still_generates            PASSED
tests/test_dxf.py::test_dxf_modelspace_has_entities             PASSED

23 passed in 9.10s
```

---

## What is NOT done (out of scope for prototype)

Per spec section 15 — explicitly deferred to Build:
- PLM/ERP integration (SolidWorks PDM, SAP)
- GD&T / tolerance stack-up
- Weld symbol generation
- FEA / stress simulation
- Multi-user RBAC
- Output version control
- MIL-SPEC / STANAG validation
- Springback compensation
- Cost estimation

---

## Approval gate

All prototype outputs require human engineering review before use in manufacturing:

| Reviewer | Reviews |
|---|---|
| Lead Mechanical Engineer | BOM completeness, assembly drawing |
| Materials Engineer | Material assignments (especially INFERRED flags) |
| CAD / Draughting Engineer | DXF geometry — profiles closed, scale 1:1 |
| Sheet Metal Specialist | Bending parameters, K-factor, bend sequence |

**Status: PENDING LEAD APPROVAL — do not advance to Build phase until approved.**
