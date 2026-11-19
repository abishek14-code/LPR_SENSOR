"""Controlled SYNTHETIC software fixtures; no physical validation claims."""
from dataclasses import replace
import copy
import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from python.config import AppConfig, ModelConfig, AcquisitionConfig, DiagnosticConfig
from python.controller import run_attempt, process_recorded_frames
from python.decision_engine import EvaluationContext, evaluate_measurement, evaluate_diagnostics
from python.diagnostics import diagnose
from python.io_utils import format_decision, save_decision_record
from python.lpr_analysis import estimate_rapp, estimate_settling, analyze_block
from python.simulator_adapter import normalize_trace, TraceAdapterConfig


@pytest.fixture
def supplied():
    """Independent linear E=110000*I reference with explicit operational evidence."""
    cfg = AppConfig(acquisition=AcquisitionConfig(enable_reverse_check=False))
    frames = {}
    for block_index, (name, amplitude) in enumerate((("A1", .005), ("B", .010), ("A2", .005))):
        v = np.repeat(np.linspace(-amplitude, amplitude, 5), 300)
        frames[name] = pd.DataFrame({
            "time_s": 61 + block_index * 80 + np.arange(len(v)) / 20,
            "actual_voltage_v": v, "requested_voltage_v": v, "current_a": v / 110000,
            "point_index": np.repeat(np.arange(5), 300), "drive_enabled": True,
            "reverse": False, "block_id": name, "run_id": "synthetic-run", "attempt_id": "synthetic-0",
        })
    evidence = dict(opening_check={"passed": True, "source": "SYNTHETIC known reference"},
                    closing_check={"passed": True, "source": "SYNTHETIC known reference"},
                    ocp_opening=pd.DataFrame({"time_s": np.arange(1, 1201)/20, "ocp_voltage_v": 0.0}),
                    ocp_closing=pd.DataFrame({"time_s": 300 + np.arange(1, 1201)/20, "ocp_voltage_v": 0.0}))
    return cfg, frames, evidence


def result_for(supplied, **kwargs):
    cfg, frames, evidence = supplied
    return evaluate_measurement(frames, cfg, **evidence, **kwargs)


def assert_contract(result, decision, code=None):
    assert result.decision == decision
    data = json.loads(json.dumps(result.to_dict(), allow_nan=False))
    for key in ("primary_reason", "reason_codes", "checks", "measurements", "thresholds", "warnings", "recommended_action", "retry_plan", "uncertainty", "rp_estimate", "corrosion_estimate", "result_status"):
        assert key in data
    assert data["corrosion_estimate"] is None
    assert data["recommended_action"]
    if code:
        assert code in data["reason_codes"]
        failures = [check for check in data["checks"] if not check["passed"] and check["reason_code"] == code]
        assert failures
        assert all("observed" in c and "expected" in c and c["explanation"] and c["recommended_action"] for c in failures)
    if decision != "ACCEPT":
        assert data["rp_estimate"] is None
    return data


def test_valid_quality_without_calibration_keeps_estimates_unavailable(supplied):
    result = result_for(supplied)
    data = assert_contract(result, "ACCEPT")
    assert result.primary_reason == "ALL_MANDATORY_CHECKS_PASSED"
    assert not result.reason_codes and all(check.passed for check in result.checks)
    assert data["measurements"]["Rapp_ohm"] == pytest.approx(110000)
    assert data["rp_estimate"] is None
    assert data["uncertainty"]["measurement"]["status"] == "HEURISTIC_ONLY"
    assert data["uncertainty"]["confidence_interval"] is None
    assert any("calibration" in warning for warning in result.warnings)


