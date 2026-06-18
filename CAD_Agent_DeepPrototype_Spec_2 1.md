# Engineering CAD AI Agent — Deep Prototype Specification
**Armoured Vehicle Manufacturing | CAD-to-Manufacturing Outputs**

| Field | Value |
|---|---|
| Document Type | Deep Prototype Specification |
| Domain | Defence & Armoured Vehicle Manufacturing |
| Stage | Proof of Concept (POC) |
| Audience | Lead AI/ML Engineer + Integration Engineer |
| Version | v1.0 — June 2026 |

> **CONFIDENTIAL — FOR INTERNAL ENGINEERING USE ONLY**

---

## 1. Executive Summary

This document defines the detailed technical specifications for building a **Deep Prototype (Proof of Concept)** of the Engineering CAD AI Agent — a system that automates the conversion of CAD design files into manufacturing-ready engineering outputs for an armoured vehicle manufacturer.

The agent accepts **CAD files (STEP, IGES, DXF)** as input and produces four categories of structured output:
- Bill of Materials (BOM)
- DXF flat drawings
- Assembly drawings
- Bending drawings

The deep prototype phase focuses on validating technical feasibility and demonstrating the core pipeline end-to-end in a controlled sandbox environment.

### 1.1 Prototype Goals

| Dimension | Target |
|---|---|
| Pipeline completeness | CAD file → all 4 output types without manual breaks |
| BOM accuracy | 70–85% match against a reference BOM (human review required) |
| DXF output quality | Geometrically correct; tolerances and layer structure acceptable for review |
| Assembly drawing | Readable, component-labelled schematic; not production-grade |
| Bending drawing | Rule-based output with correct bend direction and basic parameters |
| Scenario coverage | 5–10 representative armoured vehicle component assemblies |
| Environment | Local machine or sandboxed cloud (Vercel / Railway) |
| Demo readiness | Stakeholder-demonstrable in a single session run |

---

## 2. System Overview & Agent Architecture

### 2.1 What the Agent Does

The agent orchestrates a multi-step AI pipeline that performs the following tasks in sequence:

1. Accepts a CAD file upload (STEP, IGES, or DXF format)
2. Parses the geometry and metadata to extract component structure
3. Classifies parts and identifies: materials, quantities, assemblies, bend features
4. Generates BOM in structured tabular form (CSV / Excel)
5. Generates DXF flat-cut drawings per sheet metal part
6. Generates Assembly drawings (component layout with labels)
7. Generates Bending drawings (with bend lines, angles, allowance annotations)
8. Returns all outputs as downloadable files + summary report

### 2.2 Architecture — Processing Stages

| Stage | Component | Technology |
|---|---|---|
| Input Layer | File upload interface | Streamlit or Gradio UI |
| Parsing Layer | CAD geometry extractor | Open CASCADE / pythonocc-core / ezdxf |
| Orchestration Layer | Agent brain / LLM controller | LangChain + GPT-4o or Claude Sonnet |
| Generation Layer | Output generators (BOM, DXF, drawings) | ezdxf, matplotlib, reportlab, openpyxl |
| Output Layer | Download interface + summary log | Streamlit file download widgets |

### 2.3 Agent Autonomy Level

| Autonomy Dimension | Prototype Setting |
|---|---|
| Input handling | Semi-automated — user uploads file manually |
| CAD parsing | Automated — runs without user intervention |
| LLM reasoning | Automated — agent interprets CAD metadata and generates context |
| Output generation | Automated — all 4 outputs generated per run |
| Human review | Mandatory — engineer reviews all outputs before use |
| Error recovery | Manual — errors surfaced to user with descriptive messages |

---

## 3. Input Specification

### 3.1 Supported File Formats

| Format | Type | Prototype Support | Notes |
|---|---|---|---|
| STEP (.stp / .step) | 3D solid model | Primary | ISO 10303 — richest geometry and metadata |
| IGES (.igs / .iges) | 3D surface/solid model | Secondary | Older standard; limited metadata |
| DXF (.dxf) | 2D drawing | Tertiary | Used when 3D source not available |

