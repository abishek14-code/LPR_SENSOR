#!/usr/bin/env python3
"""Select an archived initial-attempt preset without redrawing the schematic."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent

def engineering(value):
    number=float(value)
    if number==0:return '0'
    for scale,suffix in [(1e6,'Meg'),(1e3,'k'),(1,''),(1e-3,'m'),(1e-6,'u'),(1e-9,'n'),(1e-12,'p')]:
        if abs(number)>=scale*(1-1e-12):return format(number/scale,'.12g')+suffix
    return format(number,'.12g')

def select(case):
    catalog = json.loads((ROOT/'cases.json').read_text())
    data = catalog[case]
    schematic = ROOT/'LPR_Unified.asc'
    text = schematic.read_text()
    for names in [('RU','RP','CDL','RCOUNTER'),('RF','GAIN','OFFSET','NOISE','REVERSEGAIN','UNSTABLE')]:
        pattern = r'(?m)^(TEXT [^\n]* !\.param )'+names[0]+r'=[^\n]*$'
        value = ' '.join(f'{key}={engineering(data["parameters"][key])}' for key in names)
        text, count = re.subn(pattern,lambda m:m[1]+value,text)
        if count != 1:
            raise ValueError(f'Expected one {names[0]} parameter line; schematic was not changed')
    text, count = re.subn(r'(?m)^(TEXT [^\n]* !\.param TSTOP=)[^\n]*$',lambda m:m[1]+data['stop_s'],text)
    if count != 1:raise ValueError('Expected one TSTOP line; schematic was not changed')
    text = re.sub(r';CASE: [^|\n]+ \|', ';CASE: '+case+' |', text)
    files = {}
    for name, source in data['sources'].items():
        value = source['value']
        if value.startswith('PWL('):
            tokens=value[4:-1].split()
            if len(tokens)%2:raise ValueError('Invalid PWL '+name)
            lines=[' '.join(tokens[i:i+2]) for i in range(0,len(tokens),2)]
        else:
            float(value)
            lines=['0 '+value,data['stop_s']+' '+value]
        files[name]='\n'.join(lines)+'\n'
    bank=(ROOT/f'noise_banks/seed{data["noise_seed"]}.txt').read_bytes().splitlines(keepends=True)
    noise=b''.join(bank[:data['noise_rows']])
    if hashlib.sha256(noise).hexdigest()!=data['source_noise_sha256']:
        raise ValueError('Noise bank does not match the archived case')
    stimuli=ROOT/'stimuli';stimuli.mkdir(exist_ok=True)
    for name,content in files.items():(stimuli/f'{name}.txt').write_text(content)
    (ROOT/'noise.txt').write_bytes(noise)
    schematic.write_text(text)
    (ROOT/'selected_case.json').write_text(json.dumps({'case':case,**data},indent=2)+'\n')
    print(f'Selected {case}. Simulated duration: {data["stop_s"]} s.')
    print('Reopen LPR_Unified.asc in LTspice, then Run.')
    print('This selects the initial circuit attempt; Python decisions and automatic retries are separate.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case',nargs='?')
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--interactive',action='store_true')
    args=parser.parse_args()
    catalog=json.loads((ROOT/'cases.json').read_text()); names=list(catalog)
    if args.list or args.interactive or not args.case:
        for i,name in enumerate(names,1):
            print(f'{i:2d}. {name}')
        if not args.interactive:return
    case=args.case
    if args.interactive:
        entered=input('Save and close the schematic in LTspice. Select case number or name: ').strip()
        if entered.isdigit() and 1<=int(entered)<=len(names):case=names[int(entered)-1]
        else:case=entered
    if case not in catalog:parser.error('Unknown case: '+str(case))
    select(case)

if __name__=='__main__':main()
