from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

from .config import AcquisitionConfig, ModelConfig


@dataclass
class StatefulPlant:
    model: ModelConfig
    seed: int = 42
    noise_std_a: float = 0.0
    cdl_scale: float = 1.0
    gain: float = 1.0
    fault: Optional[str] = None
    voltage_offset_v: float = 0.0
    current_offset_a: float = 0.0
    correlated_noise_a: float = 0.0
    ocp_drift_v_per_s: float = 0.0
    recovery_shift_v: float = 0.0
    low_signal_scale: float = 1.0
    switching_disturbance_v: float = 0.0
    reverse_gain: float = 1.0
    fault_parameters: Dict = field(default_factory=dict)
    eta_v: float = 0.0
    ocp_time_s: float = 0.0
    elapsed_time_s: float = 0.0
    measurement_window_time_s: float = 0.0
    driven_time_s: float = 0.0
    recovery_time_s: float = 0.0
    ocp_duration_s: float = 0.0
    windows: list = field(default_factory=list, repr=False)
    sample_hook: object = field(default=None, repr=False)
    active_rows: list = field(default_factory=list, repr=False)
    opening_path_gain: float = 1.0
    closing_path_gain: float = 1.0
    schedule: list = field(default_factory=list)
    reference_wait_time_s: float = 0.0
    _correlated_noise_state: float = field(default=0.0, init=False, repr=False)
    _rng: np.random.Generator = field(init=False, repr=False)

    def __post_init__(self):
        self._rng = np.random.default_rng(self.seed)

    @property
    def tau_s(self) -> float:
        parallel = self.model.ru_ohm * self.model.rp_ohm / (self.model.ru_ohm + self.model.rp_ohm)
        return parallel * self.model.cdl_f * self.cdl_scale

    def reset(self):
        self.eta_v = 0.0
        self.ocp_time_s = 0.0
        self.elapsed_time_s = 0.0
        self.measurement_window_time_s = 0.0
        self.driven_time_s = 0.0
        self.recovery_time_s = 0.0
        self.ocp_duration_s = 0.0
        self.windows.clear()
        self.schedule.clear()
        self.active_rows = []
        self.reference_wait_time_s = 0.0
        self._correlated_noise_state = 0.0

    def _step(self, perturbation_v: float, dt_s: float, reverse: bool = False) -> float:
        perturbation_v *= self.low_signal_scale
        eta_eq = perturbation_v * self.model.rp_ohm / (self.model.ru_ohm + self.model.rp_ohm)
        self.eta_v = eta_eq + (self.eta_v - eta_eq) * np.exp(-dt_s / self.tau_s)
        current = (perturbation_v - self.eta_v) / self.model.ru_ohm
        current *= self.gain * (self.reverse_gain if reverse else 1.0)
        current += self.current_offset_a
        if self.noise_std_a:
            current += self._rng.normal(0.0, self.noise_std_a)
        if self.correlated_noise_a:
            self._correlated_noise_state = 0.98 * self._correlated_noise_state + self._rng.normal(0.0, self.correlated_noise_a)
            current += self._correlated_noise_state
        return float(current)

    def path_check(self, phase):
        """Instantaneous noiseless software continuity probe of a known 10k load.

        This is distinct from the unknown sample and from schedule replay.
        Measurement-chain gain/offset appear in the reported probe response.
        """
        excitation, reference_ohm = .001, 10000.0
        path_gain = self.opening_path_gain if phase == "opening" else self.closing_path_gain
        expected = excitation / reference_ohm
        measured = path_gain * (self.gain * expected + self.current_offset_a)
        voltage = excitation + self.voltage_offset_v
        return {"passed": bool(np.isfinite(measured) and np.isfinite(voltage) and .1 * expected < measured < 10 * expected),
                "source": "software known-load continuity probe", "phase": phase,
                "expected_current_a": expected, "measured_current_a": measured,
                "requested_voltage_v": excitation, "measured_voltage_v": voltage,
                "reference_resistance_ohm": reference_ohm,
                "relative_current_error": abs(measured - expected) / expected,
                "voltage_offset_v": voltage - excitation}

    def _checkpoint(self):
        if self.sample_hook is not None:
            self.sample_hook(self)

    def chain_settings(self):
        return {key: getattr(self, key) for key in ("gain", "voltage_offset_v", "current_offset_a", "noise_std_a", "correlated_noise_a", "reverse_gain", "switching_disturbance_v", "low_signal_scale")}

    def acquire_block(self, block_id: str, amplitude_v: float, acquisition: AcquisitionConfig, dwell_s: Optional[float] = None, reverse: bool = False) -> pd.DataFrame:
        dwell_s = dwell_s if dwell_s is not None else acquisition.dwell_s
        points = acquisition.points(amplitude_v, reverse=reverse)
        dt = 1.0 / acquisition.sample_rate_hz
        rows = []
        self.active_rows = rows
        if not np.isfinite(dwell_s) or dwell_s <= 0:
            raise ValueError("dwell_s must be finite and positive")
        timed_out = dwell_s > acquisition.point_timeout_s
        duration = min(dwell_s, acquisition.point_timeout_s)
        event = {"kind": "acquire", "block_id": block_id, "amplitude_v": amplitude_v,
                 "dwell_s": dwell_s, "reverse": reverse, "points_v": list(points),
                 "sample_rate_hz": acquisition.sample_rate_hz, "point_count": acquisition.point_count,
                 "point_order": acquisition.point_order, "point_timeout_s": acquisition.point_timeout_s,
                 "chain": self.chain_settings(), "start_time_s": self.elapsed_time_s}
        self.schedule.append(event)
        for point_index, perturbation in enumerate(points):
            count = int(np.ceil(duration * acquisition.sample_rate_hz))
            point_elapsed = 0.0
            for sample_index in range(count):
                self._checkpoint()
                step_dt = min(dt, max(0.0, duration - point_elapsed))
                current = self._step(perturbation, step_dt, reverse=reverse)
                point_elapsed += step_dt
                self.elapsed_time_s += step_dt
                self.measurement_window_time_s += step_dt
                if perturbation != 0:
                    self.driven_time_s += step_dt
                disturbance = self.switching_disturbance_v if sample_index == 0 else 0.0
                actual_voltage = self.model.ocp_v + perturbation * self.low_signal_scale + self.voltage_offset_v + disturbance
                rows.append({
                    "block_id": block_id,
                    "point_index": point_index,
                    "time_s": self.elapsed_time_s,
                    "sample_duration_s": step_dt,
                    "point_elapsed_time_s": point_elapsed,
                    "commanded_voltage_v": self.model.ocp_v + perturbation,
                    "actual_voltage_v": actual_voltage,
                    "current_a": current,
                    "state": block_id.removesuffix("_REVERSE") + ("_REVERSE" if reverse else ""),
                    "fault": self.fault or "",
                    "reverse": reverse,
                    "drive_enabled": True,
                })
            if timed_out:
                break
        frame = pd.DataFrame(rows)
        frame.attrs["timed_out"] = timed_out
        frame.attrs["point_timeout_s"] = acquisition.point_timeout_s
        event["end_time_s"] = self.elapsed_time_s
        self.windows.append(frame)
        self.active_rows = []
        return frame

    def measure_ocp(self, duration_s: float = 1.0, shift_v: float = 0.0, *, state="OCP") -> pd.DataFrame:
        """Drive disconnected: I=0 and eta decays with Rp*Cdl, not (Ru||Rp)*Cdl."""
        dt = 1.0 / 20.0
        if not np.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("OCP duration must be finite and positive")
        count = int(np.ceil(duration_s / dt))
        event = {"kind": "ocp", "duration_s": duration_s, "sample_rate_hz": 20.0,
                 "start_time_s": self.elapsed_time_s, "state": state}
        self.schedule.append(event)
        rows = []
        self.active_rows = rows
        for index in range(count):
            self._checkpoint()
            step_dt = min(dt, duration_s - index * dt)
            self.eta_v *= np.exp(-step_dt / (self.model.rp_ohm * self.model.cdl_f * self.cdl_scale))
            self.elapsed_time_s += step_dt
            if state == "REFERENCE_WAIT":
                self.reference_wait_time_s += step_dt
            else:
                self.ocp_duration_s += step_dt
            time_s = self.elapsed_time_s
            rows.append({
                "time_s": time_s,
                "ocp_voltage_v": self.model.ocp_v + self.eta_v + self.ocp_drift_v_per_s * time_s + shift_v,
                "sample_duration_s": step_dt,
                "drive_enabled": False,
                "state": state,
                "fault": self.fault or "",
            })
        self.ocp_time_s = self.elapsed_time_s
        event["end_time_s"] = self.elapsed_time_s
        frame = pd.DataFrame(rows)
        self.windows.append(frame)
        self.active_rows = []
        return frame

    def recover(self, acquisition: AcquisitionConfig, duration_s: Optional[float] = None) -> pd.DataFrame:
        duration_s = duration_s if duration_s is not None else acquisition.recovery_s
        dt = 1.0 / acquisition.sample_rate_hz
        rows = []
        self.active_rows = rows
        if not np.isfinite(duration_s) or duration_s <= 0:
            raise ValueError("recovery duration must be finite and positive")
        count = int(np.ceil(duration_s * acquisition.sample_rate_hz))
        event = {"kind": "recover", "duration_s": duration_s, "sample_rate_hz": acquisition.sample_rate_hz,
                 "start_time_s": self.elapsed_time_s, "chain": self.chain_settings()}
        self.schedule.append(event)
        for index in range(count):
            self._checkpoint()
            step_dt = min(dt, duration_s - index * dt)
            current = self._step(0.0, step_dt)
            self.elapsed_time_s += step_dt
            self.recovery_time_s += step_dt
            rows.append({
                "block_id": "RECOVERY",
                "point_index": 0,
                "time_s": self.elapsed_time_s,
                "sample_duration_s": step_dt,
                "commanded_voltage_v": self.model.ocp_v,
                "actual_voltage_v": self.model.ocp_v,
                "current_a": current,
                "state": "RECOVERY",
                "fault": self.fault or "",
                "drive_enabled": True,
            })
        frame = pd.DataFrame(rows)
        event["end_time_s"] = self.elapsed_time_s
        self.windows.append(frame)
        self.active_rows = []
        return frame
