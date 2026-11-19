"""Native simulation integration. P3 is imported unchanged; truth stays in evaluation.

Fresh runs use unique directories. Failed stages retain their evidence and cannot
reuse an older result. Native retries replay an identical prefix in one continuous
transient, then append only the protocol authorized by the prior P3 decision.
"""
from pathlib import Path
import argparse, hashlib, json, os, re, shutil, subprocess, sys, time, uuid
from datetime import datetime, timezone
from dataclasses import replace, asdict
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import run_integrated as adapter
from common import write, sha
from p1_acquisition import pwl
from interfaces import read_vtu

APP = Path('/Applications/LTspice.app/Contents/SharedSupport/ltspice')
ENGINE = APP/'support/ltspice/drive_c/Program Files/ADI/LTspice/LTspice.exe'
WORKSPACE = ROOT.parents[1]
RUNTIME = WORKSPACE/'work/ltspice_cli_20261004/host_runtime/Bottles/ltspice'
OLD = ROOT/'inputs/P1/template'
EXTRA = ('v(sample_select)', 'v(cal_role)', 'v(attempt)', 'v(reset)')
adapter.OBSERVABLE_CHANNELS = (*adapter.OBSERVABLE_CHANNELS, *EXTRA)

def qualified_spatial(p2_root=None, sensitivity_path=None, output=None):
    p2_root=Path(p2_root or ROOT/'evidence/P2')
    """Qualify only the fresh declared planar simulation fixture, never the 3D specimen."""
    m = json.loads((p2_root/'manifest.json').read_text())
    rows = m['results']
    gates = {'flux_balance_fraction': .01, 'energy_flux_disagreement_fraction': .01,
             'mesh_change_fraction': .01, 'analytical_error_fraction': .01}
    for row in rows:
        assert row['mapping_validated']
        assert row['boundary_imbalance_fraction'] < gates['flux_balance_fraction']
        assert abs(row['energy_current_A']/row['mean_boundary_current_magnitude_A']-1) < gates['energy_flux_disagreement_fraction']
        assert abs(row['relative_Ru_difference_P4']) < gates['analytical_error_fraction']
        for name, digest in row['hashes'].items():
            assert sha(p2_root/row['case_id']/name) == digest
    assert m['mesh_refinement_change_fraction'] < gates['mesh_change_fraction']
    interfaces = {}
    # Explicit engineering distributions for the simulation study; no metrology claim.
    assumptions = {'conductivity_relative_half_width': .01, 'reference_distance_relative_half_width': .01,
                   'width_relative_half_width': .01, 'depth_relative_half_width': .01,
                   'distribution': 'independent rectangular engineering assumptions, not measured tolerances'}
    for row in rows[:3]:
        ru = row['Ru_ohm']
        param_u = ru*np.sqrt(4*.01**2/3)
        mesh_bound = ru*m['mesh_refinement_change_fraction']
        interfaces[str(row['conductivity_S_m'])] = dict(
            scope='QUALIFIED DECLARED PLANAR SIMULATION FIXTURE ONLY',
            geometry=m['physical_spec'], conductivity_S_m=row['conductivity_S_m'],
            Ru_ohm=ru, counter_to_reference_ohm=row['counter_to_reference_ohm'],
            standard_uncertainty_ohm=float(np.hypot(param_u, mesh_bound/np.sqrt(3))),
            uncertainty_assumptions=assumptions, mesh_difference_bound_ohm=mesh_bound,
            field_sha256=row['hashes']['conforming_case_t0001.vtu'],
            extraction_sha256=sha(p2_root/row['case_id']/'extracted.json'),
            physical_specimen_qualified=False, path='WE solution surface to nonloading RE / total cell current')
    result = dict(interfaces=interfaces, gates=gates, fresh_cases=len(rows),
                  mesh_change_fraction=m['mesh_refinement_change_fraction'],
                  specimen_3D=json.loads((ROOT/'evidence/RETAINED_3D_QUALIFICATION.json').read_text()))
    sensitivity=Path(sensitivity_path or ROOT/'evidence/SPATIAL_SENSITIVITY.json')
    if sensitivity.exists():
        from copy import deepcopy
        for item in json.loads(sensitivity.read_text())['results']:
            if item['conductivity_S_m']!=1. or item['reference_distance_m']==.02:continue
            value=deepcopy(interfaces['1.0']);distance=item['reference_distance_m']
            assert item['field_sha256']==value['field_sha256']
            assert abs(item['relative_error'])<gates['analytical_error_fraction']
            value['geometry']['reference_distance_m']=distance
            value['geometry']['reference_probe_m']=[.02,.005+distance]
            value['Ru_ohm']=item['Ru_ohm'];value['counter_to_reference_ohm']=rows[1]['bulk_solution_resistance_ohm']-item['Ru_ohm']
            value['standard_uncertainty_ohm']*=distance/.02
            value['mesh_difference_bound_ohm']*=distance/.02
            value['reference_extraction_sha256']=sha(sensitivity)
            interfaces[f'distance_{distance:g}']=value
    write(output or ROOT/'evidence/SPATIAL_INTERFACE.json', result)
    return result