def test_rp_eligibility_and_independent_standard_uncertainty(supplied):
    context = EvaluationContext(correction_enabled=True, simplified_model=True, ru_ohm=10000,
                                rapp_standard_uncertainty_ohm=300, ru_standard_uncertainty_ohm=400,
                                independent_uncertainties=True, calibration_source="SYNTHETIC calibration fixture")
    result = result_for(supplied, context=context)
    assert_contract(result, "ACCEPT")
    assert result.rp_estimate == pytest.approx(100000)
    assert result.uncertainty["rp_standard_uncertainty_ohm"] == 500
    assert result.uncertainty["confidence_interval"] is None
    for field in ("correction_enabled", "simplified_model"):
        assert result_for(supplied, context=replace(context, **{field: False})).rp_estimate is None
    assert result_for(supplied, context=replace(context, ru_ohm=120000)).rp_estimate is None
    assert result_for(supplied, context=replace(context, independent_uncertainties=False)).uncertainty["rp_standard_uncertainty_ohm"] is None
    assert result_for(supplied, context=replace(context, calibration_source=None)).uncertainty["rp_standard_uncertainty_ohm"] is None


def test_unsettled_wait_then_retry_exhaustion(supplied):
    cfg, frames, evidence = supplied
    for frame in frames.values():
        # A small deterministic within-point trend preserves usable excitation,
        # but violates the existing final-window stability criterion.
        frame["current_a"] += np.tile(np.linspace(0, 1e-8, 300), 5)
    waiting = result_for(supplied)
    assert_contract(waiting, "WAIT_OR_REPEAT", "NOT_SETTLED")
    assert waiting.retry_plan["repeat_permitted"] and waiting.retry_plan["remaining_retries"] == 1
    exhausted = result_for(supplied, context=EvaluationContext(retries_used=1))
    assert_contract(exhausted, "REJECT", "RETRY_LIMIT_EXHAUSTED")
    assert exhausted.primary_reason == "RETRY_LIMIT_EXHAUSTED"
    assert not exhausted.retry_plan["repeat_permitted"]


def test_recoverable_transient_is_retained_then_valid_repeat_accepts():
    record = run_attempt(AppConfig(), "synthetic-slow", fault="recoverable_slow_fault")
    assert record.decision_result.decision == "ACCEPT"
    assert record.reacquisition_count == 1
    first, second = record.attempt_history
    assert first["decision_result"]["decision"] == "WAIT_OR_REPEAT"
    assert second["decision_result"]["decision"] == "ACCEPT"
    assert first["end"] == second["start"]


def test_failed_hardware_precedes_settling_and_blocks_replay(supplied):
    cfg, frames, evidence = supplied
    evidence["opening_check"]["passed"] = False
    for frame in frames.values():
        frame["current_a"] += np.tile(np.linspace(0, 1e-8, 300), 5)
    result = result_for(supplied)
    assert_contract(result, "REJECT", "OPENING_CHECK_FAILURE")
    assert result.primary_reason == "OPENING_CHECK_FAILURE"
    assert "NOT_SETTLED" in result.reason_codes
    assert not result.retry_plan["repeat_permitted"] and not result.retry_plan["reference_required"]
    raw = diagnose(frames, cfg.diagnostics, **evidence)
    assert not raw.replay_required and not raw.recoverable
    assert all(not check.recoverable for check in result.checks if not check.passed)


@pytest.mark.parametrize("context,code", [
    ({"fault_flags": {"hardware_overload": True}}, "INSTRUMENT_FAULT"),
    ({"simulation_status": "failed"}, "SIMULATOR_FAILURE"),
    ({"simulation_status": "unavailable"}, "MISSING_SIMULATION_OUTPUT"),
    ({"expected_identity": {"run_id": "new"}, "output_identity": {"run_id": "old"}}, "OUTPUT_MISMATCH"),
    ({"expected_identity": {"source_hash": "abc"}}, "OUTPUT_MISMATCH"),
])
def test_external_fault_status_and_stale_outputs(supplied, context, code):
    data = assert_contract(result_for(supplied, context=context), "REJECT", code)
    assert data["primary_reason"] == code
    assert not data["retry_plan"]["reference_required"]


