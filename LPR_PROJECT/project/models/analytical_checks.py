from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class AnalyticalReference:
    ru_ohm: float
    rp_ohm: float
    cdl_f: float
    perturbation_v: float

    @property
    def tau_s(self) -> float:
        return (self.ru_ohm * self.rp_ohm / (self.ru_ohm + self.rp_ohm)) * self.cdl_f

    @property
    def initial_current_a(self) -> float:
        return self.perturbation_v / self.ru_ohm

    @property
    def steady_current_a(self) -> float:
        return self.perturbation_v / (self.ru_ohm + self.rp_ohm)

    @property
    def dc_resistance_ohm(self) -> float:
        return self.ru_ohm + self.rp_ohm

    def to_dict(self):
        return {
            "tau_s": self.tau_s,
            "initial_current_a": self.initial_current_a,
            "steady_current_a": self.steady_current_a,
            "dc_resistance_ohm": self.dc_resistance_ohm,
            "estimation_method": "analytical",
            "parameters": asdict(self),
        }


def reference_case() -> AnalyticalReference:
    return AnalyticalReference(10_000.0, 100_000.0, 100e-6, 10e-3)


def validate_reference_case(relative_tolerance: float = 1e-6) -> dict:
    """Return explicit numerical checks for the shared analytical reference."""
    reference = reference_case()
    expected = {
        "tau_s": 0.9090909090909091,
        "dc_resistance_ohm": 110_000.0,
        "initial_current_a": 1e-6,
        "steady_current_a": 0.09090909090909091e-6,
    }
    actual = {
        "tau_s": reference.tau_s,
        "dc_resistance_ohm": reference.dc_resistance_ohm,
        "initial_current_a": reference.initial_current_a,
        "steady_current_a": reference.steady_current_a,
    }
    checks = {
        key: abs(actual[key] - expected[key]) <= relative_tolerance * max(abs(expected[key]), 1e-30)
        for key in expected
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "expected": expected,
        "actual": actual,
        "relative_tolerance": relative_tolerance,
        "evidence_type": "analytical",
    }


if __name__ == "__main__":
    print(reference_case().to_dict())