def standard_path(directory=None):
    if directory is not None:
        binding=json.loads((Path(directory)/'STANDARD_BINDING.json').read_text())
        return ROOT/binding.get('standard_path','configs/simulation_standard.json')
    return ROOT/'configs/simulation_standard_v2.json'

def standard_record(directory=None):
    path = standard_path(directory)
    if not path.exists():
        write(path, dict(scope='SIMULATION_FIXTURE_ONLY', standard_id='independent-switched-10k',
            resistance_ohm=10000., relative_half_width=.001,
            tolerance_basis='declared rectangular engineering assumption; not a certified physical standard',
            created_utc=datetime.now(timezone.utc).isoformat(),
            validity='Only the RAW/deck/standard hash bindings recorded by this run; no physical expiry claim',
            training_command_V=[-.001,.001], holdout_command_V=[-.0005,.0005],
            reference_command_V=.001, plateau_duration_s=10., minimum_plateau_samples=100,
            protocol_revision='v2: uniformly longer independent reference observations after 1-second plateau noise failures',
            physics='Independent switched resistor shares the specimen TIA, filter and clocked measurement chain',
            calibration_scope='characterization and holdout validation; no gain correction applied to specimen data'))
    result=json.loads(path.read_text());result['_source_sha256']=sha(path)
    return result

def schedule(dwell=15., attempt=0, start=0., reference_only=False, timeout=False, reset=False):
    events=[]; t=start
    def add(code, duration, **kw):
        nonlocal t
        events.append({**dict(start=t,end=t+duration,state_code=code,requested_v=0.,drive=0,
            sample_select=1,point=0,reverse=0,calibration_role=0,recovery_after=0,attempt=attempt),**kw})
        t += duration
    def calibration(code):
        for index,v in enumerate([-.001,.001,-.0005,.0005,.001]):
            add(code,standard_record()['plateau_duration_s'],requested_v=v,drive=1,sample_select=0,point=index,
                calibration_role=1 if index<2 else 2 if index<4 else 3)
    if reference_only:
        calibration(12)
        add(13,60.)
        return events,t
    calibration(1); add(2,60.)
    for reverse in [False,True]:
        for name,amp,code in [('A1',.005,3),('B',.01,4),('A2',.005,5)]:
            if reverse: code+=3
            points=np.linspace(-amp,amp,5)
            if reverse: points=points[::-1]
            for p,v in enumerate(points):
                duration=61. if timeout and code==3 and p==0 else dwell
                if reset and code==3 and p==0: duration=.1
                add(code,duration,requested_v=float(v),drive=1,point=p,reverse=int(reverse))
                if reset and code==3 and p==0: return events,t
            add(9,1.,drive=1,recovery_after=code)
    add(10,60.); calibration(11)
    return events,t