@pytest.mark.parametrize("case", ["missing_channel", "missing_block", "empty", "malformed", "nan", "inf", "early_nan", "wrong_units", "wrong_sequence", "irregular", "missing_ocp", "missing_calibration_for_uncertainty"])
def test_invalid_inputs_and_unavailable_uncertainty(supplied, case):
    cfg, frames, evidence = supplied
    if case == "missing_channel":
        frames["A1"] = frames["A1"].drop(columns="current_a")
    elif case == "missing_block":
        del frames["B"]
    elif case == "empty":
        frames["A1"] = pd.DataFrame()
    elif case == "malformed":
        frames["A1"] = {"current_a": []}
    elif case in {"nan", "inf", "early_nan"}:
        frames["A1"].loc[0, "current_a"] = np.inf if case == "inf" else np.nan
    elif case == "wrong_units":
        frames["A1"].attrs["units"] = {"current_a": "mA"}
    elif case == "wrong_sequence":
        frames["A1"].loc[20, "point_index"] = 2
    elif case == "irregular":
        frames["A1"].loc[20, "time_s"] += .01
    elif case == "missing_ocp":
        evidence["ocp_opening"] = None
    else:
        result = result_for(supplied, context=EvaluationContext(rapp_standard_uncertainty_ohm=1))
        assert result.uncertainty["measurement"]["standard_uncertainty_ohm"] is None
        return
    result = result_for(supplied)
    data = assert_contract(result, "REJECT")
    assert data["reason_codes"] and data["primary_reason"] == data["reason_codes"][0]
    assert any(not check["passed"] for check in data["checks"])


def test_no_data_never_implies_zero_uncertainty_or_acceptance():
    result = evaluate_measurement({})
    assert_contract(result, "REJECT", "MISSING_DATA")
    assert result.uncertainty["measurement"]["heuristic_ohm"] is None
    assert diagnose({}, DiagnosticConfig()).uncertainty_ohm is None
    assert_contract(evaluate_diagnostics(None, AppConfig()), "REJECT", "MISSING_DATA")


@pytest.mark.parametrize("elapsed,expected", [(59.99, "WAIT_OR_REPEAT"), (60, "REJECT"), (60.01, "REJECT")])
def test_pending_observation_is_explicit_and_bounded(elapsed, expected):
    result = evaluate_measurement({}, context=EvaluationContext(acquisition_complete=False, observation_elapsed_s=elapsed))
    assert_contract(result, expected, "OBSERVATION_PENDING" if expected == "WAIT_OR_REPEAT" else "TIMEOUT")
    assert not result.retry_plan["automatic_retry"]
    assert not result.retry_plan["repeat_permitted"]


def test_pending_cannot_mask_fatal_or_invalid_samples(supplied):
    cfg, frames, _ = supplied
    context = EvaluationContext(acquisition_complete=False, fault_flags={"broken": True})
    assert_contract(evaluate_measurement({}, cfg, context=context), "REJECT", "INSTRUMENT_FAULT")
    frames["A1"].loc[0, "current_a"] = np.nan
    assert_contract(evaluate_measurement(frames, cfg, context=replace(context, fault_flags={})), "REJECT", "INVALID_INPUT")


def test_unexpected_exception_propagates(supplied):
    with patch("python.controller.process_recorded_frames", side_effect=RuntimeError("unexpected computation failure")):
        with pytest.raises(RuntimeError, match="unexpected"):
            result_for(supplied)


@pytest.mark.parametrize("constructor,kwargs", [
    (ModelConfig, {"ru_ohm": True}), (ModelConfig, {"rp_ohm": -1}), (ModelConfig, {"cdl_f": np.inf}),
    (AcquisitionConfig, {"max_reacquisition_attempts": True}), (AcquisitionConfig, {"point_count": "5"}),
    (AcquisitionConfig, {"enable_reverse_check": "false"}), (AcquisitionConfig, {"max_reacquisition_attempts": 2}),
    (DiagnosticConfig, {"min_samples_per_point": True}), (AppConfig, {"seed": True}),
    (AppConfig, {"model": None}), (TraceAdapterConfig, {"current_scale": True}),
])
def test_strict_configuration_types(constructor, kwargs):
    with pytest.raises(ValueError):
        constructor(**kwargs)


