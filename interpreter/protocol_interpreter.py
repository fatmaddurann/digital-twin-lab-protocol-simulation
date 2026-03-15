"""
Protocol Interpreter
=====================
Two complementary interpretation paths:

1. **ProtocolInterpreter**
   Accepts a fully-structured ``ProtocolModel`` (already parsed from JSON / YAML)
   and converts it into a flat list of ``SimulationCommandModel`` objects ready
   for the ``AnimationPipeline``.  This is the primary path used by the engine.

2. **NaturalLanguageParser**
   Accepts a free-text (natural language) protocol description — e.g. a list of
   bullet-point lab instructions — and extracts a ``ProtocolModel`` using regex
   pattern matching.  This allows users to paste a protocol from a lab notebook
   and obtain a simulation without writing JSON.

   Recognised patterns
   -------------------
   "Add X µL [of] <reagent> to <container>"    → PIPETTE step
   "Transfer X µL from <src> to <dest>"         → TRANSFER step
   "Mix [the] [reaction] [X times]"             → MIX step
   "Place <tube/plate> [into/in] <instrument>"  → PLACE step
   "Incubate at X °C for Y [min|sec|hours]"    → INCUBATE step
   "Heat [to/at] X °C [for Y ...]"              → HEAT step
   "Centrifuge [at X rpm] [for Y ...]"          → CENTRIFUGE step
   "Vortex [for Y seconds]"                     → VORTEX step
   "Thermocycle / Run PCR"                      → THERMOCYCLE step

   The parser infers reagent and labware declarations from the detected steps,
   creating a minimal but valid ProtocolModel.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from models.protocol_models import (
    ActionType,
    ColorRGBA,
    LabwareModel,
    LabwareType,
    PipetteType,
    ProtocolModel,
    ProtocolStepModel,
    ReagentModel,
    SimulationCommandModel,
    SimulationCommandType,
    ThermocycleStage,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Colour palette for auto-assigning reagent colours
# ─────────────────────────────────────────────────────────────────────────────

_REAGENT_PALETTE: List[Tuple[float, float, float, float]] = [
    (0.10, 0.45, 0.90, 0.80),  # blue     — DNA sample
    (0.20, 0.75, 0.25, 0.80),  # green    — primer mix
    (0.85, 0.40, 0.10, 0.80),  # orange   — master mix
    (0.75, 0.20, 0.75, 0.80),  # purple   — enzyme
    (0.95, 0.90, 0.10, 0.80),  # yellow   — buffer
    (0.10, 0.85, 0.85, 0.80),  # cyan     — dNTP
    (0.90, 0.10, 0.40, 0.80),  # pink     — ligase
    (0.50, 0.50, 0.50, 0.80),  # grey     — water
]


def _pick_colour(index: int) -> ColorRGBA:
    r, g, b, a = _REAGENT_PALETTE[index % len(_REAGENT_PALETTE)]
    return ColorRGBA(r=r, g=g, b=b, a=a)


# ─────────────────────────────────────────────────────────────────────────────
# PipetteType auto-selection
# ─────────────────────────────────────────────────────────────────────────────

def _select_pipette(volume_ul: float) -> PipetteType:
    if volume_ul <= 2.0:
        return PipetteType.P2
    if volume_ul <= 10.0:
        return PipetteType.P10
    if volume_ul <= 20.0:
        return PipetteType.P20
    if volume_ul <= 200.0:
        return PipetteType.P200
    return PipetteType.P1000


# ─────────────────────────────────────────────────────────────────────────────
# ProtocolInterpreter
# ─────────────────────────────────────────────────────────────────────────────

class ProtocolInterpreter:
    """
    Converts a validated ``ProtocolModel`` into ``SimulationCommandModel`` objects.

    This class is responsible for the semantic mapping of protocol actions
    to Blender animation commands.  The BlenderSimulationEngine delegates to
    this interpreter, but it can also be used standalone for testing.

    Example mapping
    ---------------
    ``"pipette 10 µL from reagent_A to pcr_tube_1"``
    →  ANIMATE_PIPETTE  (move to source)
    →  ANIMATE_LIQUID   (aspirate)
    →  ANIMATE_PIPETTE  (move to dest)
    →  ANIMATE_LIQUID   (dispense)
    """

    FPS             = 24
    FRAMES_PER_STEP = 96

    def __init__(
        self,
        fps:             int = 24,
        frames_per_step: int = 96,
    ) -> None:
        self.fps             = fps
        self.frames_per_step = frames_per_step
        self._cmd_counter    = 0

    # ── Public ────────────────────────────────────────────────────────────────

    def interpret(self, protocol: ProtocolModel) -> List[SimulationCommandModel]:
        """
        Convert every step in *protocol* to a list of simulation commands.

        Returns a flat, ordered list of ``SimulationCommandModel`` objects.
        Commands for each step occupy a dedicated frame range.
        """
        self._cmd_counter = 0
        commands: List[SimulationCommandModel] = []

        labware_index  = {lw.id: lw  for lw in protocol.labware}
        reagent_index  = {r.id:  r   for r  in protocol.reagents}

        for step in protocol.steps:
            fs, fe = self._step_frames(step.step_number)
            step_cmds = self._interpret_step(
                step, fs, fe, labware_index, reagent_index
            )
            commands.extend(step_cmds)

        logger.info(
            "Interpreted %d steps → %d simulation commands.",
            len(protocol.steps), len(commands)
        )
        return commands

    def interpret_from_json(self, json_path: str) -> List[SimulationCommandModel]:
        """Load a protocol JSON file and interpret it."""
        path = Path(json_path)
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        protocol = ProtocolModel.model_validate(data)
        return self.interpret(protocol)

    def step_summary(self, protocol: ProtocolModel) -> List[Dict[str, Any]]:
        """Return a human-readable summary dict for each step."""
        summaries = []
        for step in protocol.steps:
            fs, fe = self._step_frames(step.step_number)
            summaries.append({
                "step":        step.step_number,
                "action":      step.action.value,
                "description": step.description,
                "frame_start": fs,
                "frame_end":   fe,
                "volume_ul":   step.volume_ul,
                "source":      step.source,
                "destination": step.destination,
                "tool":        step.tool.value if step.tool else None,
            })
        return summaries

    # ── Frame helpers ─────────────────────────────────────────────────────────

    def _step_frames(self, step_number: int) -> Tuple[int, int]:
        fs = (step_number - 1) * self.frames_per_step + 1
        fe = fs + self.frames_per_step - 1
        return fs, fe

    def _next_id(self) -> int:
        self._cmd_counter += 1
        return self._cmd_counter

    def _mk(self, **kwargs) -> SimulationCommandModel:
        kwargs.setdefault("command_id", self._next_id())
        return SimulationCommandModel(**kwargs)

    # ── Step interpreter ──────────────────────────────────────────────────────

    def _interpret_step(
        self,
        step,
        fs: int,
        fe: int,
        labware_index: Dict,
        reagent_index: Dict,
    ) -> List[SimulationCommandModel]:
        action = step.action

        if action in (ActionType.PIPETTE, ActionType.TRANSFER):
            return self._interp_transfer(step, fs, fe, reagent_index)
        if action == ActionType.ASPIRATE:
            return self._interp_aspirate(step, fs, fe, reagent_index)
        if action == ActionType.DISPENSE:
            return self._interp_dispense(step, fs, fe, reagent_index)
        if action == ActionType.MIX:
            return self._interp_mix(step, fs, fe)
        if action in (ActionType.PLACE, ActionType.THERMOCYCLE):
            return self._interp_place(step, fs, fe, labware_index)
        if action in (ActionType.HEAT, ActionType.INCUBATE, ActionType.COOL):
            return self._interp_thermal(step, fs, fe)
        if action == ActionType.CENTRIFUGE:
            return self._interp_centrifuge(step, fs, fe)
        if action == ActionType.VORTEX:
            return self._interp_vortex(step, fs, fe)
        if action == ActionType.PAUSE:
            return self._interp_pause(step, fs, fe)
        # Generic fallback
        return self._interp_generic(step, fs, fe)

    # ── Action interpreters ───────────────────────────────────────────────────

    def _interp_transfer(self, step, fs, fe, reagent_index) -> List[SimulationCommandModel]:
        """PIPETTE / TRANSFER → move-to-source, aspirate, move-to-dest, dispense."""
        quarter = (fe - fs) // 4
        cmds = []

        # 1. Move pipette to source
        cmds.append(self._mk(
            command_type = SimulationCommandType.ANIMATE_PIPETTE,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fs + quarter,
            object_name  = "Pipette_P200",
            target_name  = step.source,
            label_text   = f"Aspirate {step.volume_ul} µL from {step.source}",
        ))

        # 2. Aspirate (liquid column shrinks in source)
        cmds.append(self._mk(
            command_type = SimulationCommandType.ANIMATE_LIQUID,
            step_number  = step.step_number,
            frame_start  = fs + quarter,
            frame_end    = fs + 2 * quarter,
            object_name  = f"Stock_{step.source}_liquid" if step.source else None,
            target_name  = step.source,
            volume_ul    = -(step.volume_ul or 0),
        ))

        # 3. Move to destination
        cmds.append(self._mk(
            command_type = SimulationCommandType.ANIMATE_PIPETTE,
            step_number  = step.step_number,
            frame_start  = fs + 2 * quarter,
            frame_end    = fs + 3 * quarter,
            object_name  = "Pipette_P200",
            target_name  = step.destination,
            label_text   = f"Dispense {step.volume_ul} µL into {step.destination}",
        ))

        # 4. Dispense (liquid column grows in destination)
        color = None
        if step.source and step.source in reagent_index:
            c = reagent_index[step.source].color
            color = ColorRGBA(r=c.r, g=c.g, b=c.b, a=c.a)

        cmds.append(self._mk(
            command_type = SimulationCommandType.ANIMATE_LIQUID,
            step_number  = step.step_number,
            frame_start  = fs + 3 * quarter,
            frame_end    = fe,
            object_name  = f"{step.destination}_liquid" if step.destination else None,
            target_name  = step.destination,
            volume_ul    = step.volume_ul,
            color        = color,
        ))
        return cmds

    def _interp_aspirate(self, step, fs, fe, reagent_index) -> List[SimulationCommandModel]:
        return [self._mk(
            command_type = SimulationCommandType.ANIMATE_PIPETTE,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = "Pipette_P200",
            target_name  = step.source,
            volume_ul    = step.volume_ul,
            label_text   = f"Aspirate {step.volume_ul} µL",
        )]

    def _interp_dispense(self, step, fs, fe, reagent_index) -> List[SimulationCommandModel]:
        color = None
        if step.source and step.source in reagent_index:
            c = reagent_index[step.source].color
            color = ColorRGBA(r=c.r, g=c.g, b=c.b, a=c.a)
        return [self._mk(
            command_type = SimulationCommandType.ANIMATE_LIQUID,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"{step.destination}_liquid" if step.destination else None,
            target_name  = step.destination,
            volume_ul    = step.volume_ul,
            color        = color,
            label_text   = f"Dispense {step.volume_ul} µL into {step.destination}",
        )]

    def _interp_mix(self, step, fs, fe) -> List[SimulationCommandModel]:
        return [self._mk(
            command_type = SimulationCommandType.ANIMATE_MIX,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = "Pipette_P200",
            target_name  = step.destination or step.source,
            metadata     = {"mix_cycles": step.mix_cycles or 5},
            label_text   = f"Mix {step.mix_cycles or 5}×",
        )]

    def _interp_place(self, step, fs, fe, labware_index) -> List[SimulationCommandModel]:
        src_name = step.source or step.destination
        return [self._mk(
            command_type = SimulationCommandType.PLACE_LABWARE,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = src_name,
            target_name  = step.destination,
            label_text   = f"Place {src_name} → {step.destination}",
        )]

    def _interp_thermal(self, step, fs, fe) -> List[SimulationCommandModel]:
        label = f"{'Heat' if step.action == ActionType.HEAT else 'Incubate'}"
        if step.temperature:
            label += f" @ {step.temperature} °C"
        if step.duration_s:
            label += f" / {step.duration_s}s"
        return [self._mk(
            command_type = SimulationCommandType.DISPLAY_LABEL,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"label_thermal_{step.step_number}",
            label_text   = label,
        )]

    def _interp_centrifuge(self, step, fs, fe) -> List[SimulationCommandModel]:
        return [self._mk(
            command_type = SimulationCommandType.MOVE_OBJECT,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = step.source or step.destination,
            label_text   = f"Centrifuge {step.speed_rpm or ''} rpm",
        )]

    def _interp_vortex(self, step, fs, fe) -> List[SimulationCommandModel]:
        return [self._mk(
            command_type = SimulationCommandType.ANIMATE_MIX,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = step.source or step.destination,
            metadata     = {"mix_cycles": 8},
            label_text   = "Vortex",
        )]

    def _interp_pause(self, step, fs, fe) -> List[SimulationCommandModel]:
        return [self._mk(
            command_type = SimulationCommandType.DISPLAY_LABEL,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"label_pause_{step.step_number}",
            label_text   = f"⏸ Pause — {step.description}",
        )]

    def _interp_generic(self, step, fs, fe) -> List[SimulationCommandModel]:
        return [self._mk(
            command_type = SimulationCommandType.DISPLAY_LABEL,
            step_number  = step.step_number,
            frame_start  = fs,
            frame_end    = fe,
            object_name  = f"label_step_{step.step_number}",
            label_text   = step.description[:60],
        )]


# ─────────────────────────────────────────────────────────────────────────────
# NaturalLanguageParser
# ─────────────────────────────────────────────────────────────────────────────

class NaturalLanguageParser:
    """
    Parses a free-text protocol (one instruction per line or numbered list)
    and produces a ``ProtocolModel``.

    This is a regex-based heuristic parser — it covers the most common lab
    instruction patterns but is not a full NLP system.

    Usage
    -----
    >>> parser = NaturalLanguageParser()
    >>> protocol = parser.parse(text, protocol_name="My PCR")
    """

    # Volume pattern: "10 µL", "10uL", "10 ul", "10.5 µL"
    _VOL  = r"([\d.]+)\s*[µu]?[Ll]"
    # Generic identifier: letters/digits/underscores/hyphens/spaces
    _ID   = r"([A-Za-z][\w\s\-]*)"

    # Time-duration pattern: "5 min", "30 sec", "2 hours"
    _TIME = r"([\d.]+)\s*(?:min(?:utes?)?|sec(?:onds?)?|h(?:ours?)?)\b"

    _PATTERNS: List[Tuple[str, ActionType]] = [
        # Transfer from … to …  (must come BEFORE the generic add/pipette pattern)
        (rf"transfer\s+{_VOL}\s+from\s+(\w[\w_\-]*)\s+(?:to|into)\s+(\w[\w_\-]*)",
         ActionType.TRANSFER),
        # Add / pipette  (generic form without "from")
        (rf"(?:add|pipette)\s+{_VOL}\s+(?:of\s+)?{_ID}\s+(?:to|into)\s+{_ID}",
         ActionType.PIPETTE),
        # Mix
        (r"(?:mix|vortex briefly|pipette up and down)",
         ActionType.MIX),
        # Place into instrument
        (rf"place\s+{_ID}\s+(?:in|into|on)\s+{_ID}",
         ActionType.PLACE),
        # Incubate / heat  — temperature in °C, optional time duration
        (r"(?:incubate|heat)\s+(?:at|to)\s+([\d.]+)\s*[°]?[cC]",
         ActionType.INCUBATE),
        # Run PCR / thermocycle
        (r"(?:run\s+pcr|thermocycle|pcr\s+program|cycling)",
         ActionType.THERMOCYCLE),
        # Centrifuge
        (r"centrifuge",
         ActionType.CENTRIFUGE),
        # Vortex
        (r"vortex",
         ActionType.VORTEX),
        # Seal
        (r"seal",
         ActionType.SEAL),
        # Pause
        (r"pause",
         ActionType.PAUSE),
    ]

    def parse(
        self,
        text: str,
        protocol_name: str = "Parsed Protocol",
        protocol_id:   Optional[str] = None,
    ) -> ProtocolModel:
        """Parse *text* into a ProtocolModel."""
        lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
        # Remove leading numbers / bullets ("1.", "1)", "•", "-")
        lines = [re.sub(r"^[\d]+[.)]\s*|^[•\-\*]\s*", "", line) for line in lines]
        lines = [line for line in lines if line]

        steps:     List[ProtocolStepModel] = []
        reagents:  Dict[str, ReagentModel] = {}
        labwares:  Dict[str, LabwareModel] = {}
        reagent_colour_idx = 0

        for step_num, line in enumerate(lines, start=1):
            step = self._parse_line(
                line, step_num,
                reagents, labwares, reagent_colour_idx
            )
            reagent_colour_idx += len(reagents) - reagent_colour_idx
            steps.append(step)

        # Ensure every reagent has a labware container
        for rid, reagent in reagents.items():
            if reagent.container not in labwares:
                labwares[reagent.container] = LabwareModel(
                    id=reagent.container,
                    name=reagent.container.replace("_", " ").title(),
                    labware_type=LabwareType.REAGENT_RESERVOIR,
                    capacity_ul=50000.0,
                )

        return ProtocolModel(
            protocol_id   = protocol_id or str(uuid.uuid4())[:8],
            protocol_name = protocol_name,
            version       = "1.0.0",
            created_at    = date.today().isoformat(),
            reagents      = list(reagents.values()),
            labware       = list(labwares.values()),
            steps         = steps,
        )

    def _slugify(self, s: str) -> str:
        return re.sub(r"\s+", "_", s.strip().lower())

    def _parse_line(
        self,
        line: str,
        step_num: int,
        reagents: Dict[str, ReagentModel],
        labwares: Dict[str, LabwareModel],
        colour_idx: int,
    ) -> ProtocolStepModel:
        """Match a single line against the pattern list and build a step."""
        line_low = line.lower()

        # Try each pattern in order
        for pattern, action_type in self._PATTERNS:
            m = re.search(pattern, line_low)
            if not m:
                continue

            groups = m.groups()
            vol    = None
            source = None
            dest   = None

            if action_type in (ActionType.PIPETTE, ActionType.TRANSFER):
                # groups: (volume, reagent_or_source, destination)
                if len(groups) >= 3:
                    vol    = float(groups[0]) if groups[0] else None
                    source = self._slugify(groups[1])
                    dest   = self._slugify(groups[2])
                    # Register reagent
                    if source not in reagents:
                        reagents[source] = ReagentModel(
                            id=source,
                            name=groups[1].strip().title(),
                            volume_ul=max(vol or 10.0, 10.0) * 10,
                            container=f"stock_{source}",
                            color=_pick_colour(colour_idx + len(reagents)),
                        )
                    # Register destination labware
                    if dest not in labwares:
                        lw_type = self._infer_labware_type(dest)
                        labwares[dest] = LabwareModel(
                            id=dest,
                            name=dest.replace("_", " ").title(),
                            labware_type=lw_type,
                            capacity_ul=200.0,
                        )

            elif action_type == ActionType.PLACE:
                if len(groups) >= 2:
                    source = self._slugify(groups[0])
                    dest   = self._slugify(groups[1])
                    if dest not in labwares:
                        lw_type = self._infer_labware_type(dest)
                        labwares[dest] = LabwareModel(
                            id=dest,
                            name=dest.replace("_", " ").title(),
                            labware_type=lw_type,
                            capacity_ul=9999.0,
                            is_instrument=True,
                        )

            elif action_type == ActionType.INCUBATE:
                temp = float(groups[0]) if groups[0] else None
                # Parse duration from the raw line using time pattern
                dur_s = None
                tm = re.search(self._TIME, line_low)
                if tm:
                    val  = float(tm.group(1))
                    unit_str = tm.group(0)
                    if "min" in unit_str:
                        dur_s = int(val * 60)
                    elif "h" in unit_str:
                        dur_s = int(val * 3600)
                    else:
                        dur_s = int(val)
                return ProtocolStepModel(
                    step_number  = step_num,
                    action       = action_type,
                    description  = line,
                    temperature  = temp,
                    duration_s   = dur_s,
                )

            elif action_type == ActionType.THERMOCYCLE:
                # Provide a default PCR program when none is given explicitly
                return ProtocolStepModel(
                    step_number  = step_num,
                    action       = action_type,
                    description  = line,
                    thermocycle_program=[
                        ThermocycleStage(
                            name="Initial Denaturation",
                            temperature=95.0, duration_s=180, cycles=1
                        ),
                        ThermocycleStage(
                            name="Denaturation",
                            temperature=95.0, duration_s=30, cycles=35
                        ),
                        ThermocycleStage(
                            name="Annealing",
                            temperature=58.0, duration_s=30, cycles=35
                        ),
                        ThermocycleStage(
                            name="Extension",
                            temperature=72.0, duration_s=60, cycles=35
                        ),
                        ThermocycleStage(
                            name="Hold",
                            temperature=4.0, duration_s=999, cycles=1
                        ),
                    ],
                )

            # For MIX, ensure a default volume is set (model requires it)
            if action_type == ActionType.MIX and vol is None:
                vol = 20.0

            tool = _select_pipette(vol) if vol else None
            return ProtocolStepModel(
                step_number  = step_num,
                action       = action_type,
                description  = line,
                source       = source,
                destination  = dest,
                volume_ul    = vol,
                tool         = tool,
            )

        # No pattern matched — create a generic OBSERVE step
        return ProtocolStepModel(
            step_number = step_num,
            action      = ActionType.OBSERVE,
            description = line,
        )

    @staticmethod
    def _infer_labware_type(name: str) -> LabwareType:
        """Infer labware type from its name."""
        n = name.lower()
        if any(k in n for k in ("thermocycl", "pcr machine", "cycler")):
            return LabwareType.THERMOCYCLER
        if any(k in n for k in ("plate", "96", "384")):
            return LabwareType.MICROPLATE_96
        if any(k in n for k in ("falcon", "15ml", "50ml")):
            return LabwareType.FALCON_TUBE_15ML
        if any(k in n for k in ("eppendorf", "micro", "1.5", "2ml")):
            return LabwareType.EPPENDORF_TUBE
        if "pcr" in n and "tube" in n:
            return LabwareType.PCR_TUBE
        if any(k in n for k in ("reservoir", "stock", "boat")):
            return LabwareType.REAGENT_RESERVOIR
        # Default to PCR tube for small tube-like containers
        return LabwareType.PCR_TUBE