def generate(directory, spec, spatial, events, stop, reset=False):
    directory.mkdir(parents=True,exist_ok=False)
    base=(OLD/'acquisition.cir').read_text()
    electrical=base[base.index('Vcc vcc'):base.index('.options plotwinsize')]
    # Preserve all native electrical components; only protocol and declared sweep inputs change.
    params={'RU':spatial['Ru_ohm'],'RP':100000.,'CDL':spec.get('cdl',.0001),
        'RCOUNTER':spatial['counter_to_reference_ohm'],'RF':1e6,'GAIN':spec.get('gain',1.),
        'OFFSET':spec.get('offset',0.)*1e6,'NOISE':spec.get('noise',0.)*1e6,
        'REVERSEGAIN':spec.get('reverse_gain',1.),'UNSTABLE':0}
    lines=['Native simulation integration, independent reference phases',
           '.param '+' '.join(f'{k}={v:.15g}' for k,v in params.items()),'.include p1_opamp.lib']
    keys={'req':'requested_v','state_code':'state_code','sample_select':'sample_select','drive':'drive',
          'point_code':'point','reverse':'reverse','cal_role':'calibration_role',
          'recovery_after':'recovery_after','attempt':'attempt'}
    for node,key in keys.items(): lines.append(f'V_{node} {node} 0 '+pwl(events,key))
    lines.append(f'Vreset reset 0 PWL(0 0 {stop-.01:.10g} 0 {stop-.009:.10g} 1 {stop:.10g} 1)' if reset else 'Vreset reset 0 0')
    lines.append(electrical)
    lines.extend(['.options plotwinsize=0 reltol=1e-5 abstol=1p vntol=10n',
        '.save '+' '.join(n.replace('v(','V(') for n in adapter.OBSERVABLE_CHANNELS if n!='time')+' V(i_true) V(eta_truth)',
        f'.tran 0 {stop:.10g} 0 25m','.end'])
    (directory/'acquisition.cir').write_text('\n'.join(lines)+'\n')
    shutil.copy2(OLD/'p1_opamp.lib',directory/'p1_opamp.lib')
    rng=np.random.default_rng(spec.get('seed',42)); times=np.arange(0,stop+.051,.01)
    np.savetxt(directory/'noise.txt',np.c_[times,rng.normal(size=len(times))],fmt=['%.6f','%.9g'])
    write(directory/'acquisition_plan.json',dict(events=events,stop_s=stop,origin='protocol commands, not measurements'))
    write(directory/'P2_INTERFACE.json',spatial)
    write(directory/'OFFLINE_TRUTH.json',dict(parameters=params,scenario=spec,not_controller_input=True))
    write(directory/'STANDARD_BINDING.json',dict(standard_sha256=sha(standard_path()),standard_path=str(standard_path().relative_to(ROOT)),
        circuit_sha256=sha(directory/'acquisition.cir'),scope='simulation standard only',
        standard_component='Rcal calmetal re 10k',sample_component='Rpolar cellmetal surf {RP}'))

def native(directory, timeout=900):
    directory=Path(directory).resolve()
    assert not any((directory/f'acquisition.{ext}').exists() for ext in ['raw','log'])
    engine=Path(os.environ.get('LPR_LTSPICE_EXECUTABLE','/Applications/LTspice.app/Contents/MacOS/LTspice'))
    if not engine.is_file():raise RuntimeError('Native LTspice executable missing: '+str(engine))
    command=[str(engine),'-b',str(directory/'acquisition.cir')]
    env={**os.environ,'LANG':'C','LC_ALL':'C','LC_CTYPE':'C'}
    evidence=dict(command=command,cwd=str(directory),started_ns=time.time_ns(),engine_sha256=sha(engine),
                  input_hashes={name:sha(directory/name) for name in ['acquisition.cir','p1_opamp.lib','noise.txt']})
    write(directory/'NATIVE_EXECUTION.json',evidence)
    with (directory/'console.txt').open('w') as f:
        p=subprocess.Popen(command,cwd=directory,env=env,stdout=f,stderr=subprocess.STDOUT)
        try: evidence['returncode']=p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.terminate();p.wait(timeout=10);evidence['returncode']=p.returncode;evidence['error']='timeout'
    evidence['finished_ns']=time.time_ns()
    evidence['outputs']={p.name:dict(bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns,sha256=sha(p))
        for p in (directory/'acquisition.raw',directory/'acquisition.log') if p.exists()}
    evidence['fresh_pair']=len(evidence['outputs'])==2 and all(x['bytes']>0 and x['mtime_ns']>=evidence['started_ns'] for x in evidence['outputs'].values())
    write(directory/'NATIVE_EXECUTION.json',evidence)
    if evidence['returncode']!=0 or not evidence['fresh_pair']:
        raise RuntimeError(f'Native execution failed; evidence retained in {directory}')
    adapter.read_log(directory/'acquisition.log')
    return evidence

