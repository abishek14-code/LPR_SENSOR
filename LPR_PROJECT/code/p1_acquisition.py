"""Generate native electrical acquisition decks from hashed P2 interfaces.

Operational state is produced in the circuit, not attached to a missing trace.
The existing P1 op-amp and lumped topology are retained with separate fixture,
sample and drive switches, clocked analog sample/hold, and explicit telemetry.
"""
from common import ROOT,write,sha
from pathlib import Path
import json,shutil
import numpy as np

CASES=[
 {'id':'nominal_healthy','sigma':1.,'expected':'ACCEPT'},
 {'id':'conductivity_low','sigma':.5,'expected':'ACCEPT'},
 {'id':'conductivity_high','sigma':2.,'expected':'ACCEPT'},
 {'id':'derived_Ru_variation','sigma':.5,'expected':'ACCEPT'},
 {'id':'slow_transient','sigma':1.,'cdl':.001,'retry':True,'expected':'REJECT'},
 {'id':'moderate_noise','sigma':1.,'noise':5e-9,'expected':'ACCEPT'},
 {'id':'higher_noise','sigma':1.,'noise':20e-9,'expected':'REJECT'},
 {'id':'gain_fault','sigma':1.,'gain':1.2,'expected':'REJECT'},
 {'id':'offset_fault','sigma':1.,'offset':20e-9,'expected':'REJECT'},
 {'id':'reverse_gain_fault','sigma':1.,'reverse_gain':1.3,'retry':True,'expected':'REJECT'},
 {'id':'missing_recovery','sigma':1.,'missing_recovery':True,'expected':'INCOMPLETE'},
 {'id':'recovery_instability','sigma':1.,'unstable_recovery':True,'expected':'REJECT'},
 {'id':'timeout','sigma':1.,'timeout':True,'expected':'REJECT'},
 {'id':'reset','sigma':1.,'reset':True,'expected':'INCOMPLETE'},
 {'id':'recoverable_one_retry','sigma':1.,'cdl':.0003,'retry':True,'expected':'ACCEPT'},
 {'id':'unrecoverable_transient','sigma':1.,'cdl':1.,'retry':True,'expected':'REJECT'}
]
STATE={0:'IDLE',1:'CALIBRATION_OPENING',2:'OCP_OPENING',3:'A1',4:'B',5:'A2',
       6:'A1_REVERSE',7:'B_REVERSE',8:'A2_REVERSE',9:'RECOVERY',10:'OCP_CLOSING',
       11:'CALIBRATION_CLOSING',12:'CALIBRATION_REPLAY',13:'REFERENCE_WAIT'}
POINT_DWELL=15.;RETRY_DWELL=45.;RATE=20.;REFERENCE_R=10000.

def schedule(spec):
    events=[];t=0.
    def event(kind,duration,**kw):
        nonlocal t
        events.append({'start':round(t,8),'end':round(t+duration,8),'state_code':kind,
                       'requested_v':0.,'drive':0,'sample_select':1,'point':0,'reverse':0,
                       'calibration_role':0,'recovery_after':0,'attempt':0,**kw});t+=duration
    def calibration(code,attempt):
        # Independent switched known load, same TIA/filter/ADC chain as sample.
        for p,v in enumerate([-.022,-.022,.022,.022,0.,0.]):
            event(code,1.,requested_v=v,drive=1,sample_select=0,point=p,
                  calibration_role=1 if p<4 else 2,attempt=attempt)
    for attempt in range(2 if spec.get('retry') else 1):
        if attempt:
            calibration(12,0)
            event(13,60.,attempt=0)
        calibration(1,attempt)
        event(2,60.,attempt=attempt)
        for reverse in [False,True]:
            for name,amplitude,code in [('A1',.005,3),('B',.01,4),('A2',.005,5)]:
                if reverse:code+=3
                points=np.linspace(-amplitude,amplitude,5)
                if reverse:points=points[::-1]
                for point,v in enumerate(points):
                    duration=RETRY_DWELL if attempt else POINT_DWELL
                    if spec.get('timeout') and not reverse and name=='A1' and point==0:duration=61.
                    event(code,duration,requested_v=float(v),drive=1,point=point,
                          reverse=int(reverse),attempt=attempt)
                # Missing recovery is a circuit logger/sequence fault, not adapter deletion.
                if spec.get('missing_recovery') and not reverse and name=='A1':
                    event(0,1.,requested_v=0.,drive=1,attempt=attempt,recovery_after=code)
                else:event(9,1.,drive=1,attempt=attempt,recovery_after=code)
        event(10,60.,attempt=attempt)
        calibration(11,attempt)
    event(0,.1,attempt=events[-1]['attempt'])
    return events,t

