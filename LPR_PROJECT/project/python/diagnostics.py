from dataclasses import dataclass, asdict
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .config import DiagnosticConfig
from .validation import validate_ocp
from .decision_engine import replay_allowed, recovery_allowed
from .lpr_analysis import BlockMetrics, analyze_block, correct_rp


@dataclass
class DiagnosticResult:
    passed: bool
    reason_codes: Tuple[str, ...]
    blocks: Dict[str, BlockMetrics]
    replay_required: bool
    recoverable: bool
    rapp: Optional[float]
    rp_corrected: Optional[float]
    uncertainty_ohm: Optional[float]
    inconclusive: bool = False
    measured_values: Dict = None
    thresholds: Dict = None
    ocp: Dict = None
    forward_reverse_consistent: Optional[bool] = None
    reverse_blocks: Dict[str, BlockMetrics] = None

    def to_dict(self):
        status = "INCONCLUSIVE" if self.inconclusive else "PASS" if self.passed else "FAIL"
        return {
            "status": status,
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "blocks": {key: value.to_dict() for key, value in self.blocks.items()},
            "replay_required": self.replay_required,
            "recoverable": self.recoverable,
            "Rapp": self.rapp,
            "Rp_corrected": self.rp_corrected,
            "uncertainty_ohm": self.uncertainty_ohm,
            "inconclusive": self.inconclusive,
            "measured_values": self.measured_values or {},
            "thresholds": self.thresholds or {},
            "ocp": self.ocp or {},
            "forward_reverse_consistent": self.forward_reverse_consistent,
        }


def _relative_difference(first: Optional[float], second: Optional[float]) -> float:
    if first is None or second is None:
        return float("inf")
    denominator = max(abs(first), abs(second), 1e-12)
    return abs(first - second) / denominator


def _ocp_metrics(opening, closing, diagnostics: DiagnosticConfig):
    result = {"drift_v_per_s": None, "recovery_shift_v": None, "drift_pass": True, "recovery_pass": True}
    if opening is not None and len(opening) >= 2:
        time = opening["time_s"].to_numpy(dtype=float)
        voltage = opening["ocp_voltage_v"].to_numpy(dtype=float)
        slope = float(np.polyfit(time, voltage, 1)[0])
        result["drift_v_per_s"] = slope
        result["drift_pass"] = abs(slope) <= diagnostics.ocp_drift_v_per_s
    if opening is not None and closing is not None and len(opening) and len(closing):
        shift = float(closing["ocp_voltage_v"].mean() - opening["ocp_voltage_v"].mean())
        result["recovery_shift_v"] = shift
        result["recovery_pass"] = abs(shift) <= diagnostics.max_recovery_shift_v
    return result


def _ocp_evidence(frame, minimum_duration_s=0):
    observed = {"valid": False, "samples": len(frame) if isinstance(frame, pd.DataFrame) else None,
                "duration_s": None, "error": None}
    try:
        validate_ocp(frame)
        times = frame.time_s.to_numpy(dtype=float)
        observed["duration_s"] = float(times[-1] - times[0] + np.median(np.diff(times)))
        observed["valid"] = observed["duration_s"] + 1e-8 >= minimum_duration_s
        if not observed["valid"]:
            observed["error"] = "OCP observation duration is below the configured minimum."
    except ValueError as exc:
        observed["error"] = str(exc)
    return observed


def _valid_ocp(frame, minimum_duration_s=0):
    return _ocp_evidence(frame, minimum_duration_s)["valid"]