def observations(directory):
    directory=Path(directory)
    execution=json.loads((directory/'NATIVE_EXECUTION.json').read_text())
    binding=json.loads((directory/'STANDARD_BINDING.json').read_text())
    if not execution.get('fresh_pair') or execution.get('returncode')!=0:
        raise ValueError('Missing successful fresh native execution evidence')
    for name,digest in execution['input_hashes'].items():
        if sha(directory/name)!=digest:raise ValueError('Native input changed: '+name)
    for name,identity in execution['outputs'].items():
        if sha(directory/name)!=identity['sha256']:raise ValueError('Native output changed: '+name)
    if binding['standard_sha256']!=sha(standard_path(directory)) or binding['circuit_sha256']!=sha(directory/'acquisition.cir'):
        raise ValueError('Standard-to-native-run binding mismatch')
    if binding['standard_component'] not in (directory/'acquisition.cir').read_text():
        raise ValueError('Independent standard component is absent')
    raw,provenance=adapter.parse_raw(directory/'acquisition.raw')
    planned_stop=json.loads((directory/'acquisition_plan.json').read_text())['stop_s']
    if abs(float(raw['time'].iloc[-1])-planned_stop)>1e-5:
        raise ValueError('Native trace did not reach the requested stop time')
    provenance['log']=adapter.read_log(directory/'acquisition.log')
    samples,indices=adapter.sample_observables(raw)
    for field,node in [('sample_select','v(sample_select)'),('cal_role','v(cal_role)'),('attempt','v(attempt)'),('reset','v(reset)')]:
        values=raw[node].iloc[indices].to_numpy()
        assert np.max(abs(values-np.rint(values)))<1e-5
        samples[field]=np.rint(values).astype(int)
    provenance.update(observations=len(samples),resampled=False,selected_native_rows=indices.tolist())
    return samples,provenance

