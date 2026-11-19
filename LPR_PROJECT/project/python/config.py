from dataclasses import dataclass, field
from typing import Tuple
from .validation import finite_number, integer, boolean


@dataclass(frozen=True)
class ModelConfig:
    ru_ohm: float = 10_000.0
    rp_ohm: float = 100_000.0
    cdl_f: float = 100e-6
    ocp_v: float = 0.0

    def __post_init__(self):
        for name, value in self.__dict__.items():
            finite_number(value, name, positive=name != "ocp_v")


@dataclass(frozen=True)
class AcquisitionConfig:
    a1_amplitude_v: float = 5e-3
    b_amplitude_v: float = 10e-3
    point_order: str = "forward"
    point_count: int = 5
    # Default is intentionally long enough for the healthy synthetic plant to
    # reach a demonstrably settled condition. It remains configurable.
    dwell_s: float = 15.0
    recovery_s: float = 1.0
    sample_rate_hz: float = 20.0
    reacquisition_dwell_s: float = 45.0
    max_reacquisition_attempts: int = 1
    point_timeout_s: float = 60.0
    enable_reverse_check: bool = True

    def __post_init__(self):
        for name in ("a1_amplitude_v", "b_amplitude_v", "dwell_s", "recovery_s", "sample_rate_hz", "reacquisition_dwell_s", "point_timeout_s"):
            finite_number(getattr(self, name), name, positive=True)
        integer(self.point_count, "point_count", minimum=3)
        integer(self.max_reacquisition_attempts, "max_reacquisition_attempts")
        boolean(self.enable_reverse_check, "enable_reverse_check")
        if not isinstance(self.point_order, str) or self.point_order not in {"forward", "reverse"}:
            raise ValueError("point_order must be forward or reverse")
        if self.max_reacquisition_attempts not in (0, 1):
            raise ValueError("at most one reacquisition is permitted")

    def points(self, amplitude_v: float, reverse: bool = False) -> Tuple[float, ...]:
        points = tuple(
            amplitude_v * value
            for value in (-1.0 + 2.0 * index / (self.point_count - 1) for index in range(self.point_count))
        )
        return tuple(reversed(points)) if reverse != (self.point_order == "reverse") else points


@dataclass(frozen=True)
class DiagnosticConfig:
    min_samples_per_point: int = 4
    min_r_squared: float = 0.98
    max_relative_consistency_error: float = 0.15
    min_signal_amplitude_v: float = 1e-4
    min_snr_db: float = 10.0
    settling_window_s: float = 5.0
    settling_observation_s: float = 10.0
    settling_relative_slope: float = 0.02
    settling_window_difference_fraction: float = 0.05
    max_noise_to_signal: float = 0.25
    max_settling_variation: float = 0.25
    ocp_drift_v_per_s: float = 1e-3 / 60.0
    max_recovery_shift_v: float = 2e-3
    ocp_observation_s: float = 60.0
    replay_relative_error: float = 0.05
    uncertainty_fraction: float = 0.02
    uncertainty_multiplier: float = 2.0
    min_signal_to_uncertainty: float = 10.0

    def __post_init__(self):
        for name, value in self.__dict__.items():
            finite_number(value, name)
        integer(self.min_samples_per_point, "min_samples_per_point", minimum=4)
        if not 0 <= self.min_r_squared <= 1:
            raise ValueError("min_r_squared must be between zero and one")
        if any(value <= 0 for key, value in self.__dict__.items() if key not in {"min_r_squared", "min_snr_db"}):
            raise ValueError("diagnostic magnitudes and durations must be positive")
        if self.settling_observation_s < 2 * self.settling_window_s:
            raise ValueError("observation must contain two nonoverlapping settling windows")


@dataclass(frozen=True)
class AppConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)
    diagnostics: DiagnosticConfig = field(default_factory=DiagnosticConfig)
    seed: int = 42
    controller_variant: str = "enhanced"
    reference_model: ModelConfig = field(default_factory=ModelConfig)

    def __post_init__(self):
        for name, expected in (("model", ModelConfig), ("reference_model", ModelConfig), ("acquisition", AcquisitionConfig), ("diagnostics", DiagnosticConfig)):
            if not isinstance(getattr(self, name), expected):
                raise ValueError(f"{name} must be {expected.__name__}")
        integer(self.seed, "seed")
        if not isinstance(self.controller_variant, str) or self.controller_variant not in {"baseline", "conservative", "enhanced", "no_reverse"}:
            raise ValueError("unsupported controller_variant")
