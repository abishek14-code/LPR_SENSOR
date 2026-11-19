from dataclasses import dataclass, field
from enum import Enum
from copy import deepcopy
from typing import Callable, Dict, List, Optional

import pandas as pd
import numpy as np

from .config import AppConfig
from .diagnostics import DiagnosticResult, diagnose
from .fault_injection import (
    inject_correlated_noise,
    inject_current_offset,
    inject_excessive_noise,
    inject_forward_reverse_mismatch,
    inject_gain_fault,
    inject_high_cdl_fault,
    inject_low_signal,
    inject_mixed_faults,
    inject_ocp_drift,
    inject_recovery_shift,
    inject_slow_settling_fault,
    inject_switching_disturbance,
    inject_voltage_offset,
)
from .reference_replay import ReplayResult, replay_schedule
from .stateful_model import StatefulPlant
from .io_utils import json_safe, source_provenance
from .decision_engine import CheckResult, DecisionResult, EvaluationContext, evaluate_diagnostics


class State(str, Enum):
    IDLE = "IDLE"
    OPENING_CHECK = "OPENING_CHECK"
    OCP = "OCP"
    A1 = "A1"
    RECOVERY = "RECOVERY"
    B = "B"
    A2 = "A2"
    DIAGNOSE = "DIAGNOSE"
    REFERENCE_REPLAY = "REFERENCE_REPLAY"
    REACQUIRE = "REACQUIRE"
    CLOSING_CHECK = "CLOSING_CHECK"
    RELEASE = "RELEASE"
    REJECT = "REJECT"
    A1_REVERSE = "A1_REVERSE"
    B_REVERSE = "B_REVERSE"
    A2_REVERSE = "A2_REVERSE"


class AttemptInterrupted(Exception):
    """A reset cancels this execution token permanently."""


@dataclass
class Controller:
    """Lifecycle guard with reset semantics independent of the plant model."""

    state: State = State.IDLE
    attempt_complete: bool = False
    attempt_incomplete: bool = False
    history: List[str] = field(default_factory=lambda: [State.IDLE.value])
    on_transition: Optional[Callable] = field(default=None, repr=False)
    _release_authorized: bool = field(default=False, repr=False)

    def checkpoint(self):
        if self.attempt_incomplete:
            raise AttemptInterrupted("RESET")

    def transition(self, state: State):
        self.checkpoint()
        allowed = {
            State.IDLE: {State.OPENING_CHECK},
            State.OPENING_CHECK: {State.OCP},
            State.OCP: {State.A1},
            State.A1: {State.RECOVERY, State.DIAGNOSE},
            State.B: {State.RECOVERY, State.DIAGNOSE},
            State.A2: {State.RECOVERY, State.DIAGNOSE},
            State.A1_REVERSE: {State.RECOVERY, State.DIAGNOSE},
            State.B_REVERSE: {State.RECOVERY, State.DIAGNOSE},
            State.A2_REVERSE: {State.RECOVERY, State.DIAGNOSE},
            State.RECOVERY: {State.B, State.A2, State.A1_REVERSE, State.B_REVERSE, State.A2_REVERSE, State.DIAGNOSE},
            State.DIAGNOSE: {State.CLOSING_CHECK, State.REFERENCE_REPLAY},
            State.CLOSING_CHECK: {State.RELEASE},
            State.REFERENCE_REPLAY: {State.REACQUIRE},
            State.REACQUIRE: {State.OPENING_CHECK},
        }
        if (state == State.RELEASE and not self._release_authorized) or (state != State.REJECT and state not in allowed.get(self.state, set())) or self.attempt_complete:
            raise ValueError(f"illegal transition {self.state.value} -> {state.value}")
        self.state = state
        if state != State.RELEASE:
            self.history.append(state.value)
        if self.on_transition is not None:
            self.on_transition(self)
        self.checkpoint()
        if state == State.RELEASE:
            self.history.append(state.value)
        if state in (State.RELEASE, State.REJECT):
            self.attempt_complete = True

    def reset(self):
        self.state = State.IDLE
        self.attempt_complete = False
        self.attempt_incomplete = True
        self._release_authorized = False
        self.history.append(State.IDLE.value)

    def close(self, passed: bool = False, diagnostics: Optional[DiagnosticResult] = None) -> State:
        if self.attempt_incomplete:
            return self.state
        if (not passed or self.state not in (State.DIAGNOSE, State.CLOSING_CHECK) or self.attempt_incomplete
                or diagnostics is None or not diagnostics.passed
                or not (diagnostics.measured_values or {}).get("release_evidence_required")):
            self.transition(State.REJECT)
            return self.state
        if self.state == State.DIAGNOSE:
            self.transition(State.CLOSING_CHECK)
        self._release_authorized = True
        self.transition(State.RELEASE)
        return self.state