def calibration(samples, code, provenance, standard, cfg):
    g=samples[samples.state_code==code]
    errors=[]; points=[]
    if g.empty: return None,dict(passed=False,errors=['missing standard phase'])
    if not g.drive_enabled.all() or g.sample_select.any(): errors.append('standard path not independently selected')
    for p in range(5):
        f=g[g.point_index==p]; tail=f.iloc[len(f)//2:]
        if len(tail)<standard['minimum_plateau_samples']:
            errors.append(f'point {p}: insufficient calibration samples'); continue
        if f.requested_voltage_v.nunique()!=1: errors.append('changing reference command')
        points.append(dict(point=p,command_V=float(tail.requested_voltage_v.mean()),
            measured_V=float(tail.actual_voltage_v.mean()),measured_A=float(tail.current_a.mean()),
            std_A=float(tail.current_a.std(ddof=1)),count=len(tail),start_s=float(tail.time_s.iloc[0]),end_s=float(tail.time_s.iloc[-1]),
            role_values=sorted(f.cal_role.unique().tolist())))
    if len(points)!=5: return None,dict(passed=False,errors=errors,points=points)
    expected_commands=[-.001,.001,-.0005,.0005,.001]
    for p,v in zip(points,expected_commands):
        if not np.isclose(p['command_V'],v,rtol=2e-7,atol=1e-12): errors.append('wrong standard protocol')
    if [p['role_values'] for p in points]!=[[1],[1],[2],[2],[3]]:errors.append('wrong observed calibration roles')
    resistance=standard['resistance_ohm']
    x=np.array([p['measured_V']/resistance for p in points]); y=np.array([p['measured_A'] for p in points])
    gain=float((y[1]-y[0])/(x[1]-x[0])); offset=float(y[0]-gain*x[0])
    holdout=np.abs(y[2:4]-(gain*x[2:4]+offset))
    voltage_error=max(abs(p['measured_V']-p['command_V']) for p in points)
    # Existing P3 5% reference tolerance is applied to all independent standard points.
    relative_errors=np.abs(y-x)/np.maximum(abs(x),1e-30)
    if max(relative_errors)>cfg.diagnostics.replay_relative_error:errors.append('standard current error exceeds existing P3 reference rule')
    if voltage_error>cfg.diagnostics.min_signal_amplitude_v:errors.append('standard voltage error exceeds existing P3 reference rule')
    if max(holdout/abs(x[2:4]))>cfg.diagnostics.replay_relative_error:errors.append('independent holdout failure')
    peak_std=max(p['std_A'] for p in points)
    measured_relative=max(abs(gain-1),max(relative_errors),max(holdout/abs(x[2:4])))
    relative_u=float(np.sqrt((standard['relative_half_width']/np.sqrt(3))**2+(measured_relative/np.sqrt(3))**2+(peak_std/max(abs(x)))**2))
    evidence=dict(passed=not errors,errors=errors,points=points,current_gain=gain,current_offset_A=offset,
        holdout_residual_A=holdout.tolist(),max_relative_current_error=float(max(relative_errors)),
        voltage_error_V=voltage_error,relative_standard_uncertainty=relative_u,
        uncertainty_scope='declared standard tolerance + observed transfer deviation as rectangular bounds + plateau noise; simulation only',
        source_RAW_sha256=provenance['raw_sha256'],standard_sha256=standard['_source_sha256'],
        source='SIMULATION known independent switched resistor; native observed standard phase '+str(code))
    check=dict(passed=not errors,source=evidence['source']+' RAW '+provenance['raw_sha256'],
        expected_current_a=points[4]['command_V']/resistance,measured_current_a=points[4]['measured_A'],
        measured_voltage_v=points[4]['measured_V'],requested_voltage_v=points[4]['command_V'])
    return check,evidence

def evaluate(directory, spatial, attempt=0):
    from python.config import AppConfig
    from python.controller import process_recorded_frames
    from python.decision_engine import evaluate_diagnostics,EvaluationContext
    if spatial!=json.loads((directory/'P2_INTERFACE.json').read_text()):
        raise ValueError('Requested spatial interface differs from the bound native circuit interface')
    samples,prov=observations(directory)
    selected=samples[samples.attempt==attempt].copy()
    forward,reverse,ocp=adapter.measurement_groups(selected)
    cfg=AppConfig()
    settings={key:float(forward[name].requested_voltage_v.abs().max()) for name,key in [('A1','a1_amplitude_v'),('B','b_amplitude_v')] if name in forward and forward[name].point_index.nunique()==5}
    for key,val in settings.items():assert np.isclose(val,getattr(cfg.acquisition,key),rtol=2*np.finfo(np.float32).eps,atol=0)
    cfg=replace(cfg,acquisition=replace(cfg.acquisition,**settings))
    std=standard_record(directory)
    opening,cal_open=calibration(selected,1,prov,std,cfg)
    closing,cal_close=calibration(selected,11,prov,std,cfg)
    interrupted=bool(selected.reset.any())
    context=EvaluationContext(source_type='ltspice',simulation_status='success',retries_used=attempt,
        correction_enabled=True,simplified_model=True,ru_ohm=spatial['Ru_ohm'],
        ru_standard_uncertainty_ohm=spatial['standard_uncertainty_ohm'],independent_uncertainties=True)
    if interrupted:
        from python.decision_engine import evaluate_measurement
        context=replace(context,interrupted=True,acquisition_complete=False)
        decision=evaluate_measurement(forward,cfg,context=context,reverse_frames=reverse or None,
            ocp_opening=ocp[0],ocp_closing=ocp[1],opening_check=opening,closing_check=closing)
        diagnostics=None
    else:
        record=process_recorded_frames(cfg,'observed-native-run',forward,source_type='ltspice',
            source_metadata={'source_hash':prov['raw_sha256']},reverse_frames=reverse,
            ocp_opening=ocp[0],ocp_closing=ocp[1],opening_check=opening,closing_check=closing)
        diagnostics=record.diagnostics
        if cal_open['passed'] and cal_close['passed'] and diagnostics.rapp:
            rel=max(cal_open['relative_standard_uncertainty'],cal_close['relative_standard_uncertainty'])
            context=replace(context,calibration_source='SIMULATION_FIXTURE_ONLY RAW '+prov['raw_sha256'],
                rapp_standard_uncertainty_ohm=abs(diagnostics.rapp)*rel)
        decision=evaluate_diagnostics(diagnostics,cfg,context=context)
    data=decision.to_dict()
    result=dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),execution_mode='FRESH_NATIVE_SIMULATION',
        decision=data['decision'],reason_codes=data['reason_codes'],controller_result=data,
        calibration={'opening':cal_open,'closing':cal_close},provenance=prov,
        context=asdict(context),physical_release_authorized=False,corrosion_rate=None,
        P1='fresh LTspice RAW/LOG',P2=spatial.get('scope','qualified declared simulation fixture'),P3='unchanged supplied code',
        P4='offline closed-form comparison only',reacquisitions=attempt,
        analysis_windows={name:[dict(start_s=float(f.time_s.iloc[int(len(f)*.75)]),end_s=float(f.time_s.iloc[-1]),point=int(i))
            for i,f in frame.groupby('point_index',sort=False)] for name,frame in {**forward,**{k+'_REVERSE':v for k,v in reverse.items()}}.items()})
    # Baselines see exactly the same observed A1 samples. Truth joins only AFTER all decisions.
    if 'A1' in forward and forward['A1'].point_index.nunique()==5:
        from python.lpr_analysis import estimate_rapp
        a=forward['A1']; tail=a.groupby('point_index',sort=False).tail(1)
        med=a.groupby('point_index',sort=False)[['current_a','actual_voltage_v']].mean()
        final=a.groupby('point_index',sort=False).apply(lambda x:x.iloc[int(len(x)*.75):][['current_a','actual_voltage_v']].mean(),include_groups=False)
        result['baselines']={label:estimate_rapp(f.actual_voltage_v,f.current_a).value_ohm for label,f in [('last_sample',tail),('whole_point_mean',med),('last_quarter_OLS',final)]}
    truth=json.loads((directory/'OFFLINE_TRUTH.json').read_text())['parameters']
    true_rapp=truth['RU']+truth['RP']
    result['offline_evaluation']=dict(Rapp_expected_ohm=true_rapp,Rp_expected_ohm=truth['RP'],
        Rapp_error_fraction=None if data['measurements'].get('Rapp_ohm') is None else data['measurements']['Rapp_ohm']/true_rapp-1,
        Rp_error_fraction=None if data['rp_estimate'] is None else data['rp_estimate']/truth['RP']-1,
        truth_supplied_to_diagnostics=False)
    write(directory/'decision.json',result)
    report_html(directory/'decision_report.html',result,selected)
    return result,samples

