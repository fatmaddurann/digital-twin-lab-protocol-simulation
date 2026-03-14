"""
Command-Line Interface
=======================
Provides a clean CLI for the Digital Twin Lab Protocol Simulation system.

Commands
--------
  validate  <protocol.json>              — validate a protocol and report issues
  simulate  <protocol.json> [--output]   — generate Blender scene + animation
  parse     <protocol.txt>  [--output]   — parse free-text protocol → JSON
  summary   <protocol.json>              — print step-by-step simulation summary
  schema                                 — print the JSON schema for protocols

Usage examples
--------------
  python -m interface.cli validate examples/pcr_preparation.json
  python -m interface.cli simulate examples/pcr_preparation.json --output /tmp/sim.blend
  python -m interface.cli parse    examples/pcr_natural_language.txt --output protocol.json
  python -m interface.cli summary  examples/ngs_library_prep.json
  python -m interface.cli schema
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is importable
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from models.protocol_models     import ProtocolModel
from validation.protocol_validator import ProtocolValidator
from interpreter.protocol_interpreter import ProtocolInterpreter, NaturalLanguageParser

# Blender engine import (gracefully fails outside Blender)
try:
    from simulation.blender_engine import BlenderSimulationEngine
    ENGINE_AVAILABLE = True
except Exception:
    ENGINE_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(name)s  %(message)s"
)
logger = logging.getLogger("cli")

# ─── ANSI colour helpers ────────────────────────────────────────────────────

_RED   = "\033[91m"
_GRN   = "\033[92m"
_YLW   = "\033[93m"
_BLD   = "\033[1m"
_RST   = "\033[0m"


def _ok(msg: str)   -> str: return f"{_GRN}✔  {msg}{_RST}"
def _err(msg: str)  -> str: return f"{_RED}✖  {msg}{_RST}"
def _warn(msg: str) -> str: return f"{_YLW}⚠  {msg}{_RST}"
def _hdr(msg: str)  -> str: return f"\n{_BLD}{msg}{_RST}\n{'─'*60}"


# ─── Helpers ────────────────────────────────────────────────────────────────

def _load_protocol(path: str) -> ProtocolModel:
    p = Path(path)
    if not p.exists():
        print(_err(f"File not found: {path}"))
        sys.exit(1)
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    try:
        return ProtocolModel.model_validate(data)
    except Exception as exc:
        print(_err(f"Protocol schema validation failed:\n  {exc}"))
        sys.exit(1)


# ─── Commands ───────────────────────────────────────────────────────────────

def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a protocol JSON and report all issues."""
    protocol = _load_protocol(args.protocol)
    validator = ProtocolValidator()
    result = validator.validate(protocol)

    print(_hdr(f"Validation Report — {protocol.protocol_name}"))
    print(f"  Protocol ID : {protocol.protocol_id}")
    print(f"  Steps       : {len(protocol.steps)}")
    print(f"  Reagents    : {len(protocol.reagents)}")
    print(f"  Labware     : {len(protocol.labware)}")
    print()

    if result.is_valid:
        print(_ok("Protocol is VALID"))
    else:
        print(_err(f"Protocol FAILED validation — {len(result.issues)} error(s)"))

    if result.issues:
        print("\n  Errors:")
        for issue in result.issues:
            print(f"    Step {issue.step_number:>2}  [{issue.code}]  {issue.message}")
            if issue.suggestion:
                print(f"              → {issue.suggestion}")

    if result.warnings:
        print("\n  Warnings:")
        for warn in result.warnings:
            print(f"    Step {warn.step_number:>2}  [{warn.code}]  {warn.message}")

    if args.json:
        out = result.model_dump()
        if args.output:
            Path(args.output).write_text(json.dumps(out, indent=2))
            print(f"\n  Report saved to {args.output}")
        else:
            print("\n" + json.dumps(out, indent=2))

    return 0 if result.is_valid else 1


