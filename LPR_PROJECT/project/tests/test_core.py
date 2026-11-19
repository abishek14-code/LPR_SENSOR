import json

import numpy as np

from models.analytical_checks import reference_case
from python.config import AppConfig
from python.controller import State, run_attempt
from python.lpr_analysis import correct_rp


def test_analytical_reference_case():
    reference = reference_case()
    assert np.isclose(reference.tau_s, 0.9090909, rtol=1e-6)
    assert np.isclose(reference.initial_current_a, 1e-6)
    assert np.isclose(reference.steady_current_a, 0.090909e-6, rtol=1e-4)


def test_healthy_path_does_not_replay():
    result = run_attempt(AppConfig(), "healthy")
    assert result.decision == "ACCEPT"
    assert "REFERENCE_REPLAY" not in result.state_history
    assert result.final_state == State.RELEASE.value


def test_slow_fault_replays_and_reacquires_once():
    result = run_attempt(AppConfig(), "slow_settling_fault", fault="slow_settling_fault")
    assert "REFERENCE_REPLAY" in result.state_history
    assert "REACQUIRE" in result.state_history
    assert result.reacquisition_count == 1


def test_gain_fault_replay_rejects():
    result = run_attempt(AppConfig(), "gain_fault", fault="gain_fault")
    assert "REFERENCE_REPLAY" in result.state_history
    assert result.decision == "REJECT"


def test_rp_correction_requires_explicit_simplified_model():
    rapp = reference_case().dc_resistance_ohm
    from python.lpr_analysis import ResistanceResult
    valid = correct_rp(ResistanceResult(rapp, "apparent"), 10_000.0, True, True, settled=True, measurement_valid=True)
    invalid = correct_rp(ResistanceResult(rapp, "apparent"), 10_000.0, True, False)
    assert valid.estimation_method == "Ru_corrected"
    assert np.isclose(valid.value_ohm, 100_000.0)
    assert invalid.value_ohm is None
