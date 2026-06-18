---
project: Engineering CAD AI Agent
type: deep-prototype
stage: Specific
domain: Defence & Armoured Vehicle Manufacturing
version: v1.0
date: 2026-06-09
---

# Project Context

## What we are building

An AI agent that accepts CAD files (STEP, IGES, DXF) and produces four manufacturing-ready outputs:
1. Bill of Materials (BOM) — Excel + CSV
2. DXF Flat Drawings — per sheet metal part
3. Assembly Drawings — 2D schematic with balloon callouts
4. Bending Drawings — bend sequence, angles, K-factor, flat blank dimensions

## Spec document

`CAD_Agent_DeepPrototype_Spec_2 1.md` in the project root.

## Prototype stage

**Specific** — full happy-path pipeline implemented end-to-end. All 8 processing steps wired. All 4 output types generated. Demo-ready for stakeholders.

## Tech stack

| Layer | Technology |
|---|---|
| UI | Streamlit (`app/main.py`) |
| Backend | FastAPI (`app/api.py`) |
| LLM | Claude Sonnet (claude-sonnet-4-6) via Anthropic SDK; GPT-4o as fallback |
| Agent framework | LangChain |
| CAD parsing | pythonocc-core (STEP/IGES) + ezdxf (DXF) |
| BOM output | openpyxl, pandas |
| Drawing output | ezdxf, matplotlib, reportlab |
| Database | Supabase (PostgreSQL + pgvector) |
| Deployment | Railway (backend) |

## Project root

`cad-agent/` inside `C:\Users\shara\Documents\wip_repos\TAG\Maufacturing Agent\`

## Environment variables required

- `ANTHROPIC_API_KEY`
- `OPENAI_API_KEY` (fallback LLM)
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SESSION_SECRET`
- `OUTPUT_DIR` (default: `./outputs`)
- `MAX_FILE_SIZE_MB` (default: `50`)
- `DEFAULT_K_FACTOR` (default: `0.33`)

## Known constraints

- pythonocc-core requires conda on Windows: `conda install -c conda-forge pythonocc-core`
- Max 200 parts per assembly (hard limit)
- Max 50 MB file size
- English metadata only
- Human review mandatory for all outputs

## Out of scope for prototype

PLM/ERP integration, GD&T, weld symbols, FEA, RBAC, version control, MIL-SPEC validation, springback compensation, cost estimation.
