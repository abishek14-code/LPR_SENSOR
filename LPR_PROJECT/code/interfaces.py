"""Separate observable acquisition and spatial/offline boundaries. No plant import."""
from pathlib import Path
import hashlib, re, struct, json
import numpy as np
import pandas as pd

VERSION='integration-v2.1-1'
CONTROLLER_SHA='702d3fbdf4e32a63237d4adc7bdc26f1a2f9d0596fa3cdc197ab642b1c49ff26'
CONVERGENCE_SHA='e3841ca538de5831bd005cd4e00de0df1f5a11759848aa437cbbb15ea6ba284b'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def json_write(p,data):
    from python.io_utils import json_safe
    Path(p).write_text(json.dumps(json_safe(data),indent=2,allow_nan=False)+'\n')
def check_frozen(project):
    assert sha(Path(project)/'python/controller.py')==CONTROLLER_SHA,'wrong controller baseline'
    assert sha(Path(project)/'python/convergence.py')==CONVERGENCE_SHA,'wrong convergence baseline'

def raw_transient(path):
    """Native little-endian Mac RAW: double time, float channels; no resampling."""
    b=Path(path).read_bytes();marker='Binary:\n'.encode('utf-16-le')
    pos=b.index(marker)+len(marker);h=b[:pos].decode('utf-16-le')
    if 'complex' in h.lower() or 'double' in re.search(r'Flags:([^\n]*)',h)[1].lower():
        raise ValueError('unsupported complex/double channel RAW layout')
    n=int(re.search(r'No. Points:\s*(\d+)',h)[1]);nv=int(re.search(r'No. Variables:\s*(\d+)',h)[1])
    names=[x.split()[1].lower() for x in h.split('Variables:\n')[1].split('Binary:')[0].strip().splitlines()]
    dtype=np.dtype([('time','<f8'),('channels','<f4',(nv-1,))])
    if len(names)!=nv or len(b)-pos!=n*dtype.itemsize:raise ValueError('unsupported RAW byte layout')
    a=np.frombuffer(b[pos:],dtype=dtype,count=n)
    values=np.column_stack([a['time'],a['channels']])
    if not np.isfinite(values).all():raise ValueError('nonfinite native data')
    cuts=np.r_[0,np.flatnonzero(np.diff(values[:,0])<0)+1,n]
    return [pd.DataFrame(values[i:j],columns=names) for i,j in zip(cuts[:-1],cuts[1:])],h

P1_MAP={'time_s':'time','actual_voltage_v':'v(actual)','current_a':'v(i_meas)',
        'requested_voltage_v':'v(req)','drive_monitor':'v(relay)',
        'adc_voltage_v':'v(adc)','midpoint_voltage_v':'v(vmid)'}
ONLINE_COLUMNS={'time_s','actual_voltage_v','current_a','requested_voltage_v',
                'commanded_voltage_v','commanded_perturbation_v','sample_duration_s',
                'drive_enabled','reverse','point_index','point_elapsed_time_s',
                'block_id','state','run_id','trial_id','attempt_id','source_type',
                'gain','adc_range_v','adc_rate_sps','ocp_voltage_v'}

def observable_only(frame):
    f=frame.loc[:,[c for c in frame if c in ONLINE_COLUMNS]].copy(deep=True)
    f.attrs={};return f

