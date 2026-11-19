from copy import deepcopy
from dataclasses import dataclass, asdict, replace
from typing import Dict

import numpy as np

from .config import AcquisitionConfig, DiagnosticConfig, ModelConfig
from .diagnostics import diagnose
from .stateful_model import StatefulPlant


@dataclass
class ReplayResult:
    passed: bool
    reason_codes: tuple
    metrics: Dict

    def to_dict(self):
        return {"passed": self.passed, "reason_codes": list(self.reason_codes), "metrics": self.metrics}


def replay_schedule(model: ModelConfig, acquisition: AcquisitionConfig, diagnostics: DiagnosticConfig,
                    seed: int, expected_rapp_ohm=None, enable_reverse_check=None, *, schedule=None, chain=None) -> ReplayResult:
    """Replay executed events on an independent known reference, never the sample.

    The optional config-only path is for standalone reference tests. Controller
    replay always supplies executed events including acquisition-chain settings.
    """
    reference_seed = int(np.random.SeedSequence([seed, 1907]).generate_state(1)[0])
    plant = StatefulPlant(model, seed=reference_seed)
    if schedule is None:
        settings = plant.chain_settings()
        settings.update(chain or {})
        schedule = []
        reverse_enabled = acquisition.enable_reverse_check if enable_reverse_check is None else enable_reverse_check
        for reverse in ([False, True] if reverse_enabled else [False]):
            for name, amp in (("A1", acquisition.a1_amplitude_v), ("B", acquisition.b_amplitude_v), ("A2", acquisition.a1_amplitude_v)):
                schedule.append(dict(kind="acquire", block_id=name + ("_REVERSE" if reverse else ""),
                                     amplitude_v=amp, dwell_s=acquisition.dwell_s, reverse=reverse,
                                     points_v=list(acquisition.points(amp, reverse)),
                                     sample_rate_hz=acquisition.sample_rate_hz, point_count=acquisition.point_count,
                                     point_order=acquisition.point_order, point_timeout_s=acquisition.point_timeout_s,
                                     chain=settings))
                schedule.append(dict(kind="recover", duration_s=acquisition.recovery_s,
                                     sample_rate_hz=acquisition.sample_rate_hz, chain=settings))
    if not schedule:
        raise ValueError("reference replay requires an executed schedule")
    schedule = deepcopy(schedule)
    plant.elapsed_time_s = float(schedule[0].get("start_time_s", 0.0))
    started = plant.elapsed_time_s
    forward, reverse_frames = {}, {}
    settings_keys = set(plant.chain_settings())
    for event in schedule:
        for key, value in event.get("chain", {}).items():
            if key not in settings_keys or not np.isfinite(value):
                raise ValueError("unsupported or nonfinite acquisition-chain setting")
            setattr(plant, key, value)
        if event["kind"] == "acquire":
            selected = replace(acquisition, sample_rate_hz=event["sample_rate_hz"],
                               point_count=event["point_count"], point_order=event["point_order"],
                               point_timeout_s=event["point_timeout_s"])
            if list(selected.points(event["amplitude_v"], event["reverse"])) != event["points_v"]:
                raise ValueError("executed point order does not match schedule metadata")
            frame = plant.acquire_block(event["block_id"], event["amplitude_v"], selected,
                                        dwell_s=event["dwell_s"], reverse=event["reverse"])
            name = event["block_id"].removesuffix("_REVERSE")
            (reverse_frames if event["reverse"] else forward)[name] = frame
            if frame.attrs["timed_out"]:
                break
        elif event["kind"] == "recover":
            plant.recover(replace(acquisition, sample_rate_hz=event["sample_rate_hz"]), event["duration_s"])
        elif event["kind"] == "ocp":
            plant.measure_ocp(event["duration_s"], state=event.get("state", "OCP"))
        else:
            raise ValueError(f"unsupported schedule event: {event['kind']}")
    reverse_enabled = any(e.get("reverse", False) for e in schedule)
    result = diagnose(forward, diagnostics, reverse_frames=reverse_frames if reverse_enabled else None,
                      require_release_evidence=False, reverse_required=reverse_enabled,
                      point_timeout_s=acquisition.point_timeout_s)
    expected = model.ru_ohm + model.rp_ohm if expected_rapp_ohm is None else expected_rapp_ohm
    if not np.isfinite(expected) or expected <= 0:
        raise ValueError("known reference resistance must be finite and positive")
    relative_error = None if result.rapp is None else abs(result.rapp - expected) / expected
    reasons = list(result.reason_codes)
    if relative_error is None or relative_error > diagnostics.replay_relative_error:
        reasons.append("REFERENCE_RESISTANCE_MISMATCH")
    probe = plant.path_check("opening")
    if probe["relative_current_error"] > diagnostics.replay_relative_error or abs(probe["voltage_offset_v"]) > diagnostics.min_signal_amplitude_v:
        reasons.append("REFERENCE_CHAIN_MISMATCH")
    metrics = result.to_dict()
    first = next((e for e in schedule if e["kind"] == "acquire"), {})
    metrics.update(elapsed_time_s=plant.elapsed_time_s-started,
                   expected_reference_rapp_ohm=expected, relative_resistance_error=relative_error,
                   relative_error_limit=diagnostics.replay_relative_error, reference_probe=probe,
                   reference_seed=reference_seed, reference_model=asdict(model),
                   schedule={**asdict(acquisition), "dwell_s": first.get("dwell_s"),
                             "reverse_check": reverse_enabled, "events": schedule},
                   replayed_events=plant.schedule)
    return ReplayResult(not reasons, tuple(dict.fromkeys(reasons)), metrics)