@dataclass
class AttemptRecord:
    scenario_id: str
    attempt_id: str
    controller_variant: str
    seed: int
    state_history: List[str] = field(default_factory=list)
    decision: str = "INCOMPLETE"
    final_state: str = State.IDLE.value
    diagnostics: Optional[DiagnosticResult] = None
    replay: Optional[ReplayResult] = None
    reacquisition_count: int = 0
    acquisition_time_s: float = 0.0
    driven_time_s: float = 0.0
    attempt_ids: List[str] = field(default_factory=list)
    fault_metadata: Dict = field(default_factory=dict)
    timed_out: bool = False
    source_type: str = "synthetic"
    source_metadata: Dict = field(default_factory=dict)
    ground_truth_fault: bool = False
    attempt_history: List[Dict] = field(default_factory=list)
    timing: Dict = field(default_factory=dict)
    lifecycle_codes: List[str] = field(default_factory=list)
    evaluation_truth: Dict = field(default_factory=dict)
    run_id: Optional[str] = None
    trial_id: Optional[str] = None
    provenance: Dict = field(default_factory=source_provenance)
    decision_result: Optional[DecisionResult] = None

    def to_dict(self):
        diagnostics = self.diagnostics.to_dict() if self.diagnostics else None
        replay = self.replay.to_dict() if self.replay else None
        blocks = diagnostics["blocks"] if diagnostics else {}
        a1 = blocks.get("A1")
        return json_safe({
            **self.provenance,
            "run_id": self.run_id,
            "trial_id": self.trial_id,
            "scenario_id": self.scenario_id,
            "attempt_id": self.attempt_id,
            "attempt_ids": self.attempt_ids or [self.attempt_id],
            "controller_variant": self.controller_variant,
            "final_state": self.final_state,
            "decision": self.decision,
            "decision_result": self.decision_result.to_dict() if self.decision_result else None,
            "diagnostic_codes": list(dict.fromkeys(self.lifecycle_codes + (list(self.diagnostics.reason_codes) if self.diagnostics else []))),
            "A1": a1,
            "B": blocks.get("B"),
            "A2": blocks.get("A2"),
            "reference_replay": replay,
            "reacquisition_count": self.reacquisition_count,
            "Rapp": diagnostics.get("Rapp") if diagnostics else None,
            "Rapp_estimation_method": a1.get("resistance", {}).get("estimation_method") if a1 else None,
            "Rp_corrected": diagnostics.get("Rp_corrected") if diagnostics else None,
            "Rp_corrected_estimation_method": "Ru_corrected" if diagnostics and diagnostics.get("Rp_corrected") is not None else None,
            "uncertainty_ohm": diagnostics.get("uncertainty_ohm") if diagnostics else None,
            "acquisition_time_s": self.acquisition_time_s,
            "driven_time_s": self.driven_time_s,
            "seed": self.seed,
            "state_history": self.state_history,
            "fault_metadata": self.fault_metadata,
            "timed_out": self.timed_out,
            "source_type": self.source_type,
            "source_metadata": self.source_metadata,
            "ground_truth_fault": self.ground_truth_fault,
            "diagnostic_details": diagnostics,
            "attempt_history": self.attempt_history,
            "timing": self.timing,
            "evaluation_truth": self.evaluation_truth,
        })


