from dataclasses import replace
from unittest.mock import patch

import numpy as np
import pytest

from python.config import AppConfig
from python.controller import run_attempt
from python.stateful_model import StatefulPlant


@pytest.mark.parametrize("limit", [60.0, 3.0, 3.017])
def test_timeout_stops_first_point_without_remaining_blocks(limit):
    config = AppConfig()
    config = replace(config, acquisition=replace(config.acquisition, point_timeout_s=limit))
    calls = []
    acquire = StatefulPlant.acquire_block
    def observed(self, *args, **kwargs):
        frame = acquire(self, *args, **kwargs)
        calls.append(frame)
        return frame
    with patch.object(StatefulPlant, "acquire_block", observed):
        record = run_attempt(config, "timeout", fault="timeout_fault")
    assert record.decision == "REJECT" and record.timed_out
    assert "TIMEOUT" in record.diagnostics.reason_codes
    assert len(calls) == 1
    assert calls[0].point_index.nunique() == 1
    assert calls[0].point_elapsed_time_s.iloc[-1] == pytest.approx(limit)
    assert record.acquisition_time_s == pytest.approx(limit)
    assert record.to_dict()["timed_out"] is True
    assert "B" not in record.state_history


def test_configured_timeout_applies_without_named_fault():
    config = AppConfig()
    record = run_attempt(replace(config, acquisition=replace(config.acquisition, point_timeout_s=5)), "healthy")
    assert record.timed_out and record.acquisition_time_s == pytest.approx(5)


def test_open_circuit_evolves_interface_with_rp_cdl():
    plant = StatefulPlant(AppConfig().model, eta_v=.005)
    frame = plant.measure_ocp(10)
    expected = .005 * np.exp(-10 / (100000 * 100e-6))
    assert plant.eta_v == pytest.approx(expected)
    assert frame.ocp_voltage_v.iloc[-1] == pytest.approx(expected)
    assert frame.time_s.iloc[0] == pytest.approx(.05)
    assert frame.time_s.iloc[-1] == pytest.approx(10)


def test_clock_and_stored_recovery_windows_are_continuous():
    config = AppConfig()
    plant = StatefulPlant(config.model)
    plant.measure_ocp(1)
    plant.acquire_block("A1", .005, config.acquisition)
    before = plant.eta_v
    recovery = plant.recover(config.acquisition)
    assert plant.eta_v == pytest.approx(before * np.exp(-1 / plant.tau_s))
    plant.measure_ocp(1)
    times = np.concatenate([frame.time_s for frame in plant.windows])
    assert np.all(np.diff(times) > 0)
    assert any(frame is recovery for frame in plant.windows)
    assert plant.driven_time_s == pytest.approx(60)
    assert plant.measurement_window_time_s == pytest.approx(75)
    assert plant.elapsed_time_s == pytest.approx(78)


def test_attempt_timing_decomposes_elapsed_and_replay():
    record = run_attempt(AppConfig(), "slow", fault="slow_settling_fault")
    t = record.timing
    assert t["acquisition_elapsed_time_s"] == pytest.approx(t["measurement_window_time_s"] + t["recovery_time_s"] + t["ocp_time_s"])
    assert t["total_elapsed_time_s"] == pytest.approx(t["acquisition_elapsed_time_s"] + t["replay_time_s"])
    assert t["driven_time_s"] < t["measurement_window_time_s"]


def test_configured_point_count_and_order_are_executed():
    acquisition = replace(AppConfig().acquisition, point_count=3, point_order="reverse")
    assert acquisition.points(.005) == (.005, 0, -.005)
    assert acquisition.points(.005, reverse=True) == (-.005, 0, .005)
