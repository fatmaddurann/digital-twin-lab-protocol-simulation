"""
Lab Object Library
==================
Creates and manages 3-D Blender objects for every piece of laboratory equipment
and reagent used in a protocol simulation.

All geometry is built procedurally from Blender primitives (cylinders, cubes,
spheres) so no external mesh files are required.  Materials use Principled BSDF
with physically based colour, roughness and transmission values appropriate to
lab plasticware and liquids.

Run context
-----------
This module is executed *inside* Blender (the `bpy` module is available).
When imported outside Blender for testing / type-checking, `bpy` is mocked
gracefully and all public methods become no-ops.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Try to import bpy; fall back to a lightweight stub for unit-test contexts ──
try:
    import bpy                          # type: ignore[import]
    import bmesh                        # type: ignore[import]
    from mathutils import Vector, Euler # type: ignore[import]
    BPY_AVAILABLE = True
except ImportError:
    BPY_AVAILABLE = False
    logger.warning("bpy not available — running in stub mode (no Blender rendering).")
    # Minimal stubs so the module can be imported outside Blender
    class _Stub:
        def __getattr__(self, _: str) -> "_Stub":
            return _Stub()
        def __call__(self, *a: Any, **kw: Any) -> "_Stub":
            return _Stub()
        def __iter__(self):
            return iter([])
    bpy    = _Stub()  # type: ignore
    bmesh  = _Stub()  # type: ignore
    Vector = tuple    # type: ignore
    Euler  = tuple    # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# Helper dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ObjectRecord:
    """Keeps track of a Blender object created by the library."""
    name:          str
    blender_obj:   Any              # bpy.types.Object (or stub)
    object_type:   str              # "pipette" | "tube" | "liquid" | ...
    labware_id:    Optional[str]    = None
    reagent_id:    Optional[str]    = None
    position:      Tuple[float, float, float] = (0.0, 0.0, 0.0)
    liquid_objs:   List[Any]        = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Material factory
# ─────────────────────────────────────────────────────────────────────────────

class MaterialFactory:
    """Creates or reuses Principled BSDF materials."""

    _cache: Dict[str, Any] = {}

    @classmethod
    def get_or_create(
        cls,
        name: str,
        base_color: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        roughness: float = 0.1,
        transmission: float = 0.0,
        metallic: float = 0.0,
        emission: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        emission_strength: float = 0.0,
    ) -> Any:
        if not BPY_AVAILABLE:
            return None
        if name in cls._cache:
            return cls._cache[name]

        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value    = base_color
            bsdf.inputs["Roughness"].default_value     = roughness
            bsdf.inputs["Metallic"].default_value      = metallic
            if "Transmission" in bsdf.inputs:
                bsdf.inputs["Transmission"].default_value  = transmission
            if "Emission" in bsdf.inputs:
                bsdf.inputs["Emission"].default_value  = (*emission, 1.0)
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = emission_strength

        # Enable transparency for glass/liquid materials
        if transmission > 0:
            mat.blend_method   = "BLEND"
            mat.show_transparent_back = False

        cls._cache[name] = mat
        return mat

    @classmethod
    def plastic_clear(cls) -> Any:
        return cls.get_or_create(
            "mat_plastic_clear",
            base_color=(0.9, 0.95, 1.0, 0.3),
            roughness=0.05,
            transmission=0.85,
        )

    @classmethod
    def plastic_white(cls) -> Any:
        return cls.get_or_create(
            "mat_plastic_white",
            base_color=(0.95, 0.95, 0.95, 1.0),
            roughness=0.3,
        )

    @classmethod
    def plastic_grey(cls) -> Any:
        return cls.get_or_create(
            "mat_plastic_grey",
            base_color=(0.55, 0.55, 0.55, 1.0),
            roughness=0.4,
        )

    @classmethod
    def metal_stainless(cls) -> Any:
        return cls.get_or_create(
            "mat_stainless",
            base_color=(0.8, 0.8, 0.82, 1.0),
            roughness=0.15,
            metallic=0.9,
        )

    @classmethod
    def error_highlight(cls) -> Any:
        return cls.get_or_create(
            "mat_error",
            base_color=(1.0, 0.05, 0.05, 1.0),
            roughness=0.2,
            emission=(1.0, 0.0, 0.0),
            emission_strength=2.0,
        )

    @classmethod
    def liquid(
        cls,
        r: float, g: float, b: float, a: float = 0.75
    ) -> Any:
        name = f"mat_liquid_{r:.2f}_{g:.2f}_{b:.2f}"
        return cls.get_or_create(
            name,
            base_color=(r, g, b, a),
            roughness=0.0,
            transmission=0.6,
        )

    @classmethod
    def thermocycler_body(cls) -> Any:
        return cls.get_or_create(
            "mat_thermocycler",
            base_color=(0.12, 0.12, 0.14, 1.0),
            roughness=0.35,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Object library
# ─────────────────────────────────────────────────────────────────────────────

class LabObjectLibrary:
    """
    Procedurally creates and tracks Blender 3-D objects for every
    lab entity in a protocol simulation.

    All public `create_*` methods return an :class:`ObjectRecord` and also
    register the object in the internal registry for later lookup.
    """

    # Scale factor: 1 Blender unit = 10 cm
    SCALE = 0.1

    def __init__(self) -> None:
        self._registry: Dict[str, ObjectRecord] = {}

    # ── Registry helpers ──────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[ObjectRecord]:
        return self._registry.get(name)

    def all_objects(self) -> List[ObjectRecord]:
        return list(self._registry.values())

    def _register(self, rec: ObjectRecord) -> ObjectRecord:
        self._registry[rec.name] = rec
        return rec

    # ── Generic Blender helpers ───────────────────────────────────────────────

    @staticmethod
    def _set_location(obj: Any, x: float, y: float, z: float) -> None:
        if BPY_AVAILABLE and obj:
            obj.location = (x, y, z)

    @staticmethod
    def _add_material(obj: Any, mat: Any) -> None:
        if BPY_AVAILABLE and obj and mat:
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)

    @staticmethod
    def _deselect_all() -> None:
        if BPY_AVAILABLE:
            bpy.ops.object.select_all(action="DESELECT")

    # ── Pipette ───────────────────────────────────────────────────────────────

    def create_pipette(
        self,
        name: str = "Pipette",
        pipette_type: str = "P200",
        position: Tuple[float, float, float] = (0.0, 0.0, 0.3),
    ) -> ObjectRecord:
        """Create a pipette object (cylinder body + tapered tip)."""
        logger.debug("Creating pipette '%s' at %s", name, position)
        blender_obj = None

        if BPY_AVAILABLE:
            # Body — tall cylinder
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.015, depth=0.22, location=position
            )
            body = bpy.context.active_object
            body.name = f"{name}_body"

            # Tip — cone tapering downward
            tip_pos = (position[0], position[1], position[2] - 0.13)
            bpy.ops.mesh.primitive_cone_add(
                radius1=0.012, radius2=0.002, depth=0.05,
                location=tip_pos
            )
            tip = bpy.context.active_object
            tip.name = f"{name}_tip"

            # Button top
            btn_pos = (position[0], position[1], position[2] + 0.115)
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.018, depth=0.015, location=btn_pos
            )
            button = bpy.context.active_object
            button.name = f"{name}_button"

            # Apply materials
            self._add_material(body,   MaterialFactory.plastic_clear())
            self._add_material(tip,    MaterialFactory.plastic_white())
            self._add_material(button, MaterialFactory.plastic_grey())

            # Parent tip and button to body
            self._deselect_all()
            tip.select_set(True)
            button.select_set(True)
            body.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.parent_set(type="OBJECT")
            blender_obj = body

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="pipette", position=position
        )
        return self._register(rec)

    # ── PCR / micro tube ─────────────────────────────────────────────────────

    def create_pcr_tube(
        self,
        name: str = "PCRTube",
        labware_id: Optional[str] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        color: Tuple[float, float, float, float] = (0.9, 0.95, 1.0, 0.4),
    ) -> ObjectRecord:
        """Create a PCR tube (cylinder body + conical bottom cap)."""
        logger.debug("Creating PCR tube '%s' at %s", name, position)
        blender_obj = None

        if BPY_AVAILABLE:
            # Tube body
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.004, depth=0.038, location=position
            )
            body = bpy.context.active_object
            body.name = f"{name}_body"

            # Conical bottom
            bot_pos = (position[0], position[1], position[2] - 0.022)
            bpy.ops.mesh.primitive_cone_add(
                radius1=0.004, radius2=0.001, depth=0.008,
                location=bot_pos
            )
            cone = bpy.context.active_object
            cone.name = f"{name}_cone"

            # Lid (small flat cylinder)
            lid_pos = (position[0], position[1], position[2] + 0.021)
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.0045, depth=0.004, location=lid_pos
            )
            lid = bpy.context.active_object
            lid.name = f"{name}_lid"

            mat = MaterialFactory.get_or_create(
                f"mat_tube_{name}", base_color=color,
                roughness=0.05, transmission=0.7
            )
            for obj in (body, cone, lid):
                self._add_material(obj, mat)

            # Parent cone and lid to body
            self._deselect_all()
            cone.select_set(True)
            lid.select_set(True)
            body.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.parent_set(type="OBJECT")
            blender_obj = body

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="pcr_tube", labware_id=labware_id, position=position
        )
        return self._register(rec)

    # ── Eppendorf tube ────────────────────────────────────────────────────────

    def create_eppendorf_tube(
        self,
        name: str = "EppendorfTube",
        labware_id: Optional[str] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        volume_ml: float = 1.5,
    ) -> ObjectRecord:
        """Create a 1.5 mL or 2.0 mL Eppendorf-style tube."""
        logger.debug("Creating Eppendorf tube '%s' at %s", name, position)
        radius = 0.007 if volume_ml <= 1.5 else 0.008
        height = 0.055 if volume_ml <= 1.5 else 0.065
        blender_obj = None

        if BPY_AVAILABLE:
            bpy.ops.mesh.primitive_cylinder_add(
                radius=radius, depth=height, location=position
            )
            body = bpy.context.active_object
            body.name = f"{name}_body"

            bot_pos = (position[0], position[1], position[2] - height * 0.5 - 0.006)
            bpy.ops.mesh.primitive_cone_add(
                radius1=radius, radius2=0.001, depth=0.012, location=bot_pos
            )
            cone = bpy.context.active_object
            cone.name = f"{name}_cone"

            for obj in (body, cone):
                self._add_material(obj, MaterialFactory.plastic_clear())

            self._deselect_all()
            cone.select_set(True)
            body.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.parent_set(type="OBJECT")
            blender_obj = body

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="eppendorf_tube", labware_id=labware_id, position=position
        )
        return self._register(rec)

    # ── 96-well microplate ────────────────────────────────────────────────────

    def create_96well_plate(
        self,
        name: str = "Plate96",
        labware_id: Optional[str] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> ObjectRecord:
        """Create a standard SBS-footprint 96-well plate."""
        logger.debug("Creating 96-well plate '%s' at %s", name, position)
        blender_obj = None

        if BPY_AVAILABLE:
            # Base tray
            bpy.ops.mesh.primitive_cube_add(
                size=1.0, location=position
            )
            tray = bpy.context.active_object
            tray.name = f"{name}_tray"
            tray.scale = (0.1275, 0.0853, 0.006)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(tray, MaterialFactory.plastic_white())

            # Individual wells (8 rows × 12 cols)
            well_radius = 0.003
            well_depth  = 0.010
            x_start, y_start = -0.055, -0.033
            x_step,  y_step  = 0.009,  0.009
            well_objs = []
            for row in range(8):
                for col in range(12):
                    wx = position[0] + x_start + col * x_step
                    wy = position[1] + y_start + row * y_step
                    wz = position[2] + 0.004
                    bpy.ops.mesh.primitive_cylinder_add(
                        radius=well_radius, depth=well_depth,
                        location=(wx, wy, wz)
                    )
                    well = bpy.context.active_object
                    well.name = f"{name}_well_{row}_{col}"
                    self._add_material(well, MaterialFactory.plastic_clear())
                    well_objs.append(well)

            # Parent all wells to tray
            self._deselect_all()
            for w in well_objs:
                w.select_set(True)
            tray.select_set(True)
            bpy.context.view_layer.objects.active = tray
            bpy.ops.object.parent_set(type="OBJECT")
            blender_obj = tray

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="microplate_96", labware_id=labware_id, position=position
        )
        return self._register(rec)

    # ── Reagent reservoir ─────────────────────────────────────────────────────

    def create_reagent_reservoir(
        self,
        name: str = "Reservoir",
        labware_id: Optional[str] = None,
        position: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        color: Tuple[float, float, float, float] = (0.2, 0.6, 0.9, 0.7),
        volume_ul: float = 5000.0,
    ) -> ObjectRecord:
        """Create a reagent reservoir / stock tube."""
        logger.debug("Creating reagent reservoir '%s' at %s", name, position)
        blender_obj = None

        if BPY_AVAILABLE:
            # Container body
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.015, depth=0.08, location=position
            )
            body = bpy.context.active_object
            body.name = f"{name}_body"
            self._add_material(body, MaterialFactory.plastic_clear())

            # Liquid fill  (proportional to volume)
            fill_frac = min(volume_ul / 15000.0, 0.9)
            liq_h     = 0.08 * fill_frac
            liq_pos   = (position[0], position[1], position[2] - 0.04 + liq_h / 2)
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.013, depth=liq_h, location=liq_pos
            )
            liquid = bpy.context.active_object
            liquid.name = f"{name}_liquid"
            self._add_material(
                liquid,
                MaterialFactory.liquid(color[0], color[1], color[2],
                                       color[3] if len(color) > 3 else 0.75)
            )

            # Label strip (thin flat cube on the side)
            lbl_pos = (position[0] + 0.016, position[1], position[2])
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=lbl_pos)
            label = bpy.context.active_object
            label.name = f"{name}_label"
            label.scale = (0.001, 0.008, 0.025)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(label, MaterialFactory.plastic_white())

            self._deselect_all()
            for child in (liquid, label):
                child.select_set(True)
            body.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.parent_set(type="OBJECT")
            blender_obj = body

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="reagent_reservoir", labware_id=labware_id, position=position
        )
        return self._register(rec)

    # ── Thermocycler ─────────────────────────────────────────────────────────

    def create_thermocycler(
        self,
        name: str = "Thermocycler",
        labware_id: Optional[str] = None,
        position: Tuple[float, float, float] = (0.2, 0.0, 0.0),
    ) -> ObjectRecord:
        """Create a thermal cycler instrument (body + lid + block)."""
        logger.debug("Creating thermocycler '%s' at %s", name, position)
        blender_obj = None

        if BPY_AVAILABLE:
            # Main body
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=position)
            body = bpy.context.active_object
            body.name = f"{name}_body"
            body.scale = (0.17, 0.27, 0.12)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(body, MaterialFactory.thermocycler_body())

            # Lid (hinged, slightly raised)
            lid_pos = (position[0], position[1], position[2] + 0.075)
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=lid_pos)
            lid = bpy.context.active_object
            lid.name = f"{name}_lid"
            lid.scale = (0.165, 0.265, 0.025)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(lid, MaterialFactory.plastic_grey())

            # Sample block (96-well format, recessed into body)
            block_pos = (position[0], position[1], position[2] + 0.045)
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=block_pos)
            block = bpy.context.active_object
            block.name = f"{name}_block"
            block.scale = (0.12, 0.20, 0.015)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(block, MaterialFactory.metal_stainless())

            # Display panel
            disp_pos = (position[0] + 0.09, position[1] - 0.05, position[2] + 0.02)
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=disp_pos)
            disp = bpy.context.active_object
            disp.name = f"{name}_display"
            disp.scale = (0.002, 0.055, 0.035)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(
                disp,
                MaterialFactory.get_or_create(
                    "mat_lcd", base_color=(0.0, 0.5, 1.0, 1.0),
                    roughness=0.0, emission=(0.0, 0.4, 1.0),
                    emission_strength=1.5
                )
            )

            # Parent all to body
            self._deselect_all()
            for child in (lid, block, disp):
                child.select_set(True)
            body.select_set(True)
            bpy.context.view_layer.objects.active = body
            bpy.ops.object.parent_set(type="OBJECT")
            blender_obj = body

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="thermocycler", labware_id=labware_id, position=position
        )
        return self._register(rec)

    # ── Liquid droplet / column fill ─────────────────────────────────────────

    def create_liquid_in_tube(
        self,
        tube_rec: ObjectRecord,
        color: Tuple[float, float, float, float] = (0.1, 0.5, 0.9, 0.8),
        fill_fraction: float = 0.3,
        name: Optional[str] = None,
    ) -> ObjectRecord:
        """Add a coloured liquid cylinder inside a tube object."""
        liq_name = name or f"{tube_rec.name}_liquid"
        blender_obj = None
        pos = tube_rec.position

        if BPY_AVAILABLE:
            tube_height = 0.038  # default PCR tube height
            liq_h   = tube_height * fill_fraction
            liq_z   = pos[2] - tube_height / 2 + liq_h / 2
            bpy.ops.mesh.primitive_cylinder_add(
                radius=0.0035, depth=liq_h,
                location=(pos[0], pos[1], liq_z)
            )
            liquid = bpy.context.active_object
            liquid.name = liq_name
            self._add_material(
                liquid,
                MaterialFactory.liquid(color[0], color[1], color[2],
                                       color[3] if len(color) > 3 else 0.8)
            )
            blender_obj = liquid
            tube_rec.liquid_objs.append(liquid)

        rec = ObjectRecord(
            name=liq_name, blender_obj=blender_obj,
            object_type="liquid",
            labware_id=tube_rec.labware_id, position=pos
        )
        return self._register(rec)

    # ── Error highlight sphere ────────────────────────────────────────────────

    def create_error_highlight(
        self,
        target_rec: ObjectRecord,
        name: Optional[str] = None,
    ) -> ObjectRecord:
        """Place a translucent red sphere around an object to signal an error."""
        hl_name = name or f"{target_rec.name}_error_highlight"
        blender_obj = None
        pos = target_rec.position

        if BPY_AVAILABLE:
            bpy.ops.mesh.primitive_uv_sphere_add(
                radius=0.04, location=pos
            )
            sphere = bpy.context.active_object
            sphere.name = hl_name
            self._add_material(sphere, MaterialFactory.error_highlight())
            sphere.display_type = "WIRE"
            blender_obj = sphere

        rec = ObjectRecord(
            name=hl_name, blender_obj=blender_obj,
            object_type="error_highlight", position=pos
        )
        return self._register(rec)

    # ── Text label ───────────────────────────────────────────────────────────

    def create_text_label(
        self,
        text: str,
        position: Tuple[float, float, float],
        name: Optional[str] = None,
        color: Tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0),
        size: float = 0.008,
    ) -> ObjectRecord:
        """Create a 3-D text label at the given position."""
        lbl_name = name or f"label_{text[:16].replace(' ', '_')}"
        blender_obj = None

        if BPY_AVAILABLE:
            bpy.ops.object.text_add(location=position)
            txt = bpy.context.active_object
            txt.name = lbl_name
            txt.data.body = text
            txt.data.size = size
            txt.data.align_x = "CENTER"
            mat = MaterialFactory.get_or_create(
                f"mat_{lbl_name}", base_color=color, roughness=0.5,
                emission=color[:3], emission_strength=1.0
            )
            self._add_material(txt, mat)
            blender_obj = txt

        rec = ObjectRecord(
            name=lbl_name, blender_obj=blender_obj,
            object_type="text_label", position=position
        )
        return self._register(rec)

    # ── Lab bench surface ─────────────────────────────────────────────────────

    def create_bench(
        self,
        name: str = "LabBench",
        position: Tuple[float, float, float] = (0.0, 0.0, -0.01),
        size: Tuple[float, float] = (0.8, 0.5),
    ) -> ObjectRecord:
        """Create a flat lab bench surface."""
        blender_obj = None

        if BPY_AVAILABLE:
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=position)
            bench = bpy.context.active_object
            bench.name = name
            bench.scale = (size[0], size[1], 0.01)
            bpy.ops.object.transform_apply(scale=True)
            self._add_material(
                bench,
                MaterialFactory.get_or_create(
                    "mat_bench",
                    base_color=(0.85, 0.82, 0.75, 1.0),
                    roughness=0.6
                )
            )
            blender_obj = bench

        rec = ObjectRecord(
            name=name, blender_obj=blender_obj,
            object_type="bench", position=position
        )
        return self._register(rec)
