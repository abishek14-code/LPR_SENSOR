"""Project flowchart, wired end to end from one shared-inputs file.

    shared inputs (steel area, water conductivity, electrode geometry)
      ├─> Stage A  Danish: Elmer geometry / water-field model -> effective Ru ± u
      └─> Stage B  cell-interface calibration, or explicitly assumed parameters
    Stage C  Abishek: LTspice potentiostat circuit + Randles cell -> measured channels
    Stage D  Darshan: settling, fault and uncertainty checks (uses Ru ± u) -> decision
      REJECT          -> failure reasons; numeric Rp/corrosion withheld
      ACCEPT          -> Rp ± u and conditional corrosion estimate ± u
      WAIT_OR_REPEAT  -> Darshan's plan (voltage sequence, observation time) -> back to Stage C

Existing stage code is reused, not modified: p2_fem.execute/extract (Elmer),
mesh_refinement.refine, close_integration.native/observations/evaluate/
calibration/prefix_check/schedule (LTspice + P3), and the unchanged P3 package
in project/python. Scenario faults (gain, noise, slow transient, ...) are
simulated plant/instrument conditions and never reach Darshan's stage.
"""
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import replace
import argparse, json, math, os, shutil, sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'code'))
from common import write, sha
import close_integration as ci
from close_integration import adapter
from p1_acquisition import pwl
import p2_fem

SHARED = ROOT/'configs/shared_inputs.json'
ELMER = os.environ.get('LPR_ELMERSOLVER', '/opt/homebrew/bin/ElmerSolver')  # override for a different install
SQ3 = math.sqrt(3)
# Simulated plant / instrument conditions only (names follow close_integration.CASES).
SCENARIO_KEYS = {'cdl', 'gain', 'offset', 'noise', 'seed', 'reverse_gain', 'timeout', 'reset'}
SCENARIOS = {c['id']: {k: v for k, v in c.items() if k in SCENARIO_KEYS} for c in ci.CASES}

def jsonable(value):
    return json.loads(json.dumps(value, allow_nan=False))

# ---------------------------------------------------------------- shared inputs
def load_shared(path=SHARED, **overrides):
    s = json.loads(Path(path).read_text())
    if overrides.get('conductivity') is not None: s['water']['conductivity_S_m'] = float(overrides['conductivity'])
    if overrides.get('area') is not None: s['steel']['exposed_area_m2'] = float(overrides['area'])
    if overrides.get('reference_distance') is not None:
        s['electrode_geometry']['reference_distance_from_we_m'] = float(overrides['reference_distance'])
    if overrides.get('gap') is not None: s['electrode_geometry']['we_to_ce_gap_m'] = float(overrides['gap'])
    g = s['electrode_geometry']; lo, hi = g['supported_gap_range_m']
    if not lo <= g['we_to_ce_gap_m'] <= hi: raise ValueError(f'gap outside supported mesh mapping range {lo}..{hi} m')
    if not 0 < g['reference_distance_from_we_m'] < g['we_to_ce_gap_m']: raise ValueError('RE must lie between WE and CE')
    if min(s['steel']['exposed_area_m2'], s['water']['conductivity_S_m'], g['electrode_width_m']) <= 0:
        raise ValueError('area, conductivity and width must be positive')
    return s

def fem_spec(shared):
    """Translate shared inputs into the geometry spec used by p2_fem.extract / P4."""
    g = shared['electrode_geometry']; w = g['electrode_width_m']; gap = g['we_to_ce_gap_m']
    t = (0.05 - gap)/2  # metal slab thickness; the supplied mesh spans 50 mm in y
    r = g['reference_distance_from_we_m']
    base = json.loads((ROOT/'configs/physical_integration.json').read_text())
    return dict(geometry_id='flowchart-shared-inputs-planar-v1',
        classification='planar fixture parameterised by configs/shared_inputs.json',
        width_m=w, electrolyte_length_m=gap, electrode_thickness_m=t,
        depth_m=shared['steel']['exposed_area_m2']/w, reference_distance_m=r,
        reference_probe_m=[w/2, t + r], electrode_conductivity_S_m=base['electrode_conductivity_S_m'],
        excitation_V=1.0, steel_exposed_area_m2=shared['steel']['exposed_area_m2'])

def map_mesh(source, case, spec):
    """Affine per-region mapping of the supplied connected mesh (mm) to SI shared geometry."""
    for p in source.glob('mesh.*'): shutil.copy2(p, case/p.name)
    n = np.loadtxt(case/'mesh.nodes'); x = n[:, 2]*1e-3; y = n[:, 3]*1e-3
    assert np.isclose(x.min(), 0) and np.isclose(x.max(), .04) and np.isclose(y.min(), 0) and np.isclose(y.max(), .05), 'unexpected supplied mesh extent'
    t = spec['electrode_thickness_m']; gap = spec['electrolyte_length_m']
    ym = np.where(y <= .005, y/.005*t, np.where(y <= .045, t + (y-.005)/.04*gap, t + gap + (y-.045)/.005*t))
    n[:, 2] = x*spec['width_m']/.04; n[:, 3] = ym; n[:, 4] *= 1e-3
    np.savetxt(case/'mesh.nodes', n, fmt=['%d', '%d', '%.16g', '%.16g', '%.16g'])

