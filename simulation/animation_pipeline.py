"""
Animation Pipeline
==================
Converts a list of :class:`SimulationCommandModel` objects into concrete
Blender keyframe animations.

Architecture
------------
Each simulation command has a `frame_start` and `frame_end`.  The pipeline
iterates commands in order and calls the appropriate *animator* method, which
inserts keyframes into the Blender timeline.

Animators
---------
  animate_pipette_move    — smooth arc trajectory from rest to target
  animate_liquid_transfer — grow/shrink cylinder representing liquid volume
  animate_mix             — oscillating up-down movement in the same container
  animate_place_labware   — smooth slide from bench origin to instrument
  animate_error_flash     — pulsing red emission on the error highlight object
  animate_label_appear    — fade-in of text object

All frame-time calculations assume a 24 fps timeline.  This is configurable
via the `fps` constructor parameter.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import bpy                           # type: ignore[import]
    from mathutils import Vector, Euler  # type: ignore[import]
    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False
    class _Stub:
        def __getattr__(self, _):  return _Stub()
        def __call__(self, *a, **kw): return _Stub()
    bpy    = _Stub()  # type: ignore
    Vector = tuple    # type: ignore
    Euler  = tuple    # type: ignore

from models.protocol_models import SimulationCommandModel, SimulationCommandType


# ─────────────────────────────────────────────────────────────────────────────
# Keyframe helpers
# ─────────────────────────────────────────────────────────────────────────────

def _insert_location_keyframe(obj: Any, frame: int) -> None:
    if BPY_AVAILABLE and obj:
        obj.keyframe_insert(data_path="location", frame=frame)


def _insert_rotation_keyframe(obj: Any, frame: int) -> None:
    if BPY_AVAILABLE and obj:
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)


def _insert_scale_keyframe(obj: Any, frame: int) -> None:
    if BPY_AVAILABLE and obj:
        obj.keyframe_insert(data_path="scale", frame=frame)


def _insert_visibility_keyframe(obj: Any, frame: int, visible: bool) -> None:
    if BPY_AVAILABLE and obj:
        obj.hide_viewport = not visible
        obj.hide_render   = not visible
        obj.keyframe_insert(data_path="hide_viewport", frame=frame)
        obj.keyframe_insert(data_path="hide_render",   frame=frame)


def _set_material_emission(obj: Any, strength: float) -> None:
    """Set Principled BSDF emission strength on the first material slot."""
    if not (BPY_AVAILABLE and obj):
        return
    for slot in obj.material_slots:
        mat = slot.material
        if mat and mat.use_nodes:
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf and "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = strength


def _insert_emission_keyframe(obj: Any, frame: int) -> None:
    if not (BPY_AVAILABLE and obj):
        return
    for slot in obj.material_slots:
        mat = slot.material
        if mat and mat.use_nodes:
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf and "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].keyframe_insert(
                    data_path="default_value", frame=frame
                )


def _lerp_3(a: Tuple, b: Tuple, t: float) -> Tuple:
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


# ─────────────────────────────────────────────────────────────────────────────
# Animation pipeline
# ─────────────────────────────────────────────────────────────────────────────

class AnimationPipeline:
    """
    Executes a sequence of SimulationCommandModels as Blender keyframe animations.

    Parameters
    ----------
    fps          : frames per second (default 24)
    object_store : mapping from object name → bpy.types.Object (or stub)
    """

    def __init__(
        self,
        fps: int = 24,
        object_store: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.fps          = fps
        self.object_store: Dict[str, Any] = object_store or {}
        self._last_frame  = 0

    # ── Object store ──────────────────────────────────────────────────────────

    def register_object(self, name: str, blender_obj: Any) -> None:
        self.object_store[name] = blender_obj

    def get_object(self, name: Optional[str]) -> Optional[Any]:
        if name is None:
            return None
        return self.object_store.get(name)

    # ── Scene setup ───────────────────────────────────────────────────────────

    def setup_scene(self, total_frames: int) -> None:
        """Configure Blender scene: FPS, start/end frames, camera, lighting."""
        if not BPY_AVAILABLE:
            return
        scene = bpy.context.scene
        scene.render.fps     = self.fps
        scene.frame_start    = 1
        scene.frame_end      = max(total_frames, 250)
        scene.frame_current  = 1

        # Ensure Eevee for real-time preview (faster than Cycles for lab sim)
        scene.render.engine = "BLENDER_EEVEE_NEXT" if hasattr(
            bpy.context.scene.render, "engine"
        ) else "BLENDER_EEVEE"
        if hasattr(scene.eevee, "use_ssr"):
            scene.eevee.use_ssr = True  # screen-space reflections for liquids

        self._setup_lighting()
        self._setup_camera()

    def _setup_lighting(self) -> None:
        if not BPY_AVAILABLE:
            return
        # Key light — overhead area light simulating lab ceiling
        bpy.ops.object.light_add(type="AREA", location=(0.0, 0.0, 0.8))
        key = bpy.context.active_object
        key.name = "KeyLight"
        key.data.energy = 200
        key.data.size   = 0.5
        key.rotation_euler = (0.0, 0.0, 0.0)

        # Fill light — softer side light
        bpy.ops.object.light_add(type="POINT", location=(-0.3, -0.2, 0.5))
        fill = bpy.context.active_object
        fill.name = "FillLight"
        fill.data.energy = 60

    def _setup_camera(self) -> None:
        if not BPY_AVAILABLE:
            return
        bpy.ops.object.camera_add(
            location=(0.0, -0.45, 0.35),
            rotation=(math.radians(55), 0.0, 0.0)
        )
        cam = bpy.context.active_object
        cam.name = "SimCamera"
        bpy.context.scene.camera = cam
        cam.data.lens = 50  # standard focal length

    # ── Main executor ─────────────────────────────────────────────────────────

    def execute(self, commands: List[SimulationCommandModel]) -> int:
        """
        Execute all simulation commands and return the last frame used.

        Commands are executed in order.  Each command's frame_start /
        frame_end values are respected.
        """
        if not commands:
            logger.warning("No simulation commands to execute.")
            return 0

        total = max(cmd.frame_end for cmd in commands)
        self.setup_scene(total)

        for cmd in commands:
            logger.debug(
                "Executing command %d (%s) frames %d–%d",
                cmd.command_id, cmd.command_type.value,
                cmd.frame_start, cmd.frame_end
            )
            self._dispatch(cmd)
            self._last_frame = max(self._last_frame, cmd.frame_end)

        logger.info("Animation pipeline complete — %d frames total.", self._last_frame)
        return self._last_frame

    def _dispatch(self, cmd: SimulationCommandModel) -> None:
        """Route a command to the appropriate animator."""
        dispatch_map = {
            SimulationCommandType.ANIMATE_PIPETTE:   self._animate_pipette_move,
            SimulationCommandType.ANIMATE_LIQUID:    self._animate_liquid_fill,
            SimulationCommandType.ANIMATE_TRANSFER:  self._animate_full_transfer,
            SimulationCommandType.ANIMATE_MIX:       self._animate_mix,
            SimulationCommandType.PLACE_LABWARE:     self._animate_place_labware,
            SimulationCommandType.HIGHLIGHT_ERROR:   self._animate_error_flash,
            SimulationCommandType.DISPLAY_LABEL:     self._animate_label_appear,
            SimulationCommandType.MOVE_OBJECT:       self._animate_move_object,
            SimulationCommandType.ADD_KEYFRAME:      self._add_raw_keyframe,
        }
        fn = dispatch_map.get(cmd.command_type)
        if fn:
            fn(cmd)
        else:
            logger.debug("No animator for command type '%s' — skipping.", cmd.command_type.value)

    # ── Individual animators ──────────────────────────────────────────────────

    def _animate_pipette_move(self, cmd: SimulationCommandModel) -> None:
        """
        Animate a pipette moving in a smooth parabolic arc from its current
        position to `cmd.position`, then back to rest.

        The arc peaks halfway between start and target, rising ~0.05 m above
        the straight-line path.
        """
        pipette = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and pipette and cmd.position):
            return

        start_pos  = tuple(pipette.location)
        target_pos = tuple(cmd.position)
        mid_z      = max(start_pos[2], target_pos[2]) + 0.08

        fs, fe = cmd.frame_start, cmd.frame_end
        mid_f  = (fs + fe) // 2

        # Keyframe at start
        pipette.location = start_pos
        _insert_location_keyframe(pipette, fs)

        # Arc apex
        apex = _lerp_3(start_pos, target_pos, 0.5)
        pipette.location = (apex[0], apex[1], mid_z)
        _insert_location_keyframe(pipette, mid_f)

        # Keyframe at destination
        pipette.location = target_pos
        _insert_location_keyframe(pipette, fe)

        # Smooth interpolation
        self._set_bezier_interpolation(pipette, "location", fs, fe)

    def _animate_liquid_fill(self, cmd: SimulationCommandModel) -> None:
        """
        Animate the height of a liquid column changing (volume added/removed).
        Uses Z-scale keyframes on the liquid object.
        """
        liquid = self.get_object(cmd.target_name)
        if not (BPY_AVAILABLE and liquid):
            return

        fs, fe = cmd.frame_start, cmd.frame_end
        # Record current scale
        start_sz = liquid.scale[2]
        # Compute new scale based on volume change (heuristic: 1 µL ≈ 0.001 m³)
        vol = cmd.volume_ul or 10.0
        delta_sz = (vol / 50.0) * 0.01  # scale increment per µL
        end_sz = max(start_sz + delta_sz, 0.001)

        liquid.scale = (liquid.scale[0], liquid.scale[1], start_sz)
        _insert_scale_keyframe(liquid, fs)
        liquid.scale = (liquid.scale[0], liquid.scale[1], end_sz)
        _insert_scale_keyframe(liquid, fe)

    def _animate_full_transfer(self, cmd: SimulationCommandModel) -> None:
        """
        Orchestrate a complete pipette transfer:
        1. Move pipette to source
        2. Aspirate (shrink source liquid)
        3. Move to destination
        4. Dispense (grow dest liquid)
        5. Return pipette to rest
        """
        if not BPY_AVAILABLE:
            return

        pipette = self.get_object(cmd.object_name)
        source  = self.get_object(cmd.object_name + "_src_liquid") or \
                  self.get_object(cmd.target_name  + "_liquid")
        dest_liq= self.get_object(cmd.target_name  + "_liquid") or \
                  self.get_object(cmd.target_name)

        if not pipette:
            return

        fs, fe = cmd.frame_start, cmd.frame_end
        span   = fe - fs
        q1, q2, q3, q4 = (
            fs + span // 5,
            fs + 2 * span // 5,
            fs + 3 * span // 5,
            fs + 4 * span // 5,
        )

        # 1. Move to source
        if cmd.position:
            self._keyframe_location(pipette, cmd.position, fs, q1)

        # 2. Aspirate from source (scale down)
        if source:
            sz = source.scale[2]
            source.scale = (source.scale[0], source.scale[1], sz)
            _insert_scale_keyframe(source, q1)
            source.scale = (source.scale[0], source.scale[1], max(sz - 0.005, 0.001))
            _insert_scale_keyframe(source, q2)

        # 3. Move to destination
        if cmd.metadata and "dest_position" in cmd.metadata:
            dest_pos = tuple(cmd.metadata["dest_position"])
            self._keyframe_location(pipette, dest_pos, q2, q3)

        # 4. Dispense into destination (scale up)
        if dest_liq and dest_liq is not source:
            sz = dest_liq.scale[2]
            dest_liq.scale = (dest_liq.scale[0], dest_liq.scale[1], sz)
            _insert_scale_keyframe(dest_liq, q3)
            dest_liq.scale = (dest_liq.scale[0], dest_liq.scale[1], sz + 0.005)
            _insert_scale_keyframe(dest_liq, q4)

        # 5. Return to rest
        rest = tuple(pipette.location)
        self._keyframe_location(pipette, rest, q4, fe)

    def _animate_mix(self, cmd: SimulationCommandModel) -> None:
        """
        Animate repeated up-down pipette oscillation to represent mixing.
        Number of cycles defaults to 3.
        """
        pipette = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and pipette):
            return

        cycles     = (cmd.metadata or {}).get("mix_cycles", 3)
        fs, fe     = cmd.frame_start, cmd.frame_end
        cycle_span = (fe - fs) // max(cycles, 1)
        base_loc   = tuple(pipette.location)
        dip_z      = base_loc[2] - 0.015

        for c in range(cycles):
            cf = fs + c * cycle_span
            # down
            pipette.location = (base_loc[0], base_loc[1], dip_z)
            _insert_location_keyframe(pipette, cf + cycle_span // 3)
            # up
            pipette.location = base_loc
            _insert_location_keyframe(pipette, cf + cycle_span)

    def _animate_place_labware(self, cmd: SimulationCommandModel) -> None:
        """Slide a labware item to a target instrument position."""
        obj = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and obj and cmd.position):
            return
        start = tuple(obj.location)
        self._keyframe_location(obj, start,          cmd.frame_start, cmd.frame_start)
        self._keyframe_location(obj, tuple(cmd.position), cmd.frame_start, cmd.frame_end)

    def _animate_error_flash(self, cmd: SimulationCommandModel) -> None:
        """
        Make the error-highlight sphere pulse red by alternating emission
        strength between 0 and 5 over 4 beats.
        """
        obj = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and obj):
            return

        fs, fe  = cmd.frame_start, cmd.frame_end
        span    = fe - fs
        beat    = max(span // 4, 2)

        for i in range(5):
            strength = 5.0 if i % 2 == 0 else 0.0
            _set_material_emission(obj, strength)
            _insert_emission_keyframe(obj, fs + i * beat)

        # Also make visible at start, fade out at end
        _insert_visibility_keyframe(obj, fs,  True)
        _insert_visibility_keyframe(obj, fe,  False)

    def _animate_label_appear(self, cmd: SimulationCommandModel) -> None:
        """Fade a text label in over 12 frames using scale."""
        obj = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and obj):
            return

        obj.scale = (0.0, 0.0, 0.0)
        _insert_scale_keyframe(obj, cmd.frame_start)
        obj.scale = (1.0, 1.0, 1.0)
        _insert_scale_keyframe(obj, cmd.frame_start + 12)

    def _animate_move_object(self, cmd: SimulationCommandModel) -> None:
        """Generic linear move of any object to cmd.position."""
        obj = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and obj and cmd.position):
            return
        self._keyframe_location(obj, tuple(cmd.position), cmd.frame_start, cmd.frame_end)

    def _add_raw_keyframe(self, cmd: SimulationCommandModel) -> None:
        """Insert a raw location keyframe (used for object initialisation)."""
        obj = self.get_object(cmd.object_name)
        if not (BPY_AVAILABLE and obj):
            return
        if cmd.position:
            obj.location = tuple(cmd.position)
        _insert_location_keyframe(obj, cmd.frame_start)

    # ── Low-level helpers ─────────────────────────────────────────────────────

    def _keyframe_location(
        self,
        obj: Any,
        target: Tuple,
        frame_start: int,
        frame_end: int,
    ) -> None:
        """Keyframe object location from current to target over [start, end]."""
        if not (BPY_AVAILABLE and obj):
            return
        obj.keyframe_insert(data_path="location", frame=frame_start)
        obj.location = target[:3]
        _insert_location_keyframe(obj, frame_end)

    @staticmethod
    def _set_bezier_interpolation(obj: Any, data_path: str,
                                   frame_start: int, frame_end: int) -> None:
        """Apply BEZIER interpolation to the keyframes in [start, end]."""
        if not BPY_AVAILABLE:
            return
        if not obj.animation_data or not obj.animation_data.action:
            return
        for fcurve in obj.animation_data.action.fcurves:
            if fcurve.data_path == data_path:
                for kf in fcurve.keyframe_points:
                    if frame_start <= kf.co[0] <= frame_end:
                        kf.interpolation = "BEZIER"
                        kf.handle_left_type  = "AUTO"
                        kf.handle_right_type = "AUTO"
