"""
run_simulation.py
==================
Main entry point for the Digital Twin Lab Protocol Simulation.

This script can be run in THREE different ways:

─────────────────────────────────────────────────────────────────────────────
MODE 1  —  Inside Blender (Scripting workspace)
─────────────────────────────────────────────────────────────────────────────
  1. Open Blender
  2. Switch to the "Scripting" workspace (top bar)
  3. Open this file with "Open" button in the Text Editor panel
  4. Edit the PROTOCOL_FILE variable to point to your protocol JSON
  5. Press "Run Script" (▶ button or Alt+P)

  Blender will:
    • Clear the default scene
    • Build the 3-D lab environment
    • Animate all protocol steps with keyframes
    • You can then play back the animation with Spacebar

─────────────────────────────────────────────────────────────────────────────
MODE 2  —  Blender headless (command line)
─────────────────────────────────────────────────────────────────────────────
  blender --background --python run_simulation.py -- \\
      --protocol examples/pcr_preparation.json   \\
      --output   /tmp/pcr_simulation.blend

─────────────────────────────────────────────────────────────────────────────
MODE 3  —  Standard Python (validation + command preview only)
─────────────────────────────────────────────────────────────────────────────
  python run_simulation.py --protocol examples/pcr_preparation.json

  Without bpy, the script validates the protocol and prints the full
  simulation command plan without rendering anything in Blender.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# ── Project root on sys.path ──────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s  %(name)s  %(message)s"
)
logger = logging.getLogger("run_simulation")

# ── Try bpy (only available inside Blender) ────────────────────────────────
try:
    import bpy  # type: ignore
    INSIDE_BLENDER = True
except ImportError:
    INSIDE_BLENDER = False

from models.protocol_models         import ProtocolModel
from validation.protocol_validator  import ProtocolValidator
from interpreter.protocol_interpreter import ProtocolInterpreter

if INSIDE_BLENDER:
    from simulation.blender_engine import BlenderSimulationEngine


# ─────────────────────────────────────────────────────────────────────────────
# Default configuration — edit here when running inside Blender
# ─────────────────────────────────────────────────────────────────────────────

PROTOCOL_FILE   = "examples/pcr_preparation.json"   # ← change as needed
OUTPUT_BLEND    = "simulation.blend"
FPS             = 24
FRAMES_PER_STEP = 96


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_protocol(path: str) -> ProtocolModel:
    p = Path(path)
    if not p.is_absolute():
        p = _ROOT / p
    if not p.exists():
        raise FileNotFoundError(f"Protocol file not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return ProtocolModel.model_validate(data)


def print_validation_report(protocol: ProtocolModel) -> bool:
    """Validate and print a colour-coded report. Returns True if valid."""
    validator = ProtocolValidator()
    result    = validator.validate(protocol)

    print("\n" + "=" * 65)
    print(f" Validation Report — {protocol.protocol_name}")
    print("=" * 65)
    print(f"  Status   : {'✔  VALID' if result.is_valid else '✖  INVALID'}")
    print(f"  Steps    : {len(protocol.steps)}")
    print(f"  Errors   : {len(result.issues)}")
    print(f"  Warnings : {len(result.warnings)}")

    for issue in result.issues:
        print(f"\n  [ERROR]  Step {issue.step_number}  [{issue.code}]")
        print(f"           {issue.message}")
        if issue.suggestion:
            print(f"  → Fix : {issue.suggestion}")

    for warn in result.warnings:
        print(f"\n  [WARN]   Step {warn.step_number}  [{warn.code}]")
        print(f"           {warn.message}")

    print("=" * 65 + "\n")
    return result.is_valid


def print_step_summary(protocol: ProtocolModel) -> None:
    """Print a table of all protocol steps with frame ranges."""
    interpreter = ProtocolInterpreter(fps=FPS, frames_per_step=FRAMES_PER_STEP)
    summary     = interpreter.step_summary(protocol)
    cmds        = interpreter.interpret(protocol)

    print(f"\n  Protocol     : {protocol.protocol_name}")
    print(f"  Steps        : {len(protocol.steps)}")
    print(f"  Total frames : {max(c.frame_end for c in cmds) if cmds else 0}")
    print(f"  Duration @ {FPS}fps : "
          f"{max(c.frame_end for c in cmds) / FPS:.1f}s" if cmds else "  0 s")
    print()
    print(f"  {'Step':<5}  {'Action':<16}  {'Source':<18}  {'→':<1}  "
          f"{'Destination':<18}  {'Vol':>8}  Frames")
    print("  " + "─" * 80)
    for s in summary:
        src  = (s["source"]      or "—")[:17]
        dest = (s["destination"] or "—")[:17]
        vol  = f"{s['volume_ul']} µL" if s["volume_ul"] else "—"
        print(f"  {s['step']:<5}  {s['action']:<16}  {src:<18}  →  "
              f"{dest:<18}  {vol:>8}  {s['frame_start']}–{s['frame_end']}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main simulation routine
# ─────────────────────────────────────────────────────────────────────────────

def run(protocol_path: str, output_path: str) -> None:
    logger.info("Loading protocol: %s", protocol_path)
    protocol = load_protocol(protocol_path)

    # Always validate
    is_valid = print_validation_report(protocol)
    if not is_valid:
        logger.warning(
            "Protocol has validation errors. "
            "Invalid steps will be highlighted red in the simulation."
        )

    # Print step plan
    print_step_summary(protocol)

    if not INSIDE_BLENDER:
        print("  ℹ  Blender (bpy) is not available in this environment.")
        print("  ✓  Validation and command preview complete.")
        print("  →  To render: open Blender, load this script in the")
        print("     Scripting workspace, and press Run Script.\n")
        return

    # Build and run Blender simulation
    logger.info("Building Blender scene and generating animation …")
    engine = BlenderSimulationEngine(
        fps=FPS,
        frames_per_step=FRAMES_PER_STEP,
        auto_validate=False,   # already validated above
    )
    engine.load_protocol(protocol)
    total_frames = engine.run()

    if output_path:
        engine.export_blend(output_path)
        logger.info("Saved: %s  (%d frames)", output_path, total_frames)

    print(f"\n  ✔  Simulation complete — {total_frames} frames animated.")
    if output_path:
        print(f"  ✔  Saved to: {output_path}")
    print("  →  Press Spacebar in Blender to play back the animation.\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI argument parsing (for headless / standalone usage)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    """
    Parse arguments, stripping Blender's own arguments (everything before '--').
    """
    raw = sys.argv[1:]
    if "--" in raw:
        raw = raw[raw.index("--") + 1:]

    parser = argparse.ArgumentParser(
        prog="run_simulation.py",
        description="Digital Twin Lab Protocol Simulation",
    )
    parser.add_argument(
        "--protocol", "-p",
        default=PROTOCOL_FILE,
        help=f"Path to protocol JSON (default: {PROTOCOL_FILE})"
    )
    parser.add_argument(
        "--output", "-o",
        default=OUTPUT_BLEND,
        help=f"Output .blend file path (default: {OUTPUT_BLEND})"
    )
    return parser.parse_args(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = _parse_args()
    run(protocol_path=args.protocol, output_path=args.output)