def import_p1(path,step=0,*,run_id=None,attempt_id=None):
    sweeps,header=raw_transient(path)
    if not 0<=step<len(sweeps):raise ValueError('sweep index out of range')
    f=sweeps[step]
    required={'time','v(actual)','v(i_meas)','v(req)','v(relay)'}
    if not required.issubset(f):raise ValueError('missing observable native channels')
    out=pd.DataFrame({key:f[value].to_numpy(copy=True) for key,value in P1_MAP.items() if value in f})
    dt=np.diff(out.time_s)
    if not (dt>0).all():raise ValueError('non-increasing selected native sweep')
    # Export identifiers are not claimed to be native hardware/controller attempt IDs.
    run=run_id or 'raw-export-'+sha(path)[:12]
    attempt=attempt_id or run+f'/sweep-{step}'
    out['run_id']=run;out['attempt_id']=attempt
    provenance={'source_workstream':'P1','source_file':str(path),'source_sha256':sha(path),
      'classification':'OBSERVABLE (DERIVED lossless numeric export)', 'adapter_version':VERSION,
      'step_index_zero_based':step,'number_of_native_sweeps':len(sweeps),
      'run_id':run,'attempt_id':attempt,'identity_origin':'export identity; native P3 attempt boundaries NOT FOUND',
      'channel_map':{k:v for k,v in P1_MAP.items() if v in f},
      'units':{'time_s':'s','actual_voltage_v':'V','requested_voltage_v':'V','current_a':'A; numeric helper V(i_meas)',
               'drive_monitor':'dimensionless drive command; not physical contact feedback'},
      'polarity':'positive E=V(WE)-V(RE); positive I flows WE through cell toward RE',
      'dt_min_s':float(dt.min()),'dt_max_s':float(dt.max()),
      'native_uniform':bool(np.allclose(dt,np.median(dt),rtol=1e-5,atol=1e-9)),
      'rows':len(out),'resampled':False,'seed':None,'seed_origin':'not inferred from RAW; matching stimulus provenance kept separately',
      'truth_channels_excluded':[c for c in f if c in {'v(i_true)','v(eta)'}],
      'P3_controller_sha256':CONTROLLER_SHA,'P3_convergence_sha256':CONVERGENCE_SHA,
      'metadata_not_found':['native P3 attempt boundaries','five-point sweeps','true OCP','measured calibration','reverse sweeps'],
      'P2_or_truth_online':False}
    deck=Path(path).with_suffix('.cir')
    provenance['configuration_file']=str(deck) if deck.exists() else None
    provenance['configuration_sha256']=sha(deck) if deck.exists() else None
    provenance['parameters_offline_only']=re.findall(r'^\.param\s+(.*)$',deck.read_text(),re.M|re.I) if deck.exists() else []
    provenance['retained_generation_command']='NOT RECORDED; matching native simulator version/log and deck retained'
    provenance['transformation']='binary numeric channel export; no time sorting, interpolation or resampling'
    provenance['export_procedure']='code/interfaces.py import_p1(source_RAW, zero_based_sweep)'
    stimuli=Path(path).parents[1]/'stimuli'
    provenance['stimulus_hashes']={p.name:sha(p) for p in stimuli.glob('noise_seed42.*')}
    if (stimuli/'noise_seed42.json').exists():
        provenance['stimulus_documentation']=json.loads((stimuli/'noise_seed42.json').read_text())
    return out,provenance

def native_plateaus(frame):
    """Deck-defined fixed plateaus, NOT invented controller states or point indices."""
    return {n:observable_only(frame.loc[frame.time_s.between(a,b,inclusive='both')])
            for n,a,b in [('A1',2.001,14.),('B',20.001,32.),('A2',38.001,50.)]}

def assess_native(frame,config=None):
    from python.config import AppConfig
    from python.controller import process_recorded_frames
    clean=observable_only(frame)
    r=process_recorded_frames(config or AppConfig(),'fixed-native-observation',native_plateaus(clean),
                              source_type='ltspice',attempt_id='fixed-native-attempt')
    return {'adapter_contract_status':'INCOMPLETE','controller_decision':r.decision,
            'interpretation':'native data lack required acquisition evidence; controller rejection is not a validated diagnosis of a physical fault',
            'record':r.to_dict(),'automatic_reacquisition':False,'P2_Ru_applied':False}

def clean_evidence(e):
    """Copy only observable measurement tables; reference record is a typed contract."""
    from copy import deepcopy
    clean=deepcopy(e)
    for group in ['frames','reverse_frames','recovery_frames']:
        if clean.get(group) is not None:clean[group]={k:observable_only(f) for k,f in clean[group].items()}
    for k in ['ocp_opening','ocp_closing']:
        if clean.get(k) is not None:clean[k]=observable_only(clean[k])
    return clean

def assess_recording(e,config=None):
    from python.config import AppConfig
    from python.controller import process_recorded_frames,CALIBRATION_NOW
    return process_recorded_frames(config or AppConfig(),'fixed-visible-measurement',attempt_id='fixed-recorded-attempt',
                                   calibration_now=CALIBRATION_NOW,**clean_evidence(e))

