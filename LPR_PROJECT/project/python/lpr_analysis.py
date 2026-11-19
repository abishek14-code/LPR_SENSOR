from dataclasses import dataclass, asdict
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
from .validation import validate_analysis_frame, finite_number, boolean


ESTIMATION_METHODS = {
    "apparent",
    "Ru_corrected",
    "analytical",
    "model_truth",
    "fitted_simulation",
}


@dataclass
class ResistanceResult:
    value_ohm: Optional[float]
    estimation_method: str
    valid: bool = True
    assumptions: Tuple[str, ...] = ()

    def __post_init__(self):
        if self.estimation_method not in ESTIMATION_METHODS:
            raise ValueError("unknown resistance estimation method")
        if self.value_ohm is None or not np.isfinite(self.value_ohm) or self.value_ohm <= 0:
            self.value_ohm = None
            self.valid = False

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SettlingResult:
    settled: bool
    settling_time_s: Optional[float]
    final_slope: float
    variation: float
    window_means: Tuple[float, ...] = ()
    window_difference: Optional[float] = None
    uncertainty_a: Optional[float] = None
    dwell_s: float = 0.0
    timeout: bool = False
    scale_a: Optional[float] = None


@dataclass
class BlockMetrics:
    block_id: str
    status: str
    reason_codes: Tuple[str, ...]
    resistance: ResistanceResult
    slope_v_per_a: Optional[float]
    r_squared: Optional[float]
    signal_amplitude_v: float
    noise_std_a: float
    snr_db: float
    uncertainty_ohm: Optional[float]
    settling: SettlingResult
    signal_span_v: float = 0.0
    uncertainty_voltage_v: Optional[float] = None
    point_settling: Tuple[SettlingResult, ...] = ()
    noise_to_signal: Optional[float] = None
    point_sample_counts: Tuple[int, ...] = ()

    def to_dict(self) -> Dict:
        result = asdict(self)
        result["resistance"] = self.resistance.to_dict()
        result["snr_interpretation"] = "noiseless" if self.snr_db == float("inf") else "finite" if np.isfinite(self.snr_db) else "unavailable"
        return result


def _arrays(voltage: Iterable[float], current: Iterable[float]) -> Tuple[np.ndarray, np.ndarray]:
    voltage, current = tuple(voltage), tuple(current)
    for values, name in ((voltage, "voltage"), (current, "current")):
        for value in values:
            finite_number(value, name)
    v = np.asarray(voltage, dtype=float)
    i = np.asarray(current, dtype=float)
    if v.ndim != 1 or i.ndim != 1:
        raise ValueError("measurement arrays must be one-dimensional")
    if v.size != i.size or v.size < 2:
        raise ValueError("voltage and current require equal arrays with at least two samples")
    if not np.all(np.isfinite(v)) or not np.all(np.isfinite(i)):
        raise ValueError("voltage and current must be finite")
    return v, i


