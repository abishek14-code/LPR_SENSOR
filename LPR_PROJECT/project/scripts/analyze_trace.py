"""Generate plots/metrics ONLY from supplied waveform-derived CSV, not invented runs."""
from pathlib import Path
import argparse
import csv
import json
import numpy as np
from scipy.optimize import curve_fit
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def analyze(source, output, ru=10000, rp=100000, cdl=.0001):
    with Path(source).open(newline='') as handle:
        records = list(csv.DictReader(handle))
    if len(records) < 10:
        raise ValueError('Need at least ten samples')
    scenarios = {r['scenario'] for r in records}
    if len(scenarios) != 1:
        raise ValueError('Analyze one scenario at a time')
    scenario = scenarios.pop()
    def channel(name):
        return np.array([float(r[name]) for r in records])
    t, req, actual, measured, true = map(channel, ['time', 'requested_voltage', 'actual_voltage', 'current', 'current_true'])
    if not all(np.all(np.isfinite(x)) for x in [t, req, actual, measured, true]):
        raise ValueError('Non-finite input')
    if np.any(np.diff(t) <= 0):
        raise ValueError('Time must strictly increase')
    metrics = dict(scenario=scenario, input_file=Path(source).name,
                   provenance='Computed from imported waveform CSV', samples=len(t),
                   nominal_ru_ohm=ru, nominal_rp_ohm=rp, nominal_cdl_F=cdl,
                   control_error_peak_V=float(np.max(np.abs(actual-req))),
                   measurement_error_rms_A=float(np.sqrt(np.mean((measured-true)**2))))
    if scenario.startswith(('00_', '18_')):
        mask = (t >= 11) & (t <= 12)
        if np.sum(mask) < 5:
            raise ValueError('Missing 11-12s steady-state interval')
        current = float(np.mean(measured[mask]))
        if abs(current) < 1e-15:
            raise ValueError('Near-zero current prevents resistance estimation')
        rdc = float(np.mean(actual[mask])/current)
        metrics.update(i_steady_A=current, rdc_measured_ohm=rdc, rp_estimated_ohm=rdc-ru)
        if scenario.startswith('00_'):
            mask = (t >= 1.002) & (t <= 6)
            fit, _ = curve_fit(lambda x, inf, span, tau: inf+span*np.exp(-x/tau),
                               t[mask]-1.000001, measured[mask]*1e6,
                               p0=[.090909,.909091,.909091], maxfev=5000,
                               bounds=([-np.inf,-np.inf,1e-6],[np.inf,np.inf,100]))
            expected_tau = ru*rp/(ru+rp)*cdl
            metrics.update(tau_fitted_s=float(fit[2]), tau_expected_s=expected_tau,
                           tau_relative_error=float(abs(fit[2]/expected_tau-1)),
                           rdc_relative_error=float(abs(rdc/(ru+rp)-1)))
            metrics['baseline_acceptance_check'] = bool(metrics['tau_relative_error'] < .01 and metrics['rdc_relative_error'] < .01)
    else:
        if t[-1] < 55.9:
            raise ValueError('Full-system trace must include the full 56s sequence')
        plateaus = {}
        for name, start, stop in [('A1',13,14), ('B',31,32), ('A2',49,50)]:
            mask = (t >= start) & (t <= stop)
            if np.sum(mask) < 5:
                raise ValueError(f'Insufficient samples for {name}')
            slope = np.polyfit(t[mask]-start, measured[mask], 1)[0]
            plateaus[name] = dict(voltage_V=float(np.mean(actual[mask])),
                                   current_A=float(np.mean(measured[mask])),
                                   current_true_A=float(np.mean(true[mask])),
                                   current_slope_A_per_s=float(slope),
                                   current_std_A=float(np.std(measured[mask])))
        di = plateaus['A1']['current_A']-plateaus['B']['current_A']
        if abs(di) < 1e-15:
            raise ValueError('Near-zero delta current prevents slope estimation')
        rdc = (plateaus['A1']['voltage_V']-plateaus['B']['voltage_V'])/di
        metrics.update(plateaus=plateaus, rdc_measured_ohm=rdc, rp_estimated_ohm=rdc-ru,
                       warning='Plateau estimates are invalid as Rp if not settled. '
                       'A constant offset can cancel in a two-polarity slope; inspect zero-input recovery too.')
        if scenario.startswith(('03_', '14_', '17_')):
            metrics['nominal_resistance_check'] = bool(abs(rdc/(ru+rp)-1) < .02)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output/'metrics.json').write_text(json.dumps(metrics, indent=2)+'\n')
    fig, axes = plt.subplots(3, 1, figsize=(10,8), sharex=True, constrained_layout=True)
    axes[0].plot(t, req*1e3, '--', label='Requested')
    axes[0].plot(t, actual*1e3, label='Actual WE-RE')
    axes[0].set_ylabel('Perturbation (mV)')
    axes[1].plot(t, true*1e6, label='Cell current (ground truth)')
    axes[1].plot(t, measured*1e6, alpha=.8, label='Measured from ADC input')
    axes[1].set_ylabel('Current (uA)')
    axes[2].plot(t, (measured-true)*1e9, label='Measured minus true')
    axes[2].set_ylabel('Error (nA)')
    axes[2].set_xlabel('Time (s)')
    for ax in axes:
        ax.grid(alpha=.25)
        ax.legend(loc='best', fontsize=8)
    fig.suptitle(f'{scenario} | Imported waveform data')
    fig.savefig(output/'waveforms.png', dpi=180)
    plt.close(fig)
    return metrics


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--ru', type=float, default=10000)
    ap.add_argument('--rp', type=float, default=100000)
    ap.add_argument('--cdl', type=float, default=.0001)
    args = ap.parse_args()
    try:
        print(json.dumps(analyze(args.source, args.output, args.ru, args.rp, args.cdl), indent=2))
    except (ValueError, OSError) as exc:
        ap.exit(2, f'Analysis failed: {exc}\n')
