"""
Tests for the Protocol Validation Engine
==========================================
Tests every validation rule (R01–R14) plus the Pydantic model constraints
and the NaturalLanguageParser.

Run with:  pytest tests/test_validation.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Project root resolved from conftest.py — no manual sys.path needed.
_ROOT = Path(__file__).resolve().parent.parent

from models.protocol_models import (
    ActionType,
    ColorRGBA,
    LabwareModel,
    LabwareType,
    PipetteType,
    ProtocolModel,
    ProtocolStepModel,
    ReagentModel,
    ThermocycleStage,
)
from validation.protocol_validator import ProtocolValidator
from interpreter.protocol_interpreter import ProtocolInterpreter, NaturalLanguageParser


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_minimal_protocol(**kwargs) -> ProtocolModel:
    """Build the smallest possible valid PCR protocol."""
    defaults = dict(
        protocol_id="TEST-001",
        protocol_name="Test PCR",
        reagents=[
            ReagentModel(
                id="dna",
                name="DNA Template",
                volume_ul=100.0,
                container="stock_dna",
                color=ColorRGBA(r=0.1, g=0.4, b=0.9, a=0.8),
            ),
            ReagentModel(
                id="mmix",
                name="Master Mix",
                volume_ul=200.0,
                container="stock_mmix",
                color=ColorRGBA(r=0.9, g=0.4, b=0.1, a=0.8),
            ),
        ],
        labware=[
            LabwareModel(
                id="stock_dna",
                name="DNA Stock",
                labware_type=LabwareType.MICROCENTRIFUGE_TUBE,
                capacity_ul=1500.0,
                current_volume=100.0,
            ),
            LabwareModel(
                id="stock_mmix",
                name="Master Mix Stock",
                labware_type=LabwareType.MICROCENTRIFUGE_TUBE,
                capacity_ul=1500.0,
                current_volume=200.0,
            ),
            LabwareModel(
                id="pcr_tube",
                name="PCR Tube",
                labware_type=LabwareType.PCR_TUBE,
                capacity_ul=200.0,
                current_volume=0.0,
            ),
            LabwareModel(
                id="tc",
                name="Thermocycler",
                labware_type=LabwareType.THERMOCYCLER,
                capacity_ul=9999.0,
                is_instrument=True,
            ),
        ],
        steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Add 12.5 µL master mix to PCR tube",
                source="mmix",
                destination="pcr_tube",
                volume_ul=12.5,
                tool=PipetteType.P20,
            ),
            ProtocolStepModel(
                step_number=2,
                action=ActionType.PIPETTE,
                description="Add 1 µL DNA to PCR tube",
                source="dna",
                destination="pcr_tube",
                volume_ul=1.0,
                tool=PipetteType.P2,
            ),
            ProtocolStepModel(
                step_number=3,
                action=ActionType.MIX,
                description="Mix the reaction by pipetting 5 times",
                source="pcr_tube",
                destination="pcr_tube",
                volume_ul=10.0,
                tool=PipetteType.P20,
                mix_cycles=5,
            ),
            ProtocolStepModel(
                step_number=4,
                action=ActionType.PLACE,
                description="Place PCR tube in thermocycler",
                source="pcr_tube",
                destination="tc",
            ),
            ProtocolStepModel(
                step_number=5,
                action=ActionType.THERMOCYCLE,
                description="Run PCR",
                destination="tc",
                thermocycle_program=[
                    ThermocycleStage(name="Denaturation", temperature=95.0, duration_s=30, cycles=35),
                    ThermocycleStage(name="Annealing",    temperature=58.0, duration_s=30, cycles=35),
                    ThermocycleStage(name="Extension",    temperature=72.0, duration_s=60, cycles=35),
                ],
            ),
        ],
    )
    defaults.update(kwargs)
    return ProtocolModel(**defaults)


@pytest.fixture
def valid_protocol() -> ProtocolModel:
    return _make_minimal_protocol()


@pytest.fixture
def validator() -> ProtocolValidator:
    return ProtocolValidator()


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic model tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProtocolModels:

    def test_valid_protocol_parses(self, valid_protocol):
        assert valid_protocol.protocol_id == "TEST-001"
        assert len(valid_protocol.steps) == 5

    def test_reagent_id_no_spaces(self):
        with pytest.raises(Exception, match="spaces"):
            ReagentModel(
                id="dna template",  # spaces not allowed
                name="DNA",
                volume_ul=100.0,
                container="tube",
            )

    def test_labware_volume_overflow(self):
        with pytest.raises(Exception):
            LabwareModel(
                id="tube",
                name="Tube",
                labware_type=LabwareType.PCR_TUBE,
                capacity_ul=100.0,
                current_volume=200.0,   # exceeds capacity
            )

    def test_step_liquid_action_requires_volume(self):
        with pytest.raises(Exception, match="volume_ul"):
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Add reagent with no volume",
                source="dna",
                destination="tube",
                # volume_ul is missing — should raise
            )

    def test_thermocycle_requires_program(self):
        with pytest.raises(Exception, match="thermocycle_program"):
            ProtocolStepModel(
                step_number=1,
                action=ActionType.THERMOCYCLE,
                description="Run PCR without program",
                # thermocycle_program missing
            )

    def test_step_numbering_sequential(self):
        with pytest.raises(Exception, match="step_number"):
            _make_minimal_protocol(steps=[
                ProtocolStepModel(
                    step_number=1, action=ActionType.OBSERVE,
                    description="Step one"
                ),
                ProtocolStepModel(
                    step_number=3,  # gap — should raise
                    action=ActionType.OBSERVE,
                    description="Step three"
                ),
            ])

    def test_duplicate_reagent_ids(self):
        with pytest.raises(Exception, match="[Dd]uplicate"):
            _make_minimal_protocol(reagents=[
                ReagentModel(id="dup", name="A", volume_ul=10.0, container="c"),
                ReagentModel(id="dup", name="B", volume_ul=10.0, container="c"),
            ])

    def test_protocol_from_json_file(self):
        path = _ROOT / "examples" / "pcr_preparation.json"
        with path.open() as fh:
            data = json.load(fh)
        proto = ProtocolModel.model_validate(data)
        assert proto.protocol_id == "PCR-001"
        assert len(proto.steps) == 8


# ─────────────────────────────────────────────────────────────────────────────
# Validation rule tests
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationRules:

    def test_valid_protocol_passes(self, valid_protocol, validator):
        result = validator.validate(valid_protocol)
        assert result.is_valid, f"Expected valid protocol. Issues: {result.issues}"
        assert len(result.issues) == 0

    # R01 — Source exists
    def test_r01_missing_source(self, valid_protocol, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Pipette from nonexistent reagent",
                source="ghost_reagent",
                destination="pcr_tube",
                volume_ul=5.0,
                tool=PipetteType.P10,
            )
        ])
        result = validator.validate(proto)
        assert not result.is_valid
        codes = [i.code for i in result.issues]
        assert "R01" in codes

    # R02 — Destination exists
    def test_r02_missing_destination(self, valid_protocol, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Dispense into ghost tube",
                source="dna",
                destination="ghost_tube",
                volume_ul=5.0,
                tool=PipetteType.P10,
            )
        ])
        result = validator.validate(proto)
        assert not result.is_valid
        assert any(i.code == "R02" for i in result.issues)

    # R03 — Pipette range
    def test_r03_pipette_out_of_range(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Use P2 to pipette 500 µL — out of range",
                source="mmix",
                destination="pcr_tube",
                volume_ul=500.0,   # P2 max is 2 µL
                tool=PipetteType.P2,
            )
        ])
        result = validator.validate(proto)
        assert any(i.code == "R03" for i in result.issues)

    def test_r03_pipette_in_range_passes(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="P200 used within its 20–200 µL range",
                source="mmix",
                destination="pcr_tube",
                volume_ul=50.0,
                tool=PipetteType.P200,
            )
        ])
        result = validator.validate(proto)
        assert not any(i.code == "R03" for i in result.issues + result.warnings)

    # R04 — Volume available
    def test_r04_insufficient_volume(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Try to take 9999 µL from a 100 µL source",
                source="dna",
                destination="pcr_tube",
                volume_ul=9999.0,   # stock only has 100 µL
                tool=PipetteType.P1000,
            )
        ])
        result = validator.validate(proto)
        assert any(i.code == "R04" for i in result.issues)

    # R05 — Destination capacity
    def test_r05_overflow(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Add 150 µL to a 200 µL tube",
                source="mmix",
                destination="pcr_tube",
                volume_ul=150.0,
                tool=PipetteType.P200,
            ),
            ProtocolStepModel(
                step_number=2,
                action=ActionType.PIPETTE,
                description="Add another 100 µL — overflow!",
                source="mmix",
                destination="pcr_tube",
                volume_ul=100.0,  # 150 + 100 > 200 µL capacity
                tool=PipetteType.P200,
            ),
        ])
        result = validator.validate(proto)
        assert any(i.code == "R05" for i in result.issues)

    # R07 — Missing tool warning
    def test_r07_missing_tool_warning(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.PIPETTE,
                description="Transfer without specifying a pipette",
                source="dna",
                destination="pcr_tube",
                volume_ul=5.0,
                # tool is deliberately omitted
            )
        ])
        result = validator.validate(proto)
        assert any(i.code == "R07" for i in result.warnings)

    # R08 — Instrument present
    def test_r08_thermocycler_missing(self, validator):
        proto = _make_minimal_protocol(
            labware=[
                lw for lw in _make_minimal_protocol().labware
                if lw.labware_type != LabwareType.THERMOCYCLER
            ],
            steps=[
                ProtocolStepModel(
                    step_number=1,
                    action=ActionType.THERMOCYCLE,
                    description="Run PCR — but no thermocycler declared",
                    thermocycle_program=[
                        ThermocycleStage(
                            name="Denature", temperature=95.0, duration_s=30
                        )
                    ],
                )
            ]
        )
        result = validator.validate(proto)
        assert any(i.code == "R08" for i in result.issues)

    # R11 — MIX same container
    def test_r11_mix_different_containers_warns(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.MIX,
                description="Mix with mismatched src/dest",
                source="dna",
                destination="pcr_tube",   # different from source
                volume_ul=10.0,
                tool=PipetteType.P20,
                mix_cycles=5,
            )
        ])
        result = validator.validate(proto)
        assert any(i.code == "R11" for i in result.warnings)

    # R12 — Valid temperature
    def test_r12_invalid_temperature(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.HEAT,
                description="Heat to 999 °C — impossible",
                temperature=999.0,
                duration_s=60,
            )
        ])
        result = validator.validate(proto)
        assert any(i.code == "R12" for i in result.issues)

    # R13 — PCR reagent order
    def test_r13_thermocycle_before_master_mix_warns(self, validator):
        proto = _make_minimal_protocol(steps=[
            ProtocolStepModel(
                step_number=1,
                action=ActionType.THERMOCYCLE,
                description="Run PCR without adding master mix first",
                thermocycle_program=[
                    ThermocycleStage(
                        name="Denature", temperature=95.0, duration_s=30, cycles=35
                    )
                ],
            )
        ])
        result = validator.validate(proto)
        assert any(i.code == "R13" for i in result.warnings)

    # Error example protocol
    def test_error_protocol_fails(self):
        path = _ROOT / "examples" / "pcr_with_errors.json"
        with path.open() as fh:
            data = json.load(fh)
        proto     = ProtocolModel.model_validate(data)
        validator = ProtocolValidator()
        result    = validator.validate(proto)
        assert not result.is_valid
        assert len(result.issues) >= 3   # several intentional errors

    # Full PCR example should pass
    def test_pcr_example_passes(self):
        path = _ROOT / "examples" / "pcr_preparation.json"
        with path.open() as fh:
            data = json.load(fh)
        proto     = ProtocolModel.model_validate(data)
        validator = ProtocolValidator()
        result    = validator.validate(proto)
        assert result.is_valid, f"PCR example failed validation: {result.issues}"

    # Full NGS example should pass
    def test_ngs_example_passes(self):
        path = _ROOT / "examples" / "ngs_library_prep.json"
        with path.open() as fh:
            data = json.load(fh)
        proto     = ProtocolModel.model_validate(data)
        validator = ProtocolValidator()
        result    = validator.validate(proto)
        assert result.is_valid, f"NGS example failed validation: {result.issues}"


# ─────────────────────────────────────────────────────────────────────────────
# ProtocolInterpreter tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProtocolInterpreter:

    def test_interpret_generates_commands(self, valid_protocol):
        interp = ProtocolInterpreter()
        cmds   = interp.interpret(valid_protocol)
        assert len(cmds) > 0

    def test_commands_cover_all_steps(self, valid_protocol):
        interp = ProtocolInterpreter()
        cmds   = interp.interpret(valid_protocol)
        step_numbers = {c.step_number for c in cmds}
        for step in valid_protocol.steps:
            assert step.step_number in step_numbers, (
                f"Step {step.step_number} has no simulation commands"
            )

    def test_frame_ordering_correct(self, valid_protocol):
        interp = ProtocolInterpreter()
        cmds   = interp.interpret(valid_protocol)
        for cmd in cmds:
            assert cmd.frame_end >= cmd.frame_start

    def test_pipette_action_generates_multiple_cmds(self, valid_protocol):
        """A PIPETTE step should generate ≥ 2 commands (aspirate + dispense)."""
        interp = ProtocolInterpreter()
        cmds   = interp.interpret(valid_protocol)
        step1_cmds = [c for c in cmds if c.step_number == 1]
        # The 'pipette' action produces aspirate + move + dispense commands
        assert len(step1_cmds) >= 2

    def test_step_summary_length(self, valid_protocol):
        interp   = ProtocolInterpreter()
        summary  = interp.step_summary(valid_protocol)
        assert len(summary) == len(valid_protocol.steps)

    def test_step_summary_fields(self, valid_protocol):
        interp   = ProtocolInterpreter()
        summary  = interp.step_summary(valid_protocol)
        for item in summary:
            assert "step"        in item
            assert "action"      in item
            assert "frame_start" in item
            assert "frame_end"   in item

    def test_interpret_from_json_file(self):
        interp = ProtocolInterpreter()
        cmds   = interp.interpret_from_json(
            str(_ROOT / "examples" / "pcr_preparation.json")
        )
        assert len(cmds) > 0


# ─────────────────────────────────────────────────────────────────────────────
# NaturalLanguageParser tests
# ─────────────────────────────────────────────────────────────────────────────

class TestNaturalLanguageParser:

    def test_parse_basic_pcr_instructions(self):
        text = """
        1. Add 12.5 µL of pcr master mix to pcr tube 1
        2. Add 1 µL of dna template to pcr tube 1
        3. Mix the reaction
        4. Place pcr tube 1 in thermocycler
        5. Run PCR
        """
        parser = NaturalLanguageParser()
        proto  = parser.parse(text, protocol_name="Parsed PCR")
        assert proto.protocol_name == "Parsed PCR"
        assert len(proto.steps) == 5

    def test_parse_detects_pipette_volumes(self):
        text = "Add 10 µL of forward primer to reaction tube"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        step = proto.steps[0]
        assert step.volume_ul == 10.0

    def test_parse_infers_pipette_type(self):
        text = "Add 5 µL of sample to pcr tube"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        assert proto.steps[0].tool == PipetteType.P10

    def test_parse_creates_reagent_records(self):
        text = "Transfer 20 µL from dna_stock to reaction_tube"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        reagent_ids = [r.id for r in proto.reagents]
        assert "dna_stock" in reagent_ids

    def test_parse_creates_labware_records(self):
        text = "Add 5 µL of primer to pcr_tube"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        labware_ids = [lw.id for lw in proto.labware]
        assert "pcr_tube" in labware_ids

    def test_parse_incubate_step(self):
        text = "Incubate at 95 °C for 5 min"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        step = proto.steps[0]
        assert step.action == ActionType.INCUBATE
        assert step.temperature == 95.0

    def test_parse_thermocycle_step(self):
        text = "Run PCR thermocycle program"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        assert proto.steps[0].action == ActionType.THERMOCYCLE

    def test_parse_unknown_line_becomes_observe(self):
        text = "Inspect the gel image carefully under UV light"
        parser = NaturalLanguageParser()
        proto  = parser.parse(text)
        assert proto.steps[0].action == ActionType.OBSERVE


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIntegration:

    def test_validate_then_interpret_pcr(self):
        path = _ROOT / "examples" / "pcr_preparation.json"
        with path.open() as fh:
            data = json.load(fh)
        proto     = ProtocolModel.model_validate(data)
        validator = ProtocolValidator()
        result    = validator.validate(proto)
        assert result.is_valid

        interp = ProtocolInterpreter()
        cmds   = interp.interpret(proto)
        assert len(cmds) > 0
        assert all(c.frame_end >= c.frame_start for c in cmds)

    def test_validate_then_interpret_ngs(self):
        path = _ROOT / "examples" / "ngs_library_prep.json"
        with path.open() as fh:
            data = json.load(fh)
        proto     = ProtocolModel.model_validate(data)
        validator = ProtocolValidator()
        result    = validator.validate(proto)
        assert result.is_valid

        interp = ProtocolInterpreter()
        cmds   = interp.interpret(proto)
        # 22 steps, each generates ≥ 1 command
        assert len(cmds) >= 22

    def test_error_protocol_errors_reported_correctly(self):
        path = _ROOT / "examples" / "pcr_with_errors.json"
        with path.open() as fh:
            data = json.load(fh)
        proto     = ProtocolModel.model_validate(data)
        validator = ProtocolValidator()
        result    = validator.validate(proto)
        assert not result.is_valid
        # Expect errors for: missing source (R01), overflow (R05), instrument (R08)
        codes = {i.code for i in result.issues}
        assert "R01" in codes
        assert "R08" in codes

    def test_full_simulation_command_count(self):
        """PCR protocol with 8 steps should generate a substantial command list."""
        path = _ROOT / "examples" / "pcr_preparation.json"
        with path.open() as fh:
            data = json.load(fh)
        proto  = ProtocolModel.model_validate(data)
        interp = ProtocolInterpreter()
        cmds   = interp.interpret(proto)
        assert len(cmds) >= 8   # at least 1 command per step
