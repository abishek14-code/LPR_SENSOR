"""Deterministic, measurement-only decision policy and integration boundary.

Synthetic truth, scenario names, and filenames are never policy inputs.
Legacy adapters still raise ValueError. evaluate_measurement is the structured
boundary for expected input failures; unexpected errors propagate to its caller.
"""
from dataclasses import dataclass, field, asdict
from collections.abc import Mapping
from typing import Optional
import math

from .config import AppConfig
from .io_utils import json_safe
from .lpr_analysis import ResistanceResult, correct_rp
from .validation import finite_number, integer, boolean


RECOVERY_CODES = {"NOT_SETTLED", "OCP_RECOVERY_FAILURE", "REPEATABILITY_FAILURE", "SLOPE_INCONSISTENCY"}
TRANSIENT_DEPENDENT_CODES = {"INVALID_FIT", "EXCESSIVE_UNCERTAINTY"}
VERIFICATION_CODES = {"ACQUISITION_CHAIN_SUSPECT"}
FATAL_CODES = {"TIMEOUT", "OPENING_CHECK_FAILURE", "CLOSING_CHECK_FAILURE", "REFERENCE_REPLAY_FAILURE",
               "SIMULATOR_FAILURE", "INSTRUMENT_FAULT", "INSTRUMENT_SATURATION", "OUTPUT_MISMATCH",
               "INVALID_DATA", "INVALID_INPUT", "INVALID_CONFIGURATION", "INVALID_OPERATIONAL_CONTEXT",
               "INVALID_REFERENCE_CHECK", "INCOMPATIBLE_SAMPLING"}


def fatal(code):
    return code in FATAL_CODES or code.startswith("MISSING_")


def recovery_allowed(codes):
    codes = set(codes)
    return bool(codes & RECOVERY_CODES) and not any(fatal(code) for code in codes) and codes <= RECOVERY_CODES | TRANSIENT_DEPENDENT_CODES


def replay_allowed(codes):
    codes = set(codes)
    return bool(codes) and not any(fatal(code) for code in codes) and (recovery_allowed(codes) or codes <= VERIFICATION_CODES)


