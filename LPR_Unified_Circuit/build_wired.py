"""Lay out and route every shared node of the integrated circuit explicitly."""
from pathlib import Path
from collections import defaultdict
import heapq
import itertools
import json
import math
import build_schematic as base
import select_case

ROOT=Path(__file__).resolve().parent
STEP=8
WIDTH,HEIGHT=3680,2928

def symbol(name,prefix,pins,graphics,windows=None):
    lines=['Version 4','SymbolType CELL',*graphics]
    lines+=windows or ['WINDOW 0 0 -80 Center 2','WINDOW 3 0 -48 Center 1']
    lines += [f'SYMATTR Prefix {prefix}',f'SYMATTR Value {name}']
    for i,(pin,x,y) in enumerate(pins,1):
        lines += [f'PIN {x} {y} NONE 0',f'PINATTR PinName {pin}',f'PINATTR SpiceOrder {i}']
    (ROOT/(name+'.asy')).write_text('\n'.join(lines)+'\n')

class Router:
    def __init__(self,obstacles):
        self.obstacles=obstacles
        self.occupancy=defaultdict(dict)
        self.segments=[]
        self.netpoints=defaultdict(set)
        self.endpoints=defaultdict(set)
    def add(self,net,path):
        for i,p in enumerate(path):
            mask=0
            for q in ([path[i-1]] if i else [])+([path[i+1]] if i+1<len(path) else []):
                mask |= 1 if q[0]>p[0] else 2 if q[0]<p[0] else 4 if q[1]>p[1] else 8
            self.occupancy[p][net]=self.occupancy[p].get(net,0)|mask
            self.netpoints[net].add(p)
        if len(path)>1:
            start=path[0];previous=path[0];direction=None
            for p in path[1:]:
                d=(p[0]-previous[0],p[1]-previous[1])
                if direction is not None and d!=direction:self.segments.append((net,start,previous));start=previous
                previous=p;direction=d
            self.segments.append((net,start,previous))
            for n,a,b in self.segments:
                self.endpoints[a].add(n);self.endpoints[b].add(n)
    def straight(self,net,a,b):
        assert a[0]==b[0] or a[1]==b[1]
        dx=(b[0]>a[0])-(b[0]<a[0]);dy=(b[1]>a[1])-(b[1]<a[1])
        n=max(abs(b[0]-a[0]),abs(b[1]-a[1]))
        self.add(net,[(a[0]+dx*i,a[1]+dy*i) for i in range(n+1)])
    def route(self,net,start,targets):
        # Routes can cross a straight foreign wire, but cannot touch its corners,
        # endpoints or pins, or turn at the crossing. Crossings carry no junction.
        tx=[p[0] for p in targets];ty=[p[1] for p in targets]
        loX,hiX,loY,hiY=min(tx),max(tx),min(ty),max(ty)
        def heuristic(p):return max(loX-p[0],0,p[0]-hiX)+max(loY-p[1],0,p[1]-hiY)
        counter=itertools.count();queue=[(heuristic(start),0,next(counter),(start,0))];cost={(start,0):0};parent={}
        dirs=[(1,0,1),(-1,0,1),(0,1,2),(0,-1,2)]
        final=None
        while queue:
            _,g,_,state=heapq.heappop(queue);p,axis=state
            if cost.get(state)!=g:continue
            if p in targets:final=state;break
            for dx,dy,newaxis in dirs:
                q=(p[0]+dx,p[1]+dy)
                if not(2<=q[0]<=WIDTH//STEP-2 and 38<=q[1]<=HEIGHT//STEP-2):continue
                if q in self.obstacles and q not in self.netpoints[net]:continue
                foreign=[mask for n,mask in self.occupancy[p].items() if n!=net]
                if foreign and axis!=newaxis:continue
                others=[mask for n,mask in self.occupancy[q].items() if n!=net]
                if any(n!=net for n in self.endpoints[q]):continue
                if others and (len(others)>1 or others[0]!=(12 if newaxis==1 else 3)):continue
                if others and q in targets:continue
                increment=1+(4 if axis and axis!=newaxis else 0)+(16 if others else 0)
                # Modest clearance preference avoids wire bundles on component labels.
                for xx,yy in [(q[0]+1,q[1]),(q[0]-1,q[1]),(q[0],q[1]+1),(q[0],q[1]-1)]:
                    if any(n!=net for n in self.occupancy[(xx,yy)]):increment+=0.15
                ng=g+increment;ns=(q,newaxis)
                if ng<cost.get(ns,float('inf')):
                    cost[ns]=ng;parent[ns]=state;heapq.heappush(queue,(ng+heuristic(q),ng,next(counter),ns))
        if final is None:
            for yy in range(start[1]-5,start[1]+6):
                print(''.join('S' if (xx,yy)==start else 'T' if (xx,yy) in targets else 'o' if net in self.occupancy[(xx,yy)] else 'F' if self.occupancy[(xx,yy)] else '#' if (xx,yy) in self.obstacles else '.' for xx in range(start[0]-6,start[0]+7)),flush=True)
            print('at start',self.occupancy[start],flush=True)
            raise RuntimeError(f'No wire route for {net} from {start}')
        path=[];state=final
        while state in parent:path.append(state[0]);state=parent[state]
        path.append(start);path.reverse();self.add(net,path)
        return path

def build():
    base.build()
    originals=json.loads((ROOT/'connection_contract.json').read_text())['devices']
    symbol('LPR_SW_H','S',[('A',0,0),('B',80,0),('CTL+',24,64),('CTL-',56,64)],
        ['LINE Normal 0 0 16 0','LINE Normal 16 0 56 -24','LINE Normal 64 0 80 0',
         'CIRCLE Normal 60 -4 68 4','LINE Normal 24 64 24 40','LINE Normal 56 64 56 40',
         'LINE Normal 16 32 32 32','LINE Normal 24 24 24 40','LINE Normal 48 32 64 32'],
        ['WINDOW 0 40 -64 Center 2','WINDOW 3 40 -40 Center 1'])
    symbol('LPR_VH','V',[('+',0,0),('-',80,0)],
        ['LINE Normal 0 0 8 0','LINE Normal 72 0 80 0','CIRCLE Normal 8 -32 72 32',
         'LINE Normal 20 -8 20 8','LINE Normal 12 0 28 0','LINE Normal 52 0 68 0'],
        ['WINDOW 0 40 -72 Center 2','WINDOW 3 40 48 Center 2'])
    macros={
        'Btarget':('LPR_TARGET',['vmid','req'],'target','Vmid - Vreq'),
        'Bcal_select':('LPR_SELECT',['sample_select'],'cal_select','1 - select'),
        'Bcondition':('LPR_CONDITION',['vmid','tia','reverse','nunit','state_code'],'conditioned','Gain / offset / noise'),
        'Bactual':('LPR_VOLTAGE',['we','re'],'actual','V(WE) - V(RE)'),
        'Bmeas':('LPR_CURRENT',['adc_hold','vmid'],'i_meas','(Vhold - Vmid) / RF'),
        'Binterface':('LPR_ETA',['cellmetal','surf'],'eta_truth','Interface voltage'),
    }
    lib=[];macro_pins={}
    for source,(model,inputs,out,title) in macros.items():
        tall=len(inputs)>2;half=112 if tall else 48
        ys=[(i-(len(inputs)-1)/2)*48 for i in range(len(inputs))]
        pins=[(name,-160,int(y)) for name,y in zip(inputs,ys)]+[(out,160,0),('gnd',0,half+32)]
        graphics=[f'RECTANGLE Normal -128 {-half} 128 {half}',f'TEXT 0 0 Center 1 {title}']
        for name,x,y in pins[:-2]:
            graphics.append(f'LINE Normal {x} {y} -128 {y}')
            graphics.append(f'TEXT -120 {y} Left 0 {name}')
        graphics += ['LINE Normal 128 0 160 0',f'LINE Normal 0 {half} 0 {half+32}']
        symbol(model,'X',pins,graphics,[f'WINDOW 0 0 {-half-48} Center 2',f'WINDOW 3 0 {half+56} Invisible 0'])
        macro_pins[source]=pins
        original=originals[source]
        body=f'{source} {out} gnd {original["value"]}'
        lib += [f'.subckt {model} '+ ' '.join(inputs+[out,'gnd']),body,f'.ends {model}','']
    (ROOT/'behavioral_blocks.lib').write_text('* Behavioral source equations preserved; explicit sense pins added for wiring.\n'+'\n'.join(lib))

    entries=[];obstacles=set();ports=defaultdict(list);stubs=[];expected={};asc=['Version 4',f'SHEET 1 {WIDTH} {HEIGHT}']
    def block(rect):
        x0,y0,x1,y1=rect
        for x in range(math.floor(x0/STEP),math.ceil(x1/STEP)+1):
            for y in range(math.floor(y0/STEP),math.ceil(y1/STEP)+1):obstacles.add((x,y))
    def text(x,y,s,size=2):
        asc.append(f'TEXT {x} {y} Left {size} ;{s}')
        block((x-8,y-8,x+len(s)*(8 if size==2 else 12),y+32))
    def directive(x,y,s):asc.append(f'TEXT {x} {y} Left 2 !{s}')
    def place(name,kind,x,y):
        original=originals[name];value=original['value'];inst=name;nodes=original['nodes']
        if name in macros:
            model,inputs,out,title=macros[name];kind=model;value=model;inst='U_'+name[1:];nodes=inputs+[out,'0']
            pinrel=[(a,b) for _,a,b in macro_pins[name]]
            half=112 if len(inputs)>2 else 48
            rect=(x-168,y-half-64,x+168,y+half+40)
            escapes=[(x-192,y+dy) for dx,dy in pinrel[:-2]]+[(x+192,y),(x,y+half+64)]
            origin=(x,y);native='X'+inst
        elif kind=='LPR_OPAMP':
            inst={'Xmid':'U1','Xref':'U2','Xcontrol':'U3','Xtia':'U4'}[name]
            pinrel=[(-64,-24),(-64,24),(0,-64),(0,64),(64,0)]
            rect=(x-80,y-96,x+144,y+96)
            escapes=[(x-112,y-24),(x-112,y+24),(x,y-120),(x,y+120),(x+168,y)]
            origin=(x,y);native='X'+inst
        elif kind=='LPR_SW_H':
            pinrel=[(0,0),(80,0),(24,64),(56,64)]
            rect=(x-16,y-80,x+96,y+72)
            escapes=[(x-40,y),(x+120,y),(x+24,y+104),(x+56,y+104)]
            origin=(x,y);native=inst
        elif kind in ['LPR_RH','LPR_CH','LPR_VH']:
            length=64 if kind=='LPR_CH' else 80;pinrel=[(0,0),(length,0)]
            rect=(x-16,y-80,x+length+16,y+64)
            escapes=[(x-40,y),(x+length+40,y)];origin=(x,y);native=inst
        else:
            length=64 if kind=='cap' else 80;pinrel=[(0,0),(0,length)]
            rect=(x-48,y-16,x+192,y+112)
            escapes=[(x,y-48),(x,y+144)]
            origin=(x-16,y if kind=='cap' else y-16) if kind in ['res','cap'] else (x,y-16)
            native=inst
        if name.startswith('V_') or name=='Vreset':value=f'PWL FILE=stimuli/{name}.txt'
        if name=='Btrue':value=original['value']
        block(rect)
        pins=[(x+a,y+b) for a,b in pinrel]
        entries.append((name,kind,origin,inst,value,pins,nodes))
        for pin,end,node in zip(pins,escapes,nodes):
            a=(pin[0]//STEP,pin[1]//STEP);b=(end[0]//STEP,end[1]//STEP)
            ports[node].append(b);stubs.append((node,a,b))
        expected[name]={'source_name':name,'instance':native,'nodes':nodes,'value':value,'pins':pins,
            'original_nodes':original['nodes'],'original_value':original['value'],'behavioral_wrapper':name in macros}

    text(64,24,'LPR | ONE CONNECTED CIRCUIT',4)
    text(64,88,'CASE: nominal_healthy | every shared node is joined by drawn wire | dots are junctions; plain crossings are separate nets',2)
    directive(64,144,'.param RU=10k RP=100k CDL=100u RCOUNTER=10k')
    directive(64,184,'.param RF=1Meg GAIN=1 OFFSET=0 NOISE=0 REVERSEGAIN=1 UNSTABLE=0')
    directive(64,224,'.param TSTOP=676.00002')
    text(1600,144,'Right-click any part or parameter to edit. SELECT_CASE.command loads 16 test presets.',2)
    text(1600,184,'OFFSET / RF and NOISE / RF are the equivalent current error in amperes.',2)
    text(64,336,'5 V / MIDPOINT',2)
    text(720,336,'TIA / WORKING ELECTRODE',2)
    text(1376,336,'SWITCHED Rp-Cdl CELL + 10k REFERENCE',2)
    text(2544,336,'REFERENCE / CE CONTROL',2)

    place('Vcc','voltage',128,480)
    place('Rmid_top','res',368,480)
    place('Rmid_bottom','res',368,752)
    place('Cmid','cap',128,752)
    place('Xmid','LPR_OPAMP',704,480)
    place('Xtia','LPR_OPAMP',896,832)
    place('Rfeedback','LPR_RH',896,624)
    place('Cfeedback','LPR_CH',912,432)
    place('Vsense','LPR_VH',1168,832)
    place('Ssample','LPR_SW_H',1408,832)
    place('Rpolar','LPR_RH',1680,624)
    place('Cdouble','LPR_CH',1688,832)
    place('Runcomp','LPR_RH',1968,832)
    place('Rcounter','LPR_RH',2256,832)
    place('Sdrive','LPR_SW_H',2544,832)
    place('Xcontrol','LPR_OPAMP',3072,832)
    place('Xref','LPR_OPAMP',3072,480)
    place('Btarget','macro',2592,480)
    place('Scal','LPR_SW_H',1408,1120)
    place('Rcal','LPR_RH',1792,1120)
    place('Bcal_select','macro',1168,1120)

    text(64,1280,'MEASUREMENT: TIA -> GAIN / OFFSET / NOISE -> FILTER -> SAMPLE / HOLD -> I_MEAS',2)
    place('Vnoise','voltage',128,1472)
    place('Bcondition','macro',704,1504)
    place('Rfilter','LPR_RH',1040,1504)
    place('Cfilter','cap',1216,1744)
    place('Radcload','res',1472,1744)
    place('Sadc','LPR_SW_H',1472,1504)
    place('Cadc_hold','cap',1728,1744)
    place('Bmeas','macro',1952,1504)
    place('Bactual','macro',2432,1504)
    place('Svoltage','LPR_SW_H',2752,1504)
    place('Cvoltage_hold','cap',3008,1744)
    place('Binterface','macro',3232,1120)
    place('Btrue','bv',3408,832)
    place('Vclock','voltage',368,1744)

    text(64,2048,'PROGRAMMED EXCITATION AND SWITCH CONTROL | editable PWL files in stimuli/',2)
    for index,name in enumerate(['V_req','V_drive','V_sample_select','V_state_code','V_point_code','V_reverse','V_cal_role','V_recovery_after','V_attempt','Vreset']):
        place(name,'voltage',128+352*index,2240)
    place('Vnom_gain','voltage',2432,2592)
    place('Vadc_range','voltage',2784,2592)
    place('Vadc_rate','voltage',3136,2592)
    directive(64,2592,'.include p1_opamp.lib')
    directive(64,2632,'.include behavioral_blocks.lib')
    directive(64,2672,'.model SWITCH_PATH SW(Ron=1m Roff=1e15 Vt=.5 Vh=.05)')
    directive(64,2712,'.model SAMPLE_SWITCH SW(Ron=100 Roff=1e15 Vt=.5 Vh=0)')
    directive(64,2752,'.options plotwinsize=0 reltol=1e-5 abstol=1p vntol=10n')
    directive(64,2792,'.tran 0 {TSTOP} 0 25m')
    directive(64,2832,'.include saved_channels.inc')
    block((48,2560,1320,2872))
    text(1456,2832,'I_MEAS / I_TRUE helper voltages represent amperes. Generic P1_OP and idealized sample/hold models.',1)

    router=Router(obstacles)
    for node,a,b in stubs:router.straight(node,a,b)
    # Route small local signal nets first; global rails then occupy clear channels.
    order=sorted(ports,key=lambda n:(n in ['0','vcc'],len(ports[n]),n))
    routing={}
    for node in order:
        endpoints=ports[node]
        tree={endpoints[0]};remaining=set(endpoints[1:]);count=0
        while remaining:
            start=min(remaining,key=lambda p:min(abs(p[0]-q[0])+abs(p[1]-q[1]) for q in tree))
            # Only the connected tree is a destination; disconnected stubs are not.
            path=router.route(node,start,tree)
            tree.update(path);remaining.remove(start);count+=1
        routing[node]={'terminals':len(endpoints),'connections':count}
        print('wired',node,len(endpoints),flush=True)

    # Split wires where same-net branches join, preserving plain foreign crossings.
    all_ends=defaultdict(set)
    for node,a,b in router.segments:all_ends[node].update([a,b])
    for node,ends in all_ends.items():
        for p in ends:
            if len(router.occupancy[p])>1:print('CROSSING END',node,p,router.occupancy[p],flush=True)
    emitted=set()
    for node,a,b in router.segments:
        on=[p for p in all_ends[node] if (a[0]==b[0]==p[0] and min(a[1],b[1])<=p[1]<=max(a[1],b[1])) or (a[1]==b[1]==p[1] and min(a[0],b[0])<=p[0]<=max(a[0],b[0]))]
        on=sorted(on)
        for p,q in zip(on,on[1:]):
            key=(node,p,q)
            if p!=q and key not in emitted:
                asc.append(f'WIRE {p[0]*STEP} {p[1]*STEP} {q[0]*STEP} {q[1]*STEP}');emitted.add(key)
    # One label per net; labels are for probing, never for joining distant islands.
    for node,points in ports.items():
        p=points[0];asc.append(f'FLAG {p[0]*STEP} {p[1]*STEP} {node}')
    for name,kind,origin,inst,value,pins,nodes in entries:
        asc.append(f'SYMBOL {kind} {origin[0]} {origin[1]} R0')
        if name.startswith('V_') or name in ['Vreset','Vclock','Vnoise','Btrue'] or name in macros:asc.append('WINDOW 3 24 96 Invisible 0')
        asc += [f'SYMATTR InstName {inst}',f'SYMATTR Value {value}']
    (ROOT/'LPR_Unified.asc').write_text('\n'.join(asc)+'\n')
    (ROOT/'connection_contract.json').write_text(json.dumps({'source':str(base.SOURCE),'source_sha256':base.digest(base.SOURCE),'devices':expected,'routing':routing},indent=2)+'\n')
    select_case.select('nominal_healthy')
    print('Completed wired schematic',len(entries),'symbols',len(emitted),'wire segments')

if __name__=='__main__':build()