### 3.2 Input Constraints for Prototype

- Max file size: **50 MB** per upload
- Max assembly complexity: up to **200 unique part instances** per file
- Language of metadata: **English only** (part names, descriptions)
- Part numbering must follow a structured scheme (e.g. `AV-0001-XXXX`) for BOM mapping
- Sheet metal parts must be identifiable by a flat face or extrusion profile for bending detection

### 3.3 Sample Test Input Files

Prepare the following 5–10 representative test cases before prototype development begins:

| Test Case ID | Description | Expected Complexity |
|---|---|---|
| TC-01 | Hull side armour panel — single sheet metal part | Low — 1 part, flat with bends |
| TC-02 | Door hinge assembly — 3–5 parts | Low — simple mechanical assembly |
| TC-03 | Roof mounting bracket — 2 parts with weld marks | Medium — multi-part, annotations |
| TC-04 | Engine bay firewall panel — 8–12 parts | Medium — mixed materials |
| TC-05 | Underbelly blast deflector — 15–25 parts | High — complex geometry, tolerances |
| TC-06 | Turret ring sub-assembly — 30–50 parts | High — rotational geometry, BOM depth |
| TC-07 | Interior rack frame — 10 parts, all sheet metal | Medium — pure sheet metal, bending-heavy |
| TC-08 | External grab handle — 2 parts | Low — minimal, sanity check |
| TC-09 | Ballistic glass frame assembly — 6 parts | Medium — mixed rigid and flexible parts |
| TC-10 | Rear ramp mechanism — 40+ parts | High — max complexity stress test |

---

## 4. Output Specification

### 4.1 Output 1: Bill of Materials (BOM)

**Format:**
- Primary: Excel (`.xlsx`) with structured columns
- Secondary: CSV export for ERP compatibility

#### 4.1.1 Required BOM Columns

| Column Name | Data Type | Description | Prototype Accuracy Target |
|---|---|---|---|
| Item No. | Integer | Sequential BOM line number | 100% — generated by system |
| Part Number | String | Extracted from CAD metadata | 85–95% |
| Part Name / Description | String | Human-readable component name | 75–85% — LLM-generated from geometry |
| Quantity | Integer | Number of instances in assembly | 90–95% — geometric count |
| Unit of Measure | String | EA / M / KG / SET | 80% — heuristic classification |
| Material | String | Material spec (e.g. ARMOX 500T, RHA) | 70–80% — from metadata or LLM inference |
| Mass (kg) | Float | Computed from geometry + density | 75–85% — depends on material mapping |
| Parent Assembly | String | Hierarchical parent reference | 80–90% — from STEP tree structure |
| Level | Integer | BOM indentation level (0 = top) | 95% — direct from STEP structure |
| Notes | String | LLM-generated observation or flag | Human review required |

#### 4.1.2 BOM Acceptance Criteria for Prototype

- All top-level parts are present in the BOM
- Quantities correct for at least 85% of parts
- Material field populated for at least 70% of parts
- BOM hierarchy (parent-child) correctly reflects assembly tree
- No duplicate entries for the same part instance

---

### 4.2 Output 2: DXF Flat Drawings

One DXF file per sheet metal part showing the flat (unfolded) geometry ready for CNC cutting.

#### 4.2.1 Required DXF Layer Structure

| Layer Name | Content | Line Weight |
|---|---|---|
| 0_OUTLINE | Part outer boundary / cut profile | 0.5mm |
| 1_HOLES | Circular cutouts and slots | 0.35mm |
| 2_BEND_LINES | Dashed lines showing bend locations | 0.25mm dashed |
| 3_ANNOTATIONS | Dimension labels, part number, material | 0.18mm text |
| 4_TITLE_BLOCK | Standard title block (part no., rev, date) | 0.25mm |

#### 4.2.2 DXF Acceptance Criteria for Prototype

- Geometry is topologically closed (no open profiles)
- All holes and cutouts present
- Bend lines shown as dashed layer, clearly distinguishable from cut lines
- Part number and material annotation visible in title block
- File opens without error in AutoCAD LT, FreeCAD, or LibreCAD