# ---------------------------------------------------------------- stage A (Elmer)
def stage_a_elmer(shared, out, during=None, overlap=False):
    """Danish's Elmer stage: fresh solves -> effective Ru with a standard uncertainty.

    The nominal solve runs first because the circuit needs its Ru. The three
    uncertainty solves (sigma-, sigma+, refined mesh) then run in parallel with
    each other and with during(nominal), which is used to start LTspice.
    """
    from mesh_refinement import refine
    from concurrent.futures import ThreadPoolExecutor
    spec = fem_spec(shared); out.mkdir(parents=True)
    sigma = shared['water']['conductivity_S_m']; h = shared['water']['conductivity_relative_half_width']
    src = ROOT/'inputs/P2'
    sif = (src/'conforming_case.sif').read_text().replace(
        'Linear System Solver = Iterative', 'Linear System Solver = Direct\n  Linear System Direct Method = UMFPACK')
    def solve(name, s, mesh_from=None):
        case = out/name; case.mkdir()
        if mesh_from is None: map_mesh(src, case, spec)
        else: refine(mesh_from, case)
        (case/'conforming_case.sif').write_text(sif.replace('Electric Conductivity = 1.0\n', f'Electric Conductivity = {s!r}\n'))
        run = p2_fem.execute([ELMER, 'conforming_case.sif'], case, case/'solver.console.txt')
        r = p2_fem.extract(case, spec, s); r['run'] = run; return r
    timeline = TIMELINE
    with timeline.span('Elmer nominal solve'):
        nominal = solve('nominal', sigma)
    def timed(label, *args, **kw):
        with timeline.span(label): return solve(*args, **kw)
    with ThreadPoolExecutor(3 if overlap else 1) as pool:
        jobs = [pool.submit(timed, 'Elmer sigma- solve', 'sigma_low', sigma*(1-h)),
                pool.submit(timed, 'Elmer sigma+ solve', 'sigma_high', sigma*(1+h)),
                pool.submit(timed, 'Elmer refined-mesh solve', 'nominal_refined', sigma, mesh_from=out/'nominal')]
        if not overlap: [j.result() for j in jobs]  # sequential mode: finish every Elmer solve first
        side = during(nominal) if during else None  # overlap mode: runs while the three solves above are in flight
        low, high, fine = [j.result() for j in jobs]
    ru = nominal['Ru_ohm']; g = shared['electrode_geometry']
    budget = {
        'conductivity_from_elmer_sweep': abs(high['Ru_ohm']-low['Ru_ohm'])/2/SQ3,
        'reference_distance': ru*g['reference_distance_relative_half_width']/SQ3,
        'steel_area': ru*shared['steel']['area_relative_half_width']/SQ3,
        'mesh_refinement': abs(fine['Ru_ohm']-ru)/SQ3}
    u = math.sqrt(sum(v*v for v in budget.values()))
    spatial = jsonable(dict(scope='FLOWCHART STAGE A: fresh Elmer solve from shared inputs (planar simulation fixture)',
        geometry=spec, conductivity_S_m=sigma, Ru_ohm=ru, counter_to_reference_ohm=nominal['counter_to_reference_ohm'],
        standard_uncertainty_ohm=u, uncertainty_budget_ohm=budget,
        uncertainty_assumptions=dict(basis=shared['uncertainty_basis'],
            width='enters only through area in the planar model; covered by steel_area term',
            reference_distance='dRu/dr = Ru/r for the verified uniform planar field'),
        mesh_difference_bound_ohm=abs(fine['Ru_ohm']-ru), field_sha256=nominal['hashes']['conforming_case_t0001.vtu'],
        extraction_sha256=sha(out/'nominal/extracted.json'), physical_specimen_qualified=False,
        path='WE solution surface to nonloading RE / total cell current'))
    record = dict(stage='A', owner='Danish (Elmer geometry/water field)', inputs=spec,
        solves={k: dict(case=v['case_id'], conductivity_S_m=v['conductivity_S_m'], Ru_ohm=v['Ru_ohm'],
                        P4_relative_difference=v['relative_Ru_difference_P4'], boundary_imbalance=v['boundary_imbalance_fraction'],
                        mesh_nodes=v['mesh_nodes'], mapping_validated=v['mapping_validated'])
                for k, v in dict(nominal=nominal, sigma_low=low, sigma_high=high, refined=fine).items()},
        output=spatial)
    write(out/'STAGE_A_RU.json', record)
    return spatial, record, side