def pwl(events,key):
    # Telemetry/command switches AFTER the final ADC aperture of each interval.
    # ADC falls at n*50ms + 10us; command changes at n*50ms + 50us.
    pairs=[(0.,events[0][key])]
    previous=events[0][key]
    for e in events[1:]:
        if e[key]!=previous:
            pairs.extend([(e['start']+.00004,previous),(e['start']+.00005,e[key])]);previous=e[key]
    pairs.append((events[-1]['end'],previous))
    return 'PWL('+' '.join(f'{t:.10g} {v:.12g}' for t,v in pairs)+')'

def generate(spec,out):
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    p2=ROOT/'evidence/P2'/f"sigma_{spec['sigma']:g}"/'extracted.json'
    p2r=json.loads(p2.read_text());assert p2r['mapping_validated']
    interface={'version':'P2-P1-v3','P2_case_id':p2r['case_id'],'conductivity_S_m':spec['sigma'],
      'geometry_id':p2r['geometry_id'],'electrodes':p2r['electrodes'],
      'FEM_result_id':'conforming_case_t0001.vtu','FEM_result_sha256':p2r['hashes']['conforming_case_t0001.vtu'],
      'extraction_file':str(p2.relative_to(ROOT)),'extraction_sha256':sha(p2),
      'derived_Ru_ohm':p2r['Ru_ohm'],'units':'ohm','P1_parameter_mapping':'RU',
      'counter_to_reference_ohm':p2r['counter_to_reference_ohm'],
      'limitations':p2r['geometry']['uncertainty'],'online_truth_exported':False}
    write(out/'P2_P1_interface.json',interface)
    events,stop=schedule(spec);write(out/'acquisition_plan.json',{'state_dictionary':STATE,'events':events,
       'clock_hz':RATE,'clock_aperture_us':100,'record_origin':'native generated command/telemetry plus circuit sample-hold; plan is not a synthetic measured trace',
       'fixture_R_ohm':REFERENCE_R,'fixture_metadata_known_to_instrument':True,'sample_truth_online':False,
       'instrument_timestamp':'2026-10-03T12:00:00+00:00','instrument_valid_until':'2026-10-04T12:00:00+00:00'})
    params={'RU':interface['derived_Ru_ohm'],'RP':100000.,'CDL':spec.get('cdl',.0001),
       'RCOUNTER':interface['counter_to_reference_ohm'],'RF':1e6,'GAIN':spec.get('gain',1.),
       'OFFSET':spec.get('offset',0.)*1e6,'NOISE':spec.get('noise',0.)*1e6,
       'REVERSEGAIN':spec.get('reverse_gain',1.),'UNSTABLE':int(spec.get('unstable_recovery',False))}
    # Noise is a seeded PWL electronics source. Seed/scenario remain in offline config.
    rng=np.random.default_rng(42);times=np.arange(0,stop+.051,.01);values=rng.normal(size=len(times))
    np.savetxt(out/'noise.txt',np.column_stack([times,values]),fmt=['%.6f','%.9g'])
    model=ROOT/'inputs/P1/models/p1_opamp.lib';shutil.copy2(model,out/'p1_opamp.lib')
    lines=[f'P1 integrated acquisition {spec["id"]}',
      '* NEW simulation integration case; frozen P3 remains unchanged.',
      '* P2-derived model parameters are upstream/offline only.',
      '.param '+' '.join(f'{k}={v:.15g}' for k,v in params.items()),
      '.include p1_opamp.lib']
    keys={'req':'requested_v','state_code':'state_code','sample_select':'sample_select',
          'drive':'drive','point_code':'point','reverse':'reverse','cal_role':'calibration_role',
          'recovery_after':'recovery_after','attempt':'attempt'}
    for node,key in keys.items():lines.append(f'V_{node} {node} 0 '+pwl(events,key))
    if spec.get('reset'):
        start=next(e['start'] for e in events if e['state_code']==3)+1
        lines.append(f'Vreset reset 0 PULSE(0 1 {start} 10u 10u .1 {stop+1})')
    else:lines.append('Vreset reset 0 0')
    lines.extend('''
Vcc vcc 0 5
Rmid_top vcc mid_raw 10k
Rmid_bottom mid_raw 0 10k
Cmid mid_raw 0 10u
Xmid mid_raw vmid vcc 0 vmid P1_OP
Btarget target 0 V=V(vmid)-V(req)
Xref re re_buf vcc 0 re_buf P1_OP
Xcontrol target re_buf vcc 0 drive_amp P1_OP
Sdrive drive_amp ce drive 0 SWITCH_PATH
.model SWITCH_PATH SW(Ron=1m Roff=1e15 Vt=.5 Vh=.05)
Rcounter ce re {RCOUNTER}
Vsense we metal 0
Ssample metal cellmetal sample_select 0 SWITCH_PATH
Bcal_select cal_select 0 V=1-V(sample_select)
Scal metal calmetal cal_select 0 SWITCH_PATH
Rcal calmetal re 10k
Rpolar cellmetal surf {RP}
Cdouble cellmetal surf {CDL}
Runcomp surf re {RU}
Xtia vmid we vcc 0 tia P1_OP
Rfeedback tia we {RF}
Cfeedback tia we 100p
Vnoise nunit 0 PWL FILE=noise.txt
Bcondition conditioned 0 V=V(vmid)+GAIN*if(V(reverse)>.5,REVERSEGAIN,1)*(V(tia)-V(vmid))+OFFSET+NOISE*V(nunit)+if(UNSTABLE>0,if(abs(V(state_code)-9)<.1,.1*sin(2*pi*time*2),0),0)
Rfilter conditioned adc 1k
Cfilter adc 0 1u
Radcload adc 0 1G
Bactual actual 0 V=V(we,re)
* Physical switch-capacitor track/hold models; no post-run time resampling.
Vclock clock 0 PULSE(0 1 .04989 10u 10u 100u .05)
.model SAMPLE_SWITCH SW(Ron=100 Roff=1e15 Vt=.5 Vh=0)
Sadc adc adc_hold clock 0 SAMPLE_SWITCH
Cadc_hold adc_hold 0 1n
Svoltage actual actual_hold clock 0 SAMPLE_SWITCH
Cvoltage_hold actual_hold 0 1n
Bmeas i_meas 0 V=(V(adc_hold)-V(vmid))/RF
* Instrument settings are circuit-generated telemetry, not inferred settings.
Vnom_gain nom_gain 0 1
Vadc_range adc_range 0 5
Vadc_rate adc_rate 0 20
* Truth channels saved only for the separate evaluator; adapter rejects them.
Btrue i_true 0 V=I(Vsense)
Binterface eta_truth 0 V=V(cellmetal,surf)
.options plotwinsize=0 reltol=1e-5 abstol=1p vntol=10n
.save V(clock) V(actual_hold) V(i_meas) V(req) V(drive) V(sample_select) V(state_code) V(point_code) V(reverse) V(cal_role) V(recovery_after) V(attempt) V(reset) V(adc_hold) V(vmid) V(nom_gain) V(adc_range) V(adc_rate) V(i_true) V(eta_truth) V(actual) V(ce)
'''.strip().splitlines())
    lines.extend([f'.tran 0 {stop:.10g} 0 25m','.end'])
    (out/'acquisition.cir').write_text('\n'.join(lines)+'\n')
    write(out/'offline_configuration.json',{'case':spec,'params':params,'stop_s':stop,
        'circuit_sha256':sha(out/'acquisition.cir'),'noise_sha256':sha(out/'noise.txt'),
        'reference_fixture_ohm':REFERENCE_R,'source_opamp_sha256':sha(model),
        'P2_P1_interface_sha256':sha(out/'P2_P1_interface.json')})
    return out/'acquisition.cir'

def main():
    out=ROOT/'evidence/P1';out.mkdir(exist_ok=False)
    files=[generate(s,out/s['id']) for s in CASES]
    write(out/'RUN_LIST.json',{'circuits':[str(p.relative_to(ROOT)) for p in files],
                              'case_count':len(files),'P3_V2_1_modified':False})
    print('Generated',len(files),'native decks')

if __name__=='__main__':main()
