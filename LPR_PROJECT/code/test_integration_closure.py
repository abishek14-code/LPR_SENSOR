"""Tests of actual native observable boundaries, plus adversarial interface checks."""
from pathlib import Path
import copy,json,sys
import numpy as np
import pandas as pd
import pytest
from close_integration import ROOT,adapter,observations,calibration,standard_record,qualified_spatial
adapter.load_runtime()
from python.config import AppConfig
from python.controller import process_recorded_frames
from python.decision_engine import EvaluationContext,evaluate_diagnostics
from dataclasses import replace

@pytest.fixture(scope='module')
def nominal():
    candidates=sorted((ROOT/'evidence/native').glob('*/nominal_healthy/attempt_0/decision.json'))
    assert candidates,'A genuine completed native case is required'
    directory=candidates[-1].parent
    samples,provenance=observations(directory)
    return directory,samples,provenance

def config(samples):
    c=AppConfig()
    return replace(c,acquisition=replace(c.acquisition,a1_amplitude_v=float(samples[samples.state_code==3].requested_voltage_v.abs().max()),b_amplitude_v=float(samples[samples.state_code==4].requested_voltage_v.abs().max())))

def diagnose_samples(samples,provenance,opening=True,closing=True):
    cfg=config(samples);std=standard_record();forward,reverse,ocp=adapter.measurement_groups(samples)
    op,_=calibration(samples,1,provenance,std,cfg);cl,_=calibration(samples,11,provenance,std,cfg)
    return process_recorded_frames(cfg,'untrusted-scenario-label',forward,source_type='ltspice',
        opening_check=op if opening else None,closing_check=cl if closing else None,
        ocp_opening=ocp[0],ocp_closing=ocp[1],reverse_frames=reverse),cfg

def test_actual_native_nominal_accepts(nominal):
    _,samples,prov=nominal
    record,_=diagnose_samples(samples,prov)
    assert record.decision_result.decision=='ACCEPT'

@pytest.mark.parametrize('phase',[1,11])
def test_standard_gain_fault_cannot_pass(nominal,phase):
    _,samples,prov=nominal;x=samples.copy(deep=True)
    x.loc[x.state_code==phase,'current_a']*=1.2
    check,cal=calibration(x,phase,prov,standard_record(),config(samples))
    assert not check['passed'] and not cal['passed']

def test_wrong_load_context_is_not_calibration(nominal):
    _,samples,prov=nominal;x=samples.copy(deep=True)
    x.loc[x.state_code==1,'sample_select']=1
    check,cal=calibration(x,1,prov,standard_record(),config(samples))
    assert not check['passed'] and not cal['passed']

def test_holdout_is_independent_and_required(nominal):
    _,samples,prov=nominal;x=samples.copy(deep=True)
    x.loc[(x.state_code==1)&(x.point_index==2),'current_a']+=20e-9
    _,cal=calibration(x,1,prov,standard_record(),config(samples))
    assert not cal['passed'] and 'independent holdout failure' in cal['errors']

@pytest.mark.parametrize('phase',['opening','closing'])
def test_ocp_samples_cannot_replace_reference_check(nominal,phase):
    _,samples,prov=nominal
    record,_=diagnose_samples(samples,prov,**{phase:False})
    assert record.decision_result.decision=='REJECT'
    assert 'MISSING_'+phase.upper()+'_CHECK' in record.decision_result.reason_codes

def test_correction_requires_qualified_ru_and_flags(nominal):
    _,samples,prov=nominal;record,cfg=diagnose_samples(samples,prov)
    for kw in [dict(),dict(ru_ohm=10000.),dict(ru_ohm=10000.,correction_enabled=True),dict(ru_ohm=120000.,correction_enabled=True,simplified_model=True)]:
        assert evaluate_diagnostics(record.diagnostics,cfg,context=EvaluationContext(**kw)).rp_estimate is None
    context=EvaluationContext(ru_ohm=10000.,correction_enabled=True,simplified_model=True)
    assert evaluate_diagnostics(record.diagnostics,cfg,context=context).rp_estimate==pytest.approx(record.diagnostics.rapp-10000.)