class Timeline:
    """Wall-clock record of each stage, to show which ones overlapped."""
    def __init__(self): self.rows = []; self.t0 = None
    def reset(self):
        import time; self.rows = []; self.t0 = time.perf_counter()
    def span(self, label):
        import time, contextlib, threading
        @contextlib.contextmanager
        def cm():
            a = time.perf_counter()
            try: yield
            finally: self.rows.append(dict(stage=label, start_s=round(a-self.t0, 2), end_s=round(time.perf_counter()-self.t0, 2), thread=threading.current_thread().name))
        return cm()
TIMELINE = Timeline()

# ---------------------------------------------------------------- stage B (cell interface)
def stage_b_cell(shared, out):
    c = shared['cell_interface']
    if c['mode'] == 'calibrated':
        path = Path(c['calibration_file']); path = path if path.is_absolute() else ROOT/path
        cal = json.loads(path.read_text())
        for key in ['Rp_ohm', 'Cdl_F', 'stern_geary_B_V', 'stern_geary_B_standard_uncertainty_V', 'source']:
            if key not in cal: raise ValueError(f'calibration file missing {key}')
        values = dict(Rp_ohm=cal['Rp_ohm'], Cdl_F=cal['Cdl_F'], B_V=cal['stern_geary_B_V'],
                      B_standard_uncertainty_V=cal['stern_geary_B_standard_uncertainty_V'],
                      status='CALIBRATED', source=cal['source'], calibration_sha256=sha(path))
    elif c['mode'] == 'assumed':
        a = c['assumed']; lo, hi = a['stern_geary_B_range_V']
        cdl = a['Cdl_specific_F_per_cm2']*shared['steel']['exposed_area_m2']*1e4  # F/cm2 x cm2
        values = dict(Rp_ohm=a['Rp_ohm'], Cdl_F=cdl, Cdl_specific_F_per_cm2=a['Cdl_specific_F_per_cm2'], B_V=a['stern_geary_B_V'],
                      B_standard_uncertainty_V=(hi-lo)/2/SQ3, status='EXPLICITLY_ASSUMED', source=a['source'])
    else:
        raise ValueError('cell_interface.mode must be assumed or calibrated')
    values['instrument_calibration'] = ('independent switched 10 kohm standard inside the LTspice run '
        '(opening, closing and pre-repeat reference phases), checked by Darshan\'s stage')
    record = dict(stage='B', owner='cell-interface calibration / assumptions', output=values)
    write(out/'STAGE_B_CELL.json', record)
    return values, record

# ---------------------------------------------------------------- stage C (LTspice)
def build_deck(directory, scenario, spatial, cell, events, stop, reset=False):
    """Same electrical deck as close_integration.generate, with Rp/Cdl from stage B."""
    directory.mkdir(parents=True, exist_ok=False)
    base = (ci.OLD/'acquisition.cir').read_text()
    electrical = base[base.index('Vcc vcc'):base.index('.options plotwinsize')]
    params = {'RU': spatial['Ru_ohm'], 'RP': cell['Rp_ohm'], 'CDL': scenario.get('cdl', cell['Cdl_F']),
        'RCOUNTER': spatial['counter_to_reference_ohm'], 'RF': 1e6, 'GAIN': scenario.get('gain', 1.),
        'OFFSET': scenario.get('offset', 0.)*1e6, 'NOISE': scenario.get('noise', 0.)*1e6,
        'REVERSEGAIN': scenario.get('reverse_gain', 1.), 'UNSTABLE': 0}
    lines = ['LPR flowchart pipeline: shared inputs -> Elmer Ru + cell interface -> P1 circuit',
             '.param '+' '.join(f'{k}={v:.15g}' for k, v in params.items()), '.include p1_opamp.lib']
    keys = {'req': 'requested_v', 'state_code': 'state_code', 'sample_select': 'sample_select', 'drive': 'drive',
            'point_code': 'point', 'reverse': 'reverse', 'cal_role': 'calibration_role',
            'recovery_after': 'recovery_after', 'attempt': 'attempt'}
    for node, key in keys.items(): lines.append(f'V_{node} {node} 0 '+pwl(events, key))
    lines.append(f'Vreset reset 0 PWL(0 0 {stop-.01:.10g} 0 {stop-.009:.10g} 1 {stop:.10g} 1)' if reset else 'Vreset reset 0 0')
    lines.append(electrical)
    lines.extend(['.options plotwinsize=0 reltol=1e-5 abstol=1p vntol=10n',
        '.save '+' '.join(n.replace('v(', 'V(') for n in adapter.OBSERVABLE_CHANNELS if n != 'time')+' V(i_true) V(eta_truth)',
        f'.tran 0 {stop:.10g} 0 25m', '.end'])
    (directory/'acquisition.cir').write_text('\n'.join(lines)+'\n')
    shutil.copy2(ci.OLD/'p1_opamp.lib', directory/'p1_opamp.lib')
    rng = np.random.default_rng(scenario.get('seed', 42)); times = np.arange(0, stop+.051, .01)
    np.savetxt(directory/'noise.txt', np.c_[times, rng.normal(size=len(times))], fmt=['%.6f', '%.9g'])
    write(directory/'acquisition_plan.json', dict(events=events, stop_s=stop, origin='protocol commands, not measurements'))
    write(directory/'P2_INTERFACE.json', spatial)
    write(directory/'OFFLINE_TRUTH.json', dict(parameters=params, scenario=scenario, not_controller_input=True))
    write(directory/'STANDARD_BINDING.json', dict(standard_sha256=sha(ci.standard_path()),
        standard_path=str(ci.standard_path().relative_to(ROOT)), circuit_sha256=sha(directory/'acquisition.cir'),
        scope='simulation standard only', standard_component='Rcal calmetal re 10k', sample_component='Rpolar cellmetal surf {RP}'))

