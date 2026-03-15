"""
Protocol Data Models — Pydantic v2 schemas for the Digital Twin Lab Simulation.

Defines every entity in a laboratory protocol:
  • Reagents         – liquids used in the workflow
  • Labware          – physical containers (tubes, plates, etc.)
  • Protocol Steps   – individual experimental actions
  • Protocol         – top-level document
  • Validation       – per-step and per-protocol result objects
  • Simulation Cmds  – Blender-ready command objects produced by the interpreter
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────
# Enumerations
# ─────────────────────────────────────────────

class ActionType(str, Enum):
    """All supported protocol step action types."""
    ASPIRATE          = "aspirate"          # draw liquid into pipette
    DISPENSE          = "dispense"          # release liquid from pipette
    PIPETTE           = "pipette"           # combined aspirate+dispense shorthand
    MIX               = "mix"              # aspirate-dispense cycles in same well
    TRANSFER          = "transfer"         # aspirate from src, dispense to dest
    PLACE             = "place"            # move labware to instrument
    REMOVE            = "remove"           # take labware from instrument
    INCUBATE          = "incubate"         # hold at temperature for time
    CENTRIFUGE        = "centrifuge"       # spin labware
    HEAT              = "heat"             # apply heat
    COOL              = "cool"             # apply cooling
    VORTEX            = "vortex"           # vortex mix
    LABEL             = "label"            # label a tube/plate
    SEAL              = "seal"             # seal plate or tube
    UNSEAL            = "unseal"           # remove seal
    PAUSE             = "pause"            # pause simulation / wait
    OBSERVE           = "observe"          # visual inspection step
    THERMOCYCLE       = "thermocycle"      # run PCR thermocycling


class LabwareType(str, Enum):
    """Supported labware categories."""
    PCR_TUBE          = "pcr_tube"
    MICROCENTRIFUGE_TUBE = "microcentrifuge_tube"
    EPPENDORF_TUBE    = "eppendorf_tube"
    FALCON_TUBE_15ML  = "falcon_tube_15ml"
    FALCON_TUBE_50ML  = "falcon_tube_50ml"
    MICROPLATE_96     = "microplate_96"
    MICROPLATE_384    = "microplate_384"
    REAGENT_RESERVOIR = "reagent_reservoir"
    THERMOCYCLER      = "thermocycler"
    CENTRIFUGE        = "centrifuge"
    VORTEX_MIXER      = "vortex_mixer"
    HEAT_BLOCK        = "heat_block"
    ICE_BUCKET        = "ice_bucket"
    PIPETTE_TIP_BOX   = "pipette_tip_box"
    WASTE_CONTAINER   = "waste_container"


class PipetteType(str, Enum):
    """Pipette models supported by the simulation."""
    P2               = "P2"        # 0.1 – 2 µL
    P10              = "P10"       # 1 – 10 µL
    P20              = "P20"       # 2 – 20 µL
    P200             = "P200"      # 20 – 200 µL
    P1000            = "P1000"     # 100 – 1000 µL
    MULTICHANNEL_P20 = "P20_multi" # 8-channel
    MULTICHANNEL_P200= "P200_multi"


class ErrorSeverity(str, Enum):
    WARNING = "warning"
    ERROR   = "error"
    CRITICAL= "critical"


class SimulationCommandType(str, Enum):
    """Low-level Blender animation command types."""
    CREATE_OBJECT     = "create_object"
    MOVE_OBJECT       = "move_object"
    ANIMATE_PIPETTE   = "animate_pipette"
    ANIMATE_LIQUID    = "animate_liquid"
    ANIMATE_TRANSFER  = "animate_transfer"
    ANIMATE_MIX       = "animate_mix"
    PLACE_LABWARE     = "place_labware"
    HIGHLIGHT_ERROR   = "highlight_error"
    DISPLAY_LABEL     = "display_label"
    SET_CAMERA        = "set_camera"
    ADD_KEYFRAME      = "add_keyframe"
    RENDER_SCENE      = "render_scene"


# ─────────────────────────────────────────────
# Core Data Models
# ─────────────────────────────────────────────

class ColorRGBA(BaseModel):
    """RGBA colour used for liquid / object rendering."""
    r: float = Field(ge=0.0, le=1.0, default=0.0)
    g: float = Field(ge=0.0, le=1.0, default=0.0)
    b: float = Field(ge=0.0, le=1.0, default=1.0)
    a: float = Field(ge=0.0, le=1.0, default=0.8, description="Alpha / transparency")

    model_config = {"json_schema_extra": {"example": {"r": 0.1, "g": 0.5, "b": 0.9, "a": 0.8}}}


class ReagentModel(BaseModel):
    """
    A liquid reagent used in the protocol.

    Attributes
    ----------
    id          : unique identifier referenced in steps
    name        : human-readable name
    volume_ul   : available volume in µL
    container   : labware id that currently holds this reagent
    color       : RGBA colour for 3-D rendering
    viscosity   : relative viscosity (1.0 = water)
    hazardous   : flag for safety annotation
    """
    id:          str              = Field(..., description="Unique reagent identifier")
    name:        str              = Field(..., description="Human-readable reagent name")
    volume_ul:   float            = Field(..., gt=0, description="Available volume in µL")
    container:   str              = Field(..., description="Labware ID holding this reagent")
    color:       ColorRGBA        = Field(default_factory=ColorRGBA)
    concentration: Optional[str] = Field(None, description="e.g. '10 nM', '1x'")
    viscosity:   float            = Field(default=1.0, ge=0.1, le=100.0)
    hazardous:   bool             = Field(default=False)
    notes:       Optional[str]    = None

    @field_validator("id")
    @classmethod
    def id_no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("Reagent id must not contain spaces — use underscores.")
        return v


class LabwareModel(BaseModel):
    """
    A piece of physical labware in the simulation.

    Attributes
    ----------
    id            : unique identifier
    name          : human-readable label
    labware_type  : category (see LabwareType enum)
    capacity_ul   : maximum volume in µL (per well for plates)
    current_volume: current fill level in µL
    position      : [x, y, z] position in the virtual bench (metres)
    wells         : number of wells (1 for tubes, 96/384 for plates)
    is_instrument : True for thermocyclers, centrifuges, etc.
    """
    id:             str          = Field(..., description="Unique labware identifier")
    name:           str          = Field(..., description="Human-readable label")
    labware_type:   LabwareType  = Field(...)
    capacity_ul:    float        = Field(..., gt=0, description="Max volume per well in µL")
    current_volume: float        = Field(default=0.0, ge=0.0)
    position:       List[float]  = Field(default_factory=lambda: [0.0, 0.0, 0.0],
                                         description="[x, y, z] bench position in metres")
    wells:          int          = Field(default=1, ge=1, le=384)
    is_instrument:  bool         = Field(default=False)
    color:          ColorRGBA    = Field(default_factory=ColorRGBA)
    notes:          Optional[str]= None

    @field_validator("current_volume")
    @classmethod
    def volume_within_capacity(cls, v: float, info: Any) -> float:
        # NOTE: capacity_ul may not yet be set when this validator runs;
        # cross-field checks are done by model_validator below.
        return v

    @model_validator(mode="after")
    def check_volume_capacity(self) -> "LabwareModel":
        if self.current_volume > self.capacity_ul:
            raise ValueError(
                f"Labware '{self.id}': current_volume ({self.current_volume} µL) "
                f"exceeds capacity ({self.capacity_ul} µL)."
            )
        return self


class ThermocycleStage(BaseModel):
    """A single temperature stage in a PCR thermocycle program."""
    name:        str   = Field(..., description="e.g. 'Denaturation'")
    temperature: float = Field(..., description="Target temperature in °C")
    duration_s:  int   = Field(..., gt=0, description="Hold time in seconds")
    cycles:      int   = Field(default=1, ge=1)


class ProtocolStepModel(BaseModel):
    """
    A single step in a laboratory protocol.

    Attributes
    ----------
    step_number   : sequential index (1-based)
    action        : action type (ActionType enum)
    description   : human-readable instruction
    source        : source labware/reagent id (if applicable)
    destination   : destination labware id (if applicable)
    volume_ul     : liquid volume in µL (if applicable)
    tool          : pipette type used
    mix_cycles    : number of mix cycles (for MIX action)
    temperature   : target temperature in °C (for HEAT/INCUBATE)
    duration_s    : duration in seconds (for INCUBATE/PAUSE)
    speed_rpm     : centrifuge/vortex speed
    thermocycle   : list of thermocycle stages (for THERMOCYCLE)
    notes         : freeform annotation
    """
    step_number:    int                        = Field(..., ge=1)
    action:         ActionType                 = Field(...)
    description:    str                        = Field(..., min_length=5)
    source:         Optional[str]              = Field(None)
    destination:    Optional[str]              = Field(None)
    volume_ul:      Optional[float]            = Field(None, gt=0)
    tool:           Optional[PipetteType]      = Field(None)
    mix_cycles:     Optional[int]              = Field(None, ge=1, le=200)
    temperature:    Optional[float]            = Field(None, description="°C")
    duration_s:     Optional[int]              = Field(None, gt=0)
    speed_rpm:      Optional[int]              = Field(None, gt=0)
    thermocycle_program: Optional[List[ThermocycleStage]] = Field(None)
    expected_color: Optional[ColorRGBA]        = Field(None)
    notes:          Optional[str]              = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "ProtocolStepModel":
        """Ensure that each action type has the fields it logically requires."""
        liquid_actions = {
            ActionType.ASPIRATE, ActionType.DISPENSE,
            ActionType.PIPETTE, ActionType.TRANSFER, ActionType.MIX
        }
        if self.action in liquid_actions and self.volume_ul is None:
            raise ValueError(
                f"Step {self.step_number} ({self.action}): 'volume_ul' is required "
                "for liquid-handling actions."
            )
        if self.action == ActionType.THERMOCYCLE and not self.thermocycle_program:
            raise ValueError(
                f"Step {self.step_number}: THERMOCYCLE action requires "
                "'thermocycle_program' to be defined."
            )
        if self.action in {ActionType.HEAT, ActionType.INCUBATE} and self.temperature is None:
            raise ValueError(
                f"Step {self.step_number} ({self.action}): 'temperature' is required."
            )
        return self


class AuthorModel(BaseModel):
    name:         str
    email:        Optional[str] = None
    institution:  Optional[str] = None


class ProtocolModel(BaseModel):
    """
    Top-level protocol document.

    A protocol describes a complete laboratory workflow including all reagents,
    labware, and ordered experimental steps.
    """
    protocol_id:   str                = Field(..., description="Unique protocol identifier")
    protocol_name: str                = Field(..., min_length=3)
    version:       str                = Field(default="1.0.0")
    description:   Optional[str]      = None
    author:        Optional[AuthorModel] = None
    created_at:    Optional[str]      = None       # ISO 8601 date string
    tags:          List[str]          = Field(default_factory=list)

    reagents:  List[ReagentModel]     = Field(default_factory=list)
    labware:   List[LabwareModel]     = Field(default_factory=list)
    steps:     List[ProtocolStepModel]= Field(..., min_length=1)

    # Optional simulation configuration
    simulation_config: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Blender rendering / animation config overrides"
    )

    @model_validator(mode="after")
    def validate_step_numbers(self) -> "ProtocolModel":
        """Ensure steps are numbered sequentially starting at 1."""
        for idx, step in enumerate(self.steps, start=1):
            if step.step_number != idx:
                raise ValueError(
                    f"Step numbering error: expected step {idx}, "
                    f"found step_number={step.step_number}."
                )
        return self

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ProtocolModel":
        """Enforce unique IDs across reagents and labware."""
        reagent_ids = [r.id for r in self.reagents]
        labware_ids = [lw.id for lw in self.labware]

        if len(reagent_ids) != len(set(reagent_ids)):
            seen, dupes = set(), []
            for rid in reagent_ids:
                if rid in seen:
                    dupes.append(rid)
                seen.add(rid)
            raise ValueError(f"Duplicate reagent IDs detected: {dupes}")

        if len(labware_ids) != len(set(labware_ids)):
            seen, dupes = set(), []
            for lid in labware_ids:
                if lid in seen:
                    dupes.append(lid)
                seen.add(lid)
            raise ValueError(f"Duplicate labware IDs detected: {dupes}")

        return self

    def get_reagent(self, reagent_id: str) -> Optional[ReagentModel]:
        return next((r for r in self.reagents if r.id == reagent_id), None)

    def get_labware(self, labware_id: str) -> Optional[LabwareModel]:
        return next((lw for lw in self.labware if lw.id == labware_id), None)


# ─────────────────────────────────────────────
# Validation & Simulation Output Models
# ─────────────────────────────────────────────

class StepValidationIssue(BaseModel):
    """An individual validation issue found for a single step."""
    step_number: int
    severity:    ErrorSeverity
    code:        str              = Field(..., description="Machine-readable error code")
    message:     str              = Field(..., description="Human-readable explanation")
    suggestion:  Optional[str]   = None


class ValidationResultModel(BaseModel):
    """Complete validation report for a protocol."""
    protocol_id:  str
    is_valid:     bool
    issues:       List[StepValidationIssue] = Field(default_factory=list)
    warnings:     List[StepValidationIssue] = Field(default_factory=list)
    summary:      str = ""

    @model_validator(mode="after")
    def build_summary(self) -> "ValidationResultModel":
        n_errors   = len([i for i in self.issues if i.severity != ErrorSeverity.WARNING])
        n_warnings = len(self.warnings) + len(
            [i for i in self.issues if i.severity == ErrorSeverity.WARNING]
        )
        status = "PASSED" if self.is_valid else "FAILED"
        self.summary = (
            f"Validation {status} — "
            f"{n_errors} error(s), {n_warnings} warning(s)."
        )
        return self


class SimulationCommandModel(BaseModel):
    """
    A single Blender-ready animation command produced by the interpreter.

    The Blender engine iterates over these commands and creates
    corresponding bpy operations.
    """
    command_id:   int
    command_type: SimulationCommandType
    step_number:  int
    frame_start:  int             = Field(..., ge=0)
    frame_end:    int             = Field(..., ge=0)
    object_name:  Optional[str]   = None
    target_name:  Optional[str]   = None
    position:     Optional[List[float]] = None   # [x, y, z]
    rotation:     Optional[List[float]] = None   # [rx, ry, rz] radians
    color:        Optional[ColorRGBA]   = None
    volume_ul:    Optional[float]       = None
    label_text:   Optional[str]         = None
    is_error:     bool                  = False
    metadata:     Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def frame_order(self) -> "SimulationCommandModel":
        if self.frame_end < self.frame_start:
            raise ValueError(
                f"Command {self.command_id}: frame_end ({self.frame_end}) "
                f"must be >= frame_start ({self.frame_start})."
            )
        return self
