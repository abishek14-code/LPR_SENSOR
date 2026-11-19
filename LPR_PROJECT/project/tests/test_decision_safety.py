import pytest

from python.config import AppConfig
from python.controller import Controller, State, process_recorded_frames
from python.diagnostics import diagnose
from python.stateful_model import StatefulPlant


@pytest.fixture
def supplied():
    config = AppConfig()
    plant = StatefulPlant(config.model, seed=config.seed)
    opening = plant.measure_ocp(60)
    frames = {}
    reverse = {}
    for name, amplitude in (("A1", .005), ("B", .010), ("A2", .005)):
        frames[name] = plant.acquire_block(name, amplitude, config.acquisition)
        plant.recover(config.acquisition)
        reverse[name] = plant.acquire_block(name, amplitude, config.acquisition, reverse=True)
        plant.recover(config.acquisition)
    evidence = dict(
        opening_check={"passed": True, "source": "test fixture reference check"},
        closing_check={"passed": True, "source": "test fixture reference check"},
        ocp_opening=opening, ocp_closing=plant.measure_ocp(60),
        reverse_frames=reverse,
    )
    return config, frames, evidence


@pytest.mark.parametrize("missing", ["all", "A1", "B", "A2"])
def test_missing_measurement_never_releases(supplied, missing):
    config, frames, evidence = supplied
    frames = {} if missing == "all" else {k: v for k, v in frames.items() if k != missing}
    record = process_recorded_frames(config, "missing", frames, **evidence)
    assert record.final_state == "REJECT"
    assert "MISSING_DATA" in record.diagnostics.reason_codes
    assert "RELEASE" not in record.state_history


@pytest.mark.parametrize("missing", ["opening_check", "closing_check", "ocp_opening", "ocp_closing", "reverse_frames"])
def test_missing_operational_evidence_never_releases(supplied, missing):
    config, frames, evidence = supplied
    evidence.pop(missing)
    record = process_recorded_frames(config, "missing", frames, **evidence)
    assert record.final_state == "REJECT"
    assert "RELEASE" not in record.state_history
    assert "CLOSING_CHECK" not in record.state_history


def test_explicit_supplied_evidence_can_release_without_inventing_events(supplied):
    config, frames, evidence = supplied
    record = process_recorded_frames(config, "complete", frames, **evidence)
    assert record.decision == "ACCEPT"
    assert record.state_history == ["IDLE", "DIAGNOSE", "RELEASE"]
    assert record.to_dict()["diagnostic_details"]["measured_values"]["closing_check"] == evidence["closing_check"]


def test_reverse_timeout_propagates(supplied):
    config, frames, evidence = supplied
    evidence["reverse_frames"]["A1"]["time_s"] *= 5
    result = diagnose(frames, config.diagnostics, **evidence)
    assert not result.passed
    assert "TIMEOUT" in result.reason_codes
    assert "TIMEOUT" in result.measured_values["reverse_blocks"]["A1"]["reason_codes"]


@pytest.mark.parametrize("check", ["opening_check", "closing_check"])
@pytest.mark.parametrize("value", [None, {}, {"passed": "true", "source": "fixture"}, {"passed": True}, {"passed": False, "source": "fixture"}])
def test_invalid_or_failed_checks_prevent_release(supplied, check, value):
    config, frames, evidence = supplied
    evidence[check] = value
    assert process_recorded_frames(config, "invalid", frames, **evidence).final_state == "REJECT"


def test_malformed_ocp_and_frame_are_rejected(supplied):
    config, frames, evidence = supplied
    evidence["ocp_opening"]["ocp_voltage_v"] = float("nan")
    frames["A1"] = frames["A1"].drop(columns="current_a")
    result = diagnose(frames, config.diagnostics, **evidence)
    assert not result.passed
    assert "INVALID_DATA" in result.reason_codes
    assert "MISSING_OCP_EVIDENCE" in result.reason_codes


def test_controller_close_cannot_bypass_diagnostics(supplied):
    assert Controller().close() == State.REJECT
    assert Controller(state=State.DIAGNOSE).close(passed=True) == State.REJECT
    config, frames, _ = supplied
    analysis = diagnose(frames, config.diagnostics, require_release_evidence=False)
    assert Controller(state=State.DIAGNOSE).close(True, analysis) == State.REJECT