def _plant_for_fault(config: AppConfig, fault: str, seed: int) -> StatefulPlant:
    allowed = {"", "gain_fault", "replay_failure", "slow_settling_fault", "persistent_slow_fault", "high_cdl", "voltage_offset", "current_offset", "excessive_noise", "correlated_noise", "ocp_drift", "recovery_shift", "low_signal", "forward_reverse_mismatch", "switching_disturbance", "mixed_fault", "timeout_fault", "opening_check_failure", "closing_check_failure"}
    allowed.add("recoverable_slow_fault")
    if fault not in allowed:
        raise ValueError(f"unknown fault: {fault}")
    plant = StatefulPlant(config.model, seed=seed, fault=fault or None)
    if fault == "opening_check_failure":
        plant.opening_path_gain = 0.0
    if fault == "closing_check_failure":
        plant.closing_path_gain = 0.0
    if fault == "gain_fault" or fault == "replay_failure":
        return inject_gain_fault(plant)
    if fault in {"slow_settling_fault", "persistent_slow_fault"}:
        return inject_slow_settling_fault(plant)
    if fault == "recoverable_slow_fault":
        return inject_slow_settling_fault(plant, cdl_scale=3.0)
    if fault == "high_cdl":
        return inject_high_cdl_fault(plant)
    if fault == "voltage_offset":
        return inject_voltage_offset(plant)
    if fault == "current_offset":
        return inject_current_offset(plant)
    if fault == "excessive_noise":
        return inject_excessive_noise(plant)
    if fault == "correlated_noise":
        return inject_correlated_noise(plant)
    if fault == "ocp_drift":
        return inject_ocp_drift(plant)
    if fault == "recovery_shift":
        return inject_recovery_shift(plant)
    if fault == "low_signal":
        return inject_low_signal(plant)
    if fault == "forward_reverse_mismatch":
        return inject_forward_reverse_mismatch(plant)
    if fault == "switching_disturbance":
        return inject_switching_disturbance(plant)
    if fault == "mixed_fault":
        return inject_mixed_faults(plant, gain=1.2, noise_std_a=1e-7, reverse_gain=1.3)
    return plant


def _snapshot(plant):
    return {"eta_v": float(plant.eta_v), "rng_state": deepcopy(plant._rng.bit_generator.state),
            "correlated_noise_state": float(plant._correlated_noise_state),
            "fault_parameters": deepcopy(plant.fault_parameters)}


def _window_summary(frame):
    result = {"state": str(frame["state"].iloc[0]), "samples": len(frame),
              "start_time_s": float(frame.time_s.iloc[0]),
              "end_time_s": float(frame.time_s.iloc[-1]),
              "duration_s": float(frame.sample_duration_s.sum())}
    for name in ("current_a", "actual_voltage_v", "ocp_voltage_v"):
        if name in frame:
            result[name] = {"mean": float(frame[name].mean()), "last": float(frame[name].iloc[-1]),
                            "min": float(frame[name].min()), "max": float(frame[name].max())}
    return result


def _timing(record, plant):
    replay = record.timing.get("replay_time_s", 0.0)
    record.acquisition_time_s = plant.measurement_window_time_s
    record.driven_time_s = plant.driven_time_s
    record.timing = {"measurement_window_time_s": plant.measurement_window_time_s,
                     "driven_time_s": plant.driven_time_s, "recovery_time_s": plant.recovery_time_s,
                     "ocp_time_s": plant.ocp_duration_s,
                     "acquisition_elapsed_time_s": plant.elapsed_time_s - plant.reference_wait_time_s,
                     "reference_wait_time_s": plant.reference_wait_time_s,
                     "replay_time_s": replay, "total_elapsed_time_s": plant.elapsed_time_s}