---

### 4.3 Output 3: Assembly Drawings

A 2D schematic drawing showing how components fit together, with item balloon callouts referencing the BOM.

#### 4.3.1 Required Content

- Top-down or isometric 2D projection of assembly
- Balloon callouts with item numbers matching BOM
- Exploded view *(optional for prototype — mark as stretch goal)*
- Basic dimensions: overall length × width × height
- Title block: assembly number, revision, date, scale
- Parts list table embedded in drawing (top 10 parts minimum)

#### 4.3.2 Assembly Drawing Acceptance Criteria

- All major components visible and labelled
- At least 80% of BOM items referenced in balloons
- No overlapping callouts on main component bodies
- Exported as PDF + DXF (dual format)

---

### 4.4 Output 4: Bending Drawings

Per sheet metal part: a dedicated drawing showing bend sequence, bend angles, K-factor, and resulting flat blank dimensions.

#### 4.4.1 Required Bending Parameters

| Parameter | Description | Prototype Source |
|---|---|---|
| Material thickness (T) | Sheet thickness in mm | Extracted from CAD geometry |
| Bend angle (θ) | Interior angle of each bend | Extracted from CAD feature |
| Bend radius (R) | Inner radius at bend (default: 1×T if not specified) | CAD or default rule |
| K-factor | Neutral axis ratio (default 0.33 for armour steel) | Rule-based default table |
| Bend allowance (BA) | `BA = θ × (R + K×T) × π/180` | Computed by agent |
| Flat blank length | Sum of straight segments + bend allowances | Computed by agent |
| Bend sequence | Numbered order of bends (1, 2, 3...) | Rule-based heuristic |
| Bend direction | UP / DOWN annotation per bend line | Heuristic from geometry normal |

#### 4.4.2 Bending Drawing Acceptance Criteria

- All bends identified and numbered
- Flat blank dimensions computed and annotated
- K-factor and material thickness displayed in drawing notes
- Bend angle correct within ±2° tolerance
- Output format: PDF + DXF

---

## 5. Deep Prototype Tech Stack

The table below maps every layer from the DQ Agent Development Canvas to its prototype-stage technology choice, with CAD-domain context added for each.

