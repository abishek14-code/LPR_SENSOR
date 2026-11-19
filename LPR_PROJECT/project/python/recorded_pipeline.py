"""Recorded simulator trace path using the shared analysis and decision layers."""

from .config import AppConfig
from .controller import AttemptRecord, process_recorded_frames
from .simulator_adapter import TraceAdapterConfig, load_simulator_csv, split_measurement_windows
import json
from pathlib import Path
import pandas as pd


def process_recorded_csv(path, config: AppConfig, adapter: TraceAdapterConfig | None = None, *, evidence_path=None) -> AttemptRecord:
    normalized = load_simulator_csv(path, adapter)
    frames = split_measurement_windows(normalized)
    reverse = split_measurement_windows(normalized, reverse=True)
    scenario_id = str(normalized["run_id"].iloc[0]) if "run_id" in normalized.columns else "recorded_trace"
    metadata = {
        "path": str(path),
        "source_hash": normalized.attrs.get("source_sha256"),
        "columns": list(normalized.columns),
        "adapter": normalized.attrs.get("adapter_config", {}),
    }
    evidence = {}
    if evidence_path is not None:
        def invalid_constant(value):
            raise ValueError(f"nonfinite evidence constant: {value}")
        evidence = json.loads(Path(evidence_path).read_text(), parse_constant=invalid_constant)
        if not isinstance(evidence, dict):
            raise ValueError("recorded evidence must be a JSON object")
        for key in ("run_id", "attempt_id"):
            if key not in normalized or evidence.pop(key, None) != str(normalized[key].iloc[0]):
                raise ValueError(f"evidence must match CSV {key}")
        if set(evidence) - {"opening_check", "closing_check", "ocp_opening", "ocp_closing"}:
            raise ValueError("unknown recorded evidence fields")
        for key in ("ocp_opening", "ocp_closing"):
            if key in evidence:
                evidence[key] = pd.DataFrame(evidence[key])
        metadata["evidence_path"] = str(evidence_path)
    return process_recorded_frames(
        config,
        scenario_id,
        frames,
        source_type=(adapter.source_type if adapter else "ltspice"),
        source_metadata=metadata,
        reverse_frames=reverse,
        **evidence,
    )


def evaluate_recorded_csv(path, config: AppConfig, adapter=None, *, evidence_path=None, context=None):
    """Structured boundary for expected file/adapter errors; legacy API unchanged."""
    from .decision_engine import EvaluationContext, evaluate_diagnostics, reject_input
    source_type = adapter.source_type if adapter else "ltspice"
    from .decision_engine import CheckResult
    try:
        selected = EvaluationContext(**{"source_type": source_type, **context}) if isinstance(context, dict) else EvaluationContext(source_type=source_type) if context is None else context
        if not isinstance(selected, EvaluationContext) or not isinstance(config, AppConfig):
            raise ValueError("expected AppConfig and EvaluationContext")
    except (ValueError, TypeError) as exc:
        check = CheckResult("configuration", False, str(exc), "valid typed P3 configuration/context", "INVALID_CONFIGURATION", "Invalid integration configuration.", False, "Correct the configuration.")
        return evaluate_diagnostics(None, AppConfig(), extra_codes=("INVALID_CONFIGURATION",), extra_checks=(check,))
    try:
        record = process_recorded_csv(path, config, adapter, evidence_path=evidence_path)
    except (ValueError, OSError) as exc:
        return reject_input(exc, config, source_type=source_type)
    return evaluate_diagnostics(record.diagnostics, config, context=selected)
