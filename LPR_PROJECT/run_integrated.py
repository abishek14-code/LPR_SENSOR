#!/usr/bin/env python3
"""Run unchanged supplied P3 and open an offline HTML decision report.

Default: evaluate bundled genuine native LTspice observations (offline replay).
Synthetic mode executes the existing stateful controller, including bounded retry.
No instrument commands or new LTspice/Elmer solves are issued by this entry point.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import uuid
import webbrowser
import subprocess

# Also support importlib-based tests and the external entry-point shim.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_html import atomic_write, status_page, render_report, make_trace_payload

BASE = Path(__file__).resolve().parent
PROJECT = BASE / "project"
EXIT_CODES = {"ACCEPT": 0, "REJECT": 2, "WAIT_OR_REPEAT": 3}
STATES = {0: "IDLE", 1: "CALIBRATION_OPENING", 2: "OCP_OPENING", 3: "A1",
          4: "B", 5: "A2", 6: "A1_REVERSE", 7: "B_REVERSE", 8: "A2_REVERSE",
          9: "RECOVERY", 10: "OCP_CLOSING", 11: "CALIBRATION_CLOSING",
          12: "CALIBRATION_REPLAY", 13: "REFERENCE_WAIT"}
CHANNEL_MAP = {"time_s": "time", "actual_voltage_v": "v(actual_hold)",
               "current_a": "v(i_meas)", "requested_voltage_v": "v(req)",
               "drive_enabled": "v(drive)", "point_index": "v(point_code)",
               "reverse": "v(reverse)", "gain": "v(nom_gain)",
               "adc_range_v": "v(adc_range)", "adc_rate_sps": "v(adc_rate)"}
OBSERVABLE_CHANNELS = tuple(dict.fromkeys([*CHANNEL_MAP.values(), "v(clock)", "v(state_code)"]))
# Every accepted _plant_for_fault label is exposed, plus the existing reset.
# Existing public spellings remain stable; aliases are identified in the batch catalog.
SCENARIOS = {
    "healthy": "", "gain_fault": "gain_fault", "replay_failure": "replay_failure",
    "voltage_offset": "voltage_offset", "current_offset": "current_offset",
    "excessive_noise": "excessive_noise", "correlated_noise": "correlated_noise",
    "slow_settling_fault": "slow_settling_fault",
    "slow_settling": "recoverable_slow_fault", "persistent_failure": "persistent_slow_fault",
    "high_cdl": "high_cdl", "ocp_drift": "ocp_drift", "recovery_shift": "recovery_shift",
    "low_signal": "low_signal", "forward_reverse_mismatch": "forward_reverse_mismatch",
    "switching_disturbance": "switching_disturbance", "mixed_fault": "mixed_fault",
    "timeout": "timeout_fault", "opening_check_failure": "opening_check_failure",
    "closing_check_failure": "closing_check_failure", "reset": "",
}


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_runtime():
    """Import only packaged P3, after checking its supplied-source hashes."""
    if not PROJECT.is_dir():
        raise RuntimeError("Extract the complete ZIP first. Keep this script beside project/ and evidence/.")
    manifest = json.loads((BASE / "provenance/SUPPLIED_SOURCE_SHA256.json").read_text())
    checked = {}
    for name, expected in manifest.items():
        if name.startswith("python/") and name.endswith(".py") or name == "models/analytical_checks.py":
            actual = sha256(PROJECT / name)
            if actual != expected:
                raise RuntimeError(f"Supplied source integrity check failed: {name}")
            checked[name] = actual
    if not checked:
        raise RuntimeError("No supplied P3 source hashes found")
    sys.path.insert(0, str(PROJECT))
    import numpy as np
    import pandas as pd
    import python.controller as controller
    if not Path(controller.__file__).resolve().is_relative_to(PROJECT.resolve()):
        raise RuntimeError("Wrong P3 controller imported")
    return checked


def parse_raw(path):
    """Read supported UTF-16LE real transient binary RAW; ignore all non-observables.

    The binary bytes of excluded channels are never converted into measurement
    columns or tested as diagnostics. Source hashing is provenance, not a threshold.
    """
    import numpy as np
    import pandas as pd
    data = Path(path).read_bytes()
    marker = "Binary:\n".encode("utf-16-le")
    start = data.find(marker)
    if start < 0:
        raise ValueError("Unsupported RAW: expected UTF-16LE real transient Binary format; no conversion attempted")
    offset = start + len(marker)
    try:
        header = data[:offset].decode("utf-16-le").lstrip("\ufeff")
        flags = re.search(r"^Flags:\s*(.*)$", header, re.MULTILINE).group(1).lower().split()
        if "real" not in flags or set(flags) & {"complex", "double", "fastaccess", "stepped"}:
            raise ValueError("Only real, unstepped, interleaved float32-channel transient RAW is supported")
        nv = int(re.search(r"No\. Variables:\s*(\d+)", header).group(1))
        count = int(re.search(r"No\. Points:\s*(\d+)", header).group(1))
        lines = header.split("Variables:\n", 1)[1].split("Binary:", 1)[0].strip().splitlines()
        fields = [line.split() for line in lines]
        names = [entry[1].lower() for entry in fields]
        if [int(entry[0]) for entry in fields] != list(range(nv)):
            raise ValueError("Invalid RAW variable indices")
    except (AttributeError, IndexError, UnicodeError) as exc:
        raise ValueError(f"Malformed RAW header: {exc}") from exc
    if nv < 2 or count < 2 or len(names) != nv or len(set(names)) != nv or names[0] != "time":
        raise ValueError("Invalid RAW dimensions or duplicate/non-time channel mapping")
    missing = sorted(set(OBSERVABLE_CHANNELS) - set(names))
    if missing:
        raise ValueError(f"RAW lacks required observable acquisition channels: {missing}")
    dtype = np.dtype([("t", "<f8"), ("v", "<f4", (nv - 1,))])
    if len(data) - offset != count * dtype.itemsize:
        raise ValueError("RAW byte count does not match declared point count and supported encoding")
    values = np.frombuffer(data, dtype=dtype, offset=offset, count=count)
    frame = pd.DataFrame({name: values["t"].copy() if name == "time"
                          else values["v"][:, names.index(name) - 1].astype(float)
                          for name in OBSERVABLE_CHANNELS})
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError("Nonfinite value in an observable channel")
    times = frame["time"].to_numpy()
    if times[0] < 0 or np.any(np.diff(times) <= 0):
        raise ValueError("RAW timestamps must be nonnegative and strictly increasing")
    metadata = {"raw_sha256": hashlib.sha256(data).hexdigest(), "native_rows": count,
                "observable_channels": list(OBSERVABLE_CHANNELS),
                "ignored_channels": [name for name in names if name not in OBSERVABLE_CHANNELS],
                "raw_header": header, "encoding": "UTF-16LE header; float64 time; float32 channels"}
    return frame, metadata


def sample_observables(raw):
    """Select saved falling clock-edge rows, with no interpolation or resampling."""
    import numpy as np
    import pandas as pd
    clock = raw["v(clock)"].to_numpy()
    edges = np.flatnonzero((clock[:-1] > 1e-7) & (clock[1:] <= 1e-7)) + 1
    if len(edges) < 2:
        raise ValueError("No usable native falling-clock observations")
    selected = raw.iloc[edges]
    frame = pd.DataFrame({key: selected[node].to_numpy(copy=True) for key, node in CHANNEL_MAP.items()})
    for key in ("drive_enabled", "reverse", "point_index"):
        values = frame[key].to_numpy()
        integer = np.rint(values).astype(int)
        if np.max(np.abs(values - integer)) > 1e-5:
            raise ValueError(f"Fractional acquisition context: {key}")
        if key != "point_index" and not set(integer) <= {0, 1}:
            raise ValueError(f"Nonboolean acquisition context: {key}")
        if key == "point_index" and np.any(integer < 0):
            raise ValueError("Negative point index")
        frame[key] = integer if key == "point_index" else integer.astype(bool)
    code = selected["v(state_code)"].to_numpy()
    integer = np.rint(code).astype(int)
    if np.max(np.abs(code - integer)) > 1e-5 or not set(integer) <= set(STATES):
        raise ValueError("Unknown or fractional observable state code")
    frame["state_code"] = integer
    frame["state"] = frame.state_code.map(STATES)
    dt = np.diff(frame.time_s)
    if np.any(dt <= 0):
        raise ValueError("Clock observations are not strictly increasing")
    # Existing audited adapter convention, explicitly derived from timestamps.
    # The first interval uses the median observed interval, not an invented event.
    frame["sample_duration_s"] = np.r_[np.median(dt), dt]
    return frame, edges


def measurement_groups(samples):
    forward, reverse = {}, {}
    for code in range(3, 9):
        name = STATES[code]
        group = samples.loc[samples.state_code == code].drop(columns="state_code").copy()
        if group.empty:
            continue  # P3 diagnoses missing windows; no replacement samples.
        group["block_id"] = name
        (forward if code <= 5 else reverse)[name.removesuffix("_REVERSE")] = group
    ocp = []
    for code in (2, 10):
        group = samples.loc[samples.state_code == code].copy()
        if group.empty:
            ocp.append(None)
        else:
            if group.drive_enabled.any():
                raise ValueError("An OCP-labelled interval has observable drive enabled")
            group["ocp_voltage_v"] = group.actual_voltage_v
            ocp.append(group)
    return forward, reverse, ocp


def read_log(path):
    content = Path(path).read_bytes()
    text = content.decode("utf-16") if content[:2] in (b"\xff\xfe", b"\xfe\xff") else content.decode("utf-16-le" if b"\x00" in content[:100] else "utf-8", errors="replace")
    failures = re.findall(r"^.*(?:fatal error|simulation failed|simulation aborted|timestep too small|cannot open|can't open|syntax error).*$", text, re.I | re.M)
    if failures:
        raise ValueError("Native simulation log reports failure: " + " | ".join(failures))
    if "LTspice" not in text or "Total elapsed time:" not in text:
        raise ValueError("LOG does not establish a completed LTspice execution")
    return {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(content).hexdigest(),
            "version": text.splitlines()[0], "native_log_completion_observed": True,
            "new_solver_execution": False}


def evaluate_native(raw_path, log_path, *, capture=None):
    import numpy as np
    from python.config import AppConfig
    from python.controller import process_recorded_frames
    from python.decision_engine import EvaluationContext, evaluate_diagnostics
    raw, provenance = parse_raw(raw_path)
    provenance["log"] = read_log(log_path)
    circuit_path = Path(raw_path).with_suffix(".cir")
    provenance["circuit"] = {"path": str(circuit_path.resolve()), "sha256": sha256(circuit_path)} if circuit_path.is_file() else None
    samples, indices = sample_observables(raw)
    forward, reverse, ocp = measurement_groups(samples)
    config = AppConfig()
    observed = {}
    for block, setting in (("A1", "a1_amplitude_v"), ("B", "b_amplitude_v")):
        if block in forward:
            amplitude = float(forward[block].requested_voltage_v.abs().max())
            # Only the already-audited float32 serialization discrepancy is adapted.
            # No arbitrary excitation or threshold fitting to a particular trace.
            default = getattr(config.acquisition, setting)
            if not np.isclose(amplitude, default, rtol=2 * np.finfo(np.float32).eps, atol=0):
                raise ValueError(f"{block}: unsupported command amplitude; expected nominal acquisition protocol")
            observed[setting] = amplitude
    config = replace(config, acquisition=replace(config.acquisition, **observed))
    record = process_recorded_frames(
        config, "native-observable-input", forward, source_type="ltspice",
        source_metadata={"source_hash": provenance["raw_sha256"], "generation": "Native RAW replay; no new solver run"},
        reverse_frames=reverse, ocp_opening=ocp[0], ocp_closing=ocp[1])
    record.decision_result = evaluate_diagnostics(record.diagnostics, config,
        context=EvaluationContext(source_type="ltspice", simulation_status="success"))
    if record.decision_result is None:
        raise RuntimeError("P3 did not produce a canonical decision")
    sequence = []
    # Preserve repeated and intervening intervals; never aggregate them into a fake event.
    segments = samples.state_code.ne(samples.state_code.shift()).cumsum()
    for _, group in samples.groupby(segments, sort=False):
        sequence.append({"state": str(group.state.iloc[0]), "rows": len(group),
                         "start_s": float(group.time_s.iloc[0]), "end_s": float(group.time_s.iloc[-1]),
                         "drive_enabled_values": [bool(x) for x in group.drive_enabled.unique()]})
    provenance.update(raw_path=str(Path(raw_path).resolve()), observations=len(samples),
                      selected_native_row_indices=indices.tolist(), channel_map=CHANNEL_MAP,
                      resampled=False, sorted=False, acquisition_from_observed_commands=observed,
                      sample_duration_method="timestamp differences; first duration = median observed interval",
                      measurement_units={"time_s": "s", "actual_voltage_v": "V", "current_a": "A encoded by measured helper node"},
                      polarity="signed WE–RE voltage and measured current; no polarity inversion",
                      opening_reference_check=None, closing_reference_check=None,
                      validated_calibration=None, online_Ru=None,
                      note="OCP and calibration state labels do not authorize reference or calibration checks.")
    if capture is not None:
        segment_ids = samples.state_code.ne(samples.state_code.shift()).cumsum()
        capture['frames'] = [f.copy() for _, f in samples.groupby(segment_ids, sort=False)]
    return record, provenance, sequence


def evaluate_synthetic(scenario, seed, *, capture=None):
    from python.config import AppConfig
    from python.controller import State, run_attempt
    plants = []
    def observe(controller, plant):
        # Passive observation only: no RNG draws, stepping, reseeding or mutations.
        if not plants:
            plants.append(plant)
        if scenario == "reset" and controller.state == State.A1 and plant.measurement_window_time_s >= .1:
            controller.reset()
    record = run_attempt(replace(AppConfig(), seed=seed), "synthetic-execution",
                         fault=SCENARIOS[scenario], on_sample=observe if capture is not None or scenario == "reset" else None)
    if capture is not None:
        capture['frames'] = [f.copy() for f in plants[0].windows] if plants else []
    return record


def offline_comparisons(result):
    """Run AFTER the decision. Never pass model truth or spatial summaries to P3."""
    from models.analytical_checks import validate_reference_case
    comparison = validate_reference_case()
    comparison["scope"] = "Offline analytical reference only; model parameters are not measurement diagnostics or online Ru"
    measured = result["measurements"].get("Rapp_ohm")
    if measured is not None:
        comparison["diagnostic_Rapp_ohm"] = measured
        comparison["diagnostic_Rapp_relative_error"] = abs(measured / comparison["actual"]["dc_resistance_ohm"] - 1)
        comparison["diagnostic_value_is_released_measurement"] = False
    p2 = BASE / "evidence/P2_spatial/QUALIFICATION_DECISION.json"
    spatial = json.loads(p2.read_text())
    spatial.update(qualification_source_sha256=sha256(p2), supplied_to_controller=False,
                   scope="Retained spatial audit summary; no Elmer solve or raw-field recomputation in this run")
    return {"analytical_reference": comparison, "spatial_FEM": spatial}


def compose_report(record, mode, provenance=None, sequence=None):
    canonical = record.decision_result.to_dict()
    if canonical["decision"] not in EXIT_CODES:
        raise RuntimeError("Unsupported controller decision")
    return {"schema_version": 1, "execution_mode": mode,
            "decision_scope": "Software measurement-quality policy only; not physical release authorization",
            "decision": canonical["decision"], "reason_codes": canonical["reason_codes"],
            "recommended_action": canonical["recommended_action"], "controller_result": canonical,
            "legacy_terminal_decision": record.decision, "state_history": list(record.state_history),
            "reacquisition_count": record.reacquisition_count,
            "attempt_decisions": [{"attempt_id": attempt["attempt_id"],
                                   "decision": (attempt.get("decision_result") or {}).get("decision"),
                                   "reason_codes": (attempt.get("decision_result") or {}).get("reason_codes", [])}
                                  for attempt in record.attempt_history],
            "provenance": provenance or {}, "observable_sequence": sequence or [],
            "hardware_commands_sent": False, "new_native_solver_run": False,
            "release_evidence": {"physical_release_authorized": False, "validated_calibration": "NOT_ESTABLISHED",
                                 "online_specimen_Ru": "NOT_QUALIFIED", "physical_corrosion_output": "NOT_ESTABLISHED"}}


def save_report(report, parent):
    """Create a new run directory. Do not overwrite earlier decisions or inputs."""
    from python.io_utils import json_safe
    parent = Path(parent).resolve()
    # Input trees must remain read-only; in particular do not write into project/.
    for protected in (PROJECT.resolve(), (BASE / "evidence").resolve(), (BASE / "provenance").resolve()):
        if parent == protected or parent.is_relative_to(protected):
            raise ValueError("Choose an output directory outside project/, evidence/ and provenance/")
    run_id = report.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12]
    target = parent / run_id
    target.mkdir(parents=True, exist_ok=False)
    report["run_id"] = run_id
    report.setdefault("evaluated_at_utc", datetime.now(timezone.utc).isoformat())
    # This timestamp identifies evaluation, never the native acquisition time.
    (target / "decision.json").write_text(json.dumps(json_safe(report), indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (target / "decision.txt").write_text(display(report) + "\n", encoding="utf-8")
    return target


def display(report):
    result = report["controller_result"]
    lines = [f"MODE: {report['execution_mode']}", f"DECISION: {report['decision']}",
             "REASONS: " + (", ".join(report["reason_codes"]) or "NONE"),
             "ACTION: " + report["recommended_action"],
             f"REACQUISITIONS: {report.get('reacquisition_count', 0)}/1"]
    for attempt in report.get("attempt_decisions", []):
        lines.append(f"ATTEMPT: {attempt['decision']} ({', '.join(attempt['reason_codes']) or 'NONE'})")
    provenance = report.get("provenance", {})
    if "native_rows" in provenance:
        lines.append(f"NATIVE INPUT: {provenance['native_rows']:,} rows; {provenance['observations']:,} native clock-edge observations; no resampling")
    resistance = result["measurements"].get("Rapp_ohm")
    lines.append(f"Rapp DIAGNOSTIC: {resistance:.9g} ohm" if resistance is not None else "Rapp DIAGNOSTIC: UNAVAILABLE")
    lines.append("Rp ESTIMATE: " + (str(result["rp_estimate"]) if result["rp_estimate"] is not None else "WITHHELD"))
    lines.append("PHYSICAL CORROSION OUTPUT: NOT ESTABLISHED")
    lines.append("PHYSICAL RELEASE: NOT AUTHORIZED; software ACCEPT does not certify calibration or specimen Ru")
    lines.append("HARDWARE COMMANDS: NONE")
    for warning in result.get("warnings", []):
        lines.append("WARNING: " + warning)
    if report.get("input_error"):
        lines.append("INPUT ERROR: " + report["input_error"])
    return "\n".join(lines)


@contextmanager
def capture_engine_stderr(messages):
    """Keep dependency/provenance stderr in evidence without patching P3."""
    saved = os.dup(2)
    with tempfile.TemporaryFile(mode="w+b") as stream:
        try:
            sys.stderr.flush()
            os.dup2(stream.fileno(), 2)
            yield
        finally:
            sys.stderr.flush()
            os.dup2(saved, 2)
            os.close(saved)
            stream.seek(0)
            messages.append(stream.read().decode("utf-8", errors="replace"))


def default_html_path():
    if BASE.parent.name.startswith("RUNNABLE_INTEGRATION_") and BASE.parent.parent.name == "outputs":
        return BASE.parent.parent / "decision_report.html"
    return BASE / "outputs/decision_report.html"


def check_report_path(path):
    original = Path(path).absolute()
    if original.is_symlink():
        raise ValueError("HTML target must not be a symlink")
    resolved = original.resolve()
    for protected in (PROJECT.resolve(), (BASE / "evidence").resolve(), (BASE / "provenance").resolve()):
        if resolved == protected or resolved.is_relative_to(protected):
            raise ValueError("HTML output must be outside supplied source and evidence directories")
    if resolved.suffix.lower() not in {".html", ".htm"}:
        raise ValueError("HTML output must use .html or .htm")
    return resolved


def open_report(path, run_id):
    try:
        uri = Path(path).resolve().as_uri() + "?run=" + run_id
        if sys.platform == "darwin":
            # LaunchServices avoids the Python macOS AppleScript browser adapter.
            opened = subprocess.run(["/usr/bin/open", uri], capture_output=True, text=True, timeout=8)
            return opened.returncode == 0
        return bool(webbrowser.open(uri, new=2))
    except Exception:
        return False  # The caller always exposes the absolute file path.


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("native", "synthetic"), default="native")
    parser.add_argument("--raw", type=Path, help="Native RAW using the supported bundled acquisition contract")
    parser.add_argument("--log", type=Path, help="Corresponding native LOG; defaults to RAW basename.log")
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), help="Explicit synthetic scenario")
    parser.add_argument("--seed", type=int, help="Synthetic seed only; never assigned to native observations")
    parser.add_argument("--output-dir", type=Path, default=BASE / "outputs/runs", help="Parent for unique JSON/text audit records")
    parser.add_argument("--report", type=Path, default=default_html_path(), help="HTML report path (default: outputs/decision_report.html)")
    parser.add_argument("--no-open", action="store_true", help="Write HTML without requesting a browser")
    parser.add_argument("--json", action="store_true", help="Also print the exact report data as strict JSON")
    parser.add_argument("--text", action="store_true", help="Also print the legacy terminal summary")
    parser.add_argument("--export-csv", action="store_true", help="Optional acquired observation CSV in the unique run directory")
    if "--help" in arguments or "-h" in arguments:
        parser.parse_args(arguments)
        return 0
    # Resolve the output before imports/parsing/evaluation so a failed invocation
    # cannot leave an earlier ACCEPT or old numeric values as this run's report.
    early = argparse.ArgumentParser(add_help=False)
    early.add_argument("--report", type=Path, default=default_html_path())
    early.add_argument("--no-open", action="store_true")
    run = {"run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:12],
           "started_at_utc": datetime.now(timezone.utc).isoformat()}
    lock = None
    locked = False
    report_path = None
    request_open = False
    try:
        try:
            pre, _ = early.parse_known_args(arguments)
        except SystemExit:
            pre = argparse.Namespace(report=default_html_path(), no_open="--no-open" in arguments)
        report_path = check_report_path(pre.report)
        request_open = not pre.no_open
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lock = report_path.with_name(report_path.name + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        locked = True
        with os.fdopen(fd, "w") as stream:
            stream.write(json.dumps(run))
        atomic_write(report_path, status_page(run, "RUNNING", "The current execution is acquiring or evaluating observations. No decision is available yet."))
        try:
            args = parser.parse_args(arguments)
            if args.mode == "native" and (args.scenario is not None or args.seed is not None):
                parser.error("--scenario and --seed apply only to explicit --mode synthetic")
            if args.mode == "synthetic" and (args.raw is not None or args.log is not None):
                parser.error("--raw/--log cannot be combined with synthetic mode")
        except SystemExit as exc:
            atomic_write(report_path, status_page(run, "EXECUTION_FAILED", "Invalid command-line arguments; no controller measurement decision was produced."))
            print("HTML decision report: " + str(report_path), file=sys.stderr)
            return int(exc.code or 2)
        source_hashes = load_runtime()
        from python.config import AppConfig
        from python.decision_engine import reject_input
        from python.io_utils import json_safe
        engine_stderr = []
        try:
            with capture_engine_stderr(engine_stderr):
                report = run_mode(args)
        except (OSError, ValueError) as exc:
            result = reject_input(exc, AppConfig(), source_type="ltspice" if args.mode == "native" else "synthetic").to_dict()
            report = {"schema_version": 2, "execution_mode": args.mode.upper() + "_INPUT_REJECTION",
                      "decision": result["decision"], "reason_codes": result["reason_codes"],
                      "recommended_action": result["recommended_action"], "controller_result": result,
                      "input_error": str(exc), "hardware_commands_sent": False,
                      "provenance": {"raw_path": str((args.raw or BASE / 'evidence/P1_native_nominal/acquisition.raw').resolve())} if args.mode == 'native' else {},
                      "release_evidence": {"physical_release_authorized": False},
                      "trace_payload": {"observations": [], "analysis_windows": [], "block_summary": []}}
        report.update(run)
        report['execution_status'] = 'INCOMPLETE' if report.get('legacy_terminal_decision') == 'INCOMPLETE' else 'INPUT_REJECTED' if report.get('input_error') else 'COMPLETED'
        report["engine_stderr"] = "".join(engine_stderr)
        if not report.get("input_error"):
            try:
                report["offline_comparisons"] = offline_comparisons(report["controller_result"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report["offline_comparisons"] = {"status": "UNAVAILABLE", "error": str(exc), "controller_decision_unchanged": True}
        report["source_hashes"] = source_hashes
        report['reporting_code_sha256'] = {"run_integrated.py": sha256(Path(__file__)), "decision_html.py": sha256(BASE / 'decision_html.py')}
        report['html_report'] = str(report_path)
        report['evaluated_at_utc'] = datetime.now(timezone.utc).isoformat()
        report = json_safe(report)
        # Rendering may add a presentation warning, never a measurement decision.
        rendered = render_report(report)
        destination = save_report(report, args.output_dir)
        if args.export_csv:
            import pandas as pd
            pd.DataFrame(report.get('trace_payload', {}).get('observations', [])).to_csv(destination / 'observations.csv', index=False)
        atomic_write(destination / 'decision_report.html', rendered)
        atomic_write(report_path, rendered)
        if args.json:
            print(json.dumps(report, indent=2, allow_nan=False))
        else:
            if args.text:
                print(display(report))
            print("HTML decision report: " + str(report_path))
            print("Actual controller decision: " + report['decision'])
        return EXIT_CODES[report["decision"]]
    except FileExistsError:
        print("REPORT BUSY: another invocation owns this report target. No new decision was produced. Use a different --report path. Target: " + str(report_path), file=sys.stderr)
        request_open = False
        return 1
    except Exception as exc:
        if locked and report_path is not None:
            try:
                atomic_write(report_path, status_page(run, "EXECUTION_FAILED", type(exc).__name__ + ": " + str(exc)))
            except OSError:
                print("Could not replace the report; any existing file is not evidence for this failed invocation.", file=sys.stderr)
        print("EXECUTION ERROR: " + str(exc) + "\nNo release authorized. HTML report: " + str(report_path), file=sys.stderr)
        return 1
    finally:
        if locked and lock is not None:
            lock.unlink(missing_ok=True)
        if request_open and report_path is not None:
            opened = open_report(report_path, run['run_id'])
            if not opened:
                print("Browser opening unavailable; open this absolute path: " + str(report_path), file=sys.stderr)


def run_mode(args):
    capture = {}
    if args.mode == "native":
        raw_path = args.raw or BASE / "evidence/P1_native_nominal/acquisition.raw"
        log_path = args.log or raw_path.with_suffix(".log")
        record, provenance, sequence = evaluate_native(raw_path, log_path, capture=capture)
        report = compose_report(record, "NATIVE_RAW_REPLAY", provenance, sequence)
    else:
        record = evaluate_synthetic(args.scenario or "healthy", args.seed if args.seed is not None else 42, capture=capture)
        report = compose_report(record, "SYNTHETIC_CONTROLLER_EXECUTION",
                                {"scenario": args.scenario or "healthy", "seed": args.seed if args.seed is not None else 42,
                                 "reference_checks": "Synthetic plant probes; not physical calibration",
                                 "sample_state": "Existing P3 plant evolves through retry; no reseeding between attempts"})
    from python.config import AppConfig
    report["trace_payload"] = make_trace_payload(capture.get("frames", []), record, AppConfig().diagnostics)
    return report

if __name__ == "__main__":
    raise SystemExit(main())