def cmd_simulate(args: argparse.Namespace) -> int:
    """Generate a Blender simulation from a protocol JSON."""
    protocol = _load_protocol(args.protocol)

    print(_hdr(f"Simulation — {protocol.protocol_name}"))

    # Always validate first
    validator = ProtocolValidator()
    result    = validator.validate(protocol)
    if not result.is_valid:
        print(_warn(
            f"{len(result.issues)} validation error(s) found. "
            "The simulation will highlight invalid steps in red."
        ))
        for issue in result.issues:
            print(f"  Step {issue.step_number}: {issue.message}")

    if not ENGINE_AVAILABLE:
        print(_warn(
            "Blender (bpy) is not available in this Python environment.\n"
            "  To run the simulation:\n"
            "  1. Open Blender\n"
            "  2. Go to the Scripting workspace\n"
            "  3. Open 'run_simulation.py' from this project\n"
            "  4. Press Run Script"
        ))
        # Fall back to printing the interpreter summary
        interpreter = ProtocolInterpreter()
        print("\n  Simulation command summary:")
        cmds = interpreter.interpret(protocol)
        for cmd in cmds[:20]:   # limit to first 20 for display
            print(f"    Frame {cmd.frame_start:>4}–{cmd.frame_end:<4}  "
                  f"{cmd.command_type.value:<22}  step {cmd.step_number}")
        if len(cmds) > 20:
            print(f"    … and {len(cmds) - 20} more commands")
        return 0

    output = args.output or "simulation.blend"
    engine = BlenderSimulationEngine()
    engine.load_protocol(protocol)
    total_frames = engine.run()
    engine.export_blend(output)

    print(_ok(f"Simulation complete — {total_frames} frames"))
    print(f"  Output saved to: {output}")
    return 0


def cmd_parse(args: argparse.Namespace) -> int:
    """Parse a free-text protocol file and output structured JSON."""
    p = Path(args.text_file)
    if not p.exists():
        print(_err(f"File not found: {args.text_file}"))
        return 1

    text = p.read_text(encoding="utf-8")
    parser = NaturalLanguageParser()
    protocol = parser.parse(
        text,
        protocol_name=args.name or p.stem.replace("_", " ").title()
    )

    data = protocol.model_dump()
    out_path = args.output or p.with_suffix(".json").name

    Path(out_path).write_text(json.dumps(data, indent=2), encoding="utf-8")

    print(_hdr("Natural Language Parser"))
    print(f"  Input  : {args.text_file}")
    print(f"  Output : {out_path}")
    print(f"  Steps parsed    : {len(protocol.steps)}")
    print(f"  Reagents found  : {len(protocol.reagents)}")
    print(f"  Labware found   : {len(protocol.labware)}")
    print(_ok("Parsing complete"))
    return 0


def cmd_summary(args: argparse.Namespace) -> int:
    """Print step-by-step simulation plan."""
    protocol = _load_protocol(args.protocol)
    interpreter = ProtocolInterpreter()

    print(_hdr(f"Step Summary — {protocol.protocol_name}"))
    print(f"  {'Step':<6} {'Action':<16} {'Source':<20} {'→':<2} {'Destination':<20} {'Volume':>10}  Description")
    print("  " + "─" * 90)
    for s in interpreter.step_summary(protocol):
        src  = (s["source"]      or "—")[:19]
        dest = (s["destination"] or "—")[:19]
        vol  = f"{s['volume_ul']} µL" if s["volume_ul"] else "—"
        desc = s["description"][:40]
        print(f"  {s['step']:<6} {s['action']:<16} {src:<20}   {dest:<20} {vol:>10}  {desc}")
    print()
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    """Print the JSON schema for ProtocolModel."""
    schema = ProtocolModel.model_json_schema()
    print(json.dumps(schema, indent=2))
    return 0


# ─── Argument parser ────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digital-twin-lab",
        description="Digital Twin for Lab Protocol Simulation — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # validate
    p_val = sub.add_parser("validate", help="Validate a protocol JSON file")
    p_val.add_argument("protocol",         help="Path to protocol JSON")
    p_val.add_argument("--json",           action="store_true",
                       help="Output validation report as JSON")
    p_val.add_argument("--output", "-o",   help="Save JSON report to this path")

    # simulate
    p_sim = sub.add_parser("simulate", help="Generate a Blender simulation")
    p_sim.add_argument("protocol",         help="Path to protocol JSON")
    p_sim.add_argument("--output", "-o",   help="Output .blend file path",
                       default="simulation.blend")

    # parse
    p_prs = sub.add_parser("parse", help="Parse a free-text protocol file")
    p_prs.add_argument("text_file",        help="Path to plain-text protocol")
    p_prs.add_argument("--output", "-o",   help="Output JSON path")
    p_prs.add_argument("--name",  "-n",    help="Protocol name override")

    # summary
    p_sum = sub.add_parser("summary", help="Print simulation step summary")
    p_sum.add_argument("protocol",         help="Path to protocol JSON")

    # schema
    sub.add_parser("schema", help="Print JSON schema for protocols")

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args   = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    handlers = {
        "validate": cmd_validate,
        "simulate": cmd_simulate,
        "parse":    cmd_parse,
        "summary":  cmd_summary,
        "schema":   cmd_schema,
    }
    fn = handlers.get(args.command)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
