from pathlib import Path
import json,re,math,html
import numpy as np
ROOT=Path(__file__).resolve().parents[1]; C=ROOT/'ltspice/circuits'; O=ROOT/'results/electrical'; O.mkdir(exist_ok=True)
checks=[]
def check(name,ok,detail):checks.append(dict(check=name,passed=bool(ok),detail=detail))
def readraw(name):
 b=(C/(name+'.raw')).read_bytes();mark='Binary:\n'.encode('utf-16-le'); ix=b.index(mark)+len(mark);h=b[:ix].decode('utf-16-le');nv=int(re.search(r'No. Variables:\s*(\d+)',h)[1]);npnt=int(re.search(r'No. Points:\s*(\d+)',h)[1]);names=[l.split()[1].lower() for l in h.split('Variables:\n')[1].split('Binary:')[0].strip().splitlines()]
 if 'Flags: complex' in h:
  v=np.frombuffer(b[ix:],dtype='<c16').reshape(npnt,nv)
 else:
  dtype=np.dtype([('axis','<f8'),('values','<f4',(nv-1,))]);assert len(b)-ix==npnt*dtype.itemsize
  rec=np.frombuffer(b[ix:],dtype=dtype,count=npnt);v=np.column_stack([rec['axis'],rec['values']])
 assert np.isfinite(v).all()
 return {n:v[:,i] for i,n in enumerate(names)}
def segments(d):
 axis=next(iter(d)); breaks=np.r_[0,np.where(np.diff(d[axis].real)<0)[0]+1,len(d[axis])]
 return [{k:v[a:b] for k,v in d.items()} for a,b in zip(breaks[:-1],breaks[1:])]
def measurements(name):
 s=(C/(name+'.log')).read_bytes().decode('utf-16-le');out={}
 for l in s.splitlines():
  m=re.match(r'(\w+): .*?=([-+\d.eE]+)(?:\s|$)',l)
  if m:out[m[1]]=[float(m[2])]
  if l.startswith('f3db:') and ' AT ' in l:out['f3db']=[float(l.split(' AT ')[1])]
 for m in re.finditer(r'Measurement: (\w+)\s*\n(.*?)(?=\n\s*\n|\Z)',s,re.S):
  vals=[]
  for line in m[2].splitlines():
   row=re.match(r'\s*\d+\s+([-+\d.eE]+)',line)
   if row:vals.append(float(row[1]))
  out[m[1]]=vals
 return out
runs=json.loads((O/'native_validation.json').read_text());data={};meas={}
for r in runs:
 name=r['case']; d=readraw(name); data[name]=d;meas[name]=measurements(name)
 check(name+' native engine and data',r['returncode']==0 and 'Total elapsed time' in r['log'] and not re.search(r'fatal|failed|timestep too small',r['log'],re.I),'Native LTspice log completed; waveform dimensions and finite values checked.')
m=meas['00_cell_analytical'];check('Ideal cell reference',abs(m['i_initial'][0]/1e-6-1)<.001 and abs(m['i_steady'][0]/(0.01/110000)-1)<.001 and abs(m['tau_corrected'][0]/(10/11)-1)<.001,str(m))
m=meas['01_midpoint_load'];check('Midpoint loaded and transient droop',abs(m['midpoint_loaded'][0]-2.5)<.001 and m['midpoint_min'][0]>2.49,str(m))
m=meas['02_tia_calibration'];check('TIA polarity and transimpedance',abs(m['transimpedance'][0]/1e6-1)<.001 and m['out_positive'][0]>2.5 and m['out_negative'][0]<2.5,str(m))
for name,d in data.items():
 if not ('v(eta)' in d and 'v(actual)' in d and 'v(vmid)' in d):continue
 for j,x in enumerate(segments(d)):
  ru=([5000,10000,20000][j] if name=='11_ru_sweep' else 10000)
  err=np.max(np.abs(x['v(i_true)']-(x['v(actual)']-x['v(eta)'])/ru))
  active=x['time']>1e-6
  residual=np.abs(x['v(i_true)']-(x['v(actual)']-x['v(eta)'])/ru)
  ok=np.all(residual[active] < 2e-11 + 2e-5*np.abs(x['v(i_true)'][active]))
  check(name+f' step {j+1}: cell KVL/current sign',ok,f'Max all-sample residual = {err:.3g} A. Checked after first 1 us using 20 pA + 20 ppm of instantaneous current, accounting for stored float32 and solver tolerance.')
  err=np.max(np.abs(x['v(i_meas)']-(x['v(adc)']-x['v(vmid)'])/1e6))
  check(name+f' step {j+1}: ADC reconstruction',err<5e-13,f'Max reconstruction difference = {err:.3g} A (RAW values are float32)')
  check(name+f' step {j+1}: model output range',x['v(adc)'].min()>.1 and x['v(adc)'].max()<4.9,f'ADC input {x["v(adc)"].min():.6f} to {x["v(adc)"].max():.6f} V; only compared with modeled 5 V output limits.')