def export_channels(samples, path, attempt):
    cols = [c for c in ['time_s', 'actual_voltage_v', 'current_a', 'requested_voltage_v', 'drive_enabled', 'state',
                        'point_index', 'reverse', 'gain', 'adc_range_v', 'adc_rate_sps', 'sample_select', 'cal_role', 'attempt']
            if c in samples]
    samples.loc[samples.attempt == attempt, cols].to_csv(path, index=False)

# ---------------------------------------------------------------- stage D (Darshan) plans
def acquisition_plan(cfg, observation_s, attempt, reason):
    """Darshan's acquisition choice: the voltage sequence and per-point observation time."""
    a = cfg.acquisition
    seq = {'A1': list(a.points(a.a1_amplitude_v)), 'B': list(a.points(a.b_amplitude_v)), 'A2': list(a.points(a.a1_amplitude_v))}
    if a.enable_reverse_check:
        seq.update({k+'_REVERSE': list(a.points(v, reverse=True)) for k, v in
                    [('A1', a.a1_amplitude_v), ('B', a.b_amplitude_v), ('A2', a.a1_amplitude_v)]})
    return dict(attempt=attempt, voltage_sequence_V=seq, observation_time_per_point_s=float(observation_s),
                point_timeout_s=a.point_timeout_s, sample_rate_hz=a.sample_rate_hz, chosen_by=reason)

def repeat_plan(result, cfg):
    """Turn P3's retry_plan into the next acquisition; None when no repeat is authorised."""
    rp = result['controller_result']['retry_plan']
    if not rp['repeat_permitted']: return None
    pending = rp.get('remaining_observation_s') or 0.
    dwell = pending if pending > 0 else cfg.acquisition.reacquisition_dwell_s
    plan = acquisition_plan(cfg, dwell, rp['retries_used']+1,
        f"P3 WAIT_OR_REPEAT ({', '.join(result['reason_codes'])}); retry {rp['retries_used']+1} of {rp['max_retries']}; "
        + ('remaining point observation' if pending > 0 else 'configured reacquisition dwell'))
    plan['reference_required'] = bool(rp['reference_required'])
    return plan

def check_schedule(events, plan):
    """The circuit stimulus must contain exactly the sequence Darshan's plan requested."""
    codes = {3: 'A1', 4: 'B', 5: 'A2', 6: 'A1_REVERSE', 7: 'B_REVERSE', 8: 'A2_REVERSE'}
    got = {}
    for e in events:
        if e['attempt'] == plan['attempt'] and e['state_code'] in codes: got.setdefault(codes[e['state_code']], []).append(e['requested_v'])
    for block, values in got.items():
        planned = plan['voltage_sequence_V'][block]
        # A reset/interrupted run stops early, so its stimulus is a prefix of the plan.
        if len(values) > len(planned) or not np.allclose(values, planned[:len(values)], rtol=0, atol=1e-12):
            raise AssertionError(f'stimulus for {block} differs from the planned sequence')
    durations = {round(e['end']-e['start'], 9) for e in events if e['attempt'] == plan['attempt'] and e['state_code'] in codes}
    if not durations <= {round(plan['observation_time_per_point_s'], 9), 61., .1}:
        raise AssertionError('point observation time differs from plan')

# ---------------------------------------------------------------- outcome
def failing_checks(controller):
    return [dict(check=c['name'], reason_code=c['reason_code'], explanation=c['explanation'], action=c['recommended_action'])
            for c in controller['checks'] if not c['passed']]