def _run_sequence(config, scenario_id, fault, attempt_index, plant, controller, history):
    acquisition = config.acquisition
    controller.transition(State.OPENING_CHECK)
    history["opening_check"] = plant.path_check("opening")
    from .diagnostics import _check_code
    if _check_code(history["opening_check"], "OPENING") is not None:
        return None
    controller.transition(State.OCP)
    opening = plant.measure_ocp(config.diagnostics.ocp_observation_s)
    dwell = acquisition.reacquisition_dwell_s if attempt_index else acquisition.dwell_s
    if fault == "timeout_fault":
        dwell = acquisition.point_timeout_s + 1.0
    frames, reverse_frames = {}, {} if acquisition.enable_reverse_check else None
    timeout = False
    for reverse in ([False, True] if acquisition.enable_reverse_check else [False]):
        for name, amplitude in (("A1", acquisition.a1_amplitude_v), ("B", acquisition.b_amplitude_v), ("A2", acquisition.a1_amplitude_v)):
            block = name + ("_REVERSE" if reverse else "")
            controller.transition(State(block))
            frame = plant.acquire_block(block, amplitude, acquisition, dwell_s=dwell, reverse=reverse)
            (reverse_frames if reverse else frames)[name] = frame
            if frame.attrs["timed_out"]:
                timeout = True
                break
            controller.transition(State.RECOVERY)
            plant.recover(acquisition)
        if timeout:
            break
    closing_ocp = None if timeout else plant.measure_ocp(config.diagnostics.ocp_observation_s, shift_v=plant.recovery_shift_v)
    controller.transition(State.DIAGNOSE)
    inputs = dict(reverse_frames=reverse_frames, ocp_opening=opening, ocp_closing=closing_ocp,
                  attempt_id=history["attempt_id"], opening_check=history["opening_check"],
                  reverse_required=acquisition.enable_reverse_check, point_timeout_s=acquisition.point_timeout_s)
    # This preliminary analysis cannot authorize release: closing is still pending.
    result = diagnose(frames, config.diagnostics, require_release_evidence=False, **inputs)
    history["diagnostics"] = result.to_dict()
    if result.passed:
        controller.transition(State.CLOSING_CHECK)
        history["closing_check"] = plant.path_check("closing")
        result = diagnose(frames, config.diagnostics, closing_check=history["closing_check"], **inputs)
        history["diagnostics"] = result.to_dict()
    return result


