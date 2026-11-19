from dataclasses import replace
from unittest.mock import patch

import pytest

from python.config import AppConfig
from python.controller import run_attempt, _plant_for_fault
from python.reference_replay import replay_schedule
from python.stateful_model import StatefulPlant


@pytest.mark.parametrize("reverse", [True, False])
def test_replay_consumes_actual_schedule_and_chain(reverse):
    c = AppConfig()
    a = replace(c.acquisition, dwell_s=21, reacquisition_dwell_s=24, point_count=3,
                point_order="reverse", sample_rate_hz=10, enable_reverse_check=reverse)
    r = run_attempt(replace(c, acquisition=a), "gain", fault="gain_fault", attempt_index=1)
    events = r.attempt_history[0]["executed_schedule"]
    assert r.replay.metrics["schedule"]["events"] == events
    acquisitions = [e for e in events if e["kind"] == "acquire"]
    assert len(acquisitions) == (6 if reverse else 3)
    assert all(e["dwell_s"] == 24 and e["chain"]["gain"] == 1.2 for e in acquisitions)
    assert all(e["sample_rate_hz"] == 10 and e["point_count"] == 3 for e in acquisitions)
    assert acquisitions[0]["points_v"] == [.005, 0, -.005]
    assert r.replay.metrics["replayed_events"] == events
    assert not r.replay.passed


def test_mixed_fault_rejects_without_replay_and_explicit_replay_keeps_real_chain():
    c = AppConfig()
    r = run_attempt(c, "mixed", fault="mixed_fault")
    # Nonrecoverable noise failure blocks controller replay/reacquisition.
    assert r.decision == "REJECT" and r.replay is None
    assert not r.decision_result.retry_plan["repeat_permitted"]
    # Independently exercise the replay utility on the actual faulty schedule.
    replay = replay_schedule(c.reference_model, c.acquisition, c.diagnostics, c.seed,
                             schedule=r.attempt_history[0]["executed_schedule"])
    assert not replay.passed
    chain = next(e["chain"] for e in replay.metrics["schedule"]["events"] if e["kind"] == "acquire")
    assert chain["gain"] == 1.2 and chain["noise_std_a"] == 1e-7 and chain["reverse_gain"] == 1.3


def test_reference_tolerance_is_used_and_reproducible():
    c = AppConfig()
    args = (c.reference_model, c.acquisition, c.diagnostics, c.seed)
    first = replay_schedule(*args, chain={"gain": 1.1})
    assert not first.passed
    assert first.to_dict() == replay_schedule(*args, chain={"gain": 1.1}).to_dict()
    loose = replay_schedule(c.reference_model, c.acquisition, replace(c.diagnostics, replay_relative_error=.2), c.seed, chain={"gain": 1.1})
    assert loose.passed


def test_sample_truth_is_not_a_decision_input():
    # Keep the actual measured plant fixed, vary the nominal sample model in
    # configuration. No controller comparison with that hidden truth is allowed.
    c = AppConfig()
    def fixed(config, fault, seed):
        return StatefulPlant(c.model, seed=seed)
    with patch("python.controller._plant_for_fault", side_effect=fixed):
        first = run_attempt(c, "fixed")
        second = run_attempt(replace(c, model=replace(c.model, rp_ohm=900000)), "fixed")
    assert first.decision == second.decision == "ACCEPT"
    assert first.diagnostics.to_dict() == second.diagnostics.to_dict()
    assert first.evaluation_truth["Rapp_ohm"] == 110000


def test_reference_wait_evolves_same_sample_without_rng_reset():
    r = run_attempt(AppConfig(), "slow", fault="slow_settling_fault")
    first, second = r.attempt_history
    wait = [w for w in first["windows"] if w["state"] == "REFERENCE_WAIT"]
    assert wait and wait[0]["duration_s"] == pytest.approx(r.timing["replay_time_s"])
    assert first["end"] == second["start"]
    assert r.timing["reference_wait_time_s"] == pytest.approx(r.timing["replay_time_s"])