def corrosion_estimate(rp, u_rp, shared, cell):
    area = shared['steel']['exposed_area_m2']; area_cm2 = area*1e4; c = shared['corrosion_conversion']
    B, uB = cell['B_V'], cell['B_standard_uncertainty_V']
    rp_specific = rp*area_cm2
    icorr = B/rp_specific*1e6  # uA/cm2
    rate = c['K_mm_g_per_uA_cm_yr']*icorr*c['equivalent_weight_g_per_eq']/c['density_g_cm3']
    parts = {'stern_geary_B': uB/B, 'steel_area': shared['steel']['area_relative_half_width']/SQ3}
    if u_rp is not None: parts['Rp'] = u_rp/rp
    rel = math.sqrt(sum(v*v for v in parts.values()))
    return dict(status='CONDITIONAL_ESTIMATE', icorr_uA_cm2=icorr, icorr_standard_uncertainty_uA_cm2=icorr*rel,
        corrosion_rate_mm_per_year=rate, corrosion_rate_standard_uncertainty_mm_per_year=rate*rel,
        corrosion_rate_expanded_interval_k2_mm_per_year=[rate*(1-2*rel), rate*(1+2*rel)],
        relative_uncertainty_components=parts, Rp_specific_ohm_cm2=rp_specific,
        rp_uncertainty_included=u_rp is not None, B_status=cell['status'],
        equations=['icorr = B / (Rp * A)', 'CR = K * icorr * EW / rho (ASTM G102)'],
        conditional_on=c['conditions']+[f"B source: {cell['source']}"])

def outcome(result, shared, cell, spatial):
    d = result['controller_result']; decision = result['decision']
    base = dict(decision=decision, reason_codes=result['reason_codes'], Rapp_ohm=d['measurements'].get('Rapp_ohm'),
                Ru_ohm=spatial['Ru_ohm'], Ru_standard_uncertainty_ohm=spatial['standard_uncertainty_ohm'],
                recommended_action=d['recommended_action'])
    if decision == 'ACCEPT' and d['rp_estimate'] is not None:
        u = d['uncertainty']['rp_standard_uncertainty_ohm']
        return dict(base, branch='ACCEPT: Rp and conditional corrosion estimate', Rp_ohm=d['rp_estimate'],
                    Rp_standard_uncertainty_ohm=u, corrosion=corrosion_estimate(d['rp_estimate'], u, shared, cell))
    label = 'REJECT: failure reason, numeric result withheld' if decision == 'REJECT' else \
            'WAIT_OR_REPEAT with no authorised repeat left: numeric result withheld'
    return dict(base, branch=label, Rp_ohm=None, corrosion=None, numeric_result_withheld=True,
                failure_reasons=failing_checks(d) or [dict(reason_code=c, explanation=d['primary_reason']) for c in result['reason_codes']])

# ---------------------------------------------------------------- parameter sources
SOURCES = ROOT/'configs/PARAMETER_SOURCES.json'

def parameter_audit(shared, cell, cfg, path=SOURCES):
    """Every value the run uses is matched to configs/PARAMETER_SOURCES.json.

    A value that differs from the register is reported as CHANGED_NEEDS_SOURCE,
    and a used parameter missing from the register stops the run.
    """
    reg = json.loads(Path(path).read_text()); p = reg['parameters']
    g = shared['electrode_geometry']; c = shared['corrosion_conversion']; a = cfg.acquisition
    used = {
        'water.conductivity_S_m': shared['water']['conductivity_S_m'],
        'water.conductivity_relative_half_width': shared['water']['conductivity_relative_half_width'],
        'steel.exposed_area_m2': shared['steel']['exposed_area_m2'],
        'steel.area_relative_half_width': shared['steel']['area_relative_half_width'],
        'electrode_geometry.we_to_ce_gap_m': g['we_to_ce_gap_m'],
        'electrode_geometry.reference_distance_from_we_m': g['reference_distance_from_we_m'],
        'electrode_geometry.width_m': g['electrode_width_m'],
        'cell.Rp_ohm': cell['Rp_ohm'], 'stern_geary_B_V': cell['B_V'],
        'cell.Cdl_specific_F_per_cm2': cell.get('Cdl_specific_F_per_cm2', cell['Cdl_F']/(shared['steel']['exposed_area_m2']*1e4)),
        'corrosion.K': c['K_mm_g_per_uA_cm_yr'], 'corrosion.equivalent_weight_g_per_eq': c['equivalent_weight_g_per_eq'],
        'corrosion.density_g_cm3': c['density_g_cm3'],
        'lpr.excitation_amplitudes_V': [a.a1_amplitude_v, a.b_amplitude_v], 'lpr.point_dwell_s': a.dwell_s,
        'lpr.reacquisition_dwell_s': a.reacquisition_dwell_s, 'lpr.max_reacquisition_attempts': a.max_reacquisition_attempts,
        'lpr.sample_rate_hz': a.sample_rate_hz}
    if cell['status'] == 'EXPLICITLY_ASSUMED':
        used['stern_geary_B_range_V'] = shared['cell_interface']['assumed']['stern_geary_B_range_V']
    rows = []
    for key in list(used) + [k for k in p if k not in used]:
        if key not in p: raise ValueError(f'parameter {key} has no entry in {path.name}')
        entry = p[key]; status = entry['status']
        if key in used and np.ndim(entry['value']) == np.ndim(used[key]) and not isinstance(entry['value'], str) \
                and not np.allclose(entry['value'], used[key], rtol=1e-9, atol=0):
            status = 'CHANGED_NEEDS_SOURCE'
        if key.startswith('cell.') or key == 'stern_geary_B_V':
            if cell['status'] == 'CALIBRATED': status = 'CALIBRATED (' + cell['source'] + ')'
        rows.append(dict(parameter=key, value=used.get(key, entry['value']), unit=entry['unit'], status=status,
                         basis=entry['basis'], sources=[reg['sources'][s] for s in entry['sources']]))
    counts = {}
    for r in rows: counts[r['status']] = counts.get(r['status'], 0)+1
    return dict(register=str(path), register_sha256=sha(path), counts=counts, parameters=rows,
                flagged=[r['parameter'] for r in rows if r['status'] in {'ASSUMED', 'CONFLICTS_WITH_LITERATURE', 'CHANGED_NEEDS_SOURCE'}])

