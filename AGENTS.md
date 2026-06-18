# AGENTS.md — Engineering CAD AI Agent

## Project

Engineering CAD AI Agent — Deep Prototype (Specific stage)
Domain: Defence & Armoured Vehicle Manufacturing

## Spec

`CAD_Agent_DeepPrototype_Spec_2 1.md`

## Context

`.ai/context.md`

## Repository layout

```
cad-agent/
├── app/
│   ├── main.py              # Streamlit UI entry point
│   ├── api.py               # FastAPI REST endpoints
│   ├── pipeline.py          # Master orchestration controller
│   ├── cad_parser.py        # pythonocc-core + ezdxf extraction
│   ├── llm_interpreter.py   # LangChain + LLM enrichment
│   ├── bom_generator.py     # BOM assembly + Excel/CSV export
│   ├── dxf_generator.py     # DXF flat drawing builder
│   ├── assembly_drawing.py  # Assembly drawing renderer
│   ├── bending_calculator.py# Bend math + drawing generator
│   ├── output_packager.py   # ZIP builder + summary report
│   └── models.py            # Pydantic data models
├── data/
│   ├── materials.json       # Material density + K-factor lookup
│   ├── test_cases/          # Reference CAD files
│   └── reference_boms/      # Reference BOM Excel files
├── tests/
│   ├── test_bom.py
│   ├── test_dxf.py
│   └── test_bending.py
├── logs/
├── outputs/
├── requirements.txt
├── .env.example
└── README.md
```

## Stack constraints

- Python 3.10+
- pythonocc-core via conda (`conda install -c conda-forge pythonocc-core`)
- All other deps via pip from requirements.txt
- LLM: Claude Sonnet (claude-sonnet-4-6) primary, GPT-4o fallback
- LLM responses must be JSON (structured output)

## Coding standards

- Type hints on all functions
- Pydantic models for all data structures (PartRecord, BendRecord, SessionLog)
- FastAPI for REST; Streamlit for UI
- All outputs go to `outputs/<session_id>/`
- Errors are user-facing with descriptive messages; never silent

## Knowledge Vault

Not yet initialised for this project.

## Maintenance rules

`C:\Users\shara\OneDrive\Documents\Claude\Projects\Agent-Rules\Global Rules\rules\knowledge-vault.md`