def run_attempt(config: AppConfig, scenario_id: str, fault: str = "", attempt_index: int = 0,
                *, controller=None, on_transition=None, on_sample=None, run_id=None, trial_id=None) -> AttemptRecord:
    if attempt_index < 0:
        raise ValueError("attempt_index cannot be negative")
    controller = controller or Controller()
    if controller.state != State.IDLE or controller.attempt_incomplete or controller.attempt_complete:
        raise ValueError("a fresh IDLE controller is required")
    if on_transition is not None:
        controller.on_transition = on_transition
    record = AttemptRecord(scenario_id, f"{scenario_id}-{attempt_index}", config.controller_variant, config.seed)
    record.run_id = run_id or f"{scenario_id}-{config.controller_variant}-{config.seed}"
    record.trial_id = trial_id or record.run_id
    record.state_history = controller.history
    plant = _plant_for_fault(config, fault, config.seed)
    record.evaluation_truth = {"Rapp_ohm": plant.model.ru_ohm + plant.model.rp_ohm,
                               "Rp_ohm": plant.model.rp_ohm, "estimation_method": "model_truth"}
    record.ground_truth_fault = bool(fault)
    def checkpoint(sample):
        controller.checkpoint()
        if on_sample is not None:
            on_sample(controller, sample)
        controller.checkpoint()
    plant.sample_hook = checkpoint
    for index in range(attempt_index, attempt_index + config.acquisition.max_reacquisition_attempts + 1):
        identity = f"{trial_id}/attempt-{index}" if trial_id is not None else f"{scenario_id}-{index}"
        if index == attempt_index:
            record.attempt_id = identity
        history = {"attempt_id": identity, "trial_id": record.trial_id, "sample_id": f"{record.trial_id}/sample",
                   "seed": config.seed, "start": _snapshot(plant), "diagnostics": None,
                   "fault_metadata": {"fault_type": fault or "none", "fault_parameters": deepcopy(plant.fault_parameters),
                                      "seed": config.seed, "source_attempt": identity}}
        record.attempt_ids.append(identity)
        record.attempt_history.append(history)
        window_start = len(plant.windows)
        schedule_start = len(plant.schedule)
        state_start = len(controller.history)
        try:
            result = _run_sequence(config, scenario_id, fault, index, plant, controller, history)
            record.diagnostics = result
            record.decision_result = evaluate_diagnostics(result, config,
                context=EvaluationContext(retries_used=index - attempt_index),
                extra_codes=() if result is not None else ("OPENING_CHECK_FAILURE",))
            history["decision_result"] = record.decision_result.to_dict()
            if result is None:
                from .diagnostics import _check_code
                record.lifecycle_codes.append(_check_code(history["opening_check"], "OPENING"))
                controller.transition(State.REJECT)
            elif record.decision_result.decision == "REJECT":
                record.timed_out = "TIMEOUT" in result.reason_codes
                controller.transition(State.REJECT)
            elif controller.state == State.CLOSING_CHECK:
                controller.close(result.passed and record.decision_result.decision == "ACCEPT", result)
            elif index > attempt_index or not result.replay_required:
                controller.transition(State.REJECT)
            else:
                controller.transition(State.REFERENCE_REPLAY)
                executed_schedule = deepcopy(plant.schedule[schedule_start:])
                history["executed_schedule"] = executed_schedule
                replay = replay_schedule(config.reference_model, config.acquisition, config.diagnostics,
                                         config.seed, schedule=executed_schedule)
                record.replay = replay
                record.timing["replay_time_s"] = record.timing.get("replay_time_s", 0.0) + replay.metrics["elapsed_time_s"]
                history["reference_replay"] = replay.to_dict()
                # The sample remains open-circuit while the independent reference
                # is measured; its clock and interface continue, without reseeding.
                plant.measure_ocp(replay.metrics["elapsed_time_s"], state="REFERENCE_WAIT")
                if not replay.passed:
                    record.lifecycle_codes.append("REFERENCE_REPLAY_FAILURE")
                    controller.transition(State.REJECT)
                elif not result.recoverable or config.acquisition.max_reacquisition_attempts == 0:
                    record.lifecycle_codes.append("UNRESOLVED_REFERENCE_DIAGNOSTICS")
                    controller.transition(State.REJECT)
                else:
                    controller.transition(State.REACQUIRE)
                    record.reacquisition_count = 1
        except AttemptInterrupted:
            record.lifecycle_codes.append("RESET")
            record.decision = "INCOMPLETE"
            record.timing["replay_time_s"] = plant.reference_wait_time_s
            if controller.history[-2:-1] == [State.REFERENCE_REPLAY.value] and record.replay is not None:
                record.replay.passed = False
                record.replay.metrics["interrupted"] = True
                record.replay.metrics["completed_elapsed_time_s"] = plant.reference_wait_time_s
                history["reference_replay"] = record.replay.to_dict()
            if plant.active_rows:
                plant.windows.append(pd.DataFrame(plant.active_rows))
                plant.active_rows = []
        finally:
            history["end"] = _snapshot(plant)
            history["state_history"] = controller.history[state_start:]
            history["windows"] = [_window_summary(frame) for frame in plant.windows[window_start:]]
            history["final_state"] = controller.state.value
            history["timed_out"] = record.timed_out
            history["lifecycle_codes"] = list(record.lifecycle_codes)
            history.setdefault("executed_schedule", deepcopy(plant.schedule[schedule_start:]))
            record.fault_metadata = history["fault_metadata"]
            _timing(record, plant)
        record.final_state = controller.state.value
        record.state_history = list(controller.history)
        if controller.attempt_incomplete:
            record.decision_result = evaluate_diagnostics(record.diagnostics, config, context=EvaluationContext(interrupted=True, retries_used=record.reacquisition_count), extra_codes=record.lifecycle_codes)
            history["decision_result"] = record.decision_result.to_dict()
            return record
        if controller.state in (State.RELEASE, State.REJECT):
            record.decision = "ACCEPT" if controller.state == State.RELEASE else "REJECT"
            failure_checks = []
            if record.diagnostics is None:
                failure_checks.append(CheckResult("opening_check", False, history.get("opening_check"),
                    "explicit passing opening check with provenance", record.lifecycle_codes[0],
                    "The supplied opening check failed or was unavailable; acquisition was not started.",
                    False, "Resolve the opening fault and start a fresh measurement."))
            record.decision_result = evaluate_diagnostics(record.diagnostics, config, context=EvaluationContext(retries_used=record.reacquisition_count, reference_failed="REFERENCE_REPLAY_FAILURE" in record.lifecycle_codes), extra_codes=record.lifecycle_codes, extra_checks=failure_checks)
            history["decision_result"] = record.decision_result.to_dict()
            return record
    raise RuntimeError("bounded acquisition ended without a decision")


