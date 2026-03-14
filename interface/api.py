"""
FastAPI Web Interface
======================
RESTful API for the Digital Twin Lab Protocol Simulation system.

Endpoints
---------
  POST /protocols/validate        — validate a protocol JSON
  POST /protocols/simulate        — generate simulation commands (no Blender render)
  POST /protocols/parse           — parse free-text protocol → JSON
  GET  /protocols/schema          — return the ProtocolModel JSON schema
  GET  /protocols/examples        — list available example protocols
  GET  /protocols/examples/{name} — retrieve an example protocol JSON
  GET  /health                    — health check

Run the server
--------------
  pip install fastapi uvicorn
  uvicorn interface.api:app --reload --port 8000

Then navigate to:
  http://localhost:8000/docs   (interactive Swagger UI)
  http://localhost:8000/redoc  (ReDoc API docs)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException, Body
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel as _BaseModel
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    logger.warning("FastAPI not installed — API server not available. "
                   "Run: pip install fastapi uvicorn")

from models.protocol_models import ProtocolModel
from validation.protocol_validator import ProtocolValidator
from interpreter.protocol_interpreter import ProtocolInterpreter, NaturalLanguageParser

# ─────────────────────────────────────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────────────────────────────────────

if FASTAPI_AVAILABLE:

    class ValidateRequest(_BaseModel):
        protocol: Dict[str, Any]

    class ParseRequest(_BaseModel):
        text:          str
        protocol_name: str = "Parsed Protocol"

    class SimulateRequest(_BaseModel):
        protocol: Dict[str, Any]
        fps:             int = 24
        frames_per_step: int = 96

    class StepSummaryItem(_BaseModel):
        step:        int
        action:      str
        description: str
        frame_start: int
        frame_end:   int
        volume_ul:   Optional[float]
        source:      Optional[str]
        destination: Optional[str]
        tool:        Optional[str]

    # ── App factory ───────────────────────────────────────────────────────────

    app = FastAPI(
        title       = "Digital Twin for Lab Protocol Simulation",
        description = (
            "Convert laboratory protocols into 3-D Blender simulations.\n\n"
            "Upload a protocol JSON, validate its logic, and generate a "
            "complete animation command list ready for Blender rendering."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Singletons
    _validator   = ProtocolValidator()
    _interpreter = ProtocolInterpreter()
    _nl_parser   = NaturalLanguageParser()

    # ── Routes ────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["System"])
    def health():
        """Health check — returns service status."""
        return {"status": "ok", "service": "DigitalTwinLab"}


    @app.get("/protocols/schema", tags=["Protocols"])
    def get_schema():
        """Return the JSON Schema for the ProtocolModel."""
        return ProtocolModel.model_json_schema()


    @app.get("/protocols/examples", tags=["Protocols"])
    def list_examples():
        """List available example protocol files."""
        examples_dir = _ROOT / "examples"
        files = sorted(examples_dir.glob("*.json"))
        return [{"name": f.stem, "filename": f.name} for f in files]


    @app.get("/protocols/examples/{name}", tags=["Protocols"])
    def get_example(name: str):
        """Retrieve an example protocol by name (without .json extension)."""
        path = _ROOT / "examples" / f"{name}.json"
        if not path.exists():
            raise HTTPException(404, f"Example '{name}' not found.")
        return json.loads(path.read_text())


    @app.post("/protocols/validate", tags=["Protocols"])
    def validate_protocol(body: ValidateRequest):
        """
        Validate a protocol JSON for logical correctness.

        Returns a full validation report including all errors and warnings.
        """
        try:
            protocol = ProtocolModel.model_validate(body.protocol)
        except Exception as exc:
            raise HTTPException(422, f"Schema validation failed: {exc}")

        result = _validator.validate(protocol)
        return {
            "protocol_id": result.protocol_id,
            "is_valid":    result.is_valid,
            "summary":     result.summary,
            "errors":      [i.model_dump() for i in result.issues],
            "warnings":    [i.model_dump() for i in result.warnings],
        }


    @app.post("/protocols/simulate", tags=["Protocols"])
    def simulate_protocol(body: SimulateRequest):
        """
        Generate animation simulation commands for a protocol.

        Returns the list of ``SimulationCommandModel`` objects that would be
        executed inside Blender.  Use these to preview or debug the animation
        plan before running the full Blender render.
        """
        try:
            protocol = ProtocolModel.model_validate(body.protocol)
        except Exception as exc:
            raise HTTPException(422, f"Schema validation failed: {exc}")

        # Validate first
        val_result = _validator.validate(protocol)

        # Generate simulation commands
        interpreter = ProtocolInterpreter(
            fps=body.fps, frames_per_step=body.frames_per_step
        )
        commands = interpreter.interpret(protocol)
        summary  = interpreter.step_summary(protocol)

        return {
            "protocol_id":   protocol.protocol_id,
            "protocol_name": protocol.protocol_name,
            "is_valid":      val_result.is_valid,
            "validation_summary": val_result.summary,
            "total_frames":  max((c.frame_end for c in commands), default=0),
            "total_commands": len(commands),
            "step_summary":  summary,
            "commands":      [c.model_dump() for c in commands],
        }


    @app.post("/protocols/parse", tags=["Protocols"])
    def parse_protocol(body: ParseRequest):
        """
        Parse a free-text (natural language) laboratory protocol into structured JSON.

        The parser recognises common lab instruction patterns such as:
        - "Add 10 µL DNA sample to PCR tube"
        - "Incubate at 95 °C for 5 min"
        - "Run PCR"
        """
        protocol = _nl_parser.parse(body.text, protocol_name=body.protocol_name)
        return protocol.model_dump()


else:
    # FastAPI not installed — provide a stub so the module can be imported
    class _StubApp:
        def get(self, *a, **kw):   return lambda f: f
        def post(self, *a, **kw):  return lambda f: f
        def add_middleware(self, *a, **kw): pass

    app = _StubApp()  # type: ignore
    logger.error(
        "FastAPI is not installed. "
        "Install it with: pip install fastapi uvicorn"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dev server entry-point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not FASTAPI_AVAILABLE:
        print("FastAPI is not installed. Run: pip install fastapi uvicorn")
        sys.exit(1)
    try:
        import uvicorn  # type: ignore
        uvicorn.run("interface.api:app", host="0.0.0.0", port=8000, reload=True)
    except ImportError:
        print("uvicorn is not installed. Run: pip install uvicorn")
        sys.exit(1)