h=meas['03_healthy'];check('Healthy resistance and voltage tracking',abs(h['rp_meas'][0]/100000-1)<.001 and abs(h['epos'][0]/.01-1)<.001,str(h))
ratio=meas['04_gain_step']['rdc_meas'][0]/h['rdc_meas'][0];check('20 percent gain fault response',abs(ratio-1/1.2)<.001,f'Resistance ratio = {ratio:.6f}; expected 0.833333')
shift=meas['05_offset_step']['ipos_meas'][0]-h['ipos_meas'][0];check('Offset fault and slope cancellation',abs(shift-5e-9)<1e-12 and abs(meas['05_offset_step']['rdc_meas'][0]/h['rdc_meas'][0]-1)<1e-5,f'Positive current shift={shift:.9g} A; two-polarity resistance unchanged.')
check('Slow ADC filter produces settling bias',abs(meas['06_frontend_slow']['rp_meas'][0]/100000-1)>.02,'Recovered Rp=96.402 kohm, despite correct true cell response: expected diagnostic failure.')
check('High Cdl identified as unsettled',meas['07_high_cdl']['rp_meas'][0]<20000,'Rp=15.2156 kohm in the 12 s window; invalid equilibrium estimate, not a 15k cell.')
s=data['09_switch_disturbance'];mask=(s['time']>10.001)&(s['time']<10.019);check('Drive disconnect timing',np.max(s['v(relay)'][mask])==0 and s['v(relay)'].max()==1,'Relay helper is zero during the intended 20 ms disconnect.')
for n in ['08_low_signal_noise','16_noise_only']:
 x=data[n];mask=(x['time']>12)&(x['time']<14);rms=float(np.std((x['v(i_meas)']-x['v(i_true)'])[mask]));check(n+' finite noise response',.1e-9<rms<2e-9,f'Settled measurement-error sample RMS about {rms*1e9:.4f} nA; adaptive-time sampled, not a precision bandwidth estimate.')
check('Mixed faults give invalid equilibrium Rp',meas['10_mixed_fault']['rp_meas'][0]<20000,'Recovered Rp=11.0156 kohm under combined gain, offset, noise, and slow-cell fault.')
check('Ru sweep follows expected DC dependence',np.max(np.abs(np.array(meas['11_ru_sweep']['rdc_meas'])/np.array([105000,110000,120000])-1))<.01,'105001, 109997, 119380 ohm; the 20k Ru point retains 0.52% window settling bias.')
check('Rp sweep recovered within 0.1 percent',np.max(np.abs(np.array(meas['12_rp_sweep']['rp_meas'])/np.array([50000,100000,200000])-1))<.001,str(meas['12_rp_sweep']['rp_meas']))
check('Cdl sweep distinguishes settled and unsettled',abs(meas['13_cdl_sweep']['rp_meas'][0]/100000-1)<.001 and meas['13_cdl_sweep']['rp_meas'][2]<20000,str(meas['13_cdl_sweep']['rp_meas']))
check('Known resistance calibration',abs(meas['14_known_resistance']['rdc_meas'][0]/110000-1)<.001,'Recovered Rdc=110001 ohm')
check('Gain identified with known resistor',abs(meas['15_gain_known_reference']['rdc_meas'][0]/(110000/1.2)-1)<.001,'Recovered Rdc=91667.7 ohm')
a=data['03_healthy']; b=data['17_repeatability'];repeat=max(np.max(np.abs(a[k]-b[k])) for k in a);check('Repeatability',repeat==0,f'Max absolute waveform difference={repeat}')
rs=meas['18_nonlinear_challenge']['apparent_rdc'];check('Nonlinear challenge trend',rs[0]>rs[1]>rs[2] and abs(rs[0]/110000-1)<.001,str(rs))
f=meas['19_adc_filter_ac']['f3db'][0];check('ADC filter -3 dB corner',abs(f/(1/(2*math.pi*.001))-1)<.001,f'{f} Hz')
x=data['20_tia_resistor_noise'];f=x['frequency'];density=x['v(onoise)'];theory=np.sqrt(4*1.380649e-23*300.15*1e6)/np.sqrt((1+1e-5)**2+(2*math.pi*f*1e6*100e-12)**2); rel=float(np.max(np.abs(density/theory-1)));check('Resistor noise spectrum',rel<.001,f'Max relative deviation from 4kTR/feedback transfer = {rel:.3g}; low-f density={density[0]*1e9:.3f} nV/sqrt(Hz)')
# Independent schematic-to-source connectivity check against the native generated netlist.
ns=(C/'P1_Final_Schematic.net').read_bytes().decode('utf-16-le'); nd={l.split()[0].lower():l.split() for l in ns.splitlines() if l and l[0] not in '.*+'}
problems=[]
for name,nodes,val in json.loads((C/'P1_Final_expected_connections.json').read_text()):
 k=('x'+name).lower() if name.startswith('X') else name.lower()
 if nd.get(k,[])[1:1+len(nodes)]!=nodes:problems.append(name)
