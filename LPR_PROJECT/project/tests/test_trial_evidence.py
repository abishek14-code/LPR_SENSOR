import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from python.config import AppConfig
from python.controller import run_attempt, _plant_for_fault
from python.experiments import run_seeded_experiments, _variant_config
from python.io_utils import save_decision_record, safe_stem


def test_actual_trial_seeds_and_random_realizations_differ(tmp_path):
    seeds = []
    def create(config, fault, seed):
        seeds.append(seed)
        return _plant_for_fault(config, fault, seed)
    with patch("python.controller._plant_for_fault", side_effect=create):
        records, _ = run_seeded_experiments(AppConfig(), trials=2, scenarios=("noise",), output_dir=tmp_path)
    assert seeds == [42, 43]
    assert [r["seed"] for r in records] == seeds
    assert len({r["random_realization_digest"] for r in records}) == 2
    assert len({r["trial_id"] for r in records}) == 2
    assert len({r["attempt_id"] for r in records}) == 2
    assert records[0]["A1"] != records[1]["A1"]
    assert all(a["seed"] == r["seed"] for r in records for a in r["attempt_history"])


def test_paired_variants_have_unique_ids_and_distinct_policies(tmp_path):
    c = AppConfig()
    assert len({_variant_config(c, v).acquisition for v in ("baseline", "conservative", "enhanced")}) == 3
    records, _ = run_seeded_experiments(c, trials=1, scenarios=("healthy",), variants=("baseline", "conservative", "enhanced"), output_dir=tmp_path)
    assert {r["seed"] for r in records} == {42}
    assert len({r["trial_id"] for r in records}) == len({r["attempt_id"] for r in records}) == 3


def test_truth_uses_actual_config_and_errors_are_accepted_only(tmp_path):
    c = AppConfig()
    c = replace(c, model=replace(c.model, rp_ohm=200000))
    records, summary = run_seeded_experiments(c, trials=1, scenarios=("healthy", "timeout"), output_dir=tmp_path)
    assert all(r["ground_truth_Rapp_ohm"] == 210000 for r in records)
    assert summary.loc[summary.scenario_id == "timeout", "mean_absolute_resistance_error_ohm"].isna().all()
    timeout = next(r for r in records if r["scenario_id"] == "timeout")
    assert timeout["timed_out"] is True
    assert timeout["state_history"] and timeout["attempt_history"]


def test_recovered_disturbance_is_not_false_acceptance(tmp_path):
    records, summary = run_seeded_experiments(AppConfig(), trials=1, scenarios=("slow_settling",), output_dir=tmp_path)
    r = records[0]
    assert r["decision"] == "ACCEPT" and r["recovered_fault"]
    assert not r["false_acceptance"]
    assert r["reacquisition_count"] == 1
    first, second = r["attempt_history"]
    assert first["diagnostics"]["reason_codes"]
    assert second["diagnostics"]["reason_codes"] == []
    assert first["end"] == second["start"]
    assert first["start"]["fault_parameters"]["cdl_scale"] == second["end"]["fault_parameters"]["cdl_scale"] == 3


def test_records_are_strict_json_and_text_is_auditable(tmp_path):
    record = run_attempt(AppConfig(), "timeout", fault="timeout_fault").to_dict()
    json.dumps(record, allow_nan=False)
    j, t = save_decision_record(record, tmp_path, "timeout")
    decoded = json.loads(j.read_text(), parse_constant=lambda s: pytest.fail(s))
    assert decoded["timed_out"] and "TIMEOUT" in decoded["diagnostic_codes"]
    text = t.read_text()
    for label in ("Attempt:", "Seed:", "Controller:", "Final State:", "Codes:", "Timed out:", "Timing (s):", "Revision:"):
        assert label in text


@pytest.mark.parametrize("stem", ["../escaped", "/tmp/absolute", "..\\escaped", "a/b/c", "", "../../", "你好 / .."])
def test_output_names_cannot_escape_directory(tmp_path, stem):
    r = run_attempt(AppConfig(), stem).to_dict()
    paths = save_decision_record(r, tmp_path / "out", stem)
    assert all(p.resolve().parent == (tmp_path / "out").resolve() for p in paths)
    assert json.loads(paths[0].read_text())["scenario_id"] == stem


def test_output_symlink_is_rejected_before_any_write(tmp_path):
    outside = tmp_path / "existing.txt"
    outside.write_text("keep")
    out = tmp_path / "out"
    out.mkdir()
    (out / "case.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="output path"):
        save_decision_record(run_attempt(AppConfig(), "case").to_dict(), out, "case")
    assert outside.read_text() == "keep"
    assert not (out / "case.json").exists()
