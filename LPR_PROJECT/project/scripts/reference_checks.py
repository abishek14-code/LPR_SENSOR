"""Independent ideal-cell mathematical checks. Never label these LTspice runs."""
from pathlib import Path
import argparse
import json
import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

ROOT = Path(__file__).resolve().parents[1]


def values(ru=1e4, rp=1e5, cdl=1e-4, e=0.01):
    if min(ru, rp, cdl) <= 0:
        raise ValueError('Ru, Rp and Cdl must be positive')
    return {'ru_ohm': ru, 'rp_ohm': rp, 'cdl_F': cdl, 'step_V': e,
            'rdc_ohm': ru+rp, 'tau_s': ru*rp/(ru+rp)*cdl,
            'i_initial_A': e/ru, 'i_steady_A': e/(ru+rp)}


def analytic_current(t, ru=1e4, rp=1e5, cdl=1e-4, e=0.01):
    v = values(ru, rp, cdl, e)
    return v['i_steady_A']+(v['i_initial_A']-v['i_steady_A'])*np.exp(-np.asarray(t)/v['tau_s'])


def validate():
    v = values()
    t = np.linspace(0, 15, 1501)
    solution = solve_ivp(lambda t, eta: [(0.01-eta[0])/1e4/1e-4-eta[0]/1e5/1e-4],
                         [0, 15], [0], t_eval=t, rtol=1e-10, atol=1e-13)
    if not solution.success:
        raise RuntimeError(solution.message)
    numerical = (0.01-solution.y[0])/1e4
    error = float(np.max(np.abs(numerical-analytic_current(t))))
    if error > 1e-12:
        raise AssertionError(f'ODE/closed-form discrepancy: {error}')
    beta = 0.12
    icorr = beta/(2*np.log(10)*1e5)
    faradaic = lambda eta: icorr*(np.exp(np.log(10)*eta/beta)-np.exp(-np.log(10)*eta/beta))
    nonlinear = []
    for amplitude in [0.001, 0.01, 0.05]:
        eta = brentq(lambda x: (amplitude-x)/1e4-faradaic(x), 0, amplitude)
        nonlinear.append(dict(step_V=amplitude, eta_V=eta, current_A=faradaic(eta),
                              apparent_rdc_ohm=amplitude/faradaic(eta)))
    # 1% of final current is a stricter limit than 1% of transient excursion.
    settling = v['tau_s']*np.log(abs(v['i_initial_A']-v['i_steady_A'])/(.01*abs(v['i_steady_A'])))
    return dict(provenance='PYTHON_ANALYTICAL_AND_ODE_ONLY', ltspice_executed=False,
                baseline=v, max_ode_vs_closed_form_error_A=error,
                settling_within_1_percent_final_current_s=float(settling),
                high_cdl_tau_s=values(cdl=.001)['tau_s'],
                adc_filter_f3db_Hz=float(1/(2*np.pi*1e3*1e-6)),
                adc_filter_slow_tau_s=1e4*220e-6,
                expected_gain_fault_rdc_ohm=110000/1.2,
                expected_gain_fault_apparent_rp_ohm=110000/1.2-10000,
                expected_5mV_output_offset_current_A=.005/1e6,
                nonlinear_steady_state=nonlinear,
                limitations='Does not validate the op-amp, switching, or LTspice engine.')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT/'results/electrical/reference_checks.json')
    args = ap.parse_args()
    report = validate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