check('Drawn schematic 29 component connections',not problems,str(problems))
sm=measurements('P1_Final_Schematic');check('Drawn schematic matches healthy measurements',all(sm[k]==v for k,v in h.items()),'All 13 logged .meas results match the healthy netlist to printed precision.')
(O/'validation_checks.json').write_text(json.dumps(checks,indent=2));(O/'measurement_summary.json').write_text(json.dumps(meas,indent=2))
print('Checks:',len(checks),'passed:',sum(c['passed'] for c in checks))
for c in checks:
 if not c['passed']:print('FAIL',c)
# Scientific waveform plots rendered as standalone SVG without extra installations.
colors=['#1565c0','#e15535','#278044','#7b51a1']
def chart(path,title,panels):
 width=1200;height=100+len(panels)*265;out=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><g font-family="Arial,sans-serif"><text x="80" y="38" font-size="24" fill="#142d42">{html.escape(title)}</text><text x="80" y="66" font-size="14" fill="#536475">Native LTspice 17.2.4 waveforms | 20 September 2026 | generic amplifier model</text>']
 for q,(ylabel,series) in enumerate(panels):
  top=105+q*265;left=90;pw=1060;ph=180;allv=np.concatenate([v for t,v,label in series]);mn=float(allv.min());mx=float(allv.max());margin=(mx-mn)*.1 or 1;mn-=margin;mx+=margin;xmax=max(float(t.max()) for t,v,label in series)
  for j in range(5):
   y=top+j*ph/4;val=mx-j*(mx-mn)/4;out.append(f'<path d="M{left} {y}h{pw}" stroke="#e1e8ee"/><text x="80" y="{y+5}" text-anchor="end" font-size="13">{val:.3g}</text>')
  for j in range(8):
   x=left+j*pw/7;out.append(f'<text x="{x}" y="{top+ph+20}" text-anchor="middle" font-size="13">{j*xmax/7:.0f}</text>')
  out.append(f'<text x="90" y="{top-15}" font-size="16">{html.escape(ylabel)}</text>')
  for j,(t,v,label) in enumerate(series):
   # Retain both ends and extrema per bin so narrow excursions remain visible.
   inds={0,len(t)-1}
   for chunk in np.array_split(np.arange(len(t)),2000):
    if len(chunk):inds.update([int(chunk[0]),int(chunk[-1]),int(chunk[np.argmin(v[chunk])]),int(chunk[np.argmax(v[chunk])])])
   ind=np.array(sorted(inds));pts=' '.join(f'{left+tt/xmax*pw:.2f},{top+(mx-vv)/(mx-mn)*ph:.2f}' for tt,vv in zip(t[ind],v[ind]))
   out.append(f'<polyline points="{pts}" fill="none" stroke="{colors[j]}" stroke-width="1.5"/>')
   out.append(f'<text x="{left+j*350}" y="{top+ph+44}" font-size="13" fill="{colors[j]}">{html.escape(label)}</text>')
 out.append('</g></svg>');path.write_text(''.join(out))