| Layer / Tech Stack | Prototype Technology | Purpose in This Agent | Notes |
|---|---|---|---|
| **LLM / Core Intelligence** | OpenAI API (GPT-4o) / Anthropic Claude Sonnet | Interpret CAD metadata, infer materials, generate BOM descriptions and drawing annotations | Use Claude Sonnet as primary; GPT-4o as fallback. Both callable via API key swap |
| **Agent Framework / Orchestration** | LangChain | Chain parsing → LLM interpretation → output generation steps sequentially | Sequential chain is sufficient at prototype; LangGraph reserved for build phase |
| **Backend Framework** | FastAPI | REST endpoint to receive CAD file, trigger pipeline, return output ZIP | Flask acceptable as lighter alternative if FastAPI overhead not needed |
| **Frontend / UI** | React | File upload interface, pipeline progress display, output download links, session history view | Canvas specifies React at prototype stage; Next.js added in build phase. FastAPI serves as the backend the React app calls |
| **Database / Structured Data** | Supabase (PostgreSQL) | Store session logs, BOM tables, part records, run metadata | Free tier sufficient for prototype volume |
| **Vector DB / RAG** | Supabase Vector / pgvector | Store embeddings of part descriptions for similarity-based material lookup and part classification | Enables fuzzy matching of unknown part names against known armoured vehicle component library |
| **Search / Retrieval** | Tavily / SerpAPI | Retrieve engineering standard references, material datasheets, or CAD metadata definitions on demand | Used by LLM tool call when material or part type cannot be inferred from geometry alone |
| **Memory Layer** | Supabase tables + Supabase vector store | Persist part records and BOM outputs per session; vector store holds component description embeddings across sessions | Enables cross-session lookup: if same part number seen before, retrieve prior material assignment |
| **Tool Integration Layer** | REST APIs / Python functions / n8n | CAD parser functions, ezdxf drawing tools, openpyxl BOM writer, material lookup table — all called as LangChain tools | n8n optional for no-code workflow wiring during prototyping |
| **Workflow Engine** | Sequential prompting / simple LangChain chains | Linear pipeline: intake → parse → interpret → generate BOM → generate DXF → generate drawings → package | No branching logic needed at prototype; add conditional routing in build phase |
| **Deployment** | Vercel / Railway | Host Streamlit app and FastAPI backend for stakeholder demo access | Railway preferred for Python backends; Vercel for any static frontend wrapper |
| **Authentication** | API keys | Protect FastAPI endpoints and LLM API calls | No user login required at prototype; RBAC added in build phase |
| **Observability** | Console logs + Supabase run log table | Log each pipeline step: file received, parts extracted, LLM calls made, outputs generated, errors | Structured log per session stored to Supabase for post-demo review |
| **Evaluation** | Manual test cases using Excel / Google Sheets | Compare generated BOM against reference BOM for TC-01 to TC-10; score accuracy per column | DeepEval / RAGAS / CI pipelines reserved for build phase |
| **Error Handling** | Basic try/catch with user-facing error messages | Catch: invalid file format, parse failure, LLM API timeout, missing output file | Retry LLM call once on timeout; surface all other errors to UI with descriptive message |
| **Caching Layer** | None / simple in-memory caching | Cache material lookup table in memory per session to avoid repeated Supabase reads | No distributed cache at prototype stage |
| **API Layer** | Direct API calls (Anthropic / OpenAI SDK) | Call LLM directly from LangChain chain; no gateway or versioning | API gateway + versioned APIs added in build phase |
| **Security Layer** | Minimal — API keys in `.env`, no data encryption | Protect API keys via environment variables; do not log raw CAD file contents | Full prompt injection protection, encryption, and audit logs added in build phase |
| **BOM / Drawing Generation** | openpyxl, pandas, matplotlib, reportlab | Generate BOM Excel/CSV; render 2D assembly drawing; produce bending drawing PDFs | Domain-specific output generation layer |

### 5.1 Installation

```bash
# Core agent and LLM
pip install langchain openai anthropic

# CAD parsing
pip install pythonocc-core ezdxf

# Output generation
pip install openpyxl pandas matplotlib reportlab

# Backend
pip install fastapi uvicorn

# Frontend (React — run separately)
# npx create-react-app cad-agent-ui
# cd cad-agent-ui && npm install axios

# Database and retrieval
pip install supabase tavily-python

# Testing
pip install pytest
```

---

## 6. Agent Processing Pipeline — Step by Step

### Step 1 — File Intake & Validation

- Accept file upload via React UI (POST to FastAPI `/upload` endpoint)
- Validate: file extension is STEP / IGES / DXF
- Validate: file size ≤ 50 MB
- Generate a session ID (UUID) and store metadata in Supabase
- Return: file accepted confirmation + session ID to UI

### Step 2 — CAD Geometry Extraction

- Load file using `pythonocc-core` (for STEP/IGES) or `ezdxf` (for DXF)
- Extract:
  - Part tree (assembly hierarchy)
  - Shape type per part (solid, shell, wire)
  - Face count and surface area
  - Estimated volume and mass (using material density defaults)
  - Bounding box dimensions
  - Any embedded metadata (part name, number, material)
- Output: Python dictionary of extracted component data
- Log: extraction time, number of parts found, any parse errors

### Step 3 — LLM Interpretation Pass

Pass extracted geometry metadata to LLM with a structured prompt:

```
SYSTEM: You are an engineering assistant specialising in armoured vehicle manufacturing.
Given the following CAD component metadata, identify: part type, likely material if not
specified, BOM description, whether the part is a sheet metal part (has bends), and any
manufacturing notes. Respond in JSON only.
```

- LLM returns enriched metadata per part (material inference, BOM description, sheet metal flag)
- Merge LLM output with geometric data into unified part record

