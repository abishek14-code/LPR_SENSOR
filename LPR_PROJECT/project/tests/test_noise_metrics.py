import numpy as np
import pandas as pd
import pytest

from python.config import AppConfig
from python.lpr_analysis import analyze_block, snr_db


def trace(noise=0.0, correlated=False):
    rng = np.random.default_rng(67)
    v = np.repeat(np.linspace(-.005, .005, 5), 300)
    perturbation = rng.normal(0, noise, len(v))
    if correlated:
        for i in range(1, len(v)):
            perturbation[i] += .98 * perturbation[i-1]
    return pd.DataFrame(dict(time_s=np.arange(1, len(v)+1)/20,
                             actual_voltage_v=v, current_a=v/110000+perturbation,
                             point_index=np.repeat(np.arange(5), 300)))


def test_noiseless_excitation_steps_are_not_noise():
    metrics = analyze_block("A1", trace(), AppConfig().diagnostics)
    assert metrics.status == "PASS"
    assert metrics.noise_std_a < 1e-20
    assert metrics.snr_db > 200
    assert metrics.resistance.value_ohm == pytest.approx(110000)


def test_known_white_noise_is_estimated_from_stationary_windows():
    metrics = analyze_block("A1", trace(1e-9), AppConfig().diagnostics)
    assert metrics.noise_std_a == pytest.approx(1e-9, rel=.15)
    assert metrics.snr_db > 25


def test_correlated_noise_is_not_silently_accepted():
    metrics = analyze_block("A1", trace(1e-8, correlated=True), AppConfig().diagnostics)
    assert metrics.status == "FAIL"
    assert set(metrics.reason_codes) & {"LOW_SNR", "NOT_SETTLED", "EXCESSIVE_UNCERTAINTY"}


def test_switching_spike_does_not_inflate_stationary_signal_or_noise():
    frame = trace()
    frame.loc[frame.index % 300 == 0, "current_a"] += 1e-5
    metrics = analyze_block("A1", frame, AppConfig().diagnostics)
    assert metrics.noise_std_a < 1e-20
    assert metrics.resistance.value_ohm == pytest.approx(110000)


def test_zero_noise_and_absent_signal_conventions():
    assert snr_db(1, 0) == float("inf")
    assert snr_db(0, 0) == float("-inf")


def test_low_voltage_signal_has_its_own_gate():
    frame = trace()
    frame["actual_voltage_v"] *= .01
    frame["current_a"] *= .01
    assert "INSUFFICIENT_SIGNAL" in analyze_block("A1", frame, AppConfig().diagnostics).reason_codes
