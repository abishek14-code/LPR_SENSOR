"""Check actual ASC symbol pins/wires, native netlist, and sampled native output."""
from pathlib import Path
import contextlib
import io
import json
import math
import re
import shutil
import tempfile
import select_case

ROOT=Path(__file__).resolve().parent

def read_text(path):
    data=Path(path).read_bytes()
    if b'\x00' in data[:100]:return data.decode('utf-16-le').lstrip('\ufeff')
    return data.decode('utf-8-sig')

def number(text):
    m=re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)([a-z]*)',text.lower().replace('µ','u'))
    if not m:raise ValueError(text)
    return float(m[1])*{'':1,'meg':1e6,'k':1e3,'m':1e-3,'u':1e-6,'n':1e-9,'p':1e-12,'g':1e9}[m[2]]

def get_params(text):
    result={}
    for line in text.splitlines():
        if '.param ' in line:
            result.update({k:number(v) for k,v in re.findall(r'(\w+)=([^\s]+)',line.split('.param ',1)[1])})
    return result

def asc_devices(path):
    lines=read_text(path).splitlines();symbols=[];wires=[];labels={};points=set()
    for line in lines:
        fields=line.split()
        if not fields:continue
        if fields[0]=='SYMBOL':
            assert fields[4]=='R0','Verifier needs an orientation transform for this edited symbol'
            current={'symbol':fields[1],'origin':tuple(map(int,fields[2:4]))};symbols.append(current)
        elif line.startswith('SYMATTR '):
            _,key,value=line.split(None,2);current[key]=value
        elif fields[0]=='WIRE':
            x,y,a,b=map(int,fields[1:]);wires.append(((x,y),(a,b)));points.update([(x,y),(a,b)])
        elif fields[0]=='FLAG':
            p=tuple(map(int,fields[1:3]));labels[p]=fields[3].lower();points.add(p)
    for s in symbols:
        pins={};prefix=None
        for line in read_text(path.parent/(s['symbol']+'.asy')).splitlines():
            fields=line.split()
            if line.startswith('PIN '):pin=tuple(map(int,fields[1:3]))
            elif line.startswith('PINATTR SpiceOrder '):pins[int(fields[2])]=pin
            elif line.startswith('SYMATTR Prefix '):prefix=fields[2]
        s['pins']=[(s['origin'][0]+p[0],s['origin'][1]+p[1]) for _,p in sorted(pins.items())]
        s['instance']=('X' if prefix=='X' else '')+s['InstName'];points.update(s['pins'])
    parents={p:p for p in points}
    assert len(labels)==len(set(labels.values())), 'Multiple labels could conceal disconnected wire islands'
    def find(p):
        if parents[p]!=p:parents[p]=find(parents[p])
        return parents[p]
    def union(a,b):parents[find(a)]=find(b)
    for a,b in wires:
        assert a[0]==b[0] or a[1]==b[1]
        for p in points:
            if (a[0]==b[0]==p[0] and min(a[1],b[1])<=p[1]<=max(a[1],b[1])) or (a[1]==b[1]==p[1] and min(a[0],b[0])<=p[0]<=max(a[0],b[0])):union(a,p)
    nets={}
    for p,label in labels.items():nets.setdefault(find(p),set()).add(label)
    assert all(len(names)==1 for names in nets.values()),['Short between distinct labeled nets', [sorted(n) for n in nets.values() if len(n)>1]]
    return {s['instance'].lower():{'nodes':[next(iter(nets[find(p)])) for p in s['pins']], 'value':s['Value']} for s in symbols}

def net_devices(path):
    devices={}
    contract=json.loads((ROOT/'connection_contract.json').read_text())['devices']
    counts={d['instance'].lower():len(d['nodes']) for d in contract.values()}
    for line in read_text(path).splitlines():
        if not line or line[0] not in 'RCSVXB':continue
        n=counts.get(line.split()[0].lower(),5 if line[0]=='X' else 4 if line[0]=='S' else 2)
        fields=line.split(None,n+1)
        devices[fields[0].lower()]={'nodes':[x.lower() for x in fields[1:n+1]],'value':fields[-1]}
    return devices

def check_devices(devices, root):
    contract=json.loads((ROOT/'connection_contract.json').read_text())['devices']
    case=json.loads((root/'selected_case.json').read_text())
    assert set(devices)=={x['instance'].lower() for x in contract.values()}
    reversed_passives=[]
    for name,original in contract.items():
        device=devices[original['instance'].lower()]
        old=[n.lower() for n in original['nodes']]
        if device['nodes']!=old:
            assert name[0] in 'RC' and device['nodes']==old[::-1],(name,device['nodes'],old)
            reversed_passives.append(name)
        value=device['value'].replace('µ','u').replace('μ','u')
        if name in case['sources']:
            path=value.split('=',1)[1]
            actual=[tuple(map(float,line.split())) for line in (root/path).read_text().splitlines()]
            expected=case['sources'][name]['value']
            if expected.startswith('PWL('):
                tokens=list(map(float,expected[4:-1].split()));assert actual==list(zip(tokens[::2],tokens[1::2]))
            else:assert all(pair[1]==float(expected) for pair in actual)
        else:
            assert re.sub(r'\s','',value).lower()==re.sub(r'\s','',original['value']).lower(),(name,value,original['value'])
    return {'devices':len(devices),'reversed_symmetric_passives':reversed_passives}