def process_recorded_frames(config: AppConfig, scenario_id: str, frames: Dict[str, pd.DataFrame], seed: Optional[int] = None, source_type: str = "ltspice", source_metadata: Optional[Dict] = None, *, opening_check=None, closing_check=None, ocp_opening=None, ocp_closing=None, reverse_frames=None, run_id=None, trial_id=None, attempt_id=None) -> AttemptRecord:
    from .simulator_adapter import normalize_trace, TraceAdapterConfig
    if not isinstance(frames, dict) or (reverse_frames is not None and not isinstance(reverse_frames, dict)):
        raise ValueError("measurement groups must be dictionaries")
    context_errors = []
    identities = {key: set() for key in ("run_id", "trial_id", "attempt_id", "seed")}
    normalized_groups = []
    input_channel_peaks = {}
    metadata = deepcopy(source_metadata or {})
    metadata["offline_only"] = True
    metadata["operational_context"] = {}
    for direction, group in ((False, frames), (True, reverse_frames or {})):
        normalized = {}
        for name, frame in group.items():
            try:
                data = normalize_trace(frame, TraceAdapterConfig(source_type=source_type))
                required = {"point_index", "drive_enabled", "reverse", "requested_voltage_v"}
                if not required.issubset(data.columns):
                    raise ValueError("missing required acquisition context")
                if not data["drive_enabled"].all() or not data["reverse"].eq(direction).all():
                    raise ValueError("invalid drive or forward/reverse context")
                if set(data.point_index.unique()) != set(range(config.acquisition.point_count)):
                    raise ValueError("incomplete configured point sequence")
                segments = data.point_index[data.point_index.ne(data.point_index.shift())]
                if segments.tolist() != list(range(config.acquisition.point_count)):
                    raise ValueError("point windows must follow the configured index sequence")
                if segments.duplicated().any():
                    raise ValueError("disjoint repeated point windows")
                for _, point in data.groupby("point_index", sort=False):
                    for setting in ("requested_voltage_v", "gain", "adc_range_v", "adc_rate_sps"):
                        if setting in point and point[setting].nunique() != 1:
                            raise ValueError(f"changing setting within point: {setting}")
                requested = data.groupby("point_index", sort=False).requested_voltage_v.first().to_numpy(dtype=float)
                amplitude = config.acquisition.b_amplitude_v if name == "B" else config.acquisition.a1_amplitude_v
                expected = config.acquisition.points(amplitude, reverse=direction)
                if not np.allclose(requested - requested[0], np.asarray(expected) - expected[0], rtol=1e-9, atol=1e-12):
                    raise ValueError("requested excitation sequence does not match configured amplitude/order")
                input_channel_peaks[name + ("_REVERSE" if direction else "")] = {key: float(data[key].abs().max()) for key in ("actual_voltage_v", "current_a")}
                if "block_id" in data and not data["block_id"].str.upper().str.removesuffix("_REVERSE").eq(name).all():
                    raise ValueError("block identity conflicts with measurement group")
                if "state" in data and not data["state"].eq(name + ("_REVERSE" if direction else "")).all():
                    raise ValueError("state conflicts with measurement group")
                for key in identities:
                    if key in data:
                        identities[key].update(data[key].tolist())
                columns = [key for key in data if key not in {"time_s", "current_a", "actual_voltage_v", "eta_v", "point_elapsed_time_s"}]
                metadata["operational_context"][name + ("_REVERSE" if direction else "")] = data[columns].drop_duplicates().to_dict("records")
                normalized[name] = data
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                context_errors.append(f"{name}: {exc}")
                normalized[name] = frame
        normalized_groups.append(normalized)
    supplied = {"run_id": run_id, "trial_id": trial_id, "attempt_id": attempt_id, "seed": seed}
    for key, values in identities.items():
        if len(values) > 1 or (values and supplied[key] is not None and supplied[key] not in values):
            context_errors.append(f"mixed identity boundary: {key}")
        elif values:
            supplied[key] = next(iter(values))
    attempt_id = supplied["attempt_id"] or f"{scenario_id}-recorded"
    try:
        measurement_frames = list(normalized_groups[0].values()) + list(normalized_groups[1].values())
        starts = [float(frame.time_s.min()) for frame in measurement_frames]
        ends = [float(frame.time_s.max()) for frame in measurement_frames]
        intervals = sorted(zip(starts, ends))
        if any(previous[1] >= following[0] for previous, following in zip(intervals, intervals[1:])):
            context_errors.append("measurement windows overlap or reuse timestamps")
        if starts and ocp_opening is not None and ocp_closing is not None:
            if ocp_opening.time_s.max() >= min(starts) or ocp_closing.time_s.min() <= max(ends):
                context_errors.append("OCP windows do not bracket recorded acquisition")
    except (AttributeError, TypeError, ValueError):
        context_errors.append("invalid acquisition/OCP timing context")
    result = diagnose(normalized_groups[0], config.diagnostics, attempt_id=attempt_id,
                      opening_check=opening_check, closing_check=closing_check,
                      ocp_opening=ocp_opening, ocp_closing=ocp_closing,
                      reverse_frames=normalized_groups[1] or None, reverse_required=config.acquisition.enable_reverse_check,
                      point_timeout_s=config.acquisition.point_timeout_s)
    if context_errors:
        result.passed = False
        result.reason_codes = (*result.reason_codes, "INVALID_OPERATIONAL_CONTEXT")
    result.measured_values["context_errors"] = context_errors
    result.measured_values["input_channel_peaks"] = input_channel_peaks
    result.measured_values["input_identity"] = {key: str(int(value)) if key == "seed" else str(value) for key, value in supplied.items() if value is not None}
    if metadata.get("source_hash"):
        result.measured_values["input_identity"]["source_hash"] = metadata["source_hash"]
    record = AttemptRecord(scenario_id, attempt_id, config.controller_variant, supplied["seed"])
    record.run_id, record.trial_id = supplied["run_id"], supplied["trial_id"]
    record.source_type = source_type
    record.source_metadata = metadata
    # Offline data cannot establish complete wall-clock or driven duration without supplied timing.
    record.acquisition_time_s = None
    record.driven_time_s = None
    record.timing = {"total_elapsed_time_s": None, "timing_source": "not established by offline measurement windows"}
    record.attempt_ids = [record.attempt_id]
    # These are offline decision states, not invented acquisition/check events.
    record.state_history = [State.IDLE.value, State.DIAGNOSE.value]
    record.diagnostics = result
    record.decision_result = evaluate_diagnostics(result, config, context=EvaluationContext(source_type=source_type))
    if result.passed and record.decision_result.decision == "ACCEPT":
        record.state_history.append(State.RELEASE.value)
        record.final_state, record.decision = State.RELEASE.value, "ACCEPT"
    else:
        record.state_history.append(State.REJECT.value)
        record.final_state, record.decision = State.REJECT.value, "REJECT"
    return record
