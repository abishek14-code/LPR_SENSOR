"""Build the editable graphical representation of the supplied integrated deck."""
from pathlib import Path
import hashlib
import json
import re
import shutil

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent / 'LPR_PROJECT'
BATCH = PROJECT / 'evidence/native/20261021T133828'
SOURCE = BATCH / 'nominal_healthy/attempt_0/acquisition.cir'
SYMBOLS = PROJECT / 'project/ltspice/circuits'

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)

def build():
    for name in ['res', 'cap', 'voltage', 'bv', 'sw']:
        shutil.copy2(SYMBOLS / f'{name}.asy', ROOT / f'{name}.asy')
    shutil.copy2(PROJECT / 'inputs/P1/template/p1_opamp.lib', ROOT / 'p1_opamp.lib')
    write(ROOT/'LPR_OPAMP.asy', '''Version 4
SymbolType CELL
LINE Normal -64 -48 64 0
LINE Normal 64 0 -64 48
LINE Normal -64 48 -64 -48
LINE Normal -52 -24 -36 -24
LINE Normal -44 -32 -44 -16
LINE Normal -52 24 -36 24
LINE Normal 0 -64 0 -24
LINE Normal 0 24 0 64
WINDOW 0 24 -64 Left 2
WINDOW 3 24 56 Left 2
SYMATTR Prefix X
SYMATTR Value P1_OP
SYMATTR Description P1 generic amplifier: IN+, IN-, V+, V-, OUT
PIN -64 -24 NONE 0
PINATTR PinName IN+
PINATTR SpiceOrder 1
PIN -64 24 NONE 0
PINATTR PinName IN-
PINATTR SpiceOrder 2
PIN 0 -64 NONE 0
PINATTR PinName V+
PINATTR SpiceOrder 3
PIN 0 64 NONE 0
PINATTR PinName V-
PINATTR SpiceOrder 4
PIN 64 0 NONE 0
PINATTR PinName OUT
PINATTR SpiceOrder 5
''')
    write(ROOT/'LPR_RH.asy', '''Version 4
SymbolType CELL
LINE Normal 0 0 16 0
LINE Normal 16 0 24 -12
LINE Normal 24 -12 32 12
LINE Normal 32 12 40 -12
LINE Normal 40 -12 48 12
LINE Normal 48 12 56 -12
LINE Normal 56 -12 64 0
LINE Normal 64 0 80 0
WINDOW 0 40 -48 Center 2
WINDOW 3 40 32 Center 2
SYMATTR Prefix R
SYMATTR Value R
PIN 0 0 NONE 0
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 80 0 NONE 0
PINATTR PinName B
PINATTR SpiceOrder 2
''')
    write(ROOT/'LPR_CH.asy', '''Version 4
SymbolType CELL
LINE Normal 0 0 28 0
LINE Normal 28 -16 28 16
LINE Normal 36 -16 36 16
LINE Normal 36 0 64 0
WINDOW 0 32 -48 Center 2
WINDOW 3 32 32 Center 2
SYMATTR Prefix C
SYMATTR Value C
PIN 0 0 NONE 0
PINATTR PinName A
PINATTR SpiceOrder 1
PIN 64 0 NONE 0
PINATTR PinName B
PINATTR SpiceOrder 2
''')

    catalog = {}
    noise = {}
    for row in json.loads((BATCH/'manifest.json').read_text())['results']:
        case = row['case']
        d = BATCH/case/'attempt_0'
        lines = (d/'acquisition.cir').read_text().splitlines()
        params = dict(re.findall(r'(\w+)=([^ ]+)', next(x for x in lines if x.startswith('.param '))))
        sources = {}
        for line in lines:
            if line.startswith('V_') or line.startswith('Vreset '):
                name, positive, negative, value = line.split(None, 3)
                sources[name] = dict(positive=positive, negative=negative, value=value)
        seed = json.loads((d/'OFFLINE_TRUTH.json').read_text())['parameters']['NOISE']
        scenario = json.loads((d/'OFFLINE_TRUTH.json').read_text())['scenario']
        seed = scenario.get('seed', 42)
        n = d/'noise.txt'
        if seed not in noise or n.stat().st_size > noise[seed].stat().st_size:
            noise[seed] = n
        catalog[case] = dict(parameters=params, sources=sources,
            stop_s=next(x for x in lines if x.startswith('.tran ')).split()[2],
            noise_seed=seed, noise_rows=len(n.read_text().splitlines()),
            source_circuit_sha256=digest(d/'acquisition.cir'), source_noise_sha256=digest(n),
            recorded_final_decision=row['decision'], recorded_retries=row['actual_retries'])
    for seed, path in noise.items():
        (ROOT/'noise_banks').mkdir(exist_ok=True)
        shutil.copy2(path, ROOT/f'noise_banks/seed{seed}.txt')
    write(ROOT/'cases.json', json.dumps(catalog, indent=2)+'\n')

    original = {}
    for line in SOURCE.read_text().splitlines():
        if line and line[0] in 'RCSVXB' and not line.startswith('Native'):
            name = line.split()[0]
            count = 5 if line[0]=='X' else 4 if line[0]=='S' else 2
            fields=line.split(None, count+1)
            original[name]=dict(nodes=fields[1:count+1], value=fields[-1])

    asc=['Version 4','SHEET 1 3328 2624']
    expected={}
    def text(x,y,s,size=2):asc.append(f'TEXT {x} {y} Left {size} ;{s}')
    def directive(x,y,s):asc.append(f'TEXT {x} {y} Left 2 !{s}')
    def wire(a,b):
        if a!=b:asc.append(f'WIRE {a[0]} {a[1]} {b[0]} {b[1]}')
    def flag(p,n):asc.append(f'FLAG {p[0]} {p[1]} {n}')
    def attach(p,q,n):wire(p,q);flag(q,n)
    def component(name,kind,x,y,hidden=False):
        d=original[name];value=d['value'];inst=name
        if name in ['Xmid','Xref','Xcontrol','Xtia']:
            inst={'Xmid':'U1','Xref':'U2','Xcontrol':'U3','Xtia':'U4'}[name]
        if name.startswith('V_') or name=='Vreset':value=f'PWL FILE=stimuli/{name}.txt'
        if kind in ['voltage','bv','sw']:origin=(x,y-16)
        elif kind=='res':origin=(x-16,y-16)
        elif kind=='cap':origin=(x-16,y)
        else:origin=(x,y)
        asc.append(f'SYMBOL {kind} {origin[0]} {origin[1]} R0')
        if hidden:asc.append('WINDOW 3 24 96 Invisible 0')
        asc.extend([f'SYMATTR InstName {inst}',f'SYMATTR Value {value}'])
        if kind=='LPR_OPAMP':pins=[(x-64,y-24),(x-64,y+24),(x,y-64),(x,y+64),(x+64,y)]
        elif kind=='sw':pins=[(x,y),(x,y+80),(x-48,y+64),(x-48,y+16)]
        elif kind in ['LPR_RH','LPR_CH']:pins=[(x,y),(x+(80 if kind=='LPR_RH' else 64),y)]
        else:pins=[(x,y),(x,y+(64 if kind=='cap' else 80))]
        expected[name]={'instance':('X'+inst if kind=='LPR_OPAMP' else inst),'pins':pins,**d}
        return pins
    def v(name,x,y,hidden=False):
        ps=component(name,'bv' if name.startswith('B') else 'voltage',x,y,hidden)
        wire(ps[0],(x,y-32));wire((x,y-32),(x+64,y-32));flag((x+64,y-32),original[name]['nodes'][0])
        attach(ps[1],(x,y+112),original[name]['nodes'][1])
        return ps
    def amp(name,x,y):
        ps=component(name,'LPR_OPAMP',x,y)
        for pin,delta,n in zip(ps,[(-48,0),(-48,0),(0,-32),(0,32),(48,0)],original[name]['nodes']):
            attach(pin,(pin[0]+delta[0],pin[1]+delta[1]),n)
        return ps
    def switch(name,x,y):
        ps=component(name,'sw',x,y)
        for p,q,n in zip(ps,[(x,y-32),(x,y+112),(x-128,y+64),(x-128,y+16)],original[name]['nodes']):attach(p,q,n)
        return ps

    text(64,24,'LPR | UNIFIED INTEGRATED CIRCUIT',4)
    text(64,88,'CASE: nominal_healthy | right-click parts and .param lines to edit | SELECT_CASE.command loads archived test presets',2)
    directive(64,144,'.param RU=9999.99999999992 RP=100000 CDL=0.0001 RCOUNTER=9999.99999999995')
    directive(64,184,'.param RF=1000000 GAIN=1 OFFSET=0 NOISE=0 REVERSEGAIN=1 UNSTABLE=0')
    directive(64,224,'.param TSTOP=676.00002')
    text(1568,144,'OFFSET and NOISE are volts at the conditioned output.',2)
    text(1568,184,'Current equivalents: OFFSET/RF and NOISE/RF amperes.',2)
    text(1568,224,'Protocol: 20 samples/s | normal 15 s/point | 50 s opening/closing reference',2)
    text(64,288,'A. 5 V SUPPLY / 2.5 V MIDPOINT',3)
    text(832,288,'B. RE BUFFER / CE CONTROL',3)
    text(1728,288,'C. SWITCHED REFERENCE / ELECTRODE',3)
    text(2576,288,'D. CURRENT AMPLIFIER',3)

    v('Vcc',128,416)
    component('Rmid_top','res',352,400)
    attach((352,400),(352,368),'vcc');wire((352,480),(352,544));flag((352,512),'mid_raw')
    component('Rmid_bottom','res',352,544);attach((352,624),(352,672),'0')
    component('Cmid','cap',544,544);wire((544,544),(544,512));wire((544,512),(352,512));attach((544,608),(544,672),'0')
    amp('Xmid',640,832)
    text(64,1024,'U1 buffers the midpoint used by the control loop and TIA.',2)

    amp('Xref',1008,448)
    amp('Xcontrol',1424,448)
    v('Btarget',992,720,True)
    text(864,872,'target = V(vmid) - V(req)',2)
    switch('Sdrive',1424,704)
    component('Rcounter','res',1424,896)
    attach((1424,896),(1424,864),'ce');attach((1424,976),(1424,1008),'re')
    text(832,1072,'Drive opens during the OCP observation phases.',2)

    v('Vsense',1872,416)
    switch('Ssample',1872,640)
    switch('Scal',2288,640)
    wire((1872,608),(2288,608));flag((2288,608),'metal')
    component('Rpolar','res',1872,832)
    component('Cdouble','cap',2080,832)
    wire((1872,752),(1872,800));wire((1872,800),(1872,832));wire((1872,800),(2080,800));wire((2080,800),(2080,832))
    wire((1872,912),(1872,960));wire((2080,896),(2080,960));wire((1872,960),(2080,960));flag((1872,960),'surf')
    component('Runcomp','res',1872,1008);wire((1872,960),(1872,1008));attach((1872,1088),(1872,1120),'re')
    component('Rcal','res',2288,832);wire((2288,752),(2288,832));attach((2288,912),(2288,1120),'re')
    wire((1872,1120),(2288,1120))
    v('Bcal_select',2288,416,True)
    text(2024,536,'cal_select = 1 - sample_select',1)

    amp('Xtia',2800,656)
    component('Rfeedback','LPR_RH',2752,400)
    component('Cfeedback','LPR_CH',2768,512)
    wire((2640,400),(2752,400));wire((2832,400),(2960,400));wire((2640,400),(2640,512));wire((2640,512),(2768,512));wire((2832,512),(2960,512));wire((2960,400),(2960,512))
    flag((2640,400),'we');flag((2960,400),'tia')
    text(2608,864,'Rf = 1Meg; Cf = 100p',2)
    text(2608,912,'WE is held near 2.5 V.',2)
    text(2608,960,'I = (Vtia - Vmid) / RF at DC',2)
    text(64,1184,'E. CONDITIONING / FILTER / CURRENT SAMPLE-HOLD',3)
    text(1808,1184,'F. VOLTAGE SAMPLE-HOLD',3)

    v('Bcondition',128,1344,True)
    text(64,1520,'GAIN / OFFSET / NOISE',2)
    component('Rfilter','LPR_RH',592,1344);attach((592,1344),(528,1344),'conditioned');wire((672,1344),(784,1344));flag((784,1344),'adc')
    component('Cfilter','cap',784,1424);wire((784,1344),(784,1424));attach((784,1488),(784,1520),'0')
    component('Radcload','res',976,1424);wire((976,1424),(976,1344));wire((976,1344),(784,1344));attach((976,1504),(976,1536),'0')
    switch('Sadc',1264,1328)
    component('Cadc_hold','cap',1264,1488);wire((1264,1440),(1264,1488));attach((1264,1552),(1264,1584),'0')
    v('Bmeas',1568,1344,True)
    text(1376,1520,'i_meas = (Vadc_hold - Vmid)/RF',1)
    text(64,1600,'The behavioral source applies measurement faults before the 1k / 1u filter. Right-click Bcondition to edit.',2)

    v('Bactual',1872,1344,True)
    text(1808,1520,'actual = V(we,re)',2)
    switch('Svoltage',2240,1328)
    component('Cvoltage_hold','cap',2240,1488);wire((2240,1440),(2240,1488));attach((2240,1552),(2240,1584),'0')
    v('Vnom_gain',2640,1344);v('Vadc_range',2880,1344);v('Vadc_rate',3120,1344)
    text(2608,1584,'Instrument-setting telemetry: gain / range / rate',1)

    text(64,1728,'G. ACQUISITION SEQUENCE / SWITCH CONTROL / RECORDED STATE',3)
    protocol_names=['V_req','V_drive','V_sample_select','V_state_code','V_point_code','V_reverse','V_cal_role','V_recovery_after','V_attempt','Vreset']
    for index,name in enumerate(protocol_names):v(name,128+320*index,1872,True)
    text(64,2064,'Each source reads its editable two-column PWL file in stimuli/. Keep the protocol signals synchronized when changing timing.',2)
    text(64,2136,'H. CLOCK / NOISE / DIAGNOSTIC MONITORS',3)
    v('Vclock',128,2256,True);text(64,2424,'20 Hz sample clock',2)
    v('Vnoise',640,2256,True);text(576,2424,'Seeded noise.txt',2)
    v('Btrue',1152,2256);text(1088,2424,'Offline truth only',2)
    v('Binterface',1728,2256,True);text(1664,2424,'eta_truth = V(cellmetal,surf)',1)
    directive(2208,2160,'.include p1_opamp.lib')
    directive(2208,2200,'.model SWITCH_PATH SW(Ron=1m Roff=1e15 Vt=.5 Vh=.05)')
    directive(2208,2240,'.model SAMPLE_SWITCH SW(Ron=100 Roff=1e15 Vt=.5 Vh=0)')
    directive(2208,2280,'.options plotwinsize=0 reltol=1e-5 abstol=1p vntol=10n')
    directive(2208,2320,'.tran 0 {TSTOP} 0 25m')
    directive(64,2496,'.include saved_channels.inc')
    text(64,2544,'Native editable symbols. Matching labels connect electrically. Generic amplifier and sample-hold simulation models.',2)

    assert set(expected)==set(original), (set(original)-set(expected),set(expected)-set(original))
    write(ROOT/'LPR_Unified.asc','\n'.join(asc)+'\n')
    channels=next(x for x in SOURCE.read_text().splitlines() if x.startswith('.save '))
    channels+=' V(tia) V(vmid) V(adc_hold) V(adc) V(actual) V(ce) V(re) V(we) V(conditioned) V(cellmetal) V(surf) V(calmetal) V(metal) V(mid_raw) V(target) V(re_buf) V(drive_amp) V(cal_select) V(nunit) V(recovery_after) I(Vsense)'
    write(ROOT/'saved_channels.inc','* Additional probe nodes are saved for graphical exploration.\n'+channels+'\n')
    write(ROOT/'connection_contract.json',json.dumps(dict(source=str(SOURCE),source_sha256=digest(SOURCE),devices=expected),indent=2)+'\n')
    print('Created',len(expected),'editable device symbols')

if __name__=='__main__':build()