@pytest.mark.parametrize("context", [{"ru_ohm": -1}, {"ru_ohm": np.nan}, {"fault_flags": {"fault": "false"}}, {"voltage_limit_v": True}, {"retries_used": True}, {"simulation_status": "unknown"}, {"independent_uncertainties": 1}])
def test_structured_invalid_context(supplied, context):
    assert_contract(result_for(supplied, context=context), "REJECT", "INVALID_CONFIGURATION")


@pytest.mark.parametrize("factor,expected", [(.999, "ACCEPT"), (1, "REJECT"), (1.001, "REJECT")])
@pytest.mark.parametrize("channel,field", [("actual_voltage_v", "voltage_limit_v"), ("current_a", "current_limit_a")])
def test_configured_saturation_boundaries(supplied, factor, expected, channel, field):
    _, frames, _ = supplied
    peak = max(float(frame[channel].abs().max()) for frame in frames.values())
    result = result_for(supplied, context={field: peak / factor})
    assert_contract(result, expected, "INSTRUMENT_SATURATION" if expected == "REJECT" else None)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("metric,threshold,lower_is_better", [
    ("r_squared", "min_r_squared", False), ("signal_amplitude_v", "min_signal_amplitude_v", False),
    ("snr_db", "min_snr_db", False), ("noise_to_signal", "max_noise_to_signal", True),
    ("variation", "max_settling_variation", True), ("slope", "settling_relative_slope", True),
    ("window_difference", "settling_window_difference_fraction", True), ("observation", "settling_observation_s", False),
    ("uncertainty_ratio", "min_signal_to_uncertainty", False),
])
def test_policy_threshold_boundaries(supplied, metric, threshold, lower_is_better, offset):
    cfg, frames, evidence = supplied
    raw = diagnose(frames, cfg.diagnostics, **evidence)
    boundary = getattr(cfg.diagnostics, threshold)
    value = boundary * (1 + offset * .001)
    block = copy.deepcopy(raw.blocks["A1"])
    if metric in {"variation", "slope", "window_difference", "observation"}:
        point = copy.deepcopy(block.point_settling[0])
        if metric == "variation":
            point.variation = value
        elif metric == "slope":
            point.final_slope = value * point.scale_a
        elif metric == "window_difference":
            point.window_difference = value * point.scale_a
        else:
            point.dwell_s = value
        block.point_settling = (point,) + block.point_settling[1:]
    elif metric == "uncertainty_ratio":
        block.uncertainty_voltage_v = block.signal_span_v / value
    else:
        setattr(block, metric, value)
    raw.blocks["A1"] = block
    result = evaluate_diagnostics(raw, cfg)
    failed = offset > 0 if lower_is_better else offset < 0
    assert (result.decision != "ACCEPT") is failed, (metric, value, result.reason_codes)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("metric,threshold,code", [("drift_v_per_s", "ocp_drift_v_per_s", "OCP_DRIFT"), ("recovery_shift_v", "max_recovery_shift_v", "OCP_RECOVERY_FAILURE")])
def test_ocp_threshold_boundaries(supplied, offset, metric, threshold, code):
    cfg, frames, evidence = supplied
    raw = diagnose(frames, cfg.diagnostics, **evidence)
    raw.ocp[metric] = getattr(cfg.diagnostics, threshold) * (1 + offset*.001)
    result = evaluate_diagnostics(raw, cfg)
    assert (code in result.reason_codes) is (offset > 0)


