import pytest

from python.config import AppConfig
from python.diagnostics import diagnose
from python.lpr_analysis import ResistanceResult, correct_rp
from python.stateful_model import StatefulPlant


@pytest.mark.parametrize("ru", [None, float("nan"), float("inf"), -1, 0])
def test_invalid_ru_cannot_produce_valid_correction(ru):
    result = correct_rp(ResistanceResult(110000, "apparent"), ru, True, True, settled=True, measurement_valid=True)
    assert result.value_ohm is None and not result.valid


@pytest.mark.parametrize("missing", ["enabled", "simplified_model", "settled", "measurement_valid"])
def test_each_correction_assumption_is_required(missing):
    arguments = dict(enabled=True, simplified_model=True, settled=True, measurement_valid=True)
    arguments[missing] = False
    assert not correct_rp(ResistanceResult(110000, "apparent"), 10000, **arguments).valid


def test_estimation_method_and_finite_result_are_enforced():
    with pytest.raises(ValueError, match="estimation method"):
        ResistanceResult(1, "invented")
    assert not ResistanceResult(float("nan"), "apparent").valid


def test_diagnostic_correction_does_not_conflate_enable_with_model():
    c = AppConfig()
    p = StatefulPlant(c.model)
    frames = {}
    for name, amp in (("A1", .005), ("B", .01), ("A2", .005)):
        frames[name] = p.acquire_block(name, amp, c.acquisition)
        p.recover(c.acquisition)
    args = dict(ru_ohm=10000, correction_enabled=True, require_release_evidence=False)
    assert diagnose(frames, c.diagnostics, **args).rp_corrected is None
    corrected = diagnose(frames, c.diagnostics, simplified_model=True, **args)
    assert corrected.rp_corrected == pytest.approx(100000, abs=2)
    for frame in frames.values():
        frame["time_s"] *= .1
    assert diagnose(frames, c.diagnostics, simplified_model=True, **args).rp_corrected is None
