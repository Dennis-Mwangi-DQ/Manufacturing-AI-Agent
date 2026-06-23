# Engineering CAD AI Agent

A deep prototype for armoured vehicle manufacturing. Accepts a CAD file (STEP, IGES, or DXF), runs an 8-step AI pipeline powered by Claude (Anthropic), and produces a complete manufacturing package: Bill of Materials (Excel + CSV), DXF flat drawings per sheet metal part, bending drawings (DXF + PDF), an assembly drawing (DXF + PDF), and a ZIP containing all outputs with a summary report.

---

## Prerequisites

### Python
- Python 3.10 or later

### For STEP / IGES parsing (full 3D geometry)
```bash
conda install -c conda-forge pythonocc-core
```
This is a conda-only package and is **required** for STEP/IGES files. The agent never fabricates geometry: if `pythonocc-core` is not installed, uploading a STEP or IGES file raises a clear error instructing you to install it (or export to DXF). DXF flat patterns are parsed fully with `ezdxf` and need no extra dependency.

If you cannot use conda directly, build the Docker images (see `docker-compose.yml`), which provide a `pythonocc-core`-capable environment for the API service.

### Python dependencies
```bash
pip install -r requirements.txt
```

---

## Setup

1. Copy the example environment file and fill in your API keys:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...        # Required for LLM enrichment
   OPENAI_API_KEY=                      # Optional fallback
   SUPABASE_URL=https://...             # Optional — session logging
   SUPABASE_KEY=...                     # Optional
   SESSION_SECRET=<random hex>
   OUTPUT_DIR=./outputs
   MAX_FILE_SIZE_MB=50
   DEFAULT_K_FACTOR=0.33
   LOG_LEVEL=INFO
   ```

---

## Running

Two terminals are required:

**Terminal 1 — FastAPI backend:**
```bash
uvicorn app.api:app --reload --port 8000
```

**Terminal 2 — Streamlit frontend:**
```bash
streamlit run app/main.py
```

Then open [http://localhost:8501](http://localhost:8501) in your browser.

### Batch a whole folder (CLI)

To process every CAD file under a directory (recursively) and build ONE
consolidated package (BOM + DXF flats + bending drawings + assembly + ZIP):

```bash
python -m app.batch "/path/to/folder"
python -m app.batch "/path/to/folder" --output ./outputs --name FENDER_VLH
python -m app.batch "/path/to/folder" --no-llm   # metadata-only, no network
```

- One file == one part; duplicate part numbers are merged and their quantities summed.
- Each immediate sub-folder is recorded as the part's sub-assembly.
- Unparseable files are reported as warnings and skipped (never fabricated).

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/upload` | Upload CAD file, run pipeline, returns `session_id` |
| GET | `/download/{session_id}` | Download output ZIP |
| GET | `/status/{session_id}` | Get session log JSON |
| GET | `/health` | Health check |

---

## Supabase Setup (optional)

Create a `session_logs` table in your Supabase project with the following schema:

```sql
CREATE TABLE session_logs (
    session_id         TEXT PRIMARY KEY,
    input_filename     TEXT,
    input_format       TEXT,
    parts_extracted    INTEGER DEFAULT 0,
    bom_lines          INTEGER DEFAULT 0,
    dxf_files_generated       INTEGER DEFAULT 0,
    bending_drawings_generated INTEGER DEFAULT 0,
    assembly_drawings_generated INTEGER DEFAULT 0,
    processing_time_seconds    FLOAT DEFAULT 0,
    warnings           JSONB DEFAULT '[]',
    status             TEXT DEFAULT 'PENDING',
    timestamp          TEXT,
    output_zip_path    TEXT
);
```

---

## Running Tests

```bash
pytest tests/ -v
```

To run a specific test file:
```bash
pytest tests/test_bom.py -v
pytest tests/test_dxf.py -v
pytest tests/test_bending.py -v
```

---

## Output Structure

Each pipeline run produces a ZIP with:
```
BOM.xlsx
BOM.csv
DXF/
  <part_id>_flat.dxf          (one per sheet metal part)
Bending/
  <part_id>_bending.dxf       (one per bent sheet metal part)
  <part_id>_bending.pdf
Assembly_Drawing.dxf
Assembly_Drawing.pdf
summary_report.md
```

Session logs are stored in `logs/run_<session_id>.json` when Supabase is not configured.

---

## Architecture

```
app/
  config.py            — pydantic-settings env loader
  models.py            — Pydantic v2 data models
  cad_parser.py        — STEP/IGES/DXF geometry extraction
  llm_interpreter.py   — LangChain + Claude part enrichment
  bom_generator.py     — Excel + CSV BOM output
  dxf_generator.py     — DXF flat drawings (ezdxf)
  bending_calculator.py — Bend math + bending drawings (DXF + PDF)
  assembly_drawing.py  — 2D assembly drawing (DXF + PDF)
  output_packager.py   — ZIP bundling + summary report
  pipeline.py          — Master 8-step orchestrator
  api.py               — FastAPI endpoints
  main.py              — Streamlit UI
data/
  materials.json       — Armour material database
tests/
  test_bom.py
  test_dxf.py
  test_bending.py
```

---

## Known Limitations (Prototype)

- **pythonocc-core requires conda** — STEP/IGES geometry extraction needs the conda-forge package. Without it, STEP/IGES uploads fail with a clear error rather than producing fabricated data. DXF flat patterns are unaffected.
- **Native SolidWorks files unsupported** — `.SLDPRT` / `.SLDASM` / `.SLDDRW` cannot be parsed directly; export to STEP or DXF first. The agent rejects them with an explanatory message.
- **DXF thickness is filename-derived** — A 2D flat pattern carries no thickness, so thickness is read from the export filename code (e.g. `M4` → 4 mm) and flagged for verification. Mass is only computed when both a real profile area and a thickness/material are known; otherwise it is left blank.
- **No springback compensation** — Bending drawings show theoretical bend geometry only. Real tooling requires springback-adjusted angles.
- **2D assembly drawings only** — The assembly drawing is a bounding-box projection, not a true 3D isometric view.
- **Max 200 parts per assembly** — Larger assemblies are truncated with a warning.
- **English metadata only** — Part names and notes are expected in English. Non-ASCII characters in STEP metadata may not parse correctly.
- **Human review mandatory** — All BOM data, material classifications, bend calculations, and drawings must be reviewed by a qualified engineer before use in manufacturing. The LLM can and does make errors.
- **Prototype geometry heuristics** — Sheet metal detection uses the thin-dimension heuristic (min dimension < 30 mm). Complex formed or machined parts may be misclassified.