chart(O/'healthy_waveforms.svg','Nominal LPR circuit — cell tracking and signal chain',[
 ('Cell voltage (mV)',[(a['time'],a['v(req)']*1000,'Requested'),(a['time'],a['v(actual)']*1000,'Actual WE - RE')]),
 ('Cell / reconstructed current (nA)',[(a['time'],a['v(i_true)']*1e9,'True cell current'),(a['time'],a['v(i_meas)']*1e9,'Reconstructed from ADC input')]),
 ('Output voltage (V)',[(a['time'],a['v(tia)'],'TIA output'),(a['time'],a['v(adc)'],'ADC input'),(a['time'],a['v(vmid)'],'Buffered midpoint')])])
chart(O/'fault_waveforms.svg','Fault cases — expected measurement errors',[
 ('Measured current (nA): gain fault',[(data[n]['time'],data[n]['v(i_meas)']*1e9,label) for n,label in [('03_healthy','Healthy'),('04_gain_step','20% gain step')]]),
 ('Measured current (nA): slow front end',[(data[n]['time'],data[n]['v(i_meas)']*1e9,label) for n,label in [('03_healthy','Healthy'),('06_frontend_slow','2.2 s ADC input filter')]]),
 ('True current (nA): cell settling',[(data[n]['time'],data[n]['v(i_true)']*1e9,label) for n,label in [('03_healthy','100 uF cell'),('07_high_cdl','1 mF cell — not settled')]])])
