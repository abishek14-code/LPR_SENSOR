from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.analytical_checks import validate_reference_case
from python.config import AppConfig
from python.controller import State
from python.experiments import run_seeded_experiments
from python.elmer_adapter import elmer_evidence_boundary, load_elmer_summary
from python.recorded_pipeline import process_recorded_csv
from python.simulator_adapter import TraceAdapterConfig, load_simulator_csv, normalize_trace
from python.stateful_model import StatefulPlant


def _recorded_trace():
    config = AppConfig()
    plant = StatefulPlant(config.model, seed=config.seed)
    blocks = []
    for block_id, amplitude in (("A1", config.acquisition.a1_amplitude_v), ("B", config.acquisition.b_amplitude_v), ("A2", config.acquisition.a1_amplitude_v)):
        frame = plant.acquire_block(block_id, amplitude, config.acquisition)
        blocks.append(frame)
        if block_id != "A2":
            plant.recover(config.acquisition)
    return pd.concat(blocks, ignore_index=True).rename(columns={"actual_voltage_v": "voltage_v"})


def test_simulator_adapter_validates_and_normalizes():
    source = pd.DataFrame({"time_s": [0.0, 0.1], "voltage_v": [5.0, 10.0], "current_a": [1.0, 2.0]})
    result = normalize_trace(source, TraceAdapterConfig(voltage_scale=1e-3, current_scale=1e-6))
    assert np.isclose(result["actual_voltage_v"].iloc[1], 0.01)
    assert np.isclose(result["current_a"].iloc[1], 2e-6)

    with pytest.raises(ValueError, match="missing required"):
        normalize_trace(pd.DataFrame({"time_s": [0.0, 0.1], "voltage_v": [1.0, 2.0]}))
    with pytest.raises(ValueError, match="strictly increasing"):
        normalize_trace(pd.DataFrame({"time_s": [0.1, 0.0], "voltage_v": [1.0, 2.0], "current_a": [1.0, 2.0]}))
    with pytest.raises(ValueError, match="NaN or Inf"):
        normalize_trace(pd.DataFrame({"time_s": [0.0, 0.1], "voltage_v": [1.0, np.inf], "current_a": [1.0, 2.0]}))
    with pytest.raises(ValueError, match="empty"):
        normalize_trace(pd.DataFrame())


def test_recorded_csv_end_to_end(tmp_path):
    source = _recorded_trace()
    path = tmp_path / "recorded.csv"
    source.to_csv(path, index=False)
    normalized = load_simulator_csv(path, TraceAdapterConfig(source_type="multisim"))
    assert normalized.attrs["source_type"] == "multisim"
    result = process_recorded_csv(path, AppConfig(), TraceAdapterConfig(source_type="multisim"))
    assert result.source_type == "multisim"
    # Measurement-only CSVs have no evidence authorizing release.
    assert result.final_state == State.REJECT.value
    assert "MISSING_CLOSING_CHECK" in result.diagnostics.reason_codes
    assert "MISSING_OCP_EVIDENCE" in result.diagnostics.reason_codes
    assert State.CLOSING_CHECK.value not in result.state_history
    assert State.REFERENCE_REPLAY.value not in result.state_history


def test_analytical_validation_is_explicit():
    validation = validate_reference_case()
    assert validation["status"] == "PASS"
    assert all(validation["checks"].values())


def test_elmer_boundary_remains_spatial(tmp_path):
    path = tmp_path / "elmer.csv"
    pd.DataFrame({"node": [1, 2], "potential_v": [0.1, 0.2]}).to_csv(path, index=False)
    summary = load_elmer_summary(path)
    assert summary.attrs["source_type"] == "elmer"
    assert summary.attrs["lpr_time_series"] is False
    assert elmer_evidence_boundary()["converted_to_lpr_time_series"] is False


def test_seeded_experiment_runner_writes_summary(tmp_path):
    records, summary = run_seeded_experiments(AppConfig(), trials=2, output_dir=tmp_path)
    assert len(records) == 2 * 14
    assert not summary.empty
    assert (tmp_path / "tables" / "seeded_trial_metrics.csv").is_file()
    assert (tmp_path / "tables" / "trial_records.jsonl").is_file()
    assert all(item["seed"] in (42, 43) for item in records)
