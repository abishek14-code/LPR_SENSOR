from dataclasses import replace

import numpy as np
import pandas as pd

from python.config import AppConfig
from python.controller import Controller, State, run_attempt
from python.fault_injection import apply_fault, fault_spec, inject_gain_fault
from python.lpr_analysis import estimate_settling
from python.reference_replay import replay_schedule
from python.stateful_model import StatefulPlant


def test_settling_pass_and_failure_are_explicit():
    time = np.arange(0.0, 15.0, 0.05)
    stable = estimate_settling(time, np.full_like(time, 1e-7), window_s=5.0, minimum_observation_s=10.0)
    unstable = estimate_settling(time, time * 1e-7, window_s=5.0, minimum_observation_s=10.0)
    assert stable.settled
    assert stable.window_means and stable.window_difference is not None
    assert not unstable.settled


def test_uncertainty_and_signal_threshold_are_diagnostic_gates():
    result = run_attempt(AppConfig(), "excessive_noise", fault="excessive_noise")
    assert result.decision == "REJECT"
    assert "EXCESSIVE_UNCERTAINTY" in result.diagnostics.reason_codes


def test_ocp_drift_and_recovery_checks():
    drift = run_attempt(AppConfig(), "ocp_drift", fault="ocp_drift")
    recovery = run_attempt(AppConfig(), "recovery_shift", fault="recovery_shift")
    assert "OCP_DRIFT" in drift.diagnostics.reason_codes
    assert "OCP_RECOVERY_FAILURE" in recovery.diagnostics.reason_codes


def test_forward_reverse_and_a1_a2_checks():
    mismatch = run_attempt(AppConfig(), "forward_reverse_mismatch", fault="forward_reverse_mismatch")
    assert "SLOPE_INCONSISTENCY" in mismatch.diagnostics.reason_codes

    config = AppConfig()
    plant = StatefulPlant(config.model, seed=config.seed)
    a1 = plant.acquire_block("A1", config.acquisition.a1_amplitude_v, config.acquisition)
    plant.recover(config.acquisition)
    b = plant.acquire_block("B", config.acquisition.b_amplitude_v, config.acquisition)
    plant.recover(config.acquisition)
    a2 = plant.acquire_block("A2", config.acquisition.a1_amplitude_v, config.acquisition)
    a2 = a2.copy()
    a2["current_a"] *= 1.5
    from python.diagnostics import diagnose
    result = diagnose({"A1": a1, "B": b, "A2": a2}, config.diagnostics)
    assert "REPEATABILITY_FAILURE" in result.reason_codes


def test_fault_injection_is_deterministic_and_non_mutating():
    config = AppConfig()
    plant = StatefulPlant(config.model, seed=7)
    source = plant.acquire_block("A1", config.acquisition.a1_amplitude_v, config.acquisition)
    before = source.copy(deep=True)
    spec = fault_spec("gain_change", seed=99, gain=1.2)
    first = apply_fault(source, spec)
    second = apply_fault(source, spec)
    assert source.equals(before)
    assert first.equals(second)
    assert first.attrs["fault_metadata"]["seed"] == 99


def test_reference_replay_is_stateful_and_replay_failure_rejects():
    config = AppConfig()
    first = replay_schedule(config.model, config.acquisition, config.diagnostics, config.seed, expected_rapp_ohm=110000.0)
    second = replay_schedule(config.model, config.acquisition, config.diagnostics, config.seed, expected_rapp_ohm=110000.0)
    assert first.passed
    assert first.to_dict() == second.to_dict()
    assert first.metrics["schedule"]["dwell_s"] == config.acquisition.dwell_s

    failed = run_attempt(config, "replay_failure", fault="replay_failure")
    assert failed.final_state == State.REJECT.value
    assert failed.replay is not None and not failed.replay.passed


def test_bounded_reacquisition_and_second_failure():
    recoverable = run_attempt(AppConfig(), "slow_settling_fault", fault="slow_settling_fault")
    assert recoverable.reacquisition_count == 1
    assert recoverable.attempt_ids == ["slow_settling_fault-0", "slow_settling_fault-1"]

    persistent = run_attempt(AppConfig(), "persistent_slow_fault", fault="persistent_slow_fault")
    assert persistent.final_state == State.REJECT.value
    assert persistent.reacquisition_count == 1
    assert persistent.state_history.count(State.REACQUIRE.value) == 1


def test_reset_from_every_state_marks_attempt_incomplete():
    for state in State:
        controller = Controller(state=state, attempt_complete=True)
        controller.reset()
        assert controller.state == State.IDLE
        assert controller.attempt_incomplete
        assert not controller.attempt_complete


def test_timeout_and_closing_check_cannot_release():
    timed_out = run_attempt(AppConfig(), "timeout_fault", fault="timeout_fault")
    assert timed_out.final_state == State.REJECT.value
    assert timed_out.timed_out
    assert "TIMEOUT" in timed_out.diagnostics.reason_codes

    closing_failure = run_attempt(AppConfig(), "closing_check_failure", fault="closing_check_failure")
    assert closing_failure.final_state == State.REJECT.value
    assert State.RELEASE.value not in closing_failure.state_history


def test_healthy_path_still_avoids_replay():
    result = run_attempt(AppConfig(), "healthy_day2")
    assert result.decision == "ACCEPT"
    assert State.REFERENCE_REPLAY.value not in result.state_history