def sampled(path):
    import numpy as np
    raw=path.read_bytes();marker='Binary:\n'.encode('utf-16-le');offset=raw.index(marker)+len(marker)
    header=raw[:offset].decode('utf-16-le');nv=int(re.search(r'No. Variables:\s*(\d+)',header)[1]);count=int(re.search(r'No. Points:\s*(\d+)',header)[1])
    names=[line.split()[1].lower() for line in header.split('Variables:\n')[1].split('Binary:')[0].strip().splitlines()]
    dtype=np.dtype([('time','<f8'),('values','<f4',(nv-1,))]);assert len(raw)-offset==count*dtype.itemsize
    data=np.frombuffer(raw,dtype=dtype,count=count,offset=offset)
    clock=data['values'][:,names.index('v(clock)')-1]
    indices=np.flatnonzero((clock[:-1]>1e-7)&(clock[1:]<=1e-7))+1
    columns={n:data['values'][indices,names.index(n)-1].astype(float) for n in ['v(actual_hold)','v(i_meas)','v(req)','v(state_code)','v(point_code)','v(reset)']}
    columns['time']=data['time'][indices].copy()
    assert all(np.isfinite(x).all() for x in columns.values())
    return columns,float(data['time'][-1])

def slope(samples):
    import numpy as np
    state=np.rint(samples['v(state_code)']).astype(int);point=np.rint(samples['v(point_code)']).astype(int)
    rows=[]
    for p in range(5):
        indices=np.flatnonzero((state==3)&(point==p))
        if len(indices)<4:return None
        tail=indices[-max(2,len(indices)//4):]
        rows.append([float(samples['v(actual_hold)'][tail].mean()),float(samples['v(i_meas)'][tail].mean())])
    values=np.asarray(rows)
    return float(np.linalg.lstsq(np.c_[values[:,1],np.ones(5)],values[:,0],rcond=None)[0][0])

def main():
    report={'asc_connectivity':check_devices(asc_devices(ROOT/'LPR_Unified.asc'),ROOT)}
    contract=json.loads((ROOT/'connection_contract.json').read_text())
    library=(ROOT/'behavioral_blocks.lib').read_text()
    for name,device in contract['devices'].items():
        if device.get('behavioral_wrapper'):
            expected=f"{name} {device['original_nodes'][0]} gnd {device['original_value']}"
            assert expected in library.splitlines(),name
    report['one_label_per_net_and_original_behavioral_equations']='PASS'
    if (ROOT/'LPR_Unified.net').exists():report['native_netlist_connectivity']=check_devices(net_devices(ROOT/'LPR_Unified.net'),ROOT)
    catalog=json.loads((ROOT/'cases.json').read_text())
    with tempfile.TemporaryDirectory(prefix='lpr-presets-') as name:
        tmp=Path(name)
        for filename in ['cases.json','LPR_Unified.asc','connection_contract.json']:shutil.copy2(ROOT/filename,tmp/filename)
        shutil.copytree(ROOT/'noise_banks',tmp/'noise_banks')
        for path in ROOT.glob('*.asy'):shutil.copy2(path,tmp/path.name)
        select_case.ROOT=tmp
        for name,case in catalog.items():
            with contextlib.redirect_stdout(io.StringIO()):select_case.select(name)
            check_devices(asc_devices(tmp/'LPR_Unified.asc'),tmp)
            actual=get_params((tmp/'LPR_Unified.asc').read_text())
            for key,value in case['parameters'].items():assert math.isclose(actual[key],float(value),rel_tol=1e-12,abs_tol=1e-15)
        report['preset_cases_verified']=list(catalog)
    import numpy as np
    source,source_end=sampled(ROOT/'validation/source_nominal/acquisition.raw')
    graph,graph_end=sampled(ROOT/'LPR_Unified.raw')
    assert abs(source_end-676.00002)<1e-5 and abs(graph_end-source_end)<1e-5
    assert len(source['time'])==len(graph['time'])
    assert np.array_equal(np.rint(source['time']*20),np.rint(graph['time']*20))
    for name in ['v(req)','v(state_code)','v(point_code)','v(reset)']:assert np.array_equal(source[name],graph[name]),name
    vd=float(np.max(abs(source['v(actual_hold)']-graph['v(actual_hold)'])))
    idiff=float(np.max(abs(source['v(i_meas)']-graph['v(i_meas)'])))
    rs,rg=slope(source),slope(graph)
    report['nominal_native_comparison']={'samples':len(graph['time']),'simulation_end_s':graph_end,'source_Rapp_ohm':rs,'schematic_Rapp_ohm':rg,'schematic_Rp_diagnostic_ohm':rg-10000,'maximum_sample_voltage_difference_V':vd,'maximum_sample_current_difference_A':idiff,'relative_slope_difference':abs(rs-rg)/rs}
    assert vd<5e-6 and idiff<5e-11 and abs(rs-rg)/rs<1e-4,report['nominal_native_comparison']
    report['status']='PASS'
    (ROOT/'validation/verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
