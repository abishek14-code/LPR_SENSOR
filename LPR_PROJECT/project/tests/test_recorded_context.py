import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from python.config import AppConfig
from python.recorded_pipeline import process_recorded_csv
from python.simulator_adapter import normalize_trace, split_measurement_windows
from python.stateful_model import StatefulPlant


@pytest.fixture
def recording(tmp_path):
    cfg = AppConfig()
    plant = StatefulPlant(cfg.model)
    opening_check = plant.path_check("opening")
    opening = plant.measure_ocp(60)
    frames = []
    for name, amplitude in (("A1", .005), ("B", .01), ("A2", .005)):
        for reverse in (False, True):
            frames.append(plant.acquire_block(name, amplitude, cfg.acquisition, reverse=reverse))
            plant.recover(cfg.acquisition)
    closing = plant.measure_ocp(60)
    frame = pd.concat(frames, ignore_index=True).assign(run_id="001", trial_id="trial-007", attempt_id="attempt-01", seed=991, gain=1.0, adc_rate_sps=20.0, adc_range_v=0.1)
    path = tmp_path / "recorded.csv"
    frame.to_csv(path, index=False)
    evidence = dict(run_id="001", attempt_id="attempt-01", opening_check=opening_check,
                    closing_check=plant.path_check("closing"), ocp_opening=opening.to_dict("records"), ocp_closing=closing.to_dict("records"))
    sidecar = tmp_path / "evidence.json"
    sidecar.write_text(json.dumps(evidence, allow_nan=False))
    return cfg, frame, path, sidecar


def test_complete_recorded_evidence_preserves_identity_and_releases(recording):
    cfg, frame, path, sidecar = recording
    record = process_recorded_csv(path, cfg, evidence_path=sidecar)
    assert record.decision == "ACCEPT", record.to_dict()["diagnostic_details"]
    assert (record.run_id, record.trial_id, record.attempt_id, record.seed) == ("001", "trial-007", "attempt-01", 991)
    assert record.reacquisition_count == 0 and record.replay is None
    assert record.acquisition_time_s is None
    context = record.source_metadata["operational_context"]["A1"][0]
    assert context["adc_rate_sps"] == 20 and context["gain"] == 1
    assert record.to_dict()["Rp_corrected"] is None


@pytest.mark.parametrize("column", ["run_id", "trial_id", "attempt_id", "seed"])
def test_mixed_boundaries_rejected(recording, column):
    _, frame, _, _ = recording
    frame.loc[0, column] = 992 if column == "seed" else "different"
    with pytest.raises(ValueError, match="boundary"):
        normalize_trace(frame)


@pytest.mark.parametrize("column", ["gain", "adc_range_v", "adc_rate_sps", "commanded_voltage_v", "seed", "point_index"])
def test_nonfinite_operational_metadata_rejected(recording, column):
    _, frame, _, _ = recording
    frame[column] = np.inf
    with pytest.raises(ValueError, match="nonfinite"):
        normalize_trace(frame)


def test_alias_ambiguity_rejected(recording):
    _, frame, _, _ = recording
    frame["voltage_v"] = frame.actual_voltage_v
    with pytest.raises(ValueError, match="ambiguous duplicate"):
        normalize_trace(frame)


def test_disabled_drive_cannot_release(recording):
    cfg, frame, path, sidecar = recording
    frame["drive_enabled"] = False
    frame.to_csv(path, index=False)
    record = process_recorded_csv(path, cfg, evidence_path=sidecar)
    assert record.decision == "REJECT"
    assert "INVALID_OPERATIONAL_CONTEXT" in record.diagnostics.reason_codes


def test_wrong_evidence_identity_rejected(recording):
    cfg, _, path, sidecar = recording
    data = json.loads(sidecar.read_text())
    data["attempt_id"] = "other"
    sidecar.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="match CSV attempt_id"):
        process_recorded_csv(path, cfg, evidence_path=sidecar)


def test_disjoint_duplicate_block_rejected(recording):
    _, frame, _, _ = recording
    data = normalize_trace(frame)
    data = pd.concat([data, data.iloc[:10]], ignore_index=True)
    with pytest.raises(ValueError, match="disjoint"):
        split_measurement_windows(data)


def test_false_reverse_metadata_does_not_release(recording):
    cfg, frame, path, sidecar = recording
    frame.loc[frame.reverse, "state"] = "A1"
    frame.to_csv(path, index=False)
    record = process_recorded_csv(path, cfg, evidence_path=sidecar)
    assert record.decision == "REJECT"


@pytest.mark.parametrize("problem", ["short_ocp", "wrong_ocp_time", "closing_gain", "missing_point", "changing_gain"])
def test_final_release_context_guards(recording, problem):
    cfg, frame, path, sidecar = recording
    evidence = json.loads(sidecar.read_text())
    if problem == "short_ocp":
        evidence["ocp_closing"] = evidence["ocp_closing"][:2]
    elif problem == "wrong_ocp_time":
        evidence["ocp_closing"] = evidence["ocp_opening"]
    elif problem == "closing_gain":
        evidence["closing_check"]["measured_current_a"] *= 1.2
    elif problem == "missing_point":
        frame = frame.loc[frame.point_index != 2]
    else:
        frame.loc[1, "gain"] = 2.0
    frame.to_csv(path, index=False)
    sidecar.write_text(json.dumps(evidence))
    assert process_recorded_csv(path, cfg, evidence_path=sidecar).decision == "REJECT"


def test_forward_only_recording_respects_explicit_reverse_policy(recording):
    cfg, frame, path, sidecar = recording
    frame.loc[~frame.reverse].to_csv(path, index=False)
    cfg = replace(cfg, acquisition=replace(cfg.acquisition, enable_reverse_check=False))
    assert process_recorded_csv(path, cfg, evidence_path=sidecar).decision == "ACCEPT"
    assert process_recorded_csv(path, AppConfig(), evidence_path=sidecar).decision == "REJECT"
