"""Independently seeded paired software experiments; truth is evaluation-only."""
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pandas as pd

from .config import AppConfig
from .controller import State, run_attempt
from .io_utils import json_safe, save_decision_record, source_provenance


SCENARIOS = {
    "healthy": "", "gain_fault": "gain_fault", "offset_fault": "voltage_offset",
    "noise": "excessive_noise", "correlated_noise": "correlated_noise",
    "slow_settling": "recoverable_slow_fault", "high_cdl": "high_cdl",
    "ocp_drift": "ocp_drift", "low_signal": "low_signal",
    "switching_disturbance": "switching_disturbance",
    "forward_reverse_mismatch": "forward_reverse_mismatch", "mixed_fault": "mixed_fault",
    "timeout": "timeout_fault", "reset": "",
}
# A disturbance is not necessarily a bad released estimate.
SCENARIO_POLICY = {name: "persistent_invalid" for name in SCENARIOS}
SCENARIO_POLICY.update(healthy="valid", switching_disturbance="benign_disturbance",
                       slow_settling="recoverable", reset="incomplete")


def run_demo(config: AppConfig):
    return [
        run_attempt(config, "healthy"),
        run_attempt(config, "slow_settling_fault", fault="recoverable_slow_fault"),
        run_attempt(config, "gain_fault", fault="gain_fault"),
    ]


def run_scenario(config: AppConfig, scenario_id: str, fault: str = ""):
    return run_attempt(config, scenario_id, fault=fault)


def _variant_config(config, variant):
    if variant == "baseline":
        acquisition = replace(config.acquisition, enable_reverse_check=False, max_reacquisition_attempts=0)
    elif variant == "conservative":
        acquisition = replace(config.acquisition, dwell_s=config.acquisition.reacquisition_dwell_s,
                              enable_reverse_check=True, max_reacquisition_attempts=0)
    elif variant == "enhanced":
        acquisition = config.acquisition
    elif variant == "no_reverse":
        acquisition = replace(config.acquisition, enable_reverse_check=False)
    else:
        raise ValueError(f"unknown controller variant: {variant}")
    return replace(config, acquisition=acquisition, controller_variant=variant)


def _reset_record(config, scenario_id, seed, **identities):
    def interrupt(controller, plant):
        if controller.state == State.A1 and plant.measurement_window_time_s >= .1:
            controller.reset()
    record = run_attempt(replace(config, seed=seed), scenario_id, on_sample=interrupt, **identities)
    record.fault_metadata = {"fault_type": "reset", "fault_parameters": {}, "seed": seed,
                             "affected_signal": "controller_state", "source_attempt": record.attempt_id}
    return record


def trial_record(record, scenario_id, seed, variant):
    if record.seed != seed:
        raise ValueError("reported trial seed differs from executed seed")
    data = record.to_dict()
    truth = data["evaluation_truth"].get("Rapp_ohm")
    rapp = data.get("Rapp")
    error = abs(rapp - truth) if rapp is not None and truth is not None else None
    policy = SCENARIO_POLICY[scenario_id]
    accepted, rejected = data["decision"] == "ACCEPT", data["decision"] == "REJECT"
    # Explicit evaluation tolerance, not a controller acceptance input.
    inaccurate = error is None or truth is None or error > .05 * truth
    false_acceptance = accepted and (policy == "persistent_invalid" or inaccurate)
    recovered = accepted and policy == "recoverable" and data["reacquisition_count"] > 0 and not inaccurate
    replay = data["reference_replay"]
    windows = [attempt.get("windows", []) for attempt in data["attempt_history"]]
    digest = hashlib.sha256(json.dumps(windows, sort_keys=True, allow_nan=False).encode()).hexdigest()
    data.update(
        ground_truth_Rapp_ohm=truth, scenario_policy=policy,
        evaluation_relative_error_limit=.05, diagnostic_estimate_available=rapp is not None,
        accepted_absolute_resistance_error_ohm=error if accepted else None,
        diagnostic_absolute_resistance_error_ohm=error,
        false_acceptance=bool(false_acceptance),
        false_rejection=bool(rejected and policy in {"valid", "benign_disturbance"}),
        valid_rejection=bool(rejected and policy == "persistent_invalid"),
        recovered_fault=bool(recovered),
        unresolved_recoverable=bool(policy == "recoverable" and not accepted),
        replay_result="NOT_ENTERED" if replay is None else "PASS" if replay["passed"] else "FAIL",
        inconclusive=data["decision"] == "INCONCLUSIVE",
        diagnostic_inconclusive=bool(record.diagnostics and record.diagnostics.inconclusive),
        random_realization_digest=digest,
    )
    return json_safe(data)


