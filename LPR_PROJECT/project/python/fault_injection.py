from dataclasses import dataclass, replace
from copy import deepcopy
from typing import Dict

import numpy as np

from .stateful_model import StatefulPlant


@dataclass(frozen=True)
class FaultSpec:
    fault_type: str
    fault_parameters: Dict
    seed: int
    affected_signal: str
    source_attempt: str = ""

    def to_dict(self):
        return {
            "fault_type": self.fault_type,
            "fault_parameters": dict(self.fault_parameters),
            "seed": self.seed,
            "affected_signal": self.affected_signal,
            "source_attempt": self.source_attempt,
        }


def _with(plant: StatefulPlant, fault: str, affected_signal: str, **changes) -> StatefulPlant:
    if not all(np.isfinite(value) for value in changes.values()):
        raise ValueError("fault parameters must be finite")
    if any(changes.get(key, 1) <= 0 for key in ("cdl_scale", "gain", "reverse_gain", "low_signal_scale")) or any(changes.get(key, 0) < 0 for key in ("noise_std_a", "correlated_noise_a")):
        raise ValueError("fault scale/gain must be positive and noise nonnegative")
    parameters = dict(plant.fault_parameters)
    parameters.update(changes)
    parameters.update({"fault_type": fault, "affected_signal": affected_signal})
    result = replace(plant, fault=fault, fault_parameters=parameters, **changes)
    result._rng = deepcopy(plant._rng)
    result._correlated_noise_state = plant._correlated_noise_state
    result.windows = [frame.copy(deep=True) for frame in plant.windows]
    result.schedule = deepcopy(plant.schedule)
    result.active_rows = deepcopy(plant.active_rows)
    return result


def inject_gain_fault(plant: StatefulPlant, gain: float = 1.2) -> StatefulPlant:
    return _with(plant, "gain_fault", "current_a", gain=gain)


def inject_voltage_offset(plant: StatefulPlant, offset_v: float = 1e-3) -> StatefulPlant:
    return _with(plant, "voltage_offset", "actual_voltage_v", voltage_offset_v=offset_v)


def inject_current_offset(plant: StatefulPlant, offset_a: float = 1e-8) -> StatefulPlant:
    return _with(plant, "current_offset", "current_a", current_offset_a=offset_a)


def inject_excessive_noise(plant: StatefulPlant, noise_std_a: float = 2e-7) -> StatefulPlant:
    return _with(plant, "excessive_noise", "current_a", noise_std_a=noise_std_a)


def inject_correlated_noise(plant: StatefulPlant, noise_std_a: float = 1e-7) -> StatefulPlant:
    return _with(plant, "correlated_noise", "current_a", correlated_noise_a=noise_std_a)


def inject_slow_settling_fault(plant: StatefulPlant, cdl_scale: float = 20.0) -> StatefulPlant:
    return _with(plant, "slow_settling_fault", "eta_v", cdl_scale=cdl_scale)


def inject_high_cdl_fault(plant: StatefulPlant, cdl_scale: float = 20.0) -> StatefulPlant:
    return _with(plant, "high_cdl", "eta_v", cdl_scale=cdl_scale)


def inject_ocp_drift(plant: StatefulPlant, drift_v_per_s: float = 2e-3 / 60.0) -> StatefulPlant:
    return _with(plant, "ocp_drift", "ocp_voltage_v", ocp_drift_v_per_s=drift_v_per_s)


def inject_recovery_shift(plant: StatefulPlant, shift_v: float = 3e-3) -> StatefulPlant:
    return _with(plant, "recovery_shift", "ocp_voltage_v", recovery_shift_v=shift_v)


def inject_low_signal(plant: StatefulPlant, scale: float = 0.01) -> StatefulPlant:
    return _with(plant, "low_signal", "current_a", low_signal_scale=scale)


def inject_forward_reverse_mismatch(plant: StatefulPlant, reverse_gain: float = 1.3) -> StatefulPlant:
    return _with(plant, "forward_reverse_mismatch", "reverse_current_a", reverse_gain=reverse_gain)


def inject_switching_disturbance(plant: StatefulPlant, disturbance_v: float = 2e-3) -> StatefulPlant:
    return _with(plant, "switching_disturbance", "actual_voltage_v", switching_disturbance_v=disturbance_v)


def inject_mixed_faults(plant: StatefulPlant, **changes) -> StatefulPlant:
    allowed = {
        "gain", "voltage_offset_v", "current_offset_a", "noise_std_a",
        "correlated_noise_a", "cdl_scale", "ocp_drift_v_per_s",
        "recovery_shift_v", "low_signal_scale", "switching_disturbance_v",
        "reverse_gain",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"unsupported mixed fault parameters: {sorted(unknown)}")
    return _with(plant, "mixed_fault", "multiple", **changes)


def apply_fault(frame, spec: FaultSpec):
    """Apply a deterministic trace fault without mutating the input frame."""
    result = frame.copy(deep=True)
    rng = np.random.default_rng(spec.seed)
    params = spec.fault_parameters
    if "voltage_offset_v" in params and "actual_voltage_v" in result:
        result["actual_voltage_v"] += float(params["voltage_offset_v"])
    if "current_offset_a" in params and "current_a" in result:
        result["current_a"] += float(params["current_offset_a"])
    if "gain" in params and "current_a" in result:
        result["current_a"] *= float(params["gain"])
    if "noise_std_a" in params and "current_a" in result:
        result["current_a"] += rng.normal(0.0, float(params["noise_std_a"]), len(result))
    result.attrs["fault_metadata"] = spec.to_dict()
    return result


def fault_spec(fault_type: str, seed: int = 42, source_attempt: str = "", affected_signal: str = "current_a", **parameters) -> FaultSpec:
    return FaultSpec(fault_type, parameters, seed, affected_signal, source_attempt)