def report_html(path,result,samples):
    import html
    esc=lambda x:html.escape(str(x))
    d=result['controller_result']; checks=''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in [c['name'],c['observed'],c['expected'],'PASS' if c['passed'] else 'FAIL',c['reason_code'] or ''])+'</tr>' for c in d['checks'])
    def plot(column,label):
        stride=max(1,len(samples)//1800);f=samples.iloc[::stride];x=f.time_s.to_numpy();y=f[column].to_numpy()
        if len(f)<2:return ''
        xmin,xmax=float(x.min()),float(x.max());lo,hi=float(y.min()),float(y.max());span=max(hi-lo,1e-15)
        coords=' '.join(f'{35+710*(a-xmin)/max(xmax-xmin,1e-9):.2f},{150-120*(b-lo)/span:.2f}' for a,b in zip(x,y))
        rects=''.join(f'<rect x="{35+710*(w["start_s"]-xmin)/max(xmax-xmin,1e-9):.2f}" y="30" width="{710*(w["end_s"]-w["start_s"])/max(xmax-xmin,1e-9):.2f}" height="120" fill="#dceee2"/>' for ws in result['analysis_windows'].values() for w in ws)
        return f'<h3>{label}</h3><svg viewBox="0 0 780 180">{rects}<polyline points="{coords}" fill="none" stroke="#165e8b" stroke-width="1"/><text x="35" y="172">{xmin:.2f}–{xmax:.2f} s; range {lo:.6g} to {hi:.6g}</text></svg>'
    text=f'''<!doctype html><meta charset="utf-8"><title>LPR simulation decision</title><style>body{{font:15px Arial;margin:30px;color:#163044}}h1{{font-size:32px}}table{{border-collapse:collapse;width:100%;font-size:11px}}td,th{{border:1px solid #bbb;padding:6px;overflow-wrap:anywhere}}svg{{width:100%}}@media print{{tr{{break-inside:avoid}}}}</style>
    <h1>{esc(result['decision'])}</h1><p>{esc(d['recommended_action'])}</p><p>Reasons: {esc(result['reason_codes'])}</p>
    <p>{esc(result['execution_mode'])}; {esc(result['timestamp_utc'])}; actual reacquisitions: {result['reacquisitions']}</p>
    <p>Rapp: {esc(d['measurements'].get('Rapp_ohm'))} Ω; corrected Rp: {esc(d['rp_estimate'])} Ω; simulation-model standard uncertainty of Rp: {esc(d['uncertainty']['rp_standard_uncertainty_ohm'])} Ω.</p>
    <p>ACCEPT means the implemented measurement-quality checks passed under declared simulation assumptions. It does not establish corrosion-free material or physical calibration. No hardware commands. Physical corrosion rate withheld.</p>
    <p>P1: fresh native output; P2: {esc(result['P2'])}; P3: unchanged; P4: offline evaluation. RAW SHA-256: {esc(result['provenance']['raw_sha256'])}</p>
    <p>Plots use native clock-edge observations; display decimation only. Green areas are the last-quarter analysis windows. No analysis resampling.</p>
    {plot('actual_voltage_v','Measured voltage (V)')}{plot('current_a','Measured current (A)')}
    <h2>Actual diagnostic checks</h2><table><tr><th>Check</th><th>Observed</th><th>Rule</th><th>Outcome</th><th>Reason</th></tr>{checks}</table>
    <h2>Calibration and uncertainty scope</h2><pre style="white-space:pre-wrap">{esc(json.dumps(result['calibration'],indent=2))}</pre>'''
    path.write_text(text)

def prefix_check(previous,current,end):
    a=previous[previous.time_s<=end-1e-7].reset_index(drop=True)
    b=current[current.time_s<=end-1e-7].reset_index(drop=True)
    assert len(a)==len(b), 'replayed prefix changed observation count'
    differences={k:float(np.max(abs(a[k].to_numpy(dtype=float)-b[k].to_numpy(dtype=float)))) for k in ['time_s','actual_voltage_v','current_a','requested_voltage_v','drive_enabled','sample_select','point_index','state_code','attempt']}
    for k,d in differences.items():
        tol=1e-9 if k=='time_s' else 1e-11 if k=='current_a' else 1e-7
        assert d<=tol, f'continuous replay prefix mismatch: {k} {d}'
    return dict(points=len(a),max_absolute_differences=differences,method='fresh continuous simulation of identical prefix, then appended actions; no specimen state reset at retry')

def case_run(batch,spec,interfaces):
    from python.config import AppConfig
    cfg=AppConfig();spatial=interfaces[spec.get('spatial_key',str(float(spec.get('sigma',1.))))].copy()
    case=batch/spec['id'];case.mkdir()
    events,end=schedule(timeout=spec.get('timeout',False),reset=spec.get('reset',False))
    d=case/'attempt_0';generate(d,spec,spatial,events,end+.00002,reset=spec.get('reset',False));native(d)
    result,previous=evaluate(d,spatial);attempts=[result];feedback=[];final_report=None
    if result['controller_result']['retry_plan']['repeat_permitted']:
        ref,start=schedule(start=end,reference_only=True)
        refdir=case/'reference_verification';generate(refdir,spec,spatial,events+ref,start+.00002);native(refdir)
        refs,prov=observations(refdir);prefix=prefix_check(previous,refs,end)
        check,cal=calibration(refs,12,prov,standard_record(refdir),cfg)
        write(refdir/'reference_result.json',dict(check=check,calibration=cal,prefix=prefix))
        feedback.append(dict(action='independent native reference verification',passed=cal['passed'],prefix=prefix))
        if cal['passed']:
            retry,stop=schedule(dwell=cfg.acquisition.reacquisition_dwell_s,attempt=1,start=start)
            d=case/'attempt_1';generate(d,spec,spatial,events+ref+retry,stop+.00002);native(d)
            result,current=evaluate(d,spatial,attempt=1)
            feedback.append(dict(action='native reacquisition',dwell_s=cfg.acquisition.reacquisition_dwell_s,
                prefix=prefix_check(refs,current,start),authorized_by=attempts[0]['controller_result']['retry_plan']))
            attempts.append(result)
        else:
            from python.decision_engine import evaluate_diagnostics,EvaluationContext
            result=dict(result)
            failed=evaluate_diagnostics(None,cfg,context=EvaluationContext(source_type='ltspice',simulation_status='success',reference_failed=True))
            result.update(decision=failed.decision,reason_codes=list(failed.reason_codes),controller_result=failed.to_dict())
            result['calibration']={**result['calibration'],'independent_replay':cal}
            result['provenance']=prov
            result['offline_evaluation']={**result['offline_evaluation'],'Rapp_error_fraction':None,'Rp_error_fraction':None}
            write(case/'reference_failure_decision.json',result)
            final_report=case/'reference_failure_report.html'
            report_html(final_report,result,refs)
    write(case/'FINAL_DECISION.json',result)
    summary=dict(case=spec['id'],decision=result['decision'],reason_codes=result['reason_codes'],
        actual_retries=len(attempts)-1,attempts=[dict(decision=a['decision'],reasons=a['reason_codes']) for a in attempts],
        feedback=feedback,report=str((final_report or d/'decision_report.html').relative_to(batch)),
        Rapp_ohm=result['controller_result']['measurements'].get('Rapp_ohm'),Rp_ohm=result['controller_result']['rp_estimate'],
        Rp_standard_uncertainty_ohm=result['controller_result']['uncertainty']['rp_standard_uncertainty_ohm'],
        offline_evaluation=result['offline_evaluation'])
    write(case/'CASE_RESULT.json',summary);print(json.dumps(summary),flush=True)
    return summary

CASES=[dict(id='nominal_healthy'),dict(id='conductivity_low',sigma=.5),dict(id='conductivity_high',sigma=2.),
       dict(id='recoverable',cdl=.0003),dict(id='persistent',cdl=.002),dict(id='gain_fault',gain=1.2),
       dict(id='offset_fault',offset=20e-9),dict(id='moderate_noise',noise=5e-9),dict(id='high_noise',noise=100e-9),
       dict(id='timeout',timeout=True),dict(id='reset',reset=True),
       dict(id='reference_near',spatial_key='distance_0.01'),dict(id='reference_far',spatial_key='distance_0.03'),
       dict(id='moderate_noise_seed7',noise=5e-9,seed=7),dict(id='moderate_noise_seed101',noise=5e-9,seed=101),
       dict(id='reverse_gain_fault',reverse_gain=1.3)]

def main():
    p=argparse.ArgumentParser();p.add_argument('--case',default='nominal_healthy',choices=[x['id'] for x in CASES]);p.add_argument('--matrix',action='store_true');p.add_argument('--batch',type=Path);p.add_argument('--prepare-only',action='store_true');p.add_argument('--fresh-fem',action='store_true');a=p.parse_args()
    adapter.load_runtime(); standard_record()
    batch=(a.batch or ROOT/'evidence/native'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')).resolve()
    batch.mkdir(parents=True,exist_ok=False)
    if a.fresh_fem:
        from p2_fem import main as fem
        from spatial_sensitivity import main as sensitivity
        fem(batch/'P2')
        sensitivity(batch/'P2',batch/'SPATIAL_SENSITIVITY.json')
        spatial=qualified_spatial(batch/'P2',batch/'SPATIAL_SENSITIVITY.json',batch/'SPATIAL_INTERFACE.json')
    else:
        spatial=qualified_spatial()
    if a.prepare_only:
        spec=next(x for x in CASES if x['id']==a.case);events,end=schedule(timeout=spec.get('timeout',False),reset=spec.get('reset',False))
        key=spec.get('spatial_key',str(float(spec.get('sigma',1.))))
        generate(batch/spec['id'],spec,spatial['interfaces'][key],events,end+.00002,reset=spec.get('reset',False));print(batch);return
    selected=CASES if a.matrix else [next(x for x in CASES if x['id']==a.case)]
    write(batch/'manifest.json',dict(status='RUNNING',results=[]))
    (batch/'index.html').write_text('<!doctype html><h1>RUNNING — no final decision yet</h1>')
    write(batch/'RUNNER_SOURCE_HASHES.json',{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/'run_integrated.py',standard_path()]})
    rows=[]
    try:
        for spec in selected:
            rows.append(case_run(batch,spec,spatial['interfaces']))
            write(batch/'manifest.json',dict(status='RUNNING',results=rows))
        write(batch/'manifest.json',dict(status='COMPLETED',results=rows))
    except Exception as e:
        import html
        write(batch/'manifest.json',dict(status='FAILED',error=repr(e),results=rows))
        (batch/'index.html').write_text('<!doctype html><h1>INCOMPLETE / execution failed</h1><p>'+html.escape(str(e))+'</p>')
        raise
    from publish_results import publish
    publish(batch)
    print('Completed fresh native integration:',batch,flush=True)
    return batch

if __name__=='__main__':main()
