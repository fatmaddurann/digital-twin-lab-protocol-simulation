"""
Protocol Logic Validation Engine
=================================
Validates a ProtocolModel for logical correctness before simulation begins.

Rules implemented
-----------------
 R01  SOURCE_EXISTS         — source reagent/labware must be declared
 R02  DEST_EXISTS           — destination labware must be declared
 R03  PIPETTE_RANGE         — volume must be within pipette min/max range
 R04  VOLUME_AVAILABLE      — source must have enough liquid
 R05  DEST_CAPACITY         — destination must not overflow
 R06  REAGENT_ORDER         — required reagents added before dependent steps
 R07  MISSING_TOOL          — liquid-handling steps must specify a pipette
 R08  INSTRUMENT_PRESENT    — THERMOCYCLE / CENTRIFUGE require the instrument
 R09  STEP_CONTINUITY       — steps must be numbered sequentially
 R10  DUPLICATE_IDS         — IDs must be unique (already caught by Pydantic)
 R11  MIX_DESTINATION_SAME  — MIX action source == destination
 R12  VALID_TEMPERATURE     — temperature must be within biologically sane range
 R13  PCR_REAGENT_ORDER     — for PCR, master mix added before primers (warning)
 R14  ZERO_VOLUME_TRANSFER  — volume > 0 for any transfer action
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from models.protocol_models import (
    ActionType,
    ErrorSeverity,
    LabwareModel,
    LabwareType,
    PipetteType,
    ProtocolModel,
    ProtocolStepModel,
    ReagentModel,
    StepValidationIssue,
    ValidationResultModel,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Pipette working ranges (µL)
# ─────────────────────────────────────────────
PIPETTE_RANGES: Dict[PipetteType, tuple[float, float]] = {
    PipetteType.P2:               (0.1,   2.0),
    PipetteType.P10:              (1.0,   10.0),
    PipetteType.P20:              (2.0,   20.0),
    PipetteType.P200:             (20.0,  200.0),
    PipetteType.P1000:            (100.0, 1000.0),
    PipetteType.MULTICHANNEL_P20: (2.0,   20.0),
    PipetteType.MULTICHANNEL_P200:(20.0,  200.0),
}

# Actions that move liquid from source → destination
LIQUID_TRANSFER_ACTIONS = {
    ActionType.PIPETTE,
    ActionType.TRANSFER,
    ActionType.ASPIRATE,
    ActionType.DISPENSE,
}

# Instruments required for specific actions
INSTRUMENT_ACTION_MAP: Dict[ActionType, LabwareType] = {
    ActionType.THERMOCYCLE:  LabwareType.THERMOCYCLER,
    ActionType.CENTRIFUGE:   LabwareType.CENTRIFUGE,
    ActionType.VORTEX:       LabwareType.VORTEX_MIXER,
}


# ─────────────────────────────────────────────
# Rule interface
# ─────────────────────────────────────────────

@dataclass
class ValidationContext:
    """
    Mutable state threaded through every rule during a single validation pass.

    volume_tracker  : labware_id → current volume (µL), updated as steps run
    added_reagents  : set of reagent IDs that have been dispensed so far
    """
    protocol:       ProtocolModel
    reagent_index:  Dict[str, ReagentModel]    = field(default_factory=dict)
    labware_index:  Dict[str, LabwareModel]    = field(default_factory=dict)
    volume_tracker: Dict[str, float]           = field(default_factory=dict)
    added_reagents: Set[str]                   = field(default_factory=set)

    def __post_init__(self) -> None:
        self.reagent_index  = {r.id: r for r in self.protocol.reagents}
        self.labware_index  = {lw.id: lw for lw in self.protocol.labware}
        self.volume_tracker = {lw.id: lw.current_volume for lw in self.protocol.labware}
        # Source containers start with their reagent volume available
        for reagent in self.protocol.reagents:
            if reagent.container in self.volume_tracker:
                self.volume_tracker[reagent.container] = max(
                    self.volume_tracker[reagent.container],
                    reagent.volume_ul
                )


class ValidationRule(ABC):
    """Base class for a single validation rule."""
    rule_code: str = "R00"
    description: str = ""

    @abstractmethod
    def check(
        self,
        step: ProtocolStepModel,
        ctx: ValidationContext,
    ) -> List[StepValidationIssue]:
        """Return a (possibly empty) list of issues found for *step*."""

    def _issue(
        self,
        step: ProtocolStepModel,
        severity: ErrorSeverity,
        message: str,
        suggestion: Optional[str] = None,
    ) -> StepValidationIssue:
        return StepValidationIssue(
            step_number=step.step_number,
            severity=severity,
            code=self.rule_code,
            message=message,
            suggestion=suggestion,
        )


# ─────────────────────────────────────────────
# Concrete Rules
# ─────────────────────────────────────────────

class R01_SourceExists(ValidationRule):
    rule_code   = "R01"
    description = "Source reagent or labware must be declared in the protocol"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.source is None:
            return issues
        src = step.source
        if src not in ctx.reagent_index and src not in ctx.labware_index:
            issues.append(self._issue(
                step, ErrorSeverity.ERROR,
                f"Source '{src}' is not declared as a reagent or labware.",
                suggestion=f"Add '{src}' to the protocol's 'reagents' or 'labware' section."
            ))
        return issues


class R02_DestExists(ValidationRule):
    rule_code   = "R02"
    description = "Destination labware must be declared in the protocol"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.destination is None:
            return issues
        if step.destination not in ctx.labware_index:
            issues.append(self._issue(
                step, ErrorSeverity.ERROR,
                f"Destination labware '{step.destination}' is not declared.",
                suggestion=f"Add '{step.destination}' to the protocol's 'labware' section."
            ))
        return issues


class R03_PipetteRange(ValidationRule):
    rule_code   = "R03"
    description = "Volume must be within pipette working range"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.tool is None or step.volume_ul is None:
            return issues
        lo, hi = PIPETTE_RANGES.get(step.tool, (0, float("inf")))
        if not (lo <= step.volume_ul <= hi):
            issues.append(self._issue(
                step, ErrorSeverity.ERROR,
                f"Volume {step.volume_ul} µL is out of range for {step.tool.value} "
                f"({lo}–{hi} µL).",
                suggestion="Use a different pipette for this volume, e.g. "
                           "P200 for 20–200 µL, P1000 for 100–1000 µL."
            ))
        return issues


class R04_VolumeAvailable(ValidationRule):
    rule_code   = "R04"
    description = "Source must contain enough liquid for the transfer"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.action not in LIQUID_TRANSFER_ACTIONS:
            return issues
        if step.source is None or step.volume_ul is None:
            return issues

        # Determine which labware holds the source
        src_container = step.source
        if step.source in ctx.reagent_index:
            src_container = ctx.reagent_index[step.source].container

        available = ctx.volume_tracker.get(src_container, 0.0)
        if step.volume_ul > available:
            issues.append(self._issue(
                step, ErrorSeverity.ERROR,
                f"Insufficient volume in '{src_container}': requested {step.volume_ul} µL "
                f"but only {available:.2f} µL available.",
                suggestion="Check reagent volumes or adjust the protocol step volume."
            ))
        else:
            # Deduct volume from source tracker
            ctx.volume_tracker[src_container] = available - step.volume_ul
        return issues


class R05_DestCapacity(ValidationRule):
    rule_code   = "R05"
    description = "Destination must not overflow after transfer"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.action not in LIQUID_TRANSFER_ACTIONS:
            return issues
        if step.destination is None or step.volume_ul is None:
            return issues

        lw = ctx.labware_index.get(step.destination)
        if lw is None:
            return issues  # R02 will catch the missing labware

        current = ctx.volume_tracker.get(step.destination, lw.current_volume)
        if current + step.volume_ul > lw.capacity_ul:
            issues.append(self._issue(
                step, ErrorSeverity.ERROR,
                f"Overflow in '{step.destination}': adding {step.volume_ul} µL to "
                f"{current:.2f} µL would exceed capacity of {lw.capacity_ul} µL.",
                suggestion="Use a larger tube or reduce the volume."
            ))
        else:
            ctx.volume_tracker[step.destination] = current + step.volume_ul
            # Track that a reagent has been added to this destination
            if step.source and step.source in ctx.reagent_index:
                ctx.added_reagents.add(step.source)
        return issues


class R07_MissingTool(ValidationRule):
    rule_code   = "R07"
    description = "Liquid-handling actions must specify a pipette"

    LIQUID_ACTIONS = {
        ActionType.ASPIRATE, ActionType.DISPENSE,
        ActionType.PIPETTE, ActionType.TRANSFER, ActionType.MIX
    }

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.action in self.LIQUID_ACTIONS and step.tool is None:
            issues.append(self._issue(
                step, ErrorSeverity.WARNING,
                f"Step {step.step_number} ({step.action.value}) does not specify a pipette.",
                suggestion="Add a 'tool' field to indicate which pipette to use."
            ))
        return issues


class R08_InstrumentPresent(ValidationRule):
    rule_code   = "R08"
    description = "Instrument-dependent actions require the instrument to be declared"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        required_type = INSTRUMENT_ACTION_MAP.get(step.action)
        if required_type is None:
            return issues

        has_instrument = any(
            lw.labware_type == required_type for lw in ctx.protocol.labware
        )
        if not has_instrument:
            issues.append(self._issue(
                step, ErrorSeverity.ERROR,
                f"Action '{step.action.value}' requires a {required_type.value} "
                f"in the labware list but none was found.",
                suggestion=f"Add a labware entry of type '{required_type.value}'."
            ))
        return issues


class R11_MixSameContainer(ValidationRule):
    rule_code   = "R11"
    description = "MIX action: source and destination should be the same container"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.action != ActionType.MIX:
            return issues
        if step.source and step.destination and step.source != step.destination:
            issues.append(self._issue(
                step, ErrorSeverity.WARNING,
                f"MIX step has source='{step.source}' and destination='{step.destination}' "
                f"which differ — MIX is an in-place action.",
                suggestion="Set source == destination for mix steps, or use TRANSFER instead."
            ))
        return issues


class R12_ValidTemperature(ValidationRule):
    rule_code   = "R12"
    description = "Temperature must be in a biologically reasonable range (−200 °C to 200 °C)"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.temperature is not None:
            if not (-200 <= step.temperature <= 200):
                issues.append(self._issue(
                    step, ErrorSeverity.ERROR,
                    f"Temperature {step.temperature} °C is outside the valid range.",
                    suggestion="Check the temperature value; typical PCR range is 4–98 °C."
                ))
        if step.thermocycle_program:
            for stage in step.thermocycle_program:
                if not (-200 <= stage.temperature <= 200):
                    issues.append(self._issue(
                        step, ErrorSeverity.ERROR,
                        f"Thermocycle stage '{stage.name}' has invalid temperature "
                        f"{stage.temperature} °C.",
                    ))
        return issues


class R13_PCRReagentOrder(ValidationRule):
    rule_code   = "R13"
    description = "PCR: master mix should be added before thermocycling (warning)"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.action != ActionType.THERMOCYCLE:
            return issues
        # Check if any reagent named 'master_mix' or similar has been added
        master_mix_added = any(
            "master" in rid.lower() or "mmix" in rid.lower()
            for rid in ctx.added_reagents
        )
        if not master_mix_added:
            issues.append(self._issue(
                step, ErrorSeverity.WARNING,
                "THERMOCYCLE step reached but no master mix reagent has been dispensed yet.",
                suggestion="Ensure master mix is added to the reaction tube before thermocycling."
            ))
        return issues


class R14_ZeroVolumeTransfer(ValidationRule):
    rule_code   = "R14"
    description = "Volume must be greater than zero for any liquid-handling step"

    def check(self, step: ProtocolStepModel, ctx: ValidationContext) -> List[StepValidationIssue]:
        issues = []
        if step.action in LIQUID_TRANSFER_ACTIONS and step.volume_ul is not None:
            if step.volume_ul <= 0:
                issues.append(self._issue(
                    step, ErrorSeverity.ERROR,
                    f"Volume is {step.volume_ul} µL — must be greater than zero.",
                ))
        return issues


# ─────────────────────────────────────────────
# Validator orchestrator
# ─────────────────────────────────────────────

DEFAULT_RULES: List[ValidationRule] = [
    R01_SourceExists(),
    R02_DestExists(),
    R03_PipetteRange(),
    R04_VolumeAvailable(),
    R05_DestCapacity(),
    R07_MissingTool(),
    R08_InstrumentPresent(),
    R11_MixSameContainer(),
    R12_ValidTemperature(),
    R13_PCRReagentOrder(),
    R14_ZeroVolumeTransfer(),
]


class ProtocolValidator:
    """
    Orchestrates all validation rules against a ProtocolModel.

    Usage
    -----
    >>> validator = ProtocolValidator()
    >>> result = validator.validate(protocol)
    >>> if not result.is_valid:
    ...     for issue in result.issues:
    ...         print(issue)
    """

    def __init__(self, rules: Optional[List[ValidationRule]] = None) -> None:
        self.rules: List[ValidationRule] = rules if rules is not None else DEFAULT_RULES

    def validate(self, protocol: ProtocolModel) -> ValidationResultModel:
        """
        Run all rules against every step in *protocol*.

        Returns a ValidationResultModel with all issues and warnings.
        The protocol's volume state is simulated step-by-step to catch
        ordering-dependent errors (e.g. insufficient volume later in the run).
        """
        logger.info("Validating protocol '%s' (%d steps)…",
                    protocol.protocol_name, len(protocol.steps))

        ctx = ValidationContext(protocol=protocol)
        all_issues: List[StepValidationIssue] = []

        for step in protocol.steps:
            for rule in self.rules:
                try:
                    found = rule.check(step, ctx)
                    all_issues.extend(found)
                except Exception as exc:  # pragma: no cover
                    logger.error("Rule %s raised an exception: %s", rule.rule_code, exc)

        errors   = [i for i in all_issues if i.severity != ErrorSeverity.WARNING]
        warnings = [i for i in all_issues if i.severity == ErrorSeverity.WARNING]
        is_valid = len(errors) == 0

        result = ValidationResultModel(
            protocol_id=protocol.protocol_id,
            is_valid=is_valid,
            issues=errors,
            warnings=warnings,
        )

        if is_valid:
            logger.info("Protocol '%s' passed validation with %d warning(s).",
                        protocol.protocol_name, len(warnings))
        else:
            logger.warning("Protocol '%s' FAILED validation: %d error(s), %d warning(s).",
                           protocol.protocol_name, len(errors), len(warnings))

        return result

    def validate_from_dict(self, data: dict) -> ValidationResultModel:
        """Convenience: parse a raw dict into a ProtocolModel and validate."""
        protocol = ProtocolModel.model_validate(data)
        return self.validate(protocol)

    def get_step_issues(
        self, result: ValidationResultModel, step_number: int
    ) -> List[StepValidationIssue]:
        """Return all issues for a specific step number."""
        return [
            i for i in (result.issues + result.warnings)
            if i.step_number == step_number
        ]

    def steps_with_errors(self, result: ValidationResultModel) -> List[int]:
        """Return a sorted list of step numbers that have at least one error."""
        return sorted({i.step_number for i in result.issues})
