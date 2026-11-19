from unittest.mock import patch

from python.config import AppConfig
from python.controller import run_attempt, _plant_for_fault
from python.fault_injection import inject_gain_fault
from python.stateful_model import StatefulPlant


def test_reacquisition_uses_one_original_plant_and_fault():
    with patch("python.controller._plant_for_fault", wraps=_plant_for_fault) as create:
        result = run_attempt(AppConfig(), "slow", fault="slow_settling_fault")
    assert create.call_count == 1
    assert result.reacquisition_count == 1
    first, second = result.attempt_history
    assert first["sample_id"] == second["sample_id"]
    assert first["attempt_id"] != second["attempt_id"]
    assert first["end"] == second["start"]
    assert first["end"]["eta_v"] != 0
    assert first["start"]["fault_parameters"] == second["end"]["fault_parameters"]
    assert second["fault_metadata"]["fault_type"] == "slow_settling_fault"


def test_noise_stream_continues_between_attempts():
    # A small noisy acquisition chain passes reference replay while the slow
    # sample requires retry. Excessive chain noise now correctly fails replay.
    def noisy_slow(config, fault, seed):
        plant = _plant_for_fault(config, "slow_settling_fault", seed)
        plant.correlated_noise_a = 1e-11
        return plant
    with patch("python.controller._plant_for_fault", side_effect=noisy_slow):
        result = run_attempt(AppConfig(), "noise", fault="slow_settling_fault")
    first, second = result.attempt_history
    assert first["start"]["rng_state"] != first["end"]["rng_state"]
    assert first["end"] == second["start"]
    assert first["end"]["correlated_noise_state"] != 0


def test_persistent_fault_never_disappears():
    result = run_attempt(AppConfig(), "persistent", fault="persistent_slow_fault")
    assert result.decision == "REJECT"
    assert result.reacquisition_count == 1
    assert all(a["start"]["fault_parameters"]["cdl_scale"] == 20 for a in result.attempt_history)


def test_injecting_fault_on_live_plant_does_not_reset_noise():
    plant = StatefulPlant(AppConfig().model, noise_std_a=1e-7, correlated_noise_a=1e-7)
    plant._step(.005, .1)
    clone = inject_gain_fault(plant, gain=1)
    assert clone._correlated_noise_state == plant._correlated_noise_state
    assert clone._rng.bit_generator.state == plant._rng.bit_generator.state
    assert clone._rng is not plant._rng
    assert clone._step(.005, .1) == plant._step(.005, .1)