def summarize_trials(records):
    if not records:
        return pd.DataFrame()
    frame = pd.DataFrame(records)
    rows = []
    def mean(values):
        clean = pd.Series(values, dtype=float).dropna()
        return float(clean.mean()) if len(clean) else None
    for (scenario, variant), group in frame.groupby(["scenario_id", "controller_variant"], sort=True):
        accepted = group.decision.eq("ACCEPT")
        rejected = group.decision.eq("REJECT")
        row = dict(scenario_id=scenario, controller_variant=variant, trial_count=len(group),
                   accepted=int(accepted.sum()), rejected=int(rejected.sum()),
                   incomplete=int(group.decision.eq("INCOMPLETE").sum()),
                   inconclusive=int(group.inconclusive.sum()),
                   diagnostic_inconclusive=int(group.diagnostic_inconclusive.sum()),
                   false_acceptance=int(group.false_acceptance.sum()),
                   false_rejection=int(group.false_rejection.sum()),
                   valid_rejection_detection=int(group.valid_rejection.sum()),
                   recovered_fault=int(group.recovered_fault.sum()),
                   unresolved_recoverable=int(group.unresolved_recoverable.sum()),
                   timed_out=int(group.timed_out.sum()),
                   accepted_estimate_count=int((accepted & group.diagnostic_estimate_available).sum()),
                   rejected_estimate_count=int((rejected & group.diagnostic_estimate_available).sum()),
                   incomplete_estimate_count=int((group.decision.eq("INCOMPLETE") & group.diagnostic_estimate_available).sum()),
                   mean_absolute_resistance_error_ohm=mean(group.accepted_absolute_resistance_error_ohm),
                   mean_uncertainty_ohm=mean(group.loc[accepted, "uncertainty_ohm"]),
                   mean_acquisition_time_s=mean(group.acquisition_time_s),
                   mean_driven_time_s=mean(group.driven_time_s),
                   mean_total_elapsed_time_s=mean([r["total_elapsed_time_s"] for r in group.timing]),
                   mean_reacquisition_count=mean(group.reacquisition_count),
                   replay_pass=int(group.replay_result.eq("PASS").sum()),
                   replay_fail=int(group.replay_result.eq("FAIL").sum()),
                   unique_trial_seeds=int(group.seed.nunique()),
                   distinct_realizations=int(group.random_realization_digest.nunique()))
        rows.append(row)
    return pd.DataFrame(rows)


def _write_markdown(frame, path):
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(str(row[c]) if pd.notna(row[c]) else "" for c in columns) + " |")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_seeded_experiments(config, trials=100, variants=("enhanced",),
                          scenarios=SCENARIOS.keys(), output_dir="results"):
    if not isinstance(trials, int) or trials < 1:
        raise ValueError("trials must be a positive integer")
    variants, scenarios = tuple(variants), tuple(scenarios)
    if not variants or len(set(variants)) != len(variants) or not scenarios or len(set(scenarios)) != len(scenarios):
        raise ValueError("variants and scenarios must be nonempty and unique")
    if set(scenarios) - set(SCENARIOS):
        raise ValueError("unknown experiment scenario")
    output = Path(output_dir)
    tables, generated = output / "tables", output / "generated"
    tables.mkdir(parents=True, exist_ok=True)
    generated.mkdir(parents=True, exist_ok=True)
    records = []
    for variant in variants:
        variant_config = _variant_config(config, variant)
        for scenario_id in scenarios:
            for index in range(trials):
                seed = config.seed + index
                trial_config = replace(variant_config, seed=seed)
                identity = f"{scenario_id}:{variant}:seed-{seed}:trial-{index}"
                ids = dict(run_id=f"campaign-seed-{config.seed}", trial_id=identity)
                record = (_reset_record(trial_config, scenario_id, seed, **ids) if scenario_id == "reset"
                          else run_attempt(trial_config, scenario_id, fault=SCENARIOS[scenario_id], **ids))
                records.append(trial_record(record, scenario_id, seed, variant))
    summary = summarize_trials(records)
    with (tables / "trial_records.jsonl").open("w", encoding="utf-8") as handle:
        for item in records:
            handle.write(json.dumps(item, sort_keys=True, allow_nan=False) + "\n")
    summary.to_csv(tables / "seeded_trial_metrics.csv", index=False)
    _write_markdown(summary, tables / "seeded_trial_metrics.md")
    representatives = pd.DataFrame(records).groupby(["scenario_id", "controller_variant"], sort=True).head(1)
    for scenario in scenarios:
        for variant in variants:
            sample = next(r for r in records if r["scenario_id"] == scenario and r["controller_variant"] == variant)
            stem = f"representative_{scenario}" + ("" if variant == "enhanced" else f"_{variant}")
            save_decision_record(sample, generated, stem)
    # Nested CSV cells are strict JSON, not Python repr.
    for column in representatives:
        representatives[column] = representatives[column].map(
            lambda x: json.dumps(x, sort_keys=True, allow_nan=False) if isinstance(x, (dict, list)) else x)
    representatives.to_csv(tables / "representative_decision_records.csv", index=False)
    manifest = {**source_provenance(), "evidence_type": "SOFTWARE-GENERATED",
                "configuration": asdict(config),
                "trial_count": len(records), "trials_per_scenario_method": trials,
                "variants": variants, "scenarios": scenarios, "seed_base": config.seed,
                "pairing": "Same seed across methods; distinct PCG64 initial streams across trial indices.",
                "scenario_policy": SCENARIO_POLICY,
                "evaluation_error_tolerance": .05,
                "limitations": "Simulation only; repeated deterministic cases are not new stochastic evidence."}
    (tables / "campaign_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    return records, summary