# ---------------------------------------------------------------- loop
def run(scenario_id='nominal_healthy', shared_path=SHARED, out=None, overlap=False, **overrides):
    from python.config import AppConfig
    adapter.load_runtime(); ci.standard_record()
    shared = load_shared(shared_path, **overrides); scenario = dict(SCENARIOS[scenario_id])
    out = Path(out or ROOT/'evidence/flowchart_runs'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+scenario_id)).resolve()
    out.mkdir(parents=True, exist_ok=False)
    write(out/'SHARED_INPUTS_USED.json', dict(shared=shared, source=str(shared_path), sha256=sha(shared_path), overrides=overrides))
    write(out/'STATUS.json', dict(status='RUNNING', scenario=scenario_id))
    try:
        TIMELINE.reset()
        cell, b_rec = stage_b_cell(shared, out)
        cfg = AppConfig(); loop = []
        audit = parameter_audit(shared, cell, cfg); write(out/'PARAMETER_AUDIT.json', audit)
        plan = acquisition_plan(cfg, cfg.acquisition.dwell_s, 0, 'P3 default acquisition configuration')
        events, end = ci.schedule(dwell=plan['observation_time_per_point_s'], timeout=scenario.get('timeout', False), reset=scenario.get('reset', False))
        check_schedule(events, plan)
        d = out/'C_ltspice/attempt_0'
        def first_ltspice(nominal):
            # The circuit needs only Ru and Rcounter; the Ru uncertainty is finished by the
            # parallel Elmer solves and attached before Darshan's stage reads it.
            provisional = dict(Ru_ohm=nominal['Ru_ohm'], counter_to_reference_ohm=nominal['counter_to_reference_ohm'])
            with TIMELINE.span('LTspice attempt 0'):
                build_deck(d, scenario, provisional, cell, events, end+.00002, reset=scenario.get('reset', False)); ci.native(d)
            return provisional
        spatial, a_rec, provisional = stage_a_elmer(shared, out/'A_elmer', during=first_ltspice, overlap=overlap)
        assert provisional['Ru_ohm'] == spatial['Ru_ohm'] and provisional['counter_to_reference_ohm'] == spatial['counter_to_reference_ohm']
        write(d/'P2_INTERFACE.json', spatial)  # full Ru +- u, now that every Elmer solve has finished
        with TIMELINE.span('Darshan checks, attempt 0'):
            result, prev = ci.evaluate(d, spatial, attempt=0)
        export_channels(prev, d/'measured_channels.csv', 0)
        loop.append(dict(plan=plan, directory=str(d.relative_to(out)), decision=result['decision'], reasons=result['reason_codes']))
        while (nxt := repeat_plan(result, cfg)) is not None:
            if nxt['reference_required']:
                ref, start = ci.schedule(start=end, reference_only=True)
                rd = out/f"C_ltspice/reference_before_attempt_{nxt['attempt']}"
                with TIMELINE.span('LTspice reference check'):
                    build_deck(rd, scenario, spatial, cell, events+ref, start+.00002); ci.native(rd)
                refs, prov = ci.observations(rd); prefix = ci.prefix_check(prev, refs, end)
                chk, cal = ci.calibration(refs, 12, prov, ci.standard_record(rd), cfg)
                write(rd/'reference_result.json', dict(check=chk, calibration=cal, prefix=prefix))
                loop.append(dict(action='independent reference verification', passed=cal['passed'], directory=str(rd.relative_to(out))))
                if not cal['passed']:
                    from python.decision_engine import evaluate_diagnostics, EvaluationContext
                    failed = evaluate_diagnostics(None, cfg, context=EvaluationContext(source_type='ltspice', simulation_status='success', reference_failed=True))
                    result = dict(result, decision=failed.decision, reason_codes=list(failed.reason_codes), controller_result=failed.to_dict())
                    break
                events, end, prev = events+ref, start, refs
            retry, stop = ci.schedule(dwell=nxt['observation_time_per_point_s'], attempt=nxt['attempt'], start=end)
            check_schedule(retry, nxt)
            d = out/f"C_ltspice/attempt_{nxt['attempt']}"
            with TIMELINE.span(f"LTspice attempt {nxt['attempt']}"):
                build_deck(d, scenario, spatial, cell, events+retry, stop+.00002); ci.native(d)
            with TIMELINE.span(f"Darshan checks, attempt {nxt['attempt']}"):
                result, cur = ci.evaluate(d, spatial, attempt=nxt['attempt'])
            prefix = ci.prefix_check(prev, cur, end); export_channels(cur, d/'measured_channels.csv', nxt['attempt'])
            loop.append(dict(plan=nxt, directory=str(d.relative_to(out)), decision=result['decision'], reasons=result['reason_codes'], prefix=prefix))
            events, end, prev = events+retry, stop, cur
        final = outcome(result, shared, cell, spatial)
        record = dict(status='COMPLETED', scenario=scenario_id, scenario_conditions=scenario,
            timestamp_utc=datetime.now(timezone.utc).isoformat(), stage_A=a_rec['output'], stage_B=cell,
            stage_C_D_loop=loop, final=final, offline_evaluation=result.get('offline_evaluation'),
            timeline=sorted(TIMELINE.rows, key=lambda r: r['start_s']),
            parameter_audit=dict(counts=audit['counts'], flagged=audit['flagged'], file='PARAMETER_AUDIT.json'),
            limits=['planar simulation fixture, not a physical specimen', 'cell Rp/Cdl/B ' + cell['status'].lower(),
                    'corrosion rate is conditional on the declared conversion assumptions'])
        write(out/'PIPELINE_RESULT.json', record); write(out/'STATUS.json', dict(status='COMPLETED', decision=final['decision']))
        report(out, record)
        return record, out
    except Exception as e:
        write(out/'STATUS.json', dict(status='FAILED', error=repr(e))); raise