### Step 4 — BOM Assembly

- Sort parts by assembly hierarchy level
- Assign sequential item numbers
- Map inferred materials to internal material code table (armour steel grades, aluminium alloys, rubber seals)
- Compute: mass per part (volume × density), total assembly mass
- Write structured BOM to openpyxl workbook with formatted columns
- Export: `BOM.xlsx` and `BOM.csv`

### Step 5 — DXF Generation (Sheet Metal Parts Only)

- Filter parts flagged as sheet metal by LLM interpretation
- For each sheet metal part:
  - Extract 2D profile from flat face
  - Identify holes and slots
  - Identify bend lines from feature edges
  - Project to 2D plane
- Construct DXF using `ezdxf` with the layer structure from Section 4.2.1
- Export: one `.dxf` file per sheet metal part

### Step 6 — Assembly Drawing Generation

- Select top-level assembly view (plan or isometric projection)
- Project 3D part positions to 2D using bounding box centroids
- Draw simplified part outlines as rectangles or polygons
- Add balloon callouts linked to BOM item numbers
- Render overall dimensions (L × W × H)
- Add title block with assembly metadata
- Export: `Assembly_Drawing.pdf` + `Assembly_Drawing.dxf`

### Step 7 — Bending Drawing Generation

- For each sheet metal part:
  - Retrieve thickness from geometry
  - Retrieve all bend features (angle, radius, direction)
  - Apply K-factor from material lookup table (default 0.33 for ARMOX-grade steels)
- Compute:
  - Bend allowance per bend: `BA = angle × (radius + K × thickness) × π/180`
  - Flat blank length: sum of straight segments + all bend allowances
- Construct bending drawing DXF:
  - Unfolded profile with dimensions
  - Each bend line annotated with: bend number, angle, radius, allowance, direction (UP/DOWN)
  - Notes block: material, thickness, K-factor, total blank size
- Export: `Bending_[PartNumber].pdf` + `Bending_[PartNumber].dxf` per part

### Step 8 — Output Packaging & Summary

- Bundle all outputs into a ZIP:
  - `BOM.xlsx`, `BOM.csv`
  - DXF files per part
  - `Assembly_Drawing.pdf`, `Assembly_Drawing.dxf`
  - Bending drawings (PDF + DXF per part)
- Generate a run summary report:
  - Session ID, input file name
  - Number of parts processed, BOM line count
  - Number of DXF files, number of bending drawings generated
  - Any warnings or low-confidence flags
  - Total processing time
- Return download link via FastAPI response; React UI renders download button
- Log session record to Supabase

---

## 7. Key Data Models

### 7.1 Part Record (Internal)

```json
{
  "part_id": "AV-0012-001",
  "part_name": "Hull Side Panel — LH",
  "part_type": "SHEET_METAL",
  "quantity": 1,
  "material": "ARMOX 500T",
  "thickness_mm": 8.0,
  "mass_kg": 24.3,
  "volume_mm3": 3102564,
  "bounding_box": { "L": 1200, "W": 650, "H": 8 },
  "parent_assembly": "AV-0012",
  "bom_level": 2,
  "has_bends": true,
  "bend_count": 3,
  "llm_confidence": 0.82,
  "notes": "Primary hull armour. Check weld seam compatibility."
}
```

### 7.2 Bend Record (Internal)

```json
{
  "bend_id": 1,
  "part_id": "AV-0012-001",
  "angle_deg": 90.0,
  "radius_mm": 8.0,
  "direction": "UP",
  "k_factor": 0.33,
  "bend_allowance_mm": 16.97,
  "segment_before_mm": 420.0,
  "segment_after_mm": 180.0
}
```

### 7.3 Session Log Record (Supabase)

```json
{
  "session_id": "uuid-xxxx-xxxx",
  "input_filename": "hull_panel_TC01.step",
  "input_format": "STEP",
  "parts_extracted": 12,
  "bom_lines": 12,
  "dxf_files_generated": 9,
  "bending_drawings_generated": 9,
  "processing_time_seconds": 87,
  "warnings": ["Part AV-0012-008: material INFERRED — low confidence 0.58"],
  "status": "SUCCESS",
  "timestamp": "2026-06-04T10:32:00Z"
}
```

