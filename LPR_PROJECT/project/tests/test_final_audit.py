from dataclasses import replace

import numpy as np
import pytest

from python.config import AppConfig, DiagnosticConfig, ModelConfig
from python.controller import Controller, State, run_attempt
from python.evidence import settled_plot_fit, write_analytical_table, generate_evidence
from python.fault_injection import inject_gain_fault, inject_mixed_faults
from python.lpr_analysis import analyze_block, estimate_settling
from python.stateful_model import StatefulPlant


def test_fault_clone_does_not_alias_acquisition_history():
    cfg = AppConfig()
    original = StatefulPlant(cfg.model)
    original.acquire_block("A1", .005, cfg.acquisition)
    clone = inject_gain_fault(original)
    clone.recover(cfg.acquisition)
    clone.windows[0].loc[0, "current_a"] = 999
    assert len(original.schedule) == len(original.windows) == 1
    assert original.windows[0].current_a.iloc[0] != 999
    clone.reset()
    assert clone.schedule == [] and clone.windows == []
    assert len(original.windows) == 1


def test_reset_during_replay_marks_reference_incomplete():
    def reset(controller, plant):
        if controller.state == State.REFERENCE_REPLAY and plant.reference_wait_time_s >= .1:
            controller.reset()
    record = run_attempt(AppConfig(), "reset-replay", fault="recoverable_slow_fault", on_sample=reset)
    assert record.decision == "INCOMPLETE" and "RELEASE" not in record.state_history
    assert record.replay.metrics["interrupted"] and not record.replay.passed
    assert record.timing["replay_time_s"] == pytest.approx(.1)


def test_finished_history_does_not_alias_controller():
    controller = Controller()
    record = run_attempt(AppConfig(), "healthy", controller=controller)
    history = list(record.state_history)
    controller.reset()
    assert record.state_history == history


@pytest.mark.parametrize("changes", [{"cdl_scale": 0}, {"noise_std_a": -1}, {"gain": np.inf}])
def test_invalid_fault_parameters_rejected(changes):
    with pytest.raises(ValueError):
        inject_mixed_faults(StatefulPlant(ModelConfig()), **changes)


def test_overlapping_settling_windows_cannot_pass():
    result = estimate_settling(np.arange(6.), np.ones(6), window_s=5)
    assert not result.settled
    with pytest.raises(ValueError):
        DiagnosticConfig(settling_observation_s=5, settling_window_s=5)


def test_plot_estimator_matches_analysis_and_keeps_point_evidence():
    cfg = AppConfig()
    data = StatefulPlant(cfg.model).acquire_block("A1", .005, cfg.acquisition)
    metrics = analyze_block("A1", data, cfg.diagnostics)
    assert settled_plot_fit(data)[2]["slope"] == pytest.approx(metrics.resistance.value_ohm)
    assert len(metrics.to_dict()["point_settling"]) == 5
    assert metrics.resistance.value_ohm == pytest.approx(110000, abs=2)
    data.loc[0, "point_index"] = float("nan")
    with pytest.raises(ValueError, match="point indices"):
        analyze_block("A1", data, cfg.diagnostics)


@pytest.mark.parametrize("model", [ModelConfig(), ModelConfig(ru_ohm=300, rp_ohm=2000, cdl_f=.003)])
def test_independent_configured_analytical_comparison(tmp_path, model):
    table = write_analytical_table(tmp_path, replace(AppConfig(), model=model))
    assert len(table) == 9 and table.passed.all()


def test_all_figures_and_manifest_generated(tmp_path):
    import json
    figures = generate_evidence(AppConfig(), output_dir=tmp_path)
    assert len(figures) == 5
    data = json.loads((tmp_path / "tables" / "figure_manifest.json").read_text())
    assert data["analytical_passed"]
    assert data["figure_3_decision"] == "ACCEPT"
    assert data["figure_4_decision"] == "REJECT"