def _check_code(evidence, name):
    """A supplied check needs an explicit boolean result and provenance."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get("passed"), bool) or not isinstance(evidence.get("source"), str) or not evidence["source"].strip():
        return f"MISSING_{name}_CHECK"
    return None if evidence["passed"] else f"{name}_CHECK_FAILURE"


def diagnose(
    block_frames: Dict[str, pd.DataFrame],
    diagnostics: DiagnosticConfig,
    ru_ohm: Optional[float] = None,
    correction_enabled: bool = False,
    simplified_model: bool = False,
    reverse_frames: Optional[Dict[str, pd.DataFrame]] = None,
    ocp_opening=None,
    ocp_closing=None,
    attempt_id: str = "",
    opening_check=None,
    closing_check=None,
    require_release_evidence: bool = True,
    reverse_required: bool = False,
    point_timeout_s: float = 60.0,
) -> DiagnosticResult:
    blocks = {}
    reasons = []
    validation_errors = {}
    if reverse_frames is not None and not isinstance(reverse_frames, dict):
        raise ValueError("reverse_frames must be a dictionary")
    if not isinstance(block_frames, dict):
        raise ValueError("block_frames must be a dictionary")
    for name in ("A1", "B", "A2"):
        frame = block_frames.get(name)
        if frame is None or isinstance(frame, pd.DataFrame) and frame.empty:
            reasons.append("MISSING_DATA")
            continue
        try:
            blocks[name] = analyze_block(name, frame, diagnostics, point_timeout_s)
        except (KeyError, TypeError, ValueError) as exc:
            reasons.append("INCOMPATIBLE_SAMPLING" if "uniform sample" in str(exc) else "INVALID_DATA")
            validation_errors[name] = str(exc)
    opening_evidence = _ocp_evidence(ocp_opening, diagnostics.ocp_observation_s)
    closing_evidence = _ocp_evidence(ocp_closing, diagnostics.ocp_observation_s)
    opening_valid, closing_valid = opening_evidence["valid"], closing_evidence["valid"]
    if require_release_evidence:
        for name, evidence in (("OPENING", opening_check), ("CLOSING", closing_check)):
            code = _check_code(evidence, name)
            if code:
                reasons.append(code)
        if not opening_valid or not closing_valid:
            reasons.append("MISSING_OCP_EVIDENCE")
    for metrics in blocks.values():
        reasons.extend(metrics.reason_codes)
    a1 = blocks.get("A1")
    b = blocks.get("B")
    a2 = blocks.get("A2")
    if a1 and a2 and _relative_difference(a1.resistance.value_ohm, a2.resistance.value_ohm) > diagnostics.max_relative_consistency_error:
        reasons.append("REPEATABILITY_FAILURE")
    if a1 and b and a1.slope_v_per_a and b.slope_v_per_a:
        if _relative_difference(a1.slope_v_per_a, b.slope_v_per_a) > diagnostics.max_relative_consistency_error:
            reasons.append("SLOPE_INCONSISTENCY")
    # Only a supplied known-reference probe can trigger this check. Sample
    # resistance truth is never a diagnostic input.
    for check in (opening_check, closing_check):
        if not isinstance(check, dict) or "expected_current_a" not in check:
            continue
        expected = check.get("expected_current_a")
        measured_current = check.get("measured_current_a")
        measured_voltage = check.get("measured_voltage_v")
        requested_voltage = check.get("requested_voltage_v")
        values = (expected, measured_current, measured_voltage, requested_voltage)
        if any(isinstance(v, (bool, np.bool_)) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in values) or expected <= 0:
            reasons.append("INVALID_REFERENCE_CHECK")
        elif abs(measured_current - expected) / expected > diagnostics.replay_relative_error or abs(measured_voltage - requested_voltage) > diagnostics.min_signal_amplitude_v:
            reasons.append("ACQUISITION_CHAIN_SUSPECT")
    reverse_consistent = None
    reverse_blocks = {}
    if reverse_required or reverse_frames is not None:
        for name in ("A1", "B", "A2"):
            frame = (reverse_frames or {}).get(name)
            if frame is None or isinstance(frame, pd.DataFrame) and frame.empty:
                reasons.append("MISSING_REVERSE_DATA")
                continue
            try:
                reverse_blocks[name] = analyze_block(f"{name}_REVERSE", frame, diagnostics, point_timeout_s)
                reasons.extend(reverse_blocks[name].reason_codes)
            except (KeyError, TypeError, ValueError) as exc:
                reasons.append("INCOMPATIBLE_SAMPLING" if "uniform sample" in str(exc) else "INVALID_DATA")
                validation_errors[name + "_REVERSE"] = str(exc)
        comparisons = []
        for name in ("A1", "B", "A2"):
            if name in blocks and name in reverse_blocks:
                comparisons.append(_relative_difference(blocks[name].resistance.value_ohm, reverse_blocks[name].resistance.value_ohm))
        reverse_consistent = bool(comparisons) and max(comparisons) <= diagnostics.max_relative_consistency_error
        if not reverse_consistent:
            reasons.append("SLOPE_INCONSISTENCY")
    ocp = _ocp_metrics(ocp_opening if opening_valid else None, ocp_closing if closing_valid else None, diagnostics)
    if not ocp["drift_pass"]:
        reasons.append("OCP_DRIFT")
    if not ocp["recovery_pass"]:
        reasons.append("OCP_RECOVERY_FAILURE")
    rapp = a1.resistance.value_ohm if a1 and a1.resistance.valid else None
    correction = correct_rp(a1.resistance, ru_ohm, correction_enabled, simplified_model,
                            settled=a1.settling.settled, measurement_valid=not reasons) if a1 else None
    available_uncertainties = [item.uncertainty_ohm for item in (*blocks.values(), *reverse_blocks.values()) if item.uncertainty_ohm is not None and np.isfinite(item.uncertainty_ohm)]
    uncertainty = max(available_uncertainties, default=None)
    unique_reasons = tuple(dict.fromkeys(reasons))
    replay_required = replay_allowed(unique_reasons)
    recoverable = recovery_allowed(unique_reasons)
    inconclusive = bool(set(unique_reasons) & {"EXCESSIVE_UNCERTAINTY", "INSUFFICIENT_SIGNAL", "LOW_SNR", "AMBIGUOUS_DIAGNOSTICS"})
    measured = {
        "validation_errors": validation_errors,
        "attempt_id": attempt_id,
        "Rapp": rapp,
        "uncertainty_ohm": uncertainty,
        "block_status": {name: block.status for name, block in blocks.items()},
        "release_evidence_required": require_release_evidence,
        "opening_check": opening_check,
        "closing_check": closing_check,
        "ocp_evidence": {"opening": opening_evidence, "closing": closing_evidence},
        "ocp_opening_supplied": opening_valid,
        "ocp_closing_supplied": closing_valid,
        "reverse_blocks": {name: block.to_dict() for name, block in reverse_blocks.items()},
        "resistance_correction": correction.to_dict() if correction else None,
    }
    thresholds = {**asdict(diagnostics), "point_timeout_s": point_timeout_s}
    return DiagnosticResult(
        not unique_reasons, unique_reasons, blocks, replay_required, recoverable,
        rapp, correction.value_ohm if correction and correction.valid else None, uncertainty,
        inconclusive, measured, thresholds, ocp, reverse_consistent, reverse_blocks,
    )
