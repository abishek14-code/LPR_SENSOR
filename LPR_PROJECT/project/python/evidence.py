"""Reproducible simulation figures and independent analytical comparisons."""
import json
from dataclasses import asdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from models.analytical_checks import AnalyticalReference, validate_reference_case
from .config import AppConfig
from .controller import run_attempt
from .io_utils import source_provenance
from .lpr_analysis import fit_voltage_current
from .stateful_model import StatefulPlant


def _ensure_dirs(output_dir):
    root = Path(output_dir)
    figures, tables = root / "figures", root / "tables"
    figures.mkdir(parents=True, exist_ok=True)
    tables.mkdir(parents=True, exist_ok=True)
    return root, figures, tables


def settled_plot_fit(frame):
    # Exactly the point-tail means used by analyze_block, not transient samples.
    points = [point.tail(max(2, len(point) // 4))[["actual_voltage_v", "current_a"]].mean()
              for _, point in frame.groupby("point_index", sort=True)]
    means = pd.DataFrame(points)
    voltage, current = means.actual_voltage_v.to_numpy(), means.current_a.to_numpy()
    return voltage, current, fit_voltage_current(voltage, current)


def _plot_lpr(frame, path):
    voltage, current, fit = settled_plot_fit(frame)
    predicted = fit["slope"] * current + fit["intercept"]
    order = np.argsort(current)
    figure, axis = plt.subplots()
    axis.scatter(current * 1e9, voltage * 1e3, label="SIMULATED point-tail means")
    axis.plot(current[order] * 1e9, predicted[order] * 1e3,
              label=f"Rapp = {fit['slope'] / 1e3:.3f} kohm")
    axis.set(xlabel="Current (nA)", ylabel="Voltage (mV)", title="Healthy apparent-resistance fit")
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _plot_settling(frame, path, window_s):
    figure, axis = plt.subplots()
    axis.plot(frame["time_s"], frame["current_a"] * 1e9, label="SIMULATED current")
    for index, (_, point) in enumerate(frame.groupby("point_index", sort=True)):
        end = point.time_s.iloc[-1]
        axis.axvspan(end - 2 * window_s, end - window_s, alpha=.12, color="orange",
                     label="previous window" if index == 0 else None)
        axis.axvspan(end - window_s, end, alpha=.12, color="green",
                     label="final window" if index == 0 else None)
    axis.set(xlabel="Elapsed acquisition time (s)", ylabel="Current (nA)",
             title="Settling checks: two nonoverlapping windows per point")
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _plot_sequence(record, path, title):
    states = record.state_history
    figure, axis = plt.subplots(figsize=(8, max(4, len(states) * .22)))
    y = np.arange(len(states))
    axis.plot(np.zeros(len(states)), y, "o-", markersize=3)
    for index, state in enumerate(states):
        axis.text(.02, index, state, va="center", fontsize=8)
    axis.invert_yaxis()
    axis.set_xlim(-.01, .25)
    axis.set_xticks([])
    axis.set_ylabel("Executed transition index")
    axis.set_title(title + f"\nFinal: {record.decision} — SIMULATED", fontsize=10)
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _plot_summary(summary, path):
    variants = list(summary.controller_variant.unique()) if summary is not None and not summary.empty else []
    figure, axes = plt.subplots(max(1, len(variants)), 1, figsize=(11, max(5, 4 * len(variants))), squeeze=False)
    for axis, variant in zip(axes[:, 0], variants):
        group = summary.loc[summary.controller_variant == variant]
        bottom = np.zeros(len(group))
        for column, color in (("accepted", "#298c55"), ("rejected", "#b95448"), ("incomplete", "#777777"), ("inconclusive", "#d0a343")):
            values = group[column].to_numpy()
            axis.bar(np.arange(len(group)), values, bottom=bottom, label=column, color=color)
            bottom += values
        axis.set_xticks(np.arange(len(group)))
        axis.set_xticklabels(group.scenario_id, rotation=35, ha="right", fontsize=8)
        axis.set(title=f"{variant}: SIMULATED outcomes", ylabel="Trials")
        axis.legend(ncols=4, fontsize=8, loc="upper left", bbox_to_anchor=(0, 1.2))
    if not variants:
        axes[0, 0].text(.1, .5, "No experiment summary supplied")
    figure.tight_layout(h_pad=4)
    figure.savefig(path, dpi=150)
    plt.close(figure)


def write_analytical_table(output_dir, config=None):
    _, _, tables = _ensure_dirs(output_dir)
    config = config or AppConfig()
    validation = validate_reference_case()
    rows = [dict(quantity=name, expected=expected, actual=validation["actual"][name],
                 passed=validation["checks"][name], evidence_type="ANALYTICAL", relative_tolerance=1e-6,
                 estimation_method="analytical")
            for name, expected in validation["expected"].items()]
    reference = AnalyticalReference(config.model.ru_ohm, config.model.rp_ohm,
                                    config.model.cdl_f, config.acquisition.b_amplitude_v)
    plant = StatefulPlant(config.model, seed=config.seed)
    initial = plant._step(reference.perturbation_v, 0)
    at_tau = plant._step(reference.perturbation_v, reference.tau_s)
    expected_tau = reference.steady_current_a + (reference.initial_current_a - reference.steady_current_a) / np.e
    steady = plant._step(reference.perturbation_v, 49 * reference.tau_s)
    comparisons = {
        "configured_initial_current_a": (reference.initial_current_a, initial),
        "configured_current_at_tau_a": (expected_tau, at_tau),
        "configured_steady_current_a": (reference.steady_current_a, steady),
        "configured_dc_resistance_ohm": (reference.dc_resistance_ohm, reference.perturbation_v / steady),
        "configured_tau_s": (reference.tau_s, plant.tau_s),
    }
    for name, (expected, actual) in comparisons.items():
        rows.append(dict(quantity=name, expected=expected, actual=actual,
                         passed=bool(np.isclose(actual, expected, rtol=1e-9, atol=0)),
                         evidence_type="SIMULATED_vs_ANALYTICAL", relative_tolerance=1e-9,
                         estimation_method="analytical"))
    frame = pd.DataFrame(rows)
    frame.to_csv(tables / "analytical_validation.csv", index=False)
    return frame


def generate_evidence(config: AppConfig, summary=None, output_dir="results"):
    _, figures, tables = _ensure_dirs(output_dir)
    plant = StatefulPlant(config.model, seed=config.seed)
    healthy = plant.acquire_block("A1", config.acquisition.a1_amplitude_v, config.acquisition)
    _plot_lpr(healthy, figures / "figure_1_healthy_lpr.png")
    _plot_settling(healthy, figures / "figure_2_settling_behavior.png", config.diagnostics.settling_window_s)
    recoverable = run_attempt(config, "slow_settling_fault", fault="recoverable_slow_fault")
    replay_failure = run_attempt(config, "gain_fault", fault="gain_fault")
    _plot_sequence(recoverable, figures / "figure_3_fault_replay_reacquisition.png", "Same sample: fault, replay, one reacquisition")
    _plot_sequence(replay_failure, figures / "figure_4_replay_failure_rejection.png", "Gain fault: replay failure and rejection")
    _plot_summary(summary, figures / "figure_5_seeded_experiment_summary.png")
    analytical = write_analytical_table(output_dir, config)
    paths = sorted(str(path) for path in figures.glob("*.png"))
    manifest = {**source_provenance(), "evidence_type": "SOFTWARE-GENERATED", "seed": config.seed,
                "configuration": asdict(config),
                "analytical_passed": bool(analytical.passed.all()), "figures": paths,
                "healthy_fit_Rapp_ohm": settled_plot_fit(healthy)[2]["slope"],
                "figure_3_decision": recoverable.decision, "figure_4_decision": replay_failure.decision}
    (tables / "figure_manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))
    return paths