---

## 8. Deep Prototype Success Criteria

### 8.1 Functional Success Criteria

| Criterion | Pass Threshold | Verification Method |
|---|---|---|
| Pipeline runs end-to-end without crash | 100% of test runs | Execute all 10 test cases |
| BOM line count accuracy | ≥85% of parts present vs. reference BOM | Manual comparison spreadsheet |
| BOM quantity accuracy | ≥85% quantities match reference | Column-by-column diff |
| Material field populated | ≥70% of BOM lines have material | Column completeness count |
| DXF files generated per sheet metal part | 100% of flagged parts | File count check |
| DXF geometry valid (no open profiles) | ≥90% of DXF files | ezdxf validation script |
| Assembly drawing: parts labelled | ≥80% of BOM items referenced | Manual callout count |
| Bending drawing: bend angle accuracy | Within ±2° of reference | Manual measurement vs. CAD |
| Flat blank dimension accuracy | Within ±5mm of reference | Formula verification |
| Output ZIP generated per run | 100% of successful runs | File existence check |

### 8.2 Non-Functional Success Criteria

| Criterion | Pass Threshold |
|---|---|
| Processing time (TC-01 to TC-05) | < 3 minutes per file |
| Processing time (TC-06 to TC-10) | < 8 minutes per file |
| System does not crash on invalid file input | Graceful error message returned |
| LLM API errors handled | Retry ×2, then fallback to metadata-only mode |
| Logging: each run recorded to Supabase | 100% of runs logged |
| Prototype demo: can run live in front of stakeholders | Single session, no code changes needed |

### 8.3 Failure Modes & Acceptable Degradations

| Failure Mode | Prototype Behaviour | Flag to Engineer? |
|---|---|---|
| Material not in metadata | LLM infers from geometry + part name | Yes — flag as `INFERRED` |
| Part has no flat face (not sheet metal) | Skip DXF and bending; include in BOM only | Yes — flag in summary |
| LLM returns low confidence (<0.6) | Output generated but marked `LOW_CONFIDENCE` | Yes — column flag in BOM |
| STEP file has nested sub-assemblies >3 levels deep | Flatten to 3 levels max; warn user | Yes — warning in summary |
| Bend count > 6 on single part | Generate drawing with first 6 bends; note truncation | Yes |
| Assembly has >200 parts | Process first 200; warn user of truncation | Yes — hard limit enforced |

---

## 9. Repository & Folder Structure

```
cad-agent/
├── app/
│   ├── main.py                  # Streamlit UI entry point
│   ├── pipeline.py              # Master orchestration controller
│   ├── cad_parser.py            # pythonocc-core + ezdxf extraction
│   ├── llm_interpreter.py       # LangChain + LLM enrichment
│   ├── bom_generator.py         # BOM assembly + Excel export
│   ├── dxf_generator.py         # DXF flat drawing builder
│   ├── assembly_drawing.py      # Assembly drawing renderer
│   ├── bending_calculator.py    # Bend math + drawing generator
│   └── output_packager.py       # ZIP builder + summary report
├── data/
│   ├── materials.json           # Material density + K-factor lookup table
│   ├── test_cases/              # 10 reference CAD files
│   └── reference_boms/          # Reference BOM Excel files for validation
├── tests/
│   ├── test_bom.py
│   ├── test_dxf.py
│   └── test_bending.py
├── logs/                        # Run logs (local fallback)
├── outputs/                     # Generated outputs per session
├── requirements.txt
├── .env                         # API keys (ANTHROPIC_API_KEY, SUPABASE_URL, etc.)
└── README.md
```

---

## 10. Material Reference Table (Default Lookup)