def test_independent_resistance_equation_and_array_validation():
    # Constants calculated independently from Ohm's law, not estimator output.
    assert estimate_rapp([-.010, -.005, 0, .005, .010], [-1e-7, -.5e-7, 0, .5e-7, 1e-7]).value_ohm == pytest.approx(100000)
    for voltage, current in [([1, 2], [1]), ([True, 2], [1, 2]), ([1, np.inf], [1, 2])]:
        with pytest.raises(ValueError):
            estimate_rapp(voltage, current)


def test_json_text_and_recorded_integration_use_same_result(supplied, tmp_path):
    cfg, frames, evidence = supplied
    record = process_recorded_frames(cfg, "synthetic", frames, **evidence)
    data = record.to_dict()
    path, text_path = save_decision_record(data, tmp_path, "synthetic")
    decoded = json.loads(path.read_text())
    assert decoded["decision_result"] == record.decision_result.to_dict()
    assert decoded["decision_result"]["corrosion_estimate"] is None
    text = text_path.read_text()
    assert "Canonical Decision: ACCEPT" in text and "Primary Reason:" in text
    assert "observed=" in text and "expected=" in text and "Recommended Action:" in text


def test_reset_has_compatible_lifecycle_and_no_automatic_restart():
    def reset(controller):
        if controller.state.value == "A1":
            controller.reset()
    record = run_attempt(AppConfig(), "synthetic-reset", on_transition=reset)
    assert record.decision == "INCOMPLETE"
    assert_contract(record.decision_result, "WAIT_OR_REPEAT", "RESET")
    assert record.decision_result.result_status == "INTERRUPTED"
    assert not record.decision_result.retry_plan["repeat_permitted"]


def test_fatal_evidence_survives_reset_on_reject():
    def reset(controller):
        if controller.state.value == "REJECT":
            controller.reset()
    record = run_attempt(AppConfig(), "synthetic-open-failure", fault="opening_check_failure", on_transition=reset)
    assert record.decision == "INCOMPLETE"
    assert_contract(record.decision_result, "REJECT", "OPENING_CHECK_FAILURE")
    assert "RESET" in record.decision_result.reason_codes


def test_exhaustion_prevents_reference_replay_entirely():
    cfg = AppConfig(acquisition=AcquisitionConfig(max_reacquisition_attempts=0))
    record = run_attempt(cfg, "synthetic-no-budget", fault="recoverable_slow_fault")
    assert_contract(record.decision_result, "REJECT", "RETRY_LIMIT_EXHAUSTED")
    assert record.replay is None and record.reacquisition_count == 0
    assert "REFERENCE_REPLAY" not in record.state_history


def test_unresolved_reference_check_cannot_finish_with_wait():
    from python.reference_replay import ReplayResult
    with patch("python.controller.replay_schedule", return_value=ReplayResult(True, (), {"elapsed_time_s": .1})):
        record = run_attempt(AppConfig(), "synthetic-gain", fault="gain_fault")
    assert record.decision == "REJECT"
    assert_contract(record.decision_result, "REJECT", "UNRESOLVED_REFERENCE_DIAGNOSTICS")