def read_vtu(path):
    b=Path(path).read_bytes();start=b.index(b'_',b.index(b'<AppendedData encoding="raw">'))+1
    h=b[:start].decode('latin1')
    def arr(name=None,points=False):
        section=h.split('<Points>')[1].split('</Points>')[0] if points else h
        pattern=r'<DataArray[^>]*'+('' if points else 'Name="'+re.escape(name)+'"')+r'[^>]*/>'
        tag=re.search(pattern,section)[0]
        dt={'Float64':'<f8','Int32':'<i4'}[re.search(r'type="([^"]+)"',tag)[1]]
        offset=int(re.search(r'offset="(\d+)"',tag)[1]);p=start+offset
        size=struct.unpack_from('<I',b,p)[0]
        if size%np.dtype(dt).itemsize:raise ValueError('VTU array layout')
        return np.frombuffer(b,dtype=dt,count=size//np.dtype(dt).itemsize,offset=p+4).copy()
    return {'xyz':arr(points=True).reshape(-1,3),'potential':arr('potential'),
            'J':arr('volume current').reshape(-1,3),'ids':arr('GeometryIds'),
            'conn':arr('connectivity'),'offsets':arr('offsets'),'types':arr('types')}

def p2_summary(vtu,mesh,conductivity=1.):
    """Independent triangle gradients, energy and electrode-boundary flux per depth."""
    a=read_vtu(vtu);nodes=np.loadtxt(Path(mesh)/'mesh.nodes');elements=np.loadtxt(Path(mesh)/'mesh.elements',dtype=int)
    if not np.allclose(nodes[:,2:5],a['xyz'],rtol=0,atol=1e-10):raise ValueError('VTU/mesh node ordering mismatch')
    if not np.isfinite(np.r_[a['potential'],a['xyz'].ravel(),a['J'].ravel()]).all():raise ValueError('invalid FEM fields')
    bybody={i:set(elements[elements[:,1]==i,3:].ravel()) for i in [1,2,3]}
    gradients={};power=0.;cellrows=[]
    for el in elements:
        eid,body,etype,*nids=el
        if etype!=303:raise ValueError('expected linear triangle')
        inds=np.array(nids)-1;xy=a['xyz'][inds,:2];v=a['potential'][inds]
        coeff=np.linalg.solve(np.column_stack([np.ones(3),xy]),v);grad=coeff[1:]
        area=.5*abs(np.linalg.det(np.column_stack([xy[1]-xy[0],xy[2]-xy[0]])))
        sigma=conductivity if body==2 else 1e6
        j=-sigma*grad;power+=sigma*np.dot(grad,grad)*area
        gradients[eid]=j
        cellrows.append({'element_id':eid,'body':body,'x_model':xy[:,0].mean(),'y_model':xy[:,1].mean(),
                         'area_model2':area,'Ex_native':-grad[0],'Ey_native':-grad[1],
                         'Jx_native':j[0],'Jy_native':j[1],'J_magnitude_native':np.linalg.norm(j)})
    boundaries=np.loadtxt(Path(mesh)/'mesh.boundary',dtype=int);flux={};projected={}
    for bid,normal in [(4,np.array([0.,-1.])),(5,np.array([0.,1.]))]:
        total=0.;nodal=0.
        for row in boundaries[boundaries[:,1]==bid]:
            _,_,parent,_,etype,n1,n2=row
            assert etype==202
            length=np.linalg.norm(a['xyz'][n1-1,:2]-a['xyz'][n2-1,:2])
            total+=float(np.dot(gradients[parent],normal))*length
            nodal+=float(np.dot((a['J'][n1-1,:2]+a['J'][n2-1,:2])/2,normal))*length
        flux[str(bid)]=total;projected[str(bid)]=nodal
    delta=float(a['potential'][np.isclose(a['xyz'][:,1],50)].mean()-a['potential'][np.isclose(a['xyz'][:,1],0)].mean())
    cells=pd.DataFrame(cellrows);electrolyte=cells[cells.body==2]
    mean=float(np.average(electrolyte.J_magnitude_native,weights=electrolyte.area_model2))
    std=float(np.sqrt(np.average((electrolyte.J_magnitude_native-mean)**2,weights=electrolyte.area_model2)))
    summary={'source_file':str(vtu),'source_sha256':sha(vtu),'mesh_directory':str(mesh),
       'source_workstream':'P2','classification':'DERIVED spatial/offline; no temporal data',
       'model_case':'corrected shared-interface planar three-region conduction',
       'geometry':'x=0..40; electrode y=0..5 and 45..50; electrolyte y=5..45',
       'nodes':len(nodes),'volume_triangles':len(elements),'boundary_edges':len(boundaries),
       'shared_node_ids_1_2':len(bybody[1]&bybody[2]),'shared_node_ids_2_3':len(bybody[2]&bybody[3]),
       'electrolyte_conductivity_S_m':conductivity,'electrode_conductivity_S_m':1e6,
       'boundary_conditions':'ID4 outer bottom 0 V; ID5 outer top 1 V; no side flux specified',
       'electrodes':'bottom and top conducting slabs; WE/CE distinction and RE location NOT FOUND',
       'delta_voltage_V':delta,'power_per_depth_W_m':power,'energy_current_per_depth_A_m':power/delta,
       'resistance_times_depth_ohm_m':delta**2/power,
       'boundary_current_per_depth_A_m':flux,'projected_boundary_current_per_depth_A_m':projected,
       'boundary_imbalance_fraction':abs(sum(flux.values()))/max(abs(flux['4']),abs(flux['5'])),
       'electrolyte_J_mean_native':mean,'electrolyte_J_min_native':float(electrolyte.J_magnitude_native.min()),
       'electrolyte_J_max_native':float(electrolyte.J_magnitude_native.max()),'electrolyte_J_cv':std/mean,
       'potential_min_V':float(a['potential'].min()),'potential_max_V':float(a['potential'].max()),
       'units_caveat':'R_eff*d and I/d require conductivity S/m and consistent planar coordinate scale. Actual depth d NOT FOUND. Native E/J physical scaling NOT VERIFIED.',
       'extraction':'linear triangle potential gradients; sigma*|grad V|^2 area integral; electrode outward flux sums and separate projected nodal J trapezoids',
       'physical_Ru_verified':False,'online_Ru_eligible':False,'formal_mesh_convergence':'NOT VERIFIED'}
    summary['configuration_hashes']={p.name:sha(p) for p in Path(mesh).iterdir()
                                     if p.is_file() and (p.name.startswith('mesh.') or p.suffix in {'.sif','.geo','.msh'})}
    summary['seed']=None
    summary['generation_command']='ElmerSolver conforming_case.sif; fresh command and engine identity in native manifest' if (Path(mesh)/'solver.console.txt').exists() else 'NOT RECORDED for retained native output'
    summary['adapter_version']=VERSION
    summary['P3_version']='V2.1; spatial data remain outside controller'
    points=pd.DataFrame({'x_model':a['xyz'][:,0],'y_model':a['xyz'][:,1],'z_model':a['xyz'][:,2],
                         'potential_V':a['potential'],'Jx_native':a['J'][:,0],'Jy_native':a['J'][:,1],'Jz_native':a['J'][:,2]})
    return summary,points,cells

def time_average(frame,column,start,end):
    """Offline piecewise-linear quadrature at native times, not a replay resample."""
    t=frame.time_s.to_numpy(float);y=frame[column].to_numpy(float)
    if not t[0]<=start<end<=t[-1]:raise ValueError('window outside data')
    inside=(t>start)&(t<end)
    ts=np.r_[start,t[inside],end];ys=np.r_[np.interp(start,t,y),y[inside],np.interp(end,t,y)]
    return float(np.trapezoid(ys,ts)/(end-start))

def fixed_lpr_summary(frame):
    windows=[('A1',13.,14.),('B',31.,32.),('A2',49.,50.)];rows=[]
    for label,start,end in windows:
        f=frame[frame.time_s.between(start,end)]
        rows.append({'phase':label,'window_start_s':start,'window_end_s':end,'samples':len(f),
          'voltage_time_mean_V':time_average(frame,'actual_voltage_v',start,end),
          'current_time_mean_A':time_average(frame,'current_a',start,end),
          'voltage_sample_mean_V':float(f.actual_voltage_v.mean()),'current_sample_mean_A':float(f.current_a.mean())})
    a,b,c=rows
    ratio=lambda p,q,kind:(p['voltage_'+kind+'_mean_V']-q['voltage_'+kind+'_mean_V'])/(p['current_'+kind+'_mean_A']-q['current_'+kind+'_mean_A'])
    return {'windows':rows,'Rdc_time_weighted_ohm':ratio(a,b,'time'),
            'Rdc_sample_weighted_ohm':ratio(a,b,'sample'),'Rdc_A2_B_time_ohm':ratio(c,b,'time'),
            'A1_A2_current_difference_fraction':abs(a['current_time_mean_A']-c['current_time_mean_A'])/max(abs(a['current_time_mean_A']),abs(c['current_time_mean_A'])),
            'interpretation':'offline fixed-plateau electrical estimate; not accepted P3 multi-point result'}
