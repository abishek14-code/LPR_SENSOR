"""Canonical trace loading, safe output paths, and auditable JSON/text records."""
import hashlib
import json
import math
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np


def json_safe(value):
    """Unavailable/nonfinite numbers become null; diagnostic codes retain why."""
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return {"status": "UNAVAILABLE", "unsupported_type": type(value).__name__}


@lru_cache(maxsize=1)
def source_provenance():
    root = Path(__file__).resolve().parents[1]
    try:
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip())
        return {"source_revision": revision, "source_dirty": dirty}
    except (OSError, subprocess.CalledProcessError):
        return {"source_revision": None, "source_dirty": None}


def load_trace_csv(path):
    from .simulator_adapter import load_simulator_csv
    return load_simulator_csv(path)


def safe_stem(stem):
    original = str(stem)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}", original):
        return original
    readable = re.sub(r"[^A-Za-z0-9_.-]", "_", original).strip("._-")[:70] or "record"
    return readable + "-" + hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]


def format_decision(record):
    replay = record.get("reference_replay")
    lines = [f"Scenario: {record.get('scenario_id')}",
             f"Run: {record.get('run_id')}  Trial: {record.get('trial_id')}",
             f"Attempt: {record.get('attempt_id')}  Seed: {record.get('seed')}",
             f"Controller: {record.get('controller_variant')}  Source: {record.get('source_type')}",
             f"Revision: {record.get('source_revision')}  Dirty source: {record.get('source_dirty')}"]
    for name in ("A1", "B", "A2"):
        block = record.get(name)
        lines.append(f"{name}: {block['status'] if block else 'UNAVAILABLE'}")
    lines += [
        f"Reference Replay: {'NOT ENTERED' if replay is None else 'PASS' if replay['passed'] else 'FAIL'}",
        f"Reacquisition: {record.get('reacquisition_count', 0)}/1",
        f"Final Decision: {record.get('decision')}  Final State: {record.get('final_state')}",
        f"Codes: {', '.join(record.get('diagnostic_codes', [])) or 'NONE'}",
        f"Timed out: {record.get('timed_out')}",
        f"Rapp: {record.get('Rapp')} ohm ({record.get('Rapp_estimation_method')})",
        f"Rp corrected: {record.get('Rp_corrected')} ohm",
        f"Uncertainty heuristic: {record.get('uncertainty_ohm')} ohm (no confidence coverage claim)",
        f"Timing (s): {json.dumps(json_safe(record.get('timing', {})), sort_keys=True, allow_nan=False)}",
        f"State history: {' -> '.join(record.get('state_history', []))}",
    ]
    for attempt in record.get("attempt_history", []):
        diagnostic = attempt.get("diagnostics") or {}
        lines.append(f"Attempt {attempt['attempt_id']}: state={attempt.get('final_state')}; "
                     f"decision={(attempt.get('decision_result') or {}).get('decision')}; codes={diagnostic.get('reason_codes', [])}; fault={attempt.get('fault_metadata', {})}")
    canonical = record.get("decision_result")
    if canonical:
        lines += [f"Canonical Decision: {canonical['decision']}", f"Primary Reason: {canonical['primary_reason']}",
                  f"Recommended Action: {canonical['recommended_action']}",
                  f"Retry Plan: {json.dumps(canonical['retry_plan'], sort_keys=True)}",
                  f"Estimate Status: {json.dumps(canonical['estimate_status'], sort_keys=True)}"]
        for check in canonical["checks"]:
            lines.append(f"Check {check['name']}: {'PASS' if check['passed'] else 'FAIL'}; observed={check['observed']}; expected={check['expected']}; reason={check['reason_code']}; recoverable={check['recoverable']}; {check['explanation']}; action={check['recommended_action']}")
        lines.extend("Warning: " + warning for warning in canonical["warnings"])
    return "\n".join(lines) + "\n"


def save_decision_record(record: dict, output_dir, stem: str):
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(stem)
    targets = [directory / f"{stem}{suffix}" for suffix in (".json", ".txt")]
    for path in targets:
        if path.is_symlink() or path.resolve().parent != directory:
            raise ValueError("decision output path must remain inside its output directory")
    data = json_safe(record)
    targets[0].write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    targets[1].write_text(format_decision(data), encoding="utf-8")
    return tuple(targets)