def test_stable_excessive_uncertainty_rejects(supplied):
    cfg, frames, evidence = supplied
    cfg = replace(cfg, diagnostics=replace(cfg.diagnostics, uncertainty_fraction=.2))
    result = evaluate_measurement(frames, cfg, **evidence)
    assert_contract(result, "REJECT", "EXCESSIVE_UNCERTAINTY")
    assert result.primary_reason == "EXCESSIVE_UNCERTAINTY"


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("other,code", [("A2", "REPEATABILITY_FAILURE"), ("B", "SLOPE_INCONSISTENCY")])
def test_consistency_boundaries(supplied, offset, other, code):
    cfg, frames, evidence = supplied
    cfg = replace(cfg, diagnostics=replace(cfg.diagnostics, max_relative_consistency_error=.125))
    raw = diagnose(frames, cfg.diagnostics, **evidence)
    raw.blocks["A1"].resistance.value_ohm = 16000 * (1 - .125 * (1 + offset * .001))
    raw.blocks["A1"].slope_v_per_a = raw.blocks["A1"].resistance.value_ohm
    for name in ("A2", "B"):
        raw.blocks[name].resistance.value_ohm = raw.blocks["A1"].resistance.value_ohm
        raw.blocks[name].slope_v_per_a = raw.blocks["A1"].resistance.value_ohm
    raw.blocks[other].resistance.value_ohm = 16000
    raw.blocks[other].slope_v_per_a = 16000
    result = evaluate_diagnostics(raw, cfg)
    assert (code in result.reason_codes) is (offset > 0)


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("channel", ["current", "voltage"])
def test_reference_probe_boundaries(supplied, offset, channel):
    cfg, frames, evidence = supplied
    cfg = replace(cfg, diagnostics=replace(cfg.diagnostics, replay_relative_error=.125, min_signal_amplitude_v=1/1024))
    raw = diagnose(frames, cfg.diagnostics, **evidence)
    current = 1 + .125*(1 + offset*.001) if channel == "current" else 1
    voltage = (1/1024)*(1 + offset*.001) if channel == "voltage" else 0
    raw.measured_values["opening_check"] = {"passed": True, "source": "SYNTHETIC boundary probe", "expected_current_a": 1,
                                             "measured_current_a": current, "measured_voltage_v": voltage, "requested_voltage_v": 0}
    result = evaluate_diagnostics(raw, cfg)
    assert ("ACQUISITION_CHAIN_SUSPECT" in result.reason_codes) is (offset > 0)


@pytest.mark.parametrize("count,passed", [(199, False), (200, True), (201, True)])
def test_actual_observation_boundary(count, passed):
    result = estimate_settling(np.arange(count)/20, np.full(count, 1e-7), window_s=5, minimum_observation_s=10)
    assert result.settled is passed


@pytest.mark.parametrize("timeout,timed_out", [(14.99, True), (15, False), (15.01, False)])
def test_actual_timeout_boundary(timeout, timed_out):
    result = estimate_settling(np.arange(300)/20, np.full(300, 1e-7), timeout_s=timeout)
    assert result.timeout is timed_out


@pytest.mark.parametrize("count,missing", [(3, True), (4, False), (5, False)])
def test_sample_count_boundary(count, missing):
    v = np.repeat(np.linspace(-.005, .005, 5), count)
    frame = pd.DataFrame({"time_s": np.arange(len(v))*5, "actual_voltage_v": v,
                          "current_a": v/110000, "point_index": np.repeat(np.arange(5), count)})
    result = analyze_block("A1", frame, DiagnosticConfig())
    assert ("MISSING_DATA" in result.reason_codes) is missing


def test_declared_units_must_match_explicit_conversion():
    frame = pd.DataFrame({"time": [0, 100], "voltage": [5, 10], "current": [1, 2]})
    frame.attrs["units"] = {"time": "ms", "voltage": "mV", "current": "uA"}
    with pytest.raises(ValueError, match="conflicts"):
        normalize_trace(frame)
    result = normalize_trace(frame, TraceAdapterConfig(time_scale=.001, voltage_scale=.001, current_scale=1e-6))
    assert result.time_s.iloc[-1] == pytest.approx(.1)
    assert result.current_a.iloc[-1] == pytest.approx(2e-6)
    assert result.attrs["units"]["current_a"] == "A"


@pytest.mark.parametrize("metadata", ["requested_voltage_v", "gain", "sample_duration_s"])
def test_boolean_numeric_metadata_rejects(supplied, metadata):
    _, frames, _ = supplied
    frames["A1"][metadata] = True
    assert_contract(result_for(supplied), "REJECT", "INVALID_INPUT")


def test_overlap_and_wrong_excitation_are_explicit(supplied):
    _, frames, _ = supplied
    frames["B"]["time_s"] -= 80
    result = result_for(supplied)
    assert_contract(result, "REJECT", "INVALID_OPERATIONAL_CONTEXT")
    assert any("overlap" in message for message in result.measurements["context_errors"])


