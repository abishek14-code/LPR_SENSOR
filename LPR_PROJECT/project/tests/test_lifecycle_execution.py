from unittest.mock import patch

import pytest

from python.config import AppConfig
from python.controller import Controller, State, run_attempt
from python.experiments import _reset_record
from python.stateful_model import StatefulPlant


def test_illegal_transition_and_release_without_checks_fail():
    with pytest.raises(ValueError, match="illegal transition"):
        Controller().transition(State.B)
    with pytest.raises(ValueError, match="illegal transition"):
        Controller(state=State.CLOSING_CHECK).transition(State.RELEASE)


@pytest.mark.parametrize("state", [State.OPENING_CHECK, State.OCP, State.A1, State.B, State.A2, State.RECOVERY, State.A1_REVERSE, State.B_REVERSE, State.A2_REVERSE, State.DIAGNOSE, State.CLOSING_CHECK, State.RELEASE])
def test_reset_at_execution_boundaries_never_releases(state):
    def interrupt(controller):
        if controller.state == state:
            controller.reset()
    record = run_attempt(AppConfig(), "reset", on_transition=interrupt)
    assert record.decision == "INCOMPLETE"
    assert record.final_state == "IDLE"
    assert "RELEASE" not in record.state_history
    assert "RESET" in record.to_dict()["diagnostic_codes"]


@pytest.mark.parametrize("state", [State.REFERENCE_REPLAY, State.REACQUIRE])
def test_reset_interrupts_fault_execution(state):
    record = run_attempt(AppConfig(), "reset", fault="slow_settling_fault",
                         on_transition=lambda c: c.reset() if c.state == state else None)
    assert record.decision == "INCOMPLETE"
    assert "RELEASE" not in record.state_history


def test_reset_occurs_during_actual_sampling():
    record = _reset_record(AppConfig(), "reset", 53)
    assert record.decision == "INCOMPLETE"
    assert record.acquisition_time_s == pytest.approx(.1)
    assert record.attempt_history[0]["windows"][-1]["samples"] == 2
    assert "RELEASE" not in record.state_history
    assert record.seed == 53


def test_failed_checks_are_evaluated_not_selected_from_run_name():
    original = StatefulPlant.path_check
    def fail_closing(self, phase):
        check = original(self, phase)
        if phase == "closing":
            check["passed"] = False
        return check
    with patch.object(StatefulPlant, "path_check", fail_closing):
        record = run_attempt(AppConfig(), "healthy")
    assert record.decision == "REJECT"
    assert "CLOSING_CHECK_FAILURE" in record.to_dict()["diagnostic_codes"]
    assert record.state_history[-2:] == ["CLOSING_CHECK", "REJECT"]


def test_missing_closing_check_is_not_assumed():
    original = StatefulPlant.path_check
    with patch.object(StatefulPlant, "path_check", lambda self, phase: original(self, phase) if phase == "opening" else None):
        record = run_attempt(AppConfig(), "healthy")
    assert record.decision == "REJECT"
    assert "MISSING_CLOSING_CHECK" in record.to_dict()["diagnostic_codes"]


def test_opening_failure_prevents_sample_acquisition():
    record = run_attempt(AppConfig(), "opening", fault="opening_check_failure")
    assert record.decision == "REJECT"
    assert "OPENING_CHECK_FAILURE" in record.to_dict()["diagnostic_codes"]
    assert record.acquisition_time_s == 0


def test_unknown_fault_is_not_a_healthy_case():
    with pytest.raises(ValueError, match="unknown fault"):
        run_attempt(AppConfig(), "typo", fault="typo_fault")