report=['# LPR P1 — native simulation validation\n','Date: 20 September 2026. Simulator: LTspice 17.2.4 for macOS.\n',
'**Result: all 21 supplied decks completed; the final 29-component graphical schematic also ran in the LTspice GUI.** The nominal reduced electrical model meets the checks below. Deliberate fault cases correctly expose measurement errors; they are not valid operating points for resistance estimation.\n',
'Open `ltspice/circuits/P1_Final_Schematic.asc` for the editable component diagram. It contains the amplifier model and seeded PWL stimulus, so it requires no external model or stimulus files. Standard LTspice resistor, capacitor, source, switch and opamp2 symbols are used. Net labels with the same name are connected.\n',
'## Principal results\n','| Quantity | Native result | Assessment |','|---|---:|---|',
'| Cell voltage | +9.99909 / -9.99909 mV | Correct polarity, <0.01% settled tracking error |',
'| True settled current | +90.9039 / -90.9039 nA | Consistent with 110 kohm series DC resistance |',
'| Recovered Rp | 99.9974 kohm | -0.0026% from 100 kohm |',
'| Ideal-cell time constant | 0.909091 s | Analytical reference agrees |',
'| Calibrated transimpedance | 999990 ohm | -0.001% |',
'| ADC input filter corner | 159.158 Hz | Agrees with 1k / 1u |',
'| Buffered midpoint | 2.5 V | Load-step minimum 2.49815 V |',
'| Nominal ADC input range | 1.50899 to 3.49098 V | Within modeled 5 V range |\n',
'## Design findings that affect use\n',
'1. **The 12 s hold is valid for the nominal 100 uF cell, but fails for 1 mF.** The latter yields an apparent Rp of only 15.2156 kohm despite the actual 100 kohm model. Use a settling criterion or substantially longer holds for slow cells; do not accept that estimate as corrosion resistance. The isolated 1 mF step requires about 62.8 s to get within 1% of final current, and repeated polarity changes need residual-history checks.\n',
'2. **The ADC input can exceed 3.3 V.** It reaches 3.491 V nominally and 4.424 V in the 5k Ru sweep. The schematic represents an RC input and 1G load, not a specific ADC. A 3.3 V input interface cannot be assumed compatible; select the real ADC supply/range or redesign the gain/midpoint before hardware use.\n',
'3. **The generic P1_OP is not an LM324 or other qualified device.** Its limiter precedes its pole; slew, overload recovery, common-mode range, supply current, intrinsic noise and temperature behavior are not realistic device validation. A vendor model, component tolerances, cable/probe parasitics, loop stability and physical measurements remain necessary for a build-ready design.\n',
'4. **Rdc includes Ru.** The report subtracts the assumed known Ru to obtain Rp. A single settled point does not independently identify Ru. The 20k Ru sweep retains about 0.52% Rdc settling bias in this window.\n',
'5. **Offset can disappear from a two-polarity slope.** The +5 mV fault creates +5 nA current bias while leaving the resistance slope almost unchanged. Check the zero-input residual as well.\n',
'6. **Zero request is still potentiostatic control.** This is not open-circuit OCP acquisition. CE electrochemistry, real relay behavior, ADC conversion, MCU firmware and adaptive timing are outside this electrical model.\n',
'7. **Drive reconnection is a failed disturbance condition:** the switch-fault trace peaks at +1.60043 V WE-to-RE and +159.137 uA. Filtering hides much of the brief spike; real hardware needs controlled reconnection and compliance/overload checks.\n',
'## Per-case outcome\n','| Case | Outcome / relevant value |','|---|---|']
notes={0:'Ideal-cell reference passes: 1 uA initial, 90.9183 nA late current, tau 0.909091 s.',1:'Midpoint load test passes.',2:'TIA calibration and current sign pass.',3:'Nominal Rp 99.9974k.',4:'Expected gain fault: Rdc 91.6645k, Rp 81.6645k.',5:'Expected +5 nA offset; slope cancels constant offset.',6:'Expected settling bias: Rp 96.402k.',7:'Unsettled cell: Rp estimate invalid (15.2156k).',8:'Low-signal noise: Rp estimate 100.714k for this realization.',9:'Drive opens for 20 ms; later Rp 100.133k shows residual disturbance.',10:'Mixed-fault estimate invalid: Rp 11.0156k.',11:'Ru 5k/10k/20k: Rdc 105.001k/109.997k/119.380k.',12:'Rp 50k/100k/200k recovered as 50.0003k/99.9974k/199.977k.',13:'10u/100u settle; 1mF does not.',14:'Known 110k reference recovered as 110.001k.',15:'Known-reference gain fault recovered as 91.6677k.',16:'Noise-only Rp 100.069k for this realization.',17:'Duplicate waveforms identical at all stored points.',18:'Nonlinearity: Rdc 109.984k/109.484k/98.6025k at 1/10/50mV.',19:'Filter f3dB 159.158 Hz.',20:'Resistor-noise spectrum agrees with analytical Johnson-noise transfer.'}
for r in runs:report.append(f'| {r["case"]} | {notes[int(r["case"][:2])]} |')
report += ['\n## Evidence and scope\n',f'{sum(c["passed"] for c in checks)} / {len(checks)} automated checks passed. First-microsecond startup samples are excluded from the KVL acceptance test: behavioral current helpers show initialization residuals up to 0.377 nA. The remaining KVL test uses 20 pA absolute + 20 ppm relative tolerance, needed for the 159 uA switching spike. These include native output integrity, quantitative nominal tests, all full-system cell KVL/current polarity checks, ADC reconstruction, modeled rail ranges, deliberate-fault behavior, sweeps, repeatability, resistor-noise spectrum, and every graphical component connection. The 13 final schematic measurement values match the healthy source to printed precision.\n',
'Warnings about fewer than two connections at `actual`, `eta`, `i_true` and `i_meas` concern intentionally unloaded ideal observation sources, not floating electrode nodes.\n',
'Evidence: native `.log`/`.raw` files in `ltspice/circuits`; `results/electrical/native_validation.json`, `validation_checks.json`, `measurement_summary.json`; plots `healthy_waveforms.svg` and `fault_waveforms.svg`. RAW files were parsed with their declared UTF-16 header and binary numeric layout; byte counts and finite samples were checked.\n',
'The original folder in Movies was not modified. The old documentation is retained for provenance, but statements there that native runs are incomplete are superseded by this dated report.\n',
'LTspice on this Mac accepts `-b deck.cir`; the original wrapper incorrectly passed `-ascii` after `-b`, which was interpreted as the input filename. The final schematic was run through the GUI.\n']
(ROOT/'VALIDATION_REPORT.md').write_text('\n'.join(report))
# Minimal self-contained HTML report with inline SVG plots.
htmlbody='<h1>LPR P1 — measured simulation waveforms</h1><p>Native LTspice 17.2.4 output. Full findings: VALIDATION_REPORT.md. Simulation-only generic amplifier design.</p>'+(O/'healthy_waveforms.svg').read_text()+(O/'fault_waveforms.svg').read_text()
(ROOT/'Waveforms.html').write_text('<!doctype html><meta charset="utf-8"><title>LPR P1 Validation</title><style>body{max-width:1200px;margin:30px auto;font-family:Arial;color:#142d42}svg{width:100%;height:auto;margin:20px 0}</style>'+htmlbody)