def test_truth_channels_nonfinite_values_do_not_change_observations(nominal,tmp_path):
    directory,_,_=nominal;path=directory/'acquisition.raw'
    expected,metadata=adapter.parse_raw(path)
    data=bytearray(path.read_bytes());marker='Binary:\n'.encode('utf-16-le');offset=data.index(marker)+len(marker)
    header=data[:offset].decode('utf-16-le');lines=header.split('Variables:\n')[1].split('Binary:')[0].strip().splitlines()
    names=[line.split()[1].lower() for line in lines]
    dtype=np.dtype([('t','<f8'),('v','<f4',(len(names)-1,))]);values=np.frombuffer(data,dtype=dtype,offset=offset)
    changed=[]
    for name in metadata['ignored_channels']:
        values['v'][:,names.index(name)-1]=np.nan;changed.append(name)
    assert {'v(i_true)','v(eta_truth)'}<=set(changed)
    target=tmp_path/'truth_scrubbed.raw';target.write_bytes(data)
    actual,_=adapter.parse_raw(target)
    pd.testing.assert_frame_equal(actual,expected,check_exact=True)

def test_label_changes_cannot_change_diagnostics(nominal):
    _,samples,prov=nominal;record,cfg=diagnose_samples(samples,prov)
    forward,reverse,ocp=adapter.measurement_groups(samples);std=standard_record()
    op,_=calibration(samples,1,prov,std,cfg);cl,_=calibration(samples,11,prov,std,cfg)
    other=process_recorded_frames(cfg,'known-rp-0-damaged-force-accept',forward,source_type='ltspice',
        opening_check=op,closing_check=cl,ocp_opening=ocp[0],ocp_closing=ocp[1],reverse_frames=reverse)
    first=record.decision_result.to_dict();second=other.decision_result.to_dict()
    # The existing adapter derives a descriptive attempt ID from scenario_id.
    # Compare every numerical, check and policy field; only that identity differs.
    first['measurements'].pop('attempt_id');second['measurements'].pop('attempt_id')
    assert second==first

def test_native_source_objects_are_not_mutated(nominal):
    _,samples,prov=nominal;before=samples.copy(deep=True)
    diagnose_samples(samples,prov)
    pd.testing.assert_frame_equal(samples,before,check_exact=True)

def test_only_planar_fixture_is_qualified():
    spatial=qualified_spatial()
    assert spatial['specimen_3D']['online_P3_input'] is False
    assert spatial['interfaces']['1.0']['Ru_ohm']==pytest.approx(10000,rel=1e-10)
    assert not spatial['interfaces']['1.0']['physical_specimen_qualified']

def test_supplied_p3_and_entire_project_unchanged():
    from common import sha
    hashes=json.loads((ROOT/'evidence/ORIGINAL_PROJECT_HASHES.json').read_text())
    assert len(hashes)==185
    for rel,digest in hashes.items():assert sha(ROOT/'project'/rel)==digest,rel

def test_modified_provenance_is_rejected_before_replay(nominal,tmp_path):
    import os,shutil
    directory,_,_=nominal
    for name in ['acquisition.raw','acquisition.log','acquisition.cir','p1_opamp.lib','noise.txt']:
        os.link(directory/name,tmp_path/name)
    for name in ['STANDARD_BINDING.json','acquisition_plan.json','NATIVE_EXECUTION.json']:
        shutil.copy2(directory/name,tmp_path/name)
    metadata=json.loads((tmp_path/'NATIVE_EXECUTION.json').read_text())
    metadata['outputs']['acquisition.raw']['sha256']='0'*64
    (tmp_path/'NATIVE_EXECUTION.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match='Native output changed'):
        observations(tmp_path)

def test_failed_batch_cannot_display_stale_success(tmp_path):
    from publish_results import publish
    (tmp_path/'manifest.json').write_text(json.dumps({'status':'FAILED','results':[]}))
    (tmp_path/'index.html').write_text('<h1>ACCEPT stale</h1>')
    with pytest.raises(ValueError,match='incomplete'):publish(tmp_path)
    assert 'ACCEPT' not in (tmp_path/'index.html').read_text()
    assert 'INCOMPLETE' in (tmp_path/'index.html').read_text()

def test_reference_failure_report_uses_actual_terminal_policy(nominal,tmp_path):
    from close_integration import report_html
    directory,samples,_=nominal
    report=json.loads((directory/'decision.json').read_text())
    failure=evaluate_diagnostics(None,AppConfig(),context=EvaluationContext(source_type='ltspice',simulation_status='success',reference_failed=True))
    report.update(decision=failure.decision,reason_codes=list(failure.reason_codes),controller_result=failure.to_dict())
    target=tmp_path/'reference_failure.html';report_html(target,report,samples)
    text=target.read_text()
    assert '<h1>REJECT</h1>' in text and '<h1>ACCEPT</h1>' not in text
    assert 'REFERENCE_REPLAY_FAILURE' in text
    assert failure.rp_estimate is None
