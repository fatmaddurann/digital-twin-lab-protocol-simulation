# 🧬 Digital Twin for Lab Protocol Simulation

[![CI](https://github.com/fatmadurann/digital-twin-lab-protocol-simulation/actions/workflows/ci.yml/badge.svg)](https://github.com/fatmadurann/digital-twin-lab-protocol-simulation/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

> **Convert laboratory protocols into interactive 3D simulations in Blender.**
> Validate PCR, NGS, and any lab workflow for logical errors — then watch the experiment play out in a fully animated 3D scene.

---

## ✨ Features

| Capability | Description |
|---|---|
| 📋 **Protocol Parsing** | Load structured JSON/YAML protocols or paste free-text lab instructions |
| ✅ **Logic Validation** | 11 rules catch missing reagents, volume overflow, wrong pipette, bad temperature, and more |
| 🔬 **3D Scene Generation** | Procedural Blender objects: pipettes, PCR tubes, plates, thermocycler, reagent reservoirs |
| 🎬 **Keyframe Animation** | Arc-path pipette movement, liquid fill/drain, mix oscillation, placement animations |
| 🔴 **Error Visualisation** | Invalid steps highlighted with pulsing red spheres and floating error labels |
| 🌐 **REST API** | FastAPI server with Swagger UI for web-based protocol submission |
| 💻 **CLI** | `validate`, `simulate`, `parse`, `summary`, `schema` commands |

---

## 📁 Project Structure

```
digital-twin-lab-protocol-simulation/
│
├── models/                        # Pydantic v2 data schemas
│   └── protocol_models.py         # ProtocolModel, ReagentModel, StepModel …
│
├── validation/                    # Logic validation engine
│   └── protocol_validator.py      # 11 rules — R01 through R14
│
├── simulation/                    # Blender 3D engine (requires bpy)
│   ├── object_library.py          # Procedural 3D object factory
│   ├── animation_pipeline.py      # Keyframe animation executor
│   └── blender_engine.py          # Top-level orchestrator
│
├── interpreter/                   # Protocol → Simulation commands
│   └── protocol_interpreter.py   # ProtocolInterpreter + NaturalLanguageParser
│
├── interface/                     # User-facing interfaces
│   ├── cli.py                     # Command-line interface
│   └── api.py                     # FastAPI REST server
│
├── examples/                      # Ready-to-run protocol examples
│   ├── pcr_preparation.json       # Standard 8-step PCR
│   ├── ngs_library_prep.json      # Full 22-step NGS library prep
│   └── pcr_with_errors.json       # Intentional error demo
│
├── tests/
│   └── test_validation.py         # 42 tests — models, rules, interpreter, NLP
│
├── run_simulation.py              # Main entry point (Blender + CLI)
├── conftest.py                    # pytest path configuration
├── pyproject.toml                 # Packaging & tool configuration
└── requirements.txt               # Pinned dependencies
```

---

## 🚀 Quick Start

### 1. Clone

```bash
git clone https://github.com/fatmadurann/digital-twin-lab-protocol-simulation.git
cd digital-twin-lab-protocol-simulation
```

### 2. Install

```bash
pip install -e ".[dev]"          # core + testing tools
pip install -e ".[dev,api]"      # + FastAPI server
```

### 3. Validate a protocol

```bash
python -m interface.cli validate examples/pcr_preparation.json
```

```
✔  Protocol is VALID
   Steps: 8 | Errors: 0 | Warnings: 0
```

### 4. Preview the simulation plan

```bash
python run_simulation.py --protocol examples/pcr_preparation.json
```

```
  Step   Action       Source             → Destination          Vol    Frames
  1      pipette      pcr_master_mix     → pcr_tube_1         12.5µL  1–96
  2      pipette      forward_primer     → pcr_tube_1          1.0µL  97–192
  ...
  8      thermocycle  —                  → thermocycler_1       —     673–768
  Total: 768 frames  (32.0 s at 24 fps)
```

### 5. Run inside Blender

```
1. Download Blender  https://www.blender.org/download/
2. Open Blender → Scripting workspace
3. Open  run_simulation.py
4. Edit  PROTOCOL_FILE = "examples/pcr_preparation.json"
5. Press ▶  Run Script
6. Switch to 3D Viewport → press  Space  to play
```

**Headless (no GUI):**
```bash
blender --background --python run_simulation.py -- \
    --protocol examples/pcr_preparation.json \
    --output   output/pcr_simulation.blend
```

---

## 📡 FastAPI Server

```bash
uvicorn interface.api:app --reload --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/protocols/validate` | Validate a protocol JSON |
| `POST` | `/protocols/simulate` | Generate animation commands |
| `POST` | `/protocols/parse` | Parse free-text protocol → JSON |
| `GET`  | `/protocols/schema` | JSON Schema for ProtocolModel |
| `GET`  | `/protocols/examples` | List bundled examples |
| `GET`  | `/health` | Health check |

---

## 📝 Protocol JSON Format

```json
{
  "protocol_id":   "PCR-001",
  "protocol_name": "Standard PCR",
  "version":       "1.0.0",

  "reagents": [
    {
      "id":         "dna_template",
      "name":       "Genomic DNA",
      "volume_ul":  100.0,
      "container":  "stock_dna",
      "color":      { "r": 0.1, "g": 0.4, "b": 0.9, "a": 0.8 },
      "concentration": "50 ng/µL"
    }
  ],

  "labware": [
    {
      "id":           "pcr_tube_1",
      "name":         "PCR Reaction Tube",
      "labware_type": "pcr_tube",
      "capacity_ul":  200.0
    },
    {
      "id":           "thermocycler_1",
      "labware_type": "thermocycler",
      "capacity_ul":  9999.0,
      "is_instrument": true
    }
  ],

  "steps": [
    {
      "step_number": 1,
      "action":      "pipette",
      "description": "Add 12.5 µL master mix to PCR tube",
      "source":      "master_mix",
      "destination": "pcr_tube_1",
      "volume_ul":   12.5,
      "tool":        "P20"
    },
    {
      "step_number": 5,
      "action":      "thermocycle",
      "description": "Run PCR (35 cycles)",
      "thermocycle_program": [
        { "name": "Denaturation", "temperature": 95.0, "duration_s": 30, "cycles": 35 },
        { "name": "Annealing",    "temperature": 58.0, "duration_s": 30, "cycles": 35 },
        { "name": "Extension",    "temperature": 72.0, "duration_s": 60, "cycles": 35 }
      ]
    }
  ]
}
```

**Supported `action` types:** `pipette` · `transfer` · `aspirate` · `dispense` · `mix` · `place` · `remove` · `incubate` · `heat` · `cool` · `thermocycle` · `centrifuge` · `vortex` · `seal` · `pause` · `observe`

---

## 🧪 Natural Language Parser

No JSON? Paste a protocol in plain text:

```bash
python -m interface.cli parse my_protocol.txt --output structured.json
```

```text
# my_protocol.txt
1. Add 12.5 µL PCR master mix to reaction tube
2. Add 1 µL DNA template to reaction tube
3. Mix the reaction
4. Place reaction tube in thermocycler
5. Run PCR
```

The parser recognises volumes, reagents, labware, temperatures, and instruments automatically.

---

## ✅ Validation Rules

| Rule | Code | Description |
|------|------|-------------|
| Source exists | R01 | Source reagent/labware must be declared |
| Destination exists | R02 | Destination labware must be declared |
| Pipette range | R03 | Volume within pipette working range |
| Volume available | R04 | Source has enough liquid |
| Dest capacity | R05 | Destination won't overflow |
| Missing tool | R07 | Liquid actions must specify a pipette |
| Instrument present | R08 | Thermocycler/centrifuge must be in labware |
| Mix same container | R11 | MIX source == destination |
| Valid temperature | R12 | Temperature in −200 to 200 °C |
| PCR reagent order | R13 | Master mix added before thermocycling |
| Zero volume | R14 | Transfer volume > 0 |

### Error demo

```bash
python -m interface.cli validate examples/pcr_with_errors.json
```

```
✖  Protocol FAILED — 5 error(s)
  Step  2  [R01]  Source 'primer_mix' is not declared
  Step  2  [R04]  Insufficient volume in 'primer_mix': 0 µL available
  Step  4  [R04]  Insufficient volume: 300 µL requested but 45 µL available
  Step  4  [R05]  Overflow: 300 µL would exceed 25 µL capacity
  Step  6  [R08]  THERMOCYCLE requires a thermocycler in labware
```

---

## 🧬 Example Protocols

| File | Steps | Description |
|------|-------|-------------|
| `pcr_preparation.json` | 8 | Standard PCR (DNA + primers + master mix → thermocycle) |
| `ngs_library_prep.json` | 22 | Illumina NGS library prep (fragmentation → end repair → ligation → PCR) |
| `pcr_with_errors.json` | 6 | Error demo — triggers 5 validation errors |

---

## 🧪 Running Tests

```bash
# All 42 tests
pytest

# With coverage report
pytest --cov --cov-report=term-missing

# Single test class
pytest tests/test_validation.py::TestValidationRules -v
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    User / Interface Layer                    │
│              CLI (cli.py)   FastAPI (api.py)                │
└──────────────────────┬──────────────────┬───────────────────┘
                       │                  │
          ┌────────────▼──────────────────▼──────────────┐
          │           Protocol Layer (JSON / Text)         │
          │   ProtocolModel (Pydantic)  NL Parser          │
          └──────────────────────┬────────────────────────┘
                                 │
          ┌──────────────────────▼────────────────────────┐
          │          Validation Engine (11 rules)          │
          │          ValidationResultModel                  │
          └──────────────────────┬────────────────────────┘
                                 │
          ┌──────────────────────▼────────────────────────┐
          │       Protocol Interpreter                      │
          │   ProtocolModel → [SimulationCommandModel]      │
          └──────────────────────┬────────────────────────┘
                                 │
          ┌──────────────────────▼────────────────────────┐
          │          Blender Simulation Engine              │
          │   ObjectLibrary  AnimationPipeline  bpy         │
          └───────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

- **Python 3.10+**
- **Pydantic v2** — protocol data validation
- **Blender Python API (bpy)** — 3D scene and animation
- **FastAPI + Uvicorn** — optional REST server
- **pytest** — 42-test suite
- **ruff** — linting
- **GitHub Actions** — CI (lint + test × 3 Python versions)

---

## 📄 License

MIT © 2026 [Fatma Duran](https://github.com/fatmadurann)