def test_wrong_excitation_is_not_accepted(supplied):
    _, frames, _ = supplied
    frames["B"]["requested_voltage_v"] *= 2
    result = result_for(supplied)
    assert_contract(result, "REJECT", "INVALID_OPERATIONAL_CONTEXT")
    assert any("excitation" in message for message in result.measurements["context_errors"])


@pytest.mark.parametrize("kind", ["empty", "malformed", "missing", "invalid_sidecar"])
def test_csv_boundary_returns_structured_failure(supplied, tmp_path, kind):
    from python.recorded_pipeline import evaluate_recorded_csv
    cfg, frames, _ = supplied
    path = tmp_path/"synthetic.csv"
    sidecar = None
    if kind == "empty":
        path.write_text("")
    elif kind == "malformed":
        path.write_text("time_s,actual_voltage_v,current_a\n0,broken,1\n1,2,3\n")
    elif kind == "invalid_sidecar":
        pd.concat(frames.values()).to_csv(path,index=False)
        sidecar = tmp_path/"synthetic.json"
        sidecar.write_text("[]")
    result = evaluate_recorded_csv(path,cfg,evidence_path=sidecar)
    assert_contract(result,"REJECT","INVALID_INPUT")
    assert result.uncertainty["measurement"]["heuristic_ohm"] is None


def test_csv_success_and_configured_saturation(supplied, tmp_path):
    from python.recorded_pipeline import evaluate_recorded_csv
    cfg, frames, evidence = supplied
    path = tmp_path/"synthetic.csv"
    pd.concat(frames.values()).to_csv(path,index=False)
    sidecar = tmp_path/"synthetic-evidence.json"
    evidence = {key: value.to_dict("records") if isinstance(value,pd.DataFrame) else value for key,value in evidence.items()}
    sidecar.write_text(json.dumps({"run_id":"synthetic-run","attempt_id":"synthetic-0",**evidence}))
    assert_contract(evaluate_recorded_csv(path,cfg,evidence_path=sidecar),"ACCEPT")
    assert_contract(evaluate_recorded_csv(path,cfg,evidence_path=sidecar,context={"voltage_limit_v":.01}),"REJECT","INSTRUMENT_SATURATION")


def test_cli_invalid_input_writes_rejection_and_exits_nonzero(tmp_path, capsys):
    from python.main import main
    code = main(["--recorded-csv",str(tmp_path/"missing.csv"),"--output-dir",str(tmp_path/"out")])
    assert code == 2
    data = json.loads((tmp_path/"out/generated/invalid_recorded_input.json").read_text())
    assert data["decision_result"]["decision"] == "REJECT"
    assert "Canonical Decision: REJECT" in capsys.readouterr().out


def test_unsupported_numeric_type_is_explicit(supplied):
    _, frames, _ = supplied
    frames["A1"]["current_a"] = frames["A1"].current_a.astype(complex) + 1j
    assert_contract(result_for(supplied), "REJECT", "INVALID_INPUT")


def test_malformed_evidence_still_serializes(supplied):
    _, _, evidence = supplied
    evidence["opening_check"] = object()
    result = result_for(supplied)
    data = assert_contract(result,"REJECT","MISSING_OPENING_CHECK")
    assert any(check["observed"] == {"status":"UNAVAILABLE","unsupported_type":"object"} for check in data["checks"])


def test_ocp_wrong_units_explains_rejection(supplied):
    _, _, evidence = supplied
    evidence["ocp_opening"].attrs["units"] = {"ocp_voltage_v":"mV"}
    result = result_for(supplied)
    assert_contract(result,"REJECT","MISSING_OCP_EVIDENCE")
    assert "expected canonical unit" in result.measurements["ocp_evidence"]["opening"]["error"]


