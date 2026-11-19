"""Read-only verification of a completed integrated native campaign."""
from pathlib import Path
import argparse,json
from common import ROOT,sha,write
from close_integration import observations,adapter,CASES

def validate(batch):
    batch=Path(batch).resolve();adapter.load_runtime()
    manifest=json.loads((batch/'manifest.json').read_text());assert manifest['status']=='COMPLETED'
    assert {r['case'] for r in manifest['results']}=={x['id'] for x in CASES}
    assertions=[];identity=[]
    for case in manifest['results']:
        path=batch/case['case'];decisions=sorted(path.glob('attempt_*/decision.json'))
        assert len(decisions)==1+case['actual_retries']<=2
        for decision in decisions:
            data=json.loads(decision.read_text());directory=decision.parent
            samples,provenance=observations(directory)
            assert data['provenance']['raw_sha256']==provenance['raw_sha256']
            assert data['provenance']['observations']==len(samples)
            assert data['controller_result']['corrosion_estimate'] is None
            if data['decision']!='ACCEPT':assert data['controller_result']['rp_estimate'] is None
            if data['decision']=='ACCEPT':
                assert data['calibration']['opening']['passed'] and data['calibration']['closing']['passed']
                assert all(c['passed'] for c in data['controller_result']['checks'] if c['mandatory'])
                assert data['controller_result']['rp_estimate'] is not None
                assert abs(data['offline_evaluation']['Rp_error_fraction'])<.05
            identity.append(dict(case=case['case'],attempt=directory.name,raw_sha256=provenance['raw_sha256'],
                log_sha256=sha(directory/'acquisition.log'),native_rows=provenance['native_rows'],clock_observations=len(samples)))
        if case['actual_retries']:
            assert case['attempts'][0]['decision']=='WAIT_OR_REPEAT'
            assert case['feedback'][0]['passed']
            assert case['feedback'][1]['authorized_by']['repeat_permitted']
            assert case['feedback'][1]['dwell_s']==45.
            for stage in case['feedback']:
                assert all(v==0 for v in stage['prefix']['max_absolute_differences'].values())
        assertions.append(dict(case=case['case'],decision=case['decision'],verified=True))
    indexed={x['case']:x for x in manifest['results']}
    for case in ['nominal_healthy','conductivity_low','conductivity_high','reference_near','reference_far','recoverable']:
        assert indexed[case]['decision']=='ACCEPT'
    for case in ['persistent','gain_fault','offset_fault','high_noise','timeout','reverse_gain_fault']:
        assert indexed[case]['decision']=='REJECT'
    assert indexed['reset']['decision']=='WAIT_OR_REPEAT' and indexed['reset']['actual_retries']==0
    assert indexed['recoverable']['actual_retries']==indexed['persistent']['actual_retries']==1
    hashes=json.loads((ROOT/'evidence/ORIGINAL_PROJECT_HASHES.json').read_text())
    for rel,digest in hashes.items():assert sha(ROOT/'project'/rel)==digest
    result=dict(status='PASS',cases=assertions,native_identities=identity,
        unchanged_supplied_project_files=len(hashes),historical_external_baseline='Not part of this self-contained release; packaged scientific sources verified above',
        qualification_scope='declared planar simulation fixture; retained 3D qualification remains failed',
        noise_outcomes={k:v['decision'] for k,v in indexed.items() if 'noise' in k})
    write(batch/'RELEASE_VERIFICATION.json',result);print(json.dumps({k:v for k,v in result.items() if k not in ['native_identities','cases']},indent=2))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('batch');a=p.parse_args();validate(a.batch)