| Material Code | Material Name | Density (g/cm³) | Default K-Factor | Typical Application |
|---|---|---|---|---|
| ARMOX-500T | High-hardness armour steel | 7.85 | 0.33 | Hull panels, blast plates |
| ARMOX-370T | Medium-hardness armour steel | 7.85 | 0.33 | Structural frames |
| RHA-MIL | Rolled Homogeneous Armour | 7.85 | 0.35 | Legacy armour plating |
| AL-5083 | Aluminium alloy 5083-H116 | 2.66 | 0.40 | Lightweight panels, hatches |
| AL-7075 | Aluminium alloy 7075-T6 | 2.81 | 0.38 | High-strength brackets |
| SS-316L | Stainless steel 316L | 7.99 | 0.34 | Sealing frames, wet area components |
| MILD-S275 | Structural mild steel S275 | 7.85 | 0.33 | Interior secondary structure |
| UHMWPE | Ultra-high-molecular-weight PE | 0.93 | N/A | Spall liner panels |
| RUBBER-NR | Natural rubber compound | 1.20 | N/A | Seals, vibration mounts |

---

## 11. Human-in-the-Loop Checkpoints

All prototype outputs require human engineering review before use:

| Checkpoint | Who Reviews | What to Check | Sign-off Required? |
|---|---|---|---|
| BOM completeness | Lead Mechanical Engineer | All parts present, quantities correct, materials plausible | Yes |
| Material assignments | Materials Engineer | Inferred materials match design intent, correct grade selected | Yes |
| DXF geometry | CAD / Draughting Engineer | Profiles closed, holes correct, scale is 1:1 | Yes |
| Bending parameters | Sheet Metal Specialist | K-factor appropriate, bend allowance formula correct, sequence logical | Yes |
| Assembly drawing | Lead Mechanical Engineer | All sub-assemblies visible, balloon references match BOM | Yes |
| LLM LOW_CONFIDENCE flags | Any reviewing engineer | Override or confirm LLM inference for flagged items | Yes — per flag |

---

## 12. Known Prototype Limitations

| Limitation | Impact | Mitigation at Prototype Stage |
|---|---|---|
| No parametric CAD kernel — geometry extracted, not modelled | Cannot regenerate CAD; output is static | Acceptable for prototype; flagged for build phase |
| LLM material inference may be incorrect for non-standard alloys | Wrong material in BOM | LOW_CONFIDENCE flag; mandatory human review |
| Assembly drawing is 2D schematic only (bounding-box projection) | Not engineering-drawing standard | Sufficient for POC; production build requires full STEP-to-drawing pipeline |
| Bending drawing does not account for springback | Angle may be slightly off in practice | Note on drawing: "Springback not compensated — adjust per press brake tooling" |
| No GD&T (geometric dimensioning & tolerancing) output | Cannot express form/position tolerances | Out of scope for prototype |
| No weld symbol generation | Assembly drawing lacks weld notations | Out of scope for prototype |
| Max 200-part limit per file | Large full-vehicle assemblies not supported | Split into sub-assemblies for prototype testing |
| English-only metadata parsing | Arabic or other language part names degrade LLM output | Pre-processing step: translate metadata before passing to LLM |

---

## 13. Prototype Build Milestones

| Milestone | Description | Deliverable | Target Duration |
|---|---|---|---|
| M1 — Environment Setup | Repo, dependencies, API keys, Supabase schema, Streamlit skeleton | Working blank app that accepts file upload | 2 days |
| M2 — CAD Parser | pythonocc-core integration, part tree extraction, mass/volume, metadata | JSON part record output for TC-01 and TC-02 | 3–4 days |
| M3 — LLM Interpreter | LangChain chain, prompt engineering, JSON output, confidence scoring | Enriched part records for TC-01 to TC-05 | 2–3 days |
| M4 — BOM Generator | openpyxl BOM builder, hierarchy sort, material mapping, CSV export | BOM.xlsx validated against reference for TC-01 to TC-05 | 2 days |
| M5 — DXF Generator | ezdxf flat profile extraction, layer structure, title block | DXF file per sheet metal part for TC-01, TC-03, TC-07 | 3–4 days |
| M6 — Bending Drawing | Bend detection, K-factor formula, annotated DXF + PDF | Bending drawings for TC-01, TC-03, TC-07 | 3 days |
| M7 — Assembly Drawing | 2D projection, balloons, dimensions, PDF + DXF | Assembly drawings for TC-02, TC-04 | 3 days |
| M8 — Integration & ZIP | Full pipeline wiring, output packager, session logger | Complete ZIP output for TC-01 to TC-07 | 2 days |
| M9 — Test & Validate | Run all 10 test cases, compare against reference, document accuracy scores | Test report with accuracy metrics per success criterion | 3 days |
| M10 — Demo Prep | Fix blockers from M9, prepare stakeholder demo script, deploy to Railway | Live demo session on Railway | 2 days |