def test_claimed_identity_must_match_trace(supplied):
    result = result_for(supplied,context=EvaluationContext(expected_identity={"run_id":"expected"},output_identity={"run_id":"expected"}))
    assert_contract(result,"REJECT","OUTPUT_MISMATCH")


def test_beyond_retry_budget_is_invalid(supplied):
    assert_contract(result_for(supplied,context=EvaluationContext(retries_used=2)),"REJECT","INVALID_CONFIGURATION")


def test_file_identity_and_hash_are_bound_to_loaded_bytes(supplied,tmp_path):
    import hashlib
    from python.recorded_pipeline import evaluate_recorded_csv
    cfg,frames,evidence=supplied
    path=tmp_path/"synthetic.csv"
    pd.concat(frames.values()).to_csv(path,index=False)
    sidecar=tmp_path/"synthetic.json"
    serial={key:value.to_dict("records") if isinstance(value,pd.DataFrame) else value for key,value in evidence.items()}
    sidecar.write_text(json.dumps({"run_id":"synthetic-run","attempt_id":"synthetic-0",**serial}))
    expected={"run_id":"synthetic-run","source_hash":hashlib.sha256(path.read_bytes()).hexdigest()}
    result=evaluate_recorded_csv(path,cfg,evidence_path=sidecar,context={"expected_identity":expected})
    assert_contract(result,"ACCEPT")
    assert result.measurements["source_type"]=="ltspice"
    assert_contract(evaluate_recorded_csv(path,cfg,evidence_path=sidecar,context={"expected_identity":{"source_hash":"wrong"}}),"REJECT","OUTPUT_MISMATCH")
    assert_contract(evaluate_recorded_csv(path,cfg,evidence_path=sidecar,context={"expected_identity":{"run_id":"claimed"},"output_identity":{"run_id":"claimed"}}),"REJECT","OUTPUT_MISMATCH")


def test_checks_have_deterministic_order(supplied):
    _,frames,_=supplied
    del frames["B"]
    result=result_for(supplied)
    names=[check.name for check in result.checks]
    assert names==sorted(names)
    assert result.to_dict()==result_for(supplied).to_dict()


def test_failed_sample_check_reports_observed_count(supplied):
    cfg,frames,evidence=supplied
    cfg=replace(cfg,diagnostics=replace(cfg.diagnostics,min_samples_per_point=301))
    result=evaluate_measurement(frames,cfg,**evidence)
    assert_contract(result,"REJECT","MISSING_DATA")
    check=next(check for check in result.checks if check.name=="A1.point_0.samples")
    assert not check.passed and check.observed==300 and check.expected=={"minimum":301}


@pytest.mark.parametrize("factor,expected",[(.999,"ACCEPT"),(1,"ACCEPT"),(1.001,"REJECT")])
def test_supplied_calibrated_uncertainty_threshold(supplied,factor,expected):
    result=result_for(supplied,context=EvaluationContext(rapp_standard_uncertainty_ohm=11000*factor,
                        calibration_source="SYNTHETIC standard uncertainty fixture"))
    assert_contract(result,expected,"EXCESSIVE_UNCERTAINTY" if expected=="REJECT" else None)
    check=next(check for check in result.checks if check.name=="calibrated_measurement_uncertainty")
    assert check.observed["standard_uncertainty_ohm"]==11000*factor


@pytest.mark.parametrize("context",[False,0,""])
def test_explicit_false_or_empty_context_is_not_an_omitted_default(supplied,context):
    assert_contract(result_for(supplied,context=context),"REJECT","INVALID_CONFIGURATION")


def test_invalid_empty_reverse_group_type_rejects(supplied):
    cfg,frames,evidence=supplied
    assert_contract(evaluate_measurement(frames,cfg,**evidence,reverse_frames=[]),"REJECT","INVALID_INPUT")


def test_invalid_adapter_config_type_rejects(supplied):
    _,frames,_=supplied
    with pytest.raises(ValueError,match="TraceAdapterConfig"):
        normalize_trace(frames["A1"],False)