# ---------------------------------------------------------------- report
def report(out, rec):
    import html
    e = lambda x: html.escape(str(x))
    f = rec['final']; A = rec['stage_A']; B = rec['stage_B']
    def num(x, u=None, unit=''):
        if x is None: return 'withheld'
        return f'{x:.6g} ± {u:.2g} {unit}' if u is not None else f'{x:.6g} {unit}'
    loop = ''.join(f"<li>{e(s.get('action') or 'LTspice attempt '+str(s['plan']['attempt']))}: "
                   f"{e(s.get('decision', 'PASS' if s.get('passed') else 'FAIL'))} {e(', '.join(s.get('reasons', [])))}"
                   + (f" — plan: {s['plan']['observation_time_per_point_s']:g} s/point, chosen by {e(s['plan']['chosen_by'])}" if 'plan' in s else '')
                   + f" (<a href='{e(s['directory'])}'>files</a>)</li>" for s in rec['stage_C_D_loop'])
    if f['corrosion']:
        c = f['corrosion']
        outcome_html = (f"<p>Rp = {num(f['Rp_ohm'], f['Rp_standard_uncertainty_ohm'], 'Ω')}</p>"
            f"<p>Conditional corrosion estimate: i<sub>corr</sub> = {num(c['icorr_uA_cm2'], c['icorr_standard_uncertainty_uA_cm2'], 'µA/cm²')}, "
            f"CR = {num(c['corrosion_rate_mm_per_year'], c['corrosion_rate_standard_uncertainty_mm_per_year'], 'mm/yr')} (1σ)</p>"
            "<p>Conditional on:</p><ul>"+''.join(f'<li>{e(x)}</li>' for x in c['conditional_on'])+'</ul>')
    else:
        outcome_html = ('<p>Numeric Rp and corrosion result withheld.</p><ul>'
            + ''.join(f"<li><b>{e(r.get('reason_code'))}</b> {e(r.get('check', ''))}: {e(r.get('explanation'))}</li>" for r in f['failure_reasons'])+'</ul>')
    color = {'ACCEPT': '#1d6b45', 'REJECT': '#a3262a'}.get(f['decision'], '#8a5a00')
    doc = f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LPR flowchart run</title><style>body{{font:15px/1.55 system-ui;max-width:900px;margin:32px auto;padding:0 16px;color:#1b2b38;background:#fff}}
section{{border:1px solid #ccd6de;border-radius:8px;padding:12px 16px;margin:12px 0}}h1{{color:{color}}}h2{{font-size:17px;margin:0 0 6px}}</style>
<h1>{e(f['decision'])} — {e(rec['scenario'])}</h1><p>{e(f['branch'])}. {e(f['recommended_action'])}</p>
<section><h2>Shared inputs</h2><p>Steel area {A['geometry']['steel_exposed_area_m2']:.3g} m², water conductivity {A['conductivity_S_m']:g} S/m, WE–CE gap {A['geometry']['electrolyte_length_m']:g} m, RE at {A['geometry']['reference_distance_m']:g} m, width {A['geometry']['width_m']:g} m.</p></section>
<section><h2>A · Elmer geometry / water field (Danish)</h2><p>Effective Ru = {num(A['Ru_ohm'], A['standard_uncertainty_ohm'], 'Ω')} (1σ). Budget: {e({k: round(v, 4) for k, v in A['uncertainty_budget_ohm'].items()})}</p><p><a href="A_elmer/STAGE_A_RU.json">Elmer solves</a></p></section>
<section><h2>B · Cell interface ({e(B['status'])})</h2><p>Rp {B['Rp_ohm']:g} Ω, Cdl {B['Cdl_F']:g} F, Stern–Geary B {B['B_V']:g} ± {B['B_standard_uncertainty_V']:.2g} V. {e(B['source'])}</p></section>
<section><h2>C/D · LTspice circuit ↔ Darshan's checks</h2><ol>{loop}</ol></section>
<section><h2>Decision</h2>{outcome_html}</section>
<section><h2>Timeline (wall clock, s)</h2><ul>{''.join(f"<li>{x['start_s']:.2f}–{x['end_s']:.2f}: {e(x['stage'])}</li>" for x in rec.get('timeline', []))}</ul><p>Stages that overlap in time ran at the same time.</p></section>
<p>Limits: {e('; '.join(rec['limits']))}.</p>
<section><h2>Parameter sources</h2><p>{e(rec['parameter_audit']['counts'])}. Flagged as assumed or conflicting: {e(', '.join(rec['parameter_audit']['flagged']))}.</p>
<table style="border-collapse:collapse;font-size:12px;width:100%">{''.join(f"<tr style='border-top:1px solid #ccd6de;vertical-align:top'><td><b>{e(r['parameter'])}</b><br>{e(r['value'])} {e(r['unit'])}</td><td>{e(r['status'])}</td><td>{e(r['basis'])}<br><i>{e('; '.join(r['sources']) or 'no source')}</i></td></tr>" for r in json.loads((out/'PARAMETER_AUDIT.json').read_text())['parameters'])}</table></section>"""
    (out/'index.html').write_text(doc)

BRANCH_SCENARIOS = ['nominal_healthy', 'gain_fault', 'recoverable', 'persistent', 'reset']

def _worker(job):
    """One scenario in its own process (used by --jobs)."""
    sid, shared_path, overlap, ov, out = (*job, None)[:5]
    rec, out = run(sid, shared_path, out=out, overlap=overlap, **ov)
    return sid, rec['final'], str(out)

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--scenario', default='nominal_healthy', choices=sorted(SCENARIOS))
    p.add_argument('--demo', action='store_true', help='run nominal_healthy, gain_fault and recoverable (all three branches)')
    p.add_argument('--all-branches', action='store_true', help='run ' + ', '.join(BRANCH_SCENARIOS))
    p.add_argument('--jobs', type=int, default=1, help='scenarios to run at the same time, each in its own process (default 1: one after another)')
    p.add_argument('--overlap', action='store_true', help='inside each run, start LTspice while the Elmer uncertainty solves are still running')
    p.add_argument('--shared', type=Path, default=SHARED)
    p.add_argument('--conductivity', type=float); p.add_argument('--area', type=float)
    p.add_argument('--reference-distance', type=float); p.add_argument('--gap', type=float)
    p.add_argument('--no-open', action='store_true')
    p.add_argument('--out', type=Path, help='output folder for a single scenario (used by the 3D simulator)')
    a = p.parse_args(argv)
    ov = dict(conductivity=a.conductivity, area=a.area, reference_distance=a.reference_distance, gap=a.gap)
    ids = BRANCH_SCENARIOS if a.all_branches else ['nominal_healthy', 'gain_fault', 'recoverable'] if a.demo else [a.scenario]
    jobs = [(sid, a.shared, a.overlap, ov) for sid in ids]
    if a.out is not None:
        if len(jobs) != 1: raise SystemExit('--out needs a single --scenario')
        jobs = [(*jobs[0], a.out)]
    if a.jobs > 1 and len(jobs) > 1:
        import multiprocessing as mp
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(min(a.jobs, len(jobs)), mp_context=mp.get_context('spawn')) as pool:
            results = list(pool.map(_worker, jobs))
    else:
        results = [_worker(j) for j in jobs]
    for sid, f, out in results:
        print(json.dumps(dict(scenario=sid, decision=f['decision'], reasons=f['reason_codes'], Rp_ohm=f.get('Rp_ohm'),
              corrosion_mm_per_year=(f['corrosion'] or {}).get('corrosion_rate_mm_per_year'), report=str(Path(out)/'index.html'))), flush=True)
    if not a.no_open:
        import webbrowser; webbrowser.open((Path(results[-1][2])/'index.html').as_uri())
    return results

if __name__ == '__main__':
    main()