def fit_voltage_current(voltage: Iterable[float], current: Iterable[float]) -> Dict[str, float]:
    v, i = _arrays(voltage, current)
    if np.ptp(v) <= np.finfo(float).eps:
        raise ValueError("voltage has insufficient variation")
    design = np.column_stack((i, np.ones(i.size)))
    slope, intercept = np.linalg.lstsq(design, v, rcond=None)[0]
    predicted = slope * i + intercept
    residual = v - predicted
    ss_res = float(np.sum(residual ** 2))
    ss_tot = float(np.sum((v - np.mean(v)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dof = max(1, i.size - 2)
    residual_std = float(np.sqrt(ss_res / dof))
    sxx = float(np.sum((i - np.mean(i)) ** 2))
    slope_std_error = residual_std / np.sqrt(sxx) if sxx > 0 else float("inf")
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "r_squared": float(r_squared),
        "residual_std": residual_std,
        "slope_std_error": float(slope_std_error),
    }


def estimate_rapp(voltage: Iterable[float], current: Iterable[float]) -> ResistanceResult:
    fit = fit_voltage_current(voltage, current)
    if fit["slope"] <= np.finfo(float).eps:
        return ResistanceResult(None, "apparent", valid=False)
    # The fitted relation is E = intercept + Rapp * I, so the slope itself
    # has units of ohms. Do not invert it again.
    return ResistanceResult(fit["slope"], "apparent")


def correct_rp(rapp: ResistanceResult, ru_ohm: Optional[float], enabled: bool, simplified_model: bool, *, settled=False, measurement_valid=False) -> ResistanceResult:
    for name, value in (("enabled", enabled), ("simplified_model", simplified_model), ("settled", settled), ("measurement_valid", measurement_valid)):
        boolean(value, name)
    if ru_ohm is not None:
        try:
            finite_number(ru_ohm, "ru_ohm", positive=True)
        except ValueError:
            return ResistanceResult(None, "Ru_corrected", valid=False)
    if (not enabled or not simplified_model or not settled or not measurement_valid or not rapp.valid
            or rapp.value_ohm is None or ru_ohm is None or not np.isfinite(ru_ohm) or ru_ohm <= 0):
        return ResistanceResult(None, "Ru_corrected", valid=False)
    value = rapp.value_ohm - ru_ohm
    if value <= 0:
        return ResistanceResult(None, "Ru_corrected", valid=False)
    return ResistanceResult(
        value,
        "Ru_corrected",
        assumptions=("simplified series-Ru model", "known finite Ru", "settled valid measurement"),
    )


def signal_amplitude(signal: Iterable[float]) -> float:
    values = np.asarray(tuple(signal), dtype=float)
    return float(np.ptp(values) / 2.0) if values.size else 0.0


def noise_std(signal: Iterable[float]) -> float:
    values = np.asarray(tuple(signal), dtype=float)
    return float(np.std(values, ddof=1)) if values.size > 1 else float("inf")


def snr_db(amplitude: float, noise: float) -> float:
    if amplitude <= 0 or not np.isfinite(amplitude) or noise < 0 or not np.isfinite(noise):
        return float("-inf")
    if noise == 0:
        return float("inf")
    return float(20.0 * np.log10(amplitude / noise))


def estimate_settling(
    time_s: Iterable[float],
    current_a: Iterable[float],
    relative_slope: float = 0.02,
    window_s: Optional[float] = None,
    minimum_observation_s: float = 0.0,
    window_difference_fraction: float = 0.05,
    timeout_s: Optional[float] = None,
    max_variation: float = 0.25,
) -> SettlingResult:
    for name, value in (("relative_slope", relative_slope), ("minimum_observation_s", minimum_observation_s), ("window_difference_fraction", window_difference_fraction), ("max_variation", max_variation)):
        finite_number(value, name, nonnegative=True)
    for name, value in (("window_s", window_s), ("timeout_s", timeout_s)):
        if value is not None:
            finite_number(value, name, positive=True)
    t, y = _arrays(time_s, current_a)
    if np.any(t < 0):
        raise ValueError("time must be nonnegative")
    if np.any(np.diff(t) <= 0):
        raise ValueError("time must be strictly increasing")
    if y.size < 4:
        return SettlingResult(False, None, float("inf"), float("inf"), dwell_s=0.0)
    dt = float(np.median(np.diff(t)))
    intervals = np.diff(t)
    if not np.allclose(intervals[:-1], dt, rtol=1e-5, atol=1e-9) or intervals[-1] > dt * (1 + 1e-5) + 1e-9:
        raise ValueError(f"settling requires uniform sample intervals (except a shorter final step); observed interval min/max={float(np.min(intervals))}/{float(np.max(intervals))} s, expected step={dt} s")
    dwell_s = float(t[-1] - t[0] + dt)
    if timeout_s is not None and dwell_s > timeout_s + 1e-8:
        return SettlingResult(False, None, float("inf"), float("inf"), dwell_s=dwell_s, timeout=True)
    if dwell_s + 1e-9 < minimum_observation_s:
        return SettlingResult(False, None, float("inf"), float("inf"), dwell_s=dwell_s)
    if window_s is None:
        window = max(2, y.size // 4)
    else:
        window = max(2, min(y.size, int(round(window_s / dt))))
    final_t = t[-window:]
    final_y = y[-window:]
    if y.size < 2 * window:
        return SettlingResult(False, None, float("inf"), float("inf"), dwell_s=dwell_s)
    previous_y = y[-2 * window:-window]
    slope = float(np.polyfit(final_t, final_y, 1)[0])
    scale = max(abs(float(np.mean(final_y))), float(np.ptp(y)), 1e-12)
    variation = float(np.ptp(final_y) / scale)
    window_means = (float(np.mean(previous_y)), float(np.mean(final_y)))
    window_difference = abs(window_means[1] - window_means[0])
    settled = (
        abs(slope) <= relative_slope * scale
        and variation <= max_variation
        and window_difference <= window_difference_fraction * scale
    )
    uncertainty_a = float(np.std(final_y, ddof=1) / np.sqrt(max(1, len(final_y)))) if len(final_y) > 1 else None
    return SettlingResult(
        settled,
        float(final_t[0]) if settled else None,
        slope,
        variation,
        window_means=window_means,
        window_difference=window_difference,
        uncertainty_a=uncertainty_a,
        dwell_s=dwell_s,
        scale_a=scale,
    )


def analyze_block(block_id: str, frame, diagnostics, point_timeout_s=60.0) -> BlockMetrics:
    validate_analysis_frame(frame)
    voltage = frame["actual_voltage_v"].to_numpy(dtype=float)
    current = frame["current_a"].to_numpy(dtype=float)
    time_s = frame["time_s"].to_numpy(dtype=float)
    reasons = ["TIMEOUT"] if frame.attrs.get("timed_out", False) else []
    # Fit the settled tail of each commanded point rather than treating the
    # complete stepped waveform as one independent static trace. This keeps
    # the analysis faithful to the stateful model while still exposing
    # settling failures point-by-point.
    fit_voltage = voltage
    fit_current = current
    point_settling = []
    noise_residuals = []
    point_sample_counts = []
    if "point_index" in frame.columns:
        ids = frame["point_index"].to_numpy(dtype=float)
        if not np.isfinite(ids).all() or np.any(ids < 0) or np.any(ids % 1 != 0):
            raise ValueError("invalid point indices")
        if len(np.unique(ids)) < 3:
            reasons.append("MISSING_DATA")
        tails = []
        for _, point in frame.groupby("point_index", sort=True):
            point_v = point["actual_voltage_v"].to_numpy(dtype=float)
            point_i = point["current_a"].to_numpy(dtype=float)
            point_t = point["time_s"].to_numpy(dtype=float)
            point_sample_counts.append(len(point_i))
            if len(point_i) < diagnostics.min_samples_per_point:
                reasons.append("MISSING_DATA")
                continue
            tail_count = max(2, len(point_i) // 4)
            tails.append((float(np.mean(point_v[-tail_count:])), float(np.mean(point_i[-tail_count:]))))
            noise_residuals.extend(point_i[-tail_count:] - np.mean(point_i[-tail_count:]))
            point_settling.append(estimate_settling(
                point_t,
                point_i,
                diagnostics.settling_relative_slope,
                diagnostics.settling_window_s,
                diagnostics.settling_observation_s,
                diagnostics.settling_window_difference_fraction,
                point_timeout_s,
                diagnostics.max_settling_variation,
            ))
        if tails:
            fit_voltage = np.asarray([item[0] for item in tails], dtype=float)
            fit_current = np.asarray([item[1] for item in tails], dtype=float)
    else:
        reasons.append("MISSING_POINT_METADATA")
    try:
        fit = fit_voltage_current(fit_voltage, fit_current)
        rapp = estimate_rapp(fit_voltage, fit_current)
    except ValueError:
        return BlockMetrics(block_id, "FAIL", tuple(dict.fromkeys(reasons + ["INVALID_FIT"])), ResistanceResult(None, "apparent", False), None, None, 0.0, float("inf"), float("-inf"), None, SettlingResult(False, None, float("inf"), float("inf")), point_sample_counts=tuple(point_sample_counts))
    # Point-tail means represent excitation. Pooled within-tail residuals
    # represent stationary noise; ramps/transients have separate settling gates.
    amplitude = signal_amplitude(fit_voltage)
    noise = noise_std(noise_residuals)
    signal_current = signal_amplitude(fit_current)
    snr = snr_db(signal_current, noise)
    settling = SettlingResult(False, None, float("inf"), float("inf"))
    if point_settling:
        settling = next((item for item in point_settling if item.timeout),
                        next((item for item in point_settling if not item.settled), point_settling[-1]))
    if not settling.settled:
        reasons.append("TIMEOUT" if settling.timeout else "NOT_SETTLED")
    if amplitude < diagnostics.min_signal_amplitude_v:
        reasons.append("INSUFFICIENT_SIGNAL")
    if snr < diagnostics.min_snr_db:
        reasons.append("LOW_SNR")
    if noise > diagnostics.max_noise_to_signal * signal_current:
        reasons.append("EXCESSIVE_NOISE")
    if fit["r_squared"] < diagnostics.min_r_squared or not rapp.valid:
        reasons.append("INVALID_FIT")
    uncertainty = max(
        abs(fit.get("slope_std_error", float("inf"))) * diagnostics.uncertainty_multiplier,
        abs(rapp.value_ohm or 0.0) * diagnostics.uncertainty_fraction,
    )
    signal_span = float(np.ptp(fit_voltage))
    current_span = float(np.ptp(fit_current))
    uncertainty_voltage = uncertainty * current_span if np.isfinite(uncertainty) else float("inf")
    if signal_span < diagnostics.min_signal_to_uncertainty * uncertainty_voltage:
        reasons.append("EXCESSIVE_UNCERTAINTY")
    status = "PASS" if not reasons else "FAIL"
    return BlockMetrics(
        block_id, status, tuple(dict.fromkeys(reasons)), rapp, fit["slope"], fit["r_squared"],
        amplitude, noise, snr, uncertainty, settling, signal_span, uncertainty_voltage,
        tuple(point_settling),
        noise / signal_current if signal_current > 0 else None,
        tuple(point_sample_counts),
    )