@dataclass(frozen=True)
class EvaluationContext:
    source_type: str = "synthetic"
    simulation_status: Optional[str] = None
    fault_flags: dict = field(default_factory=dict)
    voltage_limit_v: Optional[float] = None
    current_limit_a: Optional[float] = None
    expected_identity: dict = field(default_factory=dict)
    output_identity: dict = field(default_factory=dict)
    acquisition_complete: bool = True
    observation_elapsed_s: float = 0.0
    retries_used: int = 0
    interrupted: bool = False
    reference_failed: bool = False
    correction_enabled: bool = False
    simplified_model: bool = False
    ru_ohm: Optional[float] = None
    rapp_standard_uncertainty_ohm: Optional[float] = None
    ru_standard_uncertainty_ohm: Optional[float] = None
    independent_uncertainties: bool = False
    calibration_source: Optional[str] = None

    def __post_init__(self):
        if self.source_type not in {"synthetic", "ltspice", "multisim", "recorded"}:
            raise ValueError("unsupported source_type")
        if self.simulation_status not in {None, "success", "failed", "pending", "unavailable"}:
            raise ValueError("unsupported simulation_status")
        for name in ("acquisition_complete", "interrupted", "reference_failed", "correction_enabled", "simplified_model", "independent_uncertainties"):
            boolean(getattr(self, name), name)
        integer(self.retries_used, "retries_used")
        finite_number(self.observation_elapsed_s, "observation_elapsed_s", nonnegative=True)
        for name in ("voltage_limit_v", "current_limit_a", "ru_ohm"):
            if getattr(self, name) is not None:
                finite_number(getattr(self, name), name, positive=True)
        for name in ("rapp_standard_uncertainty_ohm", "ru_standard_uncertainty_ohm"):
            if getattr(self, name) is not None:
                finite_number(getattr(self, name), name, nonnegative=True)
        for name in ("fault_flags", "expected_identity", "output_identity"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be a dictionary")
        if any(not isinstance(k, str) or type(v) is not bool for k, v in self.fault_flags.items()):
            raise ValueError("fault_flags must map indicator names to booleans")
        for name in ("expected_identity", "output_identity"):
            if any(not isinstance(k, str) or not isinstance(v, str) or not v.strip() for k, v in getattr(self, name).items()):
                raise ValueError(f"{name} must map names to nonempty string identities or hashes")
        if self.calibration_source is not None and (not isinstance(self.calibration_source, str) or not self.calibration_source.strip()):
            raise ValueError("calibration_source must be a nonempty provenance string")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    observed: object
    expected: object
    reason_code: Optional[str] = None
    explanation: str = ""
    recoverable: bool = False
    recommended_action: str = "No action required."
    mandatory: bool = True


@dataclass(frozen=True)
class DecisionResult:
    decision: str
    primary_reason: str
    reason_codes: tuple
    checks: tuple
    measurements: dict
    thresholds: dict
    warnings: tuple
    recommended_action: str
    retry_plan: dict
    uncertainty: dict
    rp_estimate: Optional[float]
    corrosion_estimate: Optional[float]
    result_status: str
    estimate_status: dict

    def to_dict(self):
        return json_safe(asdict(self))


def _check(name, passed, observed, expected, code=None, recoverable=False, action=None):
    return CheckResult(name, bool(passed), observed, expected, None if passed else code,
                       "Expected condition satisfied." if passed else f"{name} did not satisfy the expected condition.",
                       bool(not passed and recoverable), action or ("No action required." if passed else "Verify the reference and observe longer or repeat within the configured budget." if recoverable else "Correct the reported condition before submitting another measurement."))


def diagnostic_checks(result):
    """Expose existing estimator evidence without recomputing a physical model."""
    from .diagnostics import _relative_difference, _check_code
    checks = []
    thresholds = result.thresholds or {}
    codes = set(result.reason_codes)
    recoverable = recovery_allowed(codes)
    blocks = dict(result.blocks)
    reverse = (result.measured_values or {}).get("reverse_blocks", {})
    all_blocks = {**blocks, **{name + "_REVERSE": block for name, block in (result.reverse_blocks or {}).items()}}
    for name, block in all_blocks.items():
        checks.append(_check(name + ".point_count", len(block.point_sample_counts) >= 3, len(block.point_sample_counts), {"minimum": 3}, "MISSING_DATA"))
        for index, count in enumerate(block.point_sample_counts):
            checks.append(_check(f"{name}.point_{index}.samples", count >= thresholds["min_samples_per_point"], count, {"minimum": thresholds["min_samples_per_point"]}, "MISSING_DATA"))
        checks.extend([
            _check(f"{name}.positive_resistance", block.resistance.valid, block.resistance.value_ohm, "> 0 ohm", "INVALID_FIT", recoverable),
            _check(f"{name}.r_squared", block.r_squared is not None and block.r_squared >= thresholds["min_r_squared"], block.r_squared, {"minimum": thresholds["min_r_squared"]}, "INVALID_FIT", recoverable),
            _check(f"{name}.signal_amplitude", block.signal_amplitude_v >= thresholds["min_signal_amplitude_v"], block.signal_amplitude_v, {"minimum_v": thresholds["min_signal_amplitude_v"]}, "INSUFFICIENT_SIGNAL"),
            _check(f"{name}.snr", block.snr_db >= thresholds["min_snr_db"], {"db": block.snr_db, "interpretation": block.to_dict()["snr_interpretation"]}, {"minimum_db": thresholds["min_snr_db"]}, "LOW_SNR"),
            _check(f"{name}.uncertainty", block.uncertainty_voltage_v is not None and block.signal_span_v >= thresholds["min_signal_to_uncertainty"] * block.uncertainty_voltage_v, {"signal_span_v": block.signal_span_v, "uncertainty_voltage_v": block.uncertainty_voltage_v, "heuristic_ohm": block.uncertainty_ohm}, {"minimum_signal_to_uncertainty": thresholds["min_signal_to_uncertainty"]}, "EXCESSIVE_UNCERTAINTY", recoverable),
            _check(f"{name}.noise", block.noise_to_signal is not None and block.noise_to_signal <= thresholds["max_noise_to_signal"], {"noise_std_a": block.noise_std_a, "noise_to_signal": block.noise_to_signal}, {"maximum_noise_to_signal": thresholds["max_noise_to_signal"]}, "EXCESSIVE_NOISE"),
        ])
        for index, point in enumerate(block.point_settling):
            prefix = f"{name}.point_{index}"
            checks.extend([
                _check(prefix + ".observation", point.dwell_s + 1e-9 >= thresholds["settling_observation_s"], point.dwell_s, {"minimum_s": thresholds["settling_observation_s"]}, "NOT_SETTLED", True, "Observe longer within the point timeout, or request a bounded repeat."),
                _check(prefix + ".timeout", not point.timeout, point.dwell_s, {"maximum_s": thresholds["point_timeout_s"]}, "TIMEOUT"),
                _check(prefix + ".settling_slope", point.scale_a is not None and abs(point.final_slope) <= thresholds["settling_relative_slope"] * point.scale_a, {"slope_a_per_s": point.final_slope, "scale_a": point.scale_a}, {"maximum_a_per_s": None if point.scale_a is None else thresholds["settling_relative_slope"] * point.scale_a}, "NOT_SETTLED", True),
                _check(prefix + ".settling_variation", point.variation <= thresholds["max_settling_variation"], point.variation, {"maximum_fraction": thresholds["max_settling_variation"]}, "NOT_SETTLED", True),
                _check(prefix + ".window_difference", point.scale_a is not None and point.window_difference is not None and point.window_difference <= thresholds["settling_window_difference_fraction"] * point.scale_a, {"difference_a": point.window_difference, "window_means_a": point.window_means}, {"maximum_a": None if point.scale_a is None else thresholds["settling_window_difference_fraction"] * point.scale_a}, "NOT_SETTLED", True),
            ])
    for label, first, second, code in (("a1_a2_consistency", "A1", "A2", "REPEATABILITY_FAILURE"), ("a1_b_consistency", "A1", "B", "SLOPE_INCONSISTENCY")):
        if first in blocks and second in blocks:
            observed = _relative_difference(blocks[first].resistance.value_ohm, blocks[second].resistance.value_ohm)
            checks.append(_check(label, observed <= thresholds["max_relative_consistency_error"], observed, {"maximum_fraction": thresholds["max_relative_consistency_error"]}, code, True))
    if result.forward_reverse_consistent is not None:
        comparisons = {name: _relative_difference(blocks[name].resistance.value_ohm, block["resistance"]["value_ohm"]) for name, block in reverse.items() if name in blocks}
        checks.append(_check("forward_reverse_consistency", result.forward_reverse_consistent, comparisons, {"maximum_fraction": thresholds["max_relative_consistency_error"]}, "SLOPE_INCONSISTENCY", True))
    measured = result.measured_values or {}
    for phase in ("opening", "closing"):
        evidence = measured.get(phase + "_check")
        if measured.get("release_evidence_required") or evidence is not None:
            check_code = _check_code(evidence, phase.upper())
            checks.append(_check(phase + "_check", check_code is None, evidence, "explicit passing check with source provenance", check_code))
        if isinstance(evidence, dict) and "expected_current_a" in evidence and "INVALID_REFERENCE_CHECK" not in codes:
            observed = {"relative_current_error": abs(evidence["measured_current_a"] - evidence["expected_current_a"]) / evidence["expected_current_a"], "absolute_voltage_error_v": abs(evidence["measured_voltage_v"] - evidence["requested_voltage_v"])}
            checks.append(_check(phase + ".known_reference_probe", observed["relative_current_error"] <= thresholds["replay_relative_error"] and observed["absolute_voltage_error_v"] <= thresholds["min_signal_amplitude_v"], observed, {"maximum_relative_current_error": thresholds["replay_relative_error"], "maximum_voltage_error_v": thresholds["min_signal_amplitude_v"]}, "ACQUISITION_CHAIN_SUSPECT", True))
    for label, key, threshold, code in (("ocp_drift", "drift_v_per_s", "ocp_drift_v_per_s", "OCP_DRIFT"), ("ocp_recovery", "recovery_shift_v", "max_recovery_shift_v", "OCP_RECOVERY_FAILURE")):
        value = (result.ocp or {}).get(key)
        if value is not None:
            checks.append(_check(label, abs(value) <= thresholds[threshold], value, {"maximum_absolute": thresholds[threshold]}, code, code in RECOVERY_CODES))
    represented = {check.reason_code for check in checks if not check.passed}
    for code in sorted(codes - represented):
        observed = {"validation_errors": measured.get("validation_errors", {}), "context_errors": measured.get("context_errors", [])}
        expected = {"condition": code + " must be absent"}
        if code == "MISSING_DATA":
            observed = {"present_blocks": sorted(blocks), "missing_blocks": sorted({"A1", "B", "A2"} - set(blocks))}
            expected = {"required_blocks": ["A1", "B", "A2"], "minimum_samples_per_point": thresholds["min_samples_per_point"]}
        elif code == "MISSING_REVERSE_DATA":
            observed = {"present_reverse_blocks": sorted(reverse)}
            expected = {"required_reverse_blocks": ["A1", "B", "A2"]}
        elif code == "MISSING_OCP_EVIDENCE":
            observed = measured.get("ocp_evidence", {key: measured.get(key) for key in ("ocp_opening_supplied", "ocp_closing_supplied")})
            expected = {"both_valid": True, "minimum_duration_s": thresholds["ocp_observation_s"]}
        elif code == "INVALID_REFERENCE_CHECK":
            observed = {key: measured.get(key) for key in ("opening_check", "closing_check")}
            expected = "finite reference probe fields and positive expected current"
        checks.append(_check(code.lower(), False, observed, expected, code, recoverable or code in VERIFICATION_CODES))
    if "context_errors" in measured:
        checks.append(_check("acquisition_context", not measured["context_errors"], measured["context_errors"], "matching identities, configured point/excitation sequence, nonoverlapping windows and bracketing OCP", "INVALID_OPERATIONAL_CONTEXT"))
    return checks


def evaluate_diagnostics(result, config: AppConfig, *, context=None, extra_codes=(), extra_checks=()):
    """Pure policy reducer. No printing, simulation, clock reads, or retry loop."""
    context = EvaluationContext() if context is None else context
    if not isinstance(context, EvaluationContext):
        raise ValueError("context must be an EvaluationContext")
    checks = diagnostic_checks(result) if result is not None else []
    checks.extend(extra_checks)
    codes = set(extra_codes) | (set(result.reason_codes) if result is not None else set())
    if context.retries_used > config.acquisition.max_reacquisition_attempts:
        codes.add("INVALID_CONFIGURATION")
        checks.append(_check("retry_count", False, context.retries_used, {"maximum": config.acquisition.max_reacquisition_attempts}, "INVALID_CONFIGURATION"))
    if result is None and context.acquisition_complete and not context.interrupted and context.simulation_status != "pending" and not codes:
        codes.add("MISSING_DATA")
    if result is not None and result.passed and not (result.measured_values or {}).get("release_evidence_required"):
        codes.add("MISSING_RELEASE_EVIDENCE")
    if context.reference_failed:
        codes.add("REFERENCE_REPLAY_FAILURE")
    if context.simulation_status in {"failed", "unavailable"}:
        code = "SIMULATOR_FAILURE" if context.simulation_status == "failed" else "MISSING_SIMULATION_OUTPUT"
        codes.add(code)
        checks.append(_check("simulation_status", False, context.simulation_status, "success", code))
    elif context.simulation_status == "success":
        checks.append(_check("simulation_status", True, "success", "success"))
    for name, active in sorted(context.fault_flags.items()):
        checks.append(_check("instrument_fault." + name, not active, active, False, "INSTRUMENT_FAULT"))
        if active:
            codes.add("INSTRUMENT_FAULT")
    input_identity = (result.measured_values or {}).get("input_identity", {}) if result is not None else {}
    identity_consistent = all(k not in input_identity or input_identity[k] == v for k, v in context.output_identity.items())
    actual_identity = {**context.output_identity, **input_identity}
    if context.expected_identity or not identity_consistent:
        matched = identity_consistent and all(actual_identity.get(k) == v for k, v in context.expected_identity.items())
        checks.append(_check("output_identity", matched, actual_identity, context.expected_identity or context.output_identity, "OUTPUT_MISMATCH"))
        if not matched:
            codes.add("OUTPUT_MISMATCH")
    if result is not None:
        for name, peaks in (result.measured_values or {}).get("input_channel_peaks", {}).items():
            for channel, limit in (("actual_voltage_v", context.voltage_limit_v), ("current_a", context.current_limit_a)):
                if limit is not None:
                    checks.append(_check(name + "." + channel + ".saturation", peaks[channel] < limit, peaks[channel], {"absolute_limit": limit, "comparison": "strictly below"}, "INSTRUMENT_SATURATION"))
    if context.calibration_source is not None and context.rapp_standard_uncertainty_ohm is not None and result is not None and result.rapp is not None:
        fraction = context.rapp_standard_uncertainty_ohm / abs(result.rapp)
        limit = 1.0 / config.diagnostics.min_signal_to_uncertainty
        roundoff_relative = 4 * math.ulp(1.0)
        checks.append(_check("calibrated_measurement_uncertainty", fraction <= limit or math.isclose(fraction, limit, rel_tol=roundoff_relative, abs_tol=0.0),
            {"standard_uncertainty_ohm": context.rapp_standard_uncertainty_ohm, "relative_fraction": fraction},
            {"maximum_standard_fraction": limit, "roundoff_relative_tolerance": roundoff_relative}, "EXCESSIVE_UNCERTAINTY"))
    for check in checks:
        if not check.passed and check.mandatory and check.reason_code:
            codes.add(check.reason_code)
    warnings = ["Project diagnostic thresholds are provisional software policy, not validated physical limits.",
                "Corrosion-rate estimate unavailable: no verified kinetics/material/area calibration and conversion model."]
    if context.simulation_status is None and context.source_type != "synthetic":
        warnings.append("Upstream simulation execution status is unverified; supplied measurements alone cannot validate it.")
    for name in ("voltage_limit_v", "current_limit_a"):
        if getattr(context, name) is None:
            warnings.append(name + " is not supplied; instrument saturation on that channel is unverified.")
    calibration = context.calibration_source
    if calibration is None and context.rapp_standard_uncertainty_ohm is not None:
        warnings.append("Supplied standard uncertainty is not applied: calibration_source is missing.")
    if calibration is None:
        warnings.append("Measurement calibration is unavailable; acceptance verifies the declared software quality policy only.")
    budget = max(0, config.acquisition.max_reacquisition_attempts - context.retries_used)
    remaining_time = max(0.0, config.acquisition.point_timeout_s - context.observation_elapsed_s)
    has_fatal = any(fatal(code) for code in codes)
    recoverable = recovery_allowed(codes)
    verifying = bool(codes) and codes <= VERIFICATION_CODES
    pending = not context.acquisition_complete or context.simulation_status == "pending"
    if context.interrupted:
        codes.add("RESET")
    if has_fatal:
        decision, status, action = "REJECT", "UNUSABLE", "Resolve the fatal fault or invalid evidence; do not replay or retry this attempt."
    elif context.interrupted:
        decision, status, action = "WAIT_OR_REPEAT", "INTERRUPTED", "Start a fresh controller run explicitly; the interrupted execution cannot resume or release."
    elif pending:
        if remaining_time > 0:
            codes.add("OBSERVATION_PENDING")
            decision, status, action = "WAIT_OR_REPEAT", "PENDING", "Continue observation only within the remaining point timeout."
        else:
            codes.add("TIMEOUT")
            decision, status, action = "REJECT", "UNUSABLE", "Observation timeout exhausted; stop this acquisition."
    elif codes and not (recoverable or verifying):
        decision, status, action = "REJECT", "UNUSABLE", "Correct the failed mandatory criteria before starting a new measurement."
    elif recoverable and budget == 0:
        codes.add("RETRY_LIMIT_EXHAUSTED")
        decision, status, action = "REJECT", "UNUSABLE", "Stop: the configured reacquisition budget is exhausted."
    elif recoverable or verifying:
        decision, status, action = "WAIT_OR_REPEAT", "RECOVERABLE", "Verify the acquisition chain using an independent reference; repeat only after it passes and within the retry budget."
    else:
        decision, status, action = "ACCEPT", "QUALITY_VERIFIED", "Use the accepted measurement for eligible calculations; retain the reported limitations."
    priority = lambda code: (0 if code in {"INSTRUMENT_FAULT", "OPENING_CHECK_FAILURE", "CLOSING_CHECK_FAILURE", "REFERENCE_REPLAY_FAILURE", "SIMULATOR_FAILURE"} else 1 if fatal(code) else 2 if code == "RETRY_LIMIT_EXHAUSTED" else 3 if code not in RECOVERY_CODES | TRANSIENT_DEPENDENT_CODES | VERIFICATION_CODES | {"RESET", "OBSERVATION_PENDING"} else 4, code)
    ordered = tuple(sorted(codes, key=priority))
    represented = {check.reason_code for check in checks if not check.passed}
    for code in ordered:
        if code not in represented:
            checks.append(_check(code.lower(), False, {"retries_used": context.retries_used, "elapsed_s": context.observation_elapsed_s}, {"max_retries": config.acquisition.max_reacquisition_attempts, "point_timeout_s": config.acquisition.point_timeout_s}, code, decision == "WAIT_OR_REPEAT", action))
    # A recoverable local check cannot authorize recovery of a fatal overall result.
    if has_fatal:
        checks = [CheckResult(**{**asdict(check), "recoverable": False}) if not check.passed else check for check in checks]
    rapp = result.rapp if result is not None else None
    settled = bool(result is not None and result.blocks and all(block.settling.settled for block in result.blocks.values()))
    correction = correct_rp(ResistanceResult(rapp, "apparent"), context.ru_ohm, context.correction_enabled,
                            context.simplified_model, settled=settled, measurement_valid=decision == "ACCEPT")
    heuristic = result.uncertainty_ohm if result is not None else None
    measurement_uncertainty = context.rapp_standard_uncertainty_ohm if calibration is not None else None
    rp_uncertainty = None
    if correction.valid and measurement_uncertainty is not None and context.ru_standard_uncertainty_ohm is not None and context.independent_uncertainties:
        rp_uncertainty = math.hypot(measurement_uncertainty, context.ru_standard_uncertainty_ohm)
    uncertainty = {
        "measurement": {"status": "SUPPLIED_STANDARD_UNCERTAINTY" if measurement_uncertainty is not None else "HEURISTIC_ONLY" if heuristic is not None else "UNAVAILABLE", "standard_uncertainty_ohm": measurement_uncertainty, "heuristic_ohm": heuristic, "calibration_source": calibration, "method": "supplied standard uncertainty" if measurement_uncertainty is not None else "max(multiplier * OLS slope standard error, fraction * Rapp); not a confidence interval"},
        "parameter": {"status": "SUPPLIED" if context.ru_standard_uncertainty_ohm is not None else "UNAVAILABLE", "ru_standard_uncertainty_ohm": context.ru_standard_uncertainty_ohm},
        "numerical": {"status": "UNAVAILABLE", "reason": "No numerical error bound supplied or calculated."},
        "model": {"status": "UNVERIFIED", "reason": "Simplified model has no physical validation or quantified discrepancy."},
        "rp_standard_uncertainty_ohm": rp_uncertainty,
        "propagation_assumptions": ["Independent Rapp and Ru standard uncertainties; variance addition for subtraction."] if rp_uncertainty is not None else [],
        "confidence_interval": None,
    }
    rp_reason = "AVAILABLE_UNDER_DECLARED_SIMPLIFIED_MODEL" if correction.valid else "Requires ACCEPT, settled data, explicit series-Ru model and correction enablement, and known positive Ru below Rapp."
    if correction.valid and rp_uncertainty is None:
        warnings.append("Rp uncertainty is unavailable: both calibrated standard uncertainties and their independence are required for implemented propagation.")
    measurements = dict(result.measured_values or {}) if result is not None else {}
    measurements.update(Rapp_ohm=rapp, Ru_ohm=context.ru_ohm, source_type=context.source_type)
    thresholds = dict(result.thresholds or {}) if result is not None else asdict(config.diagnostics)
    thresholds.update(voltage_limit_v=context.voltage_limit_v, current_limit_a=context.current_limit_a, point_timeout_s=config.acquisition.point_timeout_s, max_reacquisition_attempts=config.acquisition.max_reacquisition_attempts)
    return DecisionResult(decision, ordered[0] if ordered else "ALL_MANDATORY_CHECKS_PASSED", ordered, tuple(sorted(checks, key=lambda check: (check.name, check.reason_code or ""))), measurements, thresholds, tuple(warnings), action,
                          {"retries_used": context.retries_used, "max_retries": config.acquisition.max_reacquisition_attempts, "remaining_retries": budget, "repeat_permitted": decision == "WAIT_OR_REPEAT" and recoverable and budget > 0 and not context.interrupted,
                           "reference_required": decision == "WAIT_OR_REPEAT" and (recoverable or verifying) and not pending and not context.interrupted,
                           "remaining_observation_s": remaining_time if pending else 0.0, "automatic_retry": False},
                          uncertainty, correction.value_ohm, None, status,
                          {"rp": rp_reason, "corrosion": "UNAVAILABLE: verified electrochemical/material/area calibration and conversion model missing."})


def evaluate_measurement(frames, config=None, *, context=None, opening_check=None, closing_check=None,
                         ocp_opening=None, ocp_closing=None, reverse_frames=None):
    """Safe standalone API, retaining the existing strict recorded-data contract.

    context may be EvaluationContext or a dictionary of its declared fields.
    An incomplete acquisition carries only validated partial data and never
    authorizes release. Evaluation is deterministic for its explicit inputs.
    """
    config = config if config is not None else AppConfig()
    try:
        if not isinstance(config, AppConfig):
            raise ValueError("config must be an AppConfig")
        context = EvaluationContext(**context) if isinstance(context, dict) else EvaluationContext() if context is None else context
        if not isinstance(context, EvaluationContext):
            raise ValueError("context must be EvaluationContext or its field dictionary")
    except (ValueError, TypeError) as exc:
        check = _check("configuration", False, str(exc), "valid typed P3 configuration/context", "INVALID_CONFIGURATION")
        return evaluate_diagnostics(None, AppConfig(), extra_codes=("INVALID_CONFIGURATION",), extra_checks=(check,))
    extra_checks = []
    try:
        if not isinstance(frames, Mapping) or (reverse_frames is not None and not isinstance(reverse_frames, Mapping)):
            raise ValueError("measurement groups must map A1/B/A2 names to DataFrames")
        if set(frames) - {"A1", "B", "A2"} or set(reverse_frames or {}) - {"A1", "B", "A2"}:
            raise ValueError("unsupported measurement block names")
        from .simulator_adapter import normalize_trace, TraceAdapterConfig
        from .validation import validate_analysis_frame
        groups = []
        for direction, group in enumerate((frames, reverse_frames or {})):
            normalized = {}
            for name, frame in group.items():
                data = normalize_trace(frame, TraceAdapterConfig(source_type=context.source_type))
                validate_analysis_frame(data)
                if not context.acquisition_complete or context.simulation_status == "pending":
                    required = {"point_index", "drive_enabled", "reverse", "requested_voltage_v"}
                    if not required <= set(data):
                        raise ValueError(f"{name}: missing partial acquisition metadata: {sorted(required - set(data))}")
                    if not data.drive_enabled.all() or not data.reverse.eq(bool(direction)).all():
                        raise ValueError(f"{name}: invalid partial drive/direction context")
                    if data.attrs.get("timed_out") is True:
                        extra_checks.append(_check(name + ".timeout", False, True, False, "TIMEOUT"))
                    for _, point in data.groupby("point_index", sort=False):
                        intervals = point.time_s.diff().dropna().to_numpy(dtype=float)
                        if len(intervals) >= 2:
                            import numpy as np
                            step = float(np.median(intervals))
                            if not np.allclose(intervals[:-1], step, rtol=1e-5, atol=1e-9) or intervals[-1] > step * (1 + 1e-5) + 1e-9:
                                raise ValueError("partial acquisition requires uniform sample intervals; adaptive sampling is unsupported")
                for key, value in context.output_identity.items():
                    if key in {"run_id", "trial_id", "attempt_id", "seed"} and key in data and str(data[key].iloc[0]) != value:
                        extra_checks.append(_check("output_identity." + key, False, str(data[key].iloc[0]), value, "OUTPUT_MISMATCH"))
                for channel, limit in (("actual_voltage_v", context.voltage_limit_v), ("current_a", context.current_limit_a)):
                    if limit is not None and (not context.acquisition_complete or context.simulation_status == "pending"):
                        peak = float(data[channel].abs().max())
                        extra_checks.append(_check(name + "." + channel + ".saturation", peak < limit, peak, {"absolute_limit": limit, "comparison": "strictly below"}, "INSTRUMENT_SATURATION"))
                normalized[name] = data
            groups.append(normalized)
        if not context.acquisition_complete or context.simulation_status == "pending":
            # Validate every supplied sample, including instrument faults, but
            # missing future windows are expected until the explicit deadline.
            for phase, evidence in (("opening", opening_check), ("closing", closing_check)):
                if evidence is not None:
                    from .diagnostics import _check_code
                    code = _check_code(evidence, phase.upper())
                    if code:
                        extra_checks.append(_check(phase + "_check", False, evidence, "passing supplied check with provenance", code))
            return evaluate_diagnostics(None, config, context=context, extra_checks=extra_checks)
        from .controller import process_recorded_frames
        record = process_recorded_frames(config, "supplied_measurement", groups[0], reverse_frames=groups[1] or None,
                                         source_type=context.source_type, opening_check=opening_check, closing_check=closing_check,
                                         ocp_opening=ocp_opening, ocp_closing=ocp_closing)
        return evaluate_diagnostics(record.diagnostics, config, context=context, extra_checks=extra_checks)
    except ValueError as exc:
        message = str(exc)
        code = "INCOMPATIBLE_SAMPLING" if "uniform sample" in message else "INVALID_INPUT"
        check = _check("input_validation", False, message, "canonical finite, correctly sequenced SI input", code)
        return evaluate_diagnostics(None, config, context=context, extra_codes=(code,), extra_checks=extra_checks + [check])


def reject_input(error, config=None, *, source_type="recorded"):
    """Structured rejection for an expected adapter/file validation failure."""
    config = config if config is not None else AppConfig()
    check = _check("input_validation", False, str(error), "readable canonical input and matching operational evidence", "INVALID_INPUT")
    return evaluate_diagnostics(None, config, context=EvaluationContext(source_type=source_type),
                                extra_codes=("INVALID_INPUT",), extra_checks=(check,))