**Estimated Total Duration: 25–30 engineering days** (1 lead engineer + 1 support engineer)

---

## 14. Environment Variables & Credentials Required

| Variable Name | Description | Where to Get |
|---|---|---|
| `OPENAI_API_KEY` | Alternative LLM (GPT-4o) | platform.openai.com |
| `SUPABASE_URL` | Supabase project URL | Supabase project dashboard |
| `SUPABASE_KEY` | Supabase anon/service key | Supabase dashboard → API |
| `SESSION_SECRET` | Random secret for session ID generation | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `OUTPUT_DIR` | Local path for generated output files | Set to `./outputs` in dev |
| `MAX_FILE_SIZE_MB` | Max upload size enforcement | Set to `50` |
| `DEFAULT_K_FACTOR` | Default K-factor if material not matched | Set to `0.33` |

---

## 15. Out of Scope for Deep Prototype

The following are explicitly excluded from the prototype and reserved for the production build phase:

- Integration with PLM systems (Siemens NX, SolidWorks PDM, CATIA)
- Integration with ERP systems (SAP, Oracle) for BOM push
- GD&T / tolerance stack-up analysis
- Weld symbol generation and welding procedure reference
- Finite Element Analysis (FEA) or stress simulation
- Multi-user collaboration and role-based access control
- Version control of CAD inputs and generated outputs
- Automated validation against MIL-SPEC or STANAG standards
- Full parametric drawing regeneration from changed CAD
- Production-grade security (encryption at rest, audit trail, IP protection)
- Springback compensation in bending calculations
- Cost estimation or procurement BOM enrichment

---

## Appendix A — Glossary

| Term | Definition |
|---|---|
| STEP | Standard for the Exchange of Product model data — ISO 10303 — primary 3D CAD format |
| IGES | Initial Graphics Exchange Specification — older 3D CAD interchange format |
| DXF | Drawing Exchange Format — Autodesk 2D/3D CAD interchange format |
| BOM | Bill of Materials — structured list of all parts, quantities, and materials in an assembly |
| K-Factor | Ratio representing position of neutral axis in sheet metal during bending (typically 0.30–0.50) |
| Bend Allowance | Additional material length required to account for stretching during a bend |
| Open CASCADE | Open-source CAD kernel used to process STEP/IGES geometry |
| pythonocc-core | Python bindings for the Open CASCADE Technology (OCCT) library |
| ezdxf | Python library for reading and writing DXF files |
| RHA | Rolled Homogeneous Armour — standard military armour steel |
| ARMOX | SSAB brand of high-hardness armour steel |
| PLM | Product Lifecycle Management — enterprise system for managing CAD and engineering data |
| LangChain | Python framework for building LLM-powered agent workflows |
| Supabase | Open-source Firebase alternative — PostgreSQL + file storage + vector DB |

---

## Appendix B — Reference Standards

- **ISO 10303** — Product data representation and exchange (STEP)
- **ISO 2768** — General tolerances for linear and angular dimensions
- **ISO 5455** — Technical drawings: scales
- **ISO 128** — Technical drawings: general principles of presentation
- **DIN 6935** — Cold bending of flat steel products (bend allowance standard)
- **MIL-DTL-12560** — Armour plate, steel, wrought (for RHA)
- **STANAG 4569** — Protection levels for occupants of armoured vehicles
