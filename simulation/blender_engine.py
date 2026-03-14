"""
Blender Simulation Engine
==========================
Top-level orchestrator that wires together the object library, animation
pipeline, and simulation commands to produce a complete Blender scene from a
validated :class:`ProtocolModel`.

Workflow
--------
1. ``BlenderSimulationEngine.load_protocol(protocol)``
      - validates the protocol
      - builds the 3-D scene (bench + labware + reagents + pipette)
2. ``engine.run()``
      - executes the animation pipeline against the command list
      - returns the total number of frames animated
3. ``engine.export_blend(path)``
      - saves the scene to a .blend file

Error visualisation
-------------------
If any step fails validation, the engine creates a red highlight sphere and a
floating error label, then inserts a flash animation on those frames.

Run context
-----------
Designed to be executed as a Blender Python script (Text Editor → Run Script,
or blender --background --python scene.py).  Can also be imported standalone
for unit-testing (bpy stubs kick in automatically).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import bpy          # type: ignore[import]
    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False
    class _Stub:
        def __getattr__(self, _):  return _Stub()
        def __call__(self, *a, **kw): return _Stub()
    bpy = _Stub()  # type: ignore

# Ensure the project root is in sys.path when running inside Blender
_HERE = Path(__file__).resolve().parent.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from models.protocol_models import (
    ActionType,
    ColorRGBA,
    LabwareType,
    ProtocolModel,
    SimulationCommandModel,
    SimulationCommandType,
)
from simulation.object_library   import LabObjectLibrary, ObjectRecord
from simulation.animation_pipeline import AnimationPipeline
from validation.protocol_validator import ProtocolValidator, ValidationResultModel


# ─────────────────────────────────────────────────────────────────────────────
# Layout constants  (Blender units ≈ metres)
# ─────────────────────────────────────────────────────────────────────────────

BENCH_ORIGIN    = (0.0, 0.0, 0.0)
PIPETTE_REST    = (0.0, -0.15, 0.25)    # above and behind bench centre
TUBE_SPACING    = 0.025                 # 2.5 cm between adjacent tubes
ERROR_LABEL_DZ  = 0.06                  # height above object for error label

# Map labware type → factory method name on LabObjectLibrary
LABWARE_FACTORY: Dict[LabwareType, str] = {
    LabwareType.PCR_TUBE:             "create_pcr_tube",
    LabwareType.MICROCENTRIFUGE_TUBE: "create_eppendorf_tube",
    LabwareType.EPPENDORF_TUBE:       "create_eppendorf_tube",
    LabwareType.MICROPLATE_96:        "create_96well_plate",
    LabwareType.REAGENT_RESERVOIR:    "create_reagent_reservoir",
    LabwareType.THERMOCYCLER:         "create_thermocycler",
    LabwareType.FALCON_TUBE_15ML:     "create_eppendorf_tube",
    LabwareType.FALCON_TUBE_50ML:     "create_eppendorf_tube",
}

# Frames per protocol step (at 24 fps this gives ~4 seconds per step)
FRAMES_PER_STEP = 96


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class BlenderSimulationEngine:
    """
    Full simulation engine: scene construction → validation → animation.

    Parameters
    ----------
    fps              : animation frames per second (default 24)
    frames_per_step  : frames allocated to each protocol step
    auto_validate    : run validator before building scene (default True)
    """

    def __init__(
        self,
        fps:             int  = 24,
        frames_per_step: int  = FRAMES_PER_STEP,
        auto_validate:   bool = True,
    ) -> None:
        self.fps              = fps
        self.frames_per_step  = frames_per_step
        self.auto_validate    = auto_validate

        self.library          = LabObjectLibrary()
        self.pipeline         = AnimationPipeline(fps=fps)
        self.validator        = ProtocolValidator()

        self._protocol:       Optional[ProtocolModel]        = None
        self._validation:     Optional[ValidationResultModel]= None
        self._commands:       List[SimulationCommandModel]   = []
        self._cmd_counter:    int                            = 0

        # name → ObjectRecord for quick lookup during command generation
        self._labware_recs:   Dict[str, ObjectRecord] = {}
        self._reagent_recs:   Dict[str, ObjectRecord] = {}
        self._pipette_rec:    Optional[ObjectRecord]  = None

    # ── Public API ────────────────────────────────────────────────────────────

    def load_protocol(self, protocol: ProtocolModel) -> "BlenderSimulationEngine":
        """
        Validate, build the 3-D scene, and generate the animation command list.
        Returns self for chaining.
        """
        self._protocol = protocol
        logger.info("Loading protocol '%s'…", protocol.protocol_name)

        # Step 1 — Validate
        if self.auto_validate:
            self._validation = self.validator.validate(protocol)
            if not self._validation.is_valid:
                logger.warning(
                    "Protocol has %d validation error(s). "
                    "Error steps will be highlighted in the simulation.",
                    len(self._validation.issues)
                )

        # Step 2 — Build scene
        self._build_scene()

        # Step 3 — Generate commands
        self._generate_commands()

        return self

    def run(self) -> int:
        """Execute the animation pipeline. Returns the last frame animated."""
        if not self._commands:
            raise RuntimeError("No commands loaded. Call load_protocol() first.")

        # Register all blender objects with the pipeline
        for rec in self.library.all_objects():
            if rec.blender_obj is not None:
                self.pipeline.register_object(rec.name, rec.blender_obj)

        return self.pipeline.execute(self._commands)

    def export_blend(self, path: str) -> None:
        """Save the Blender scene to a .blend file."""
        if not BPY_AVAILABLE:
            logger.warning("bpy not available — cannot export .blend file.")
            return
        bpy.ops.wm.save_as_mainfile(filepath=str(path))
        logger.info("Scene saved to %s", path)

    @property
    def validation_result(self) -> Optional[ValidationResultModel]:
        return self._validation

    @property
    def commands(self) -> List[SimulationCommandModel]:
        return list(self._commands)

    # ── Scene construction ────────────────────────────────────────────────────

    def _build_scene(self) -> None:
        """Clear the default scene and populate with lab objects."""
        if BPY_AVAILABLE:
            self._clear_default_scene()

        proto = self._protocol
        assert proto is not None

        # Bench
        self.library.create_bench(
            name="LabBench",
            position=(0.0, 0.0, -0.015),
            size=(0.70, 0.45)
        )

        # Pipette (shared, moves across the scene)
        tool_name = "Pipette_P200"
        self._pipette_rec = self.library.create_pipette(
            name=tool_name,
            pipette_type="P200",
            position=PIPETTE_REST,
        )

        # Place labware
        tube_count = 0
        for lw in proto.labware:
            pos = self._labware_position(lw.labware_type, tube_count)
            factory_name = LABWARE_FACTORY.get(lw.labware_type, "create_eppendorf_tube")
            factory_fn   = getattr(self.library, factory_name)

            # Build kwargs based on method signature
            kwargs: Dict = dict(name=lw.name, labware_id=lw.id, position=pos)
            if factory_name in ("create_pcr_tube", "create_reagent_reservoir"):
                c = lw.color
                kwargs["color"] = (c.r, c.g, c.b, c.a)
            if factory_name == "create_reagent_reservoir":
                kwargs["volume_ul"] = lw.current_volume or 5000.0

            rec = factory_fn(**kwargs)
            self._labware_recs[lw.id] = rec

            if lw.labware_type not in (LabwareType.THERMOCYCLER,
                                        LabwareType.MICROPLATE_96):
                tube_count += 1

        # Place reagent reservoirs / stock tubes for each reagent
        for idx, reagent in enumerate(proto.reagents):
            if reagent.container not in self._labware_recs:
                pos = self._reagent_position(idx)
                c   = reagent.color
                rec = self.library.create_reagent_reservoir(
                    name=f"Stock_{reagent.name}",
                    labware_id=reagent.container,
                    position=pos,
                    color=(c.r, c.g, c.b, c.a),
                    volume_ul=reagent.volume_ul,
                )
                self._reagent_recs[reagent.id] = rec

        logger.info("Scene built: %d labware, %d reagent stocks.",
                    len(self._labware_recs), len(self._reagent_recs))

    @staticmethod
    def _clear_default_scene() -> None:
        """Remove default Blender cube, light, and camera."""
        if not BPY_AVAILABLE:
            return
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        for mesh in list(bpy.data.meshes):
            bpy.data.meshes.remove(mesh)

    @staticmethod
    def _labware_position(lw_type: LabwareType, idx: int) -> Tuple[float, float, float]:
        """Compute a position on the bench for a labware item."""
        if lw_type == LabwareType.THERMOCYCLER:
            return (0.22, 0.0, 0.065)
        if lw_type in (LabwareType.MICROPLATE_96, LabwareType.MICROPLATE_384):
            return (0.0, 0.1, 0.008)
        # Small tubes — row along the bench
        x = -0.15 + idx * TUBE_SPACING
        return (x, -0.05, 0.02)

    @staticmethod
    def _reagent_position(idx: int) -> Tuple[float, float, float]:
        """Position for reagent stock containers at the back of the bench."""
        x = -0.20 + idx * 0.045
        return (x, 0.15, 0.04)

    # ── Command generation ────────────────────────────────────────────────────

    def _next_cmd_id(self) -> int:
        self._cmd_counter += 1
        return self._cmd_counter

    def _step_frames(self, step_number: int) -> Tuple[int, int]:
        """Return (frame_start, frame_end) for a given step."""
        fs = (step_number - 1) * self.frames_per_step + 1
        fe = fs + self.frames_per_step - 1
        return fs, fe

    def _generate_commands(self) -> None:
        """Convert each protocol step into one or more SimulationCommandModels."""
        proto = self._protocol
        assert proto is not None
        error_steps = (
            set(self._validation.steps_with_errors(self._validation))
            if self._validation
            else set()
        )

        for step in proto.steps:
            fs, fe = self._step_frames(step.step_number)
            is_err = step.step_number in error_steps

            # --- Route by action type ---
            action = step.action

            if action in (ActionType.PIPETTE, ActionType.TRANSFER):
                self._cmd_transfer(step, fs, fe)

            elif action == ActionType.ASPIRATE:
                self._cmd_aspirate(step, fs, fe)

            elif action == ActionType.DISPENSE:
                self._cmd_dispense(step, fs, fe)

            elif action == ActionType.MIX:
                self._cmd_mix(step, fs, fe)

            elif action in (ActionType.PLACE, ActionType.THERMOCYCLE):
                self._cmd_place(step, fs, fe)

            elif action in (ActionType.HEAT, ActionType.INCUBATE,
                             ActionType.COOL, ActionType.PAUSE):
                self._cmd_incubate(step, fs, fe)

            elif action == ActionType.CENTRIFUGE:
                self._cmd_centrifuge(step, fs, fe)

            elif action == ActionType.VORTEX:
                self._cmd_vortex(step, fs, fe)

            # Label every step with its description
            self._cmd_step_label(step, fs, fe)

            # Highlight errors
            if is_err:
                self._cmd_error_highlight(step, fs, fe)

        logger.info("Generated %d simulation commands.", len(self._commands))

    # ── Command builders ──────────────────────────────────────────────────────

    def _get_rec_for_id(self, entity_id: Optional[str]) -> Optional[ObjectRecord]:
        """Look up an ObjectRecord by labware or reagent ID."""
        if entity_id is None:
            return None
        return (
            self._labware_recs.get(entity_id)
            or self._reagent_recs.get(entity_id)
            or self.library.get(entity_id)
        )

    def _cmd_transfer(self, step, fs: int, fe: int) -> None:
        src_rec  = self._get_rec_for_id(step.source)
        dest_rec = self._get_rec_for_id(step.destination)
        pip_name = self._pipette_rec.name if self._pipette_rec else "Pipette_P200"

        src_pos  = list(src_rec.position)  if src_rec  else list(PIPETTE_REST)
        dest_pos = list(dest_rec.position) if dest_rec else [0.0, 0.0, 0.1]

        color    = None
        if step.source:
            reagent = self._protocol.get_reagent(step.source)
            if reagent:
                c = reagent.color
                color = ColorRGBA(r=c.r, g=c.g, b=c.b, a=c.a)

        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.ANIMATE_TRANSFER,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = pip_name,
            target_name  = dest_rec.name if dest_rec else None,
            position     = src_pos,
            volume_ul    = step.volume_ul,
            color        = color,
            metadata     = {
                "dest_position": dest_pos,
                "source_id": step.source,
                "dest_id":   step.destination,
            }
        ))

    def _cmd_aspirate(self, step, fs: int, fe: int) -> None:
        src_rec  = self._get_rec_for_id(step.source)
        pip_name = self._pipette_rec.name if self._pipette_rec else "Pipette_P200"
        src_pos  = list(src_rec.position) if src_rec else list(PIPETTE_REST)

        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.ANIMATE_PIPETTE,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = pip_name,
            position     = src_pos,
            volume_ul    = step.volume_ul,
        ))

    def _cmd_dispense(self, step, fs: int, fe: int) -> None:
        dest_rec = self._get_rec_for_id(step.destination)
        pip_name = self._pipette_rec.name if self._pipette_rec else "Pipette_P200"
        dest_pos = list(dest_rec.position) if dest_rec else [0.0, 0.0, 0.1]

        color = None
        if step.source:
            reagent = self._protocol.get_reagent(step.source)
            if reagent:
                c = reagent.color
                color = ColorRGBA(r=c.r, g=c.g, b=c.b, a=c.a)

        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.ANIMATE_LIQUID,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = pip_name,
            target_name  = dest_rec.name + "_liquid" if dest_rec else None,
            position     = dest_pos,
            volume_ul    = step.volume_ul,
            color        = color,
        ))

    def _cmd_mix(self, step, fs: int, fe: int) -> None:
        dest_rec = self._get_rec_for_id(step.destination or step.source)
        pip_name = self._pipette_rec.name if self._pipette_rec else "Pipette_P200"
        dest_pos = list(dest_rec.position) if dest_rec else [0.0, 0.0, 0.1]

        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.ANIMATE_MIX,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = pip_name,
            target_name  = dest_rec.name if dest_rec else None,
            position     = dest_pos,
            volume_ul    = step.volume_ul,
            metadata     = {"mix_cycles": step.mix_cycles or 5},
        ))

    def _cmd_place(self, step, fs: int, fe: int) -> None:
        src_rec  = self._get_rec_for_id(step.source or step.destination)
        dest_rec = self._get_rec_for_id(step.destination)
        if not src_rec:
            return

        dest_pos = list(dest_rec.position) if dest_rec else [0.22, 0.0, 0.10]
        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.PLACE_LABWARE,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = src_rec.name,
            position     = dest_pos,
        ))

    def _cmd_incubate(self, step, fs: int, fe: int) -> None:
        """Show a static 'hold' by pulsing the heat block or tube."""
        lw_rec = self._get_rec_for_id(step.destination or step.source)
        if not lw_rec:
            return
        # Just add a label showing temp + duration
        label = f"{step.temperature or '?'} °C"
        if step.duration_s:
            label += f" / {step.duration_s}s"
        lbl_pos = [
            lw_rec.position[0],
            lw_rec.position[1],
            lw_rec.position[2] + ERROR_LABEL_DZ,
        ]
        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.DISPLAY_LABEL,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"label_incubate_{step.step_number}",
            position     = lbl_pos,
            label_text   = label,
        ))

    def _cmd_centrifuge(self, step, fs: int, fe: int) -> None:
        """Animate tube moving to centrifuge."""
        src_rec  = self._get_rec_for_id(step.source or step.destination)
        cent_rec = next(
            (r for r in self._labware_recs.values()
             if r.object_type == "centrifuge"), None
        )
        if src_rec and cent_rec:
            self._commands.append(SimulationCommandModel(
                command_id   = self._next_cmd_id(),
                command_type = SimulationCommandType.PLACE_LABWARE,
                step_number  = step.step_number,
                frame_start  = fs,
                frame_end    = fe,
                object_name  = src_rec.name,
                position     = list(cent_rec.position),
            ))

    def _cmd_vortex(self, step, fs: int, fe: int) -> None:
        """Animate tube shaking (rapid scale oscillation)."""
        src_rec = self._get_rec_for_id(step.source or step.destination)
        if not src_rec:
            return
        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.ANIMATE_MIX,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = src_rec.name,
            metadata     = {"mix_cycles": 8},
        ))

    def _cmd_step_label(self, step, fs: int, fe: int) -> None:
        """Create a floating step-description label above the active labware."""
        lw_rec = self._get_rec_for_id(step.destination or step.source)
        lbl_pos = [0.0, -0.18, 0.05]
        if lw_rec:
            lbl_pos = [
                lw_rec.position[0],
                lw_rec.position[1] - 0.04,
                lw_rec.position[2] + ERROR_LABEL_DZ * 0.8,
            ]

        short_desc = step.description[:50]
        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.DISPLAY_LABEL,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"label_step_{step.step_number}",
            position     = lbl_pos,
            label_text   = f"[Step {step.step_number}] {short_desc}",
        ))

    def _cmd_error_highlight(self, step, fs: int, fe: int) -> None:
        """Generate error highlight + error label commands for a failed step."""
        lw_rec = self._get_rec_for_id(step.destination or step.source)
        if not lw_rec:
            return

        # Create highlight object (only when bpy is available)
        if BPY_AVAILABLE:
            hl_rec = self.library.create_error_highlight(lw_rec)
        else:
            from simulation.object_library import ObjectRecord
            hl_rec = ObjectRecord(
                name=f"{lw_rec.name}_error_highlight",
                blender_obj=None,
                object_type="error_highlight",
                position=lw_rec.position
            )

        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.HIGHLIGHT_ERROR,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = hl_rec.name,
            is_error     = True,
        ))

        # Error text label
        issues = (
            self._validation.get_step_issues(self._validation, step.step_number)
            if self._validation else []
        )
        msg = issues[0].message[:45] + "…" if issues else "Validation Error"
        lbl_pos = [
            lw_rec.position[0],
            lw_rec.position[1],
            lw_rec.position[2] + ERROR_LABEL_DZ,
        ]
        self._commands.append(SimulationCommandModel(
            command_id   = self._next_cmd_id(),
            command_type = SimulationCommandType.DISPLAY_LABEL,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"label_error_{step.step_number}",
            position     = lbl_pos,
            label_text   = f"❌ {msg}",
            is_error     = True,
            color        = ColorRGBA(r=1.0, g=0.1, b=0.1, a=1.0),
        ))
