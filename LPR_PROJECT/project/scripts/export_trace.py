"""Convert LTspice ASCII .raw or tab-delimited waveform export to team CSV.

Binary raw files are rejected; use -ascii or waveform Export Data as Text.
Stepped runs require an explicit --step (one based), so steps cannot be merged.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import re


def load_text(path):
    data = Path(path).read_bytes()
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        return data.decode('utf-16')
    if b'\x00' in data[:100]:
        return data.decode('utf-16-le')
    return data.decode('utf-8-sig')


def number(s):
    result = float(s.replace('D', 'E').replace('d', 'e'))
    if not math.isfinite(result):
        raise ValueError('Non-finite data encountered')
    return result


def read_trace(path, step=None):
    try:
        text = load_text(path)
    except UnicodeError as exc:
        raise ValueError('Binary raw unsupported: rerun with -ascii or export waveform text.') from exc
    if 'Binary:' in text:
        raise ValueError('Binary raw unsupported: rerun with -ascii or export waveform text.')
    segments = []
    if re.search(r'^Variables:', text, flags=re.M):
        if re.search(r'^Flags:.*complex', text, flags=re.M):
            raise ValueError('AC/complex raw is not a transient trace')
        try:
            head, body = text.split('Values:', 1)
            variables = re.split(r'^Variables:\s*$', head, maxsplit=1, flags=re.M)[1].strip().splitlines()
            names = [line.split()[1].lower() for line in variables]
            nvars = len(names)
            tokens = body.split()
            stride = nvars+1
            if len(tokens) % stride:
                raise ValueError('Malformed ASCII raw point length')
            rows = []
            for i in range(0, len(tokens), stride):
                point_index = int(tokens[i])
                if point_index != len(rows):
                    raise ValueError('Nonsequential raw point index')
                rows.append([number(x) for x in tokens[i+1:i+stride]])
            declared = int(re.search(r'No\. Points:\s*(\d+)', head).group(1))
            if declared != len(rows):
                raise ValueError('Raw point count mismatch')
        except (IndexError, AttributeError) as exc:
            raise ValueError('Malformed ASCII raw header') from exc
        segment = []
        for row in rows:
            if segment and row[0] < segment[-1][0]:
                segments.append(segment)
                segment = []
            segment.append(row)
        if segment:
            segments.append(segment)
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise ValueError('Empty trace')
        names = re.split(r'\s+', lines[0].lower())
        segment = []
        for line in lines[1:]:
            if line.lower().startswith('step information:'):
                if segment:
                    segments.append(segment)
                    segment = []
                continue
            row = [number(x) for x in re.split(r'\s+', line)]
            if len(row) != len(names):
                raise ValueError('Text column count does not match header')
            if segment and row[0] < segment[-1][0]:
                segments.append(segment)
                segment = []
            segment.append(row)
        if segment:
            segments.append(segment)
    if not names or names[0] != 'time':
        raise ValueError('Expected a transient trace with time as first variable')
    if not segments:
        raise ValueError('No samples found')
    if len(segments) > 1 and step is None:
        raise ValueError(f'{len(segments)} steps found; select --step 1 through {len(segments)}')
    index = 0 if step is None else step-1
    if index < 0 or index >= len(segments):
        raise ValueError('Requested step out of range')
    rows = segments[index]
    if len(rows) < 2:
        raise ValueError('Need at least two samples')
    if any(rows[i][0] <= rows[i-1][0] for i in range(1, len(rows))):
        raise ValueError('Times must strictly increase within each selected step')
    return names, rows, len(segments)


def phase(t, scenario):
    # Fixed stimulus labels only; never inferred as actual controller states.
    if scenario.startswith(('00_', '18_')):
        return 'BASELINE' if t < 1 else 'STEP'
    if t < 2: return 'BASELINE'
    if t <= 14: return 'A1'
    if t < 20: return 'RECOVERY_1'
    if t <= 32: return 'B'
    if t < 38: return 'RECOVERY_2'
    if t <= 50: return 'A2'
    return 'RECOVERY_3'


def convert(source, output, scenario, step=None):
    source, output = Path(source), Path(output)
    names, rows, count = read_trace(source, step)
    mapping = {'requested_voltage': 'v(req)', 'actual_voltage': 'v(actual)',
               'current': 'v(i_meas)', 'current_true': 'v(i_true)', 'relay_state': 'v(relay)'}
    missing = [name for name in mapping.values() if name not in names]
    if missing:
        raise ValueError('Missing traces: '+', '.join(missing)+'. Export all five helper traces.')
    indices = {key: names.index(name) for key, name in mapping.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = ['run_id', 'seed', 'scenario', 'controller', 'state', 'time',
              'requested_voltage', 'actual_voltage', 'current', 'current_true',
              'relay_state', 'fault', 'diagnostic', 'action', 'result', 'source']
    with output.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            record = dict(run_id=source.stem+(f'_step{step}' if step else ''), seed=42,
                          scenario=scenario, controller='fixed_electrical_stimulus',
                          state=phase(row[0], scenario), time=row[0], fault='',
                          diagnostic='', action='', result='', source='LTspice_import')
            record.update({key: row[index] for key, index in indices.items()})
            writer.writerow(record)
    metadata = dict(source_file=source.name, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    provenance='LTspice_import; source file authenticity not independently verified',
                    samples=len(rows), steps_in_source=count, selected_step=step or 1,
                    units=dict(time='s', requested_voltage='V', actual_voltage='V',
                               current='A', current_true='A'),
                    warning='current_true is simulation ground truth, not a real instrument channel. '
                    'P3 diagnostic logic must use current, not current_true or scenario labels.')
    output.with_suffix('.json').write_text(json.dumps(metadata, indent=2)+'\n')
    return metadata


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source', type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--scenario', required=True)
    ap.add_argument('--step', type=int)
    args = ap.parse_args()
    try:
        print(json.dumps(convert(args.source, args.output, args.scenario, args.step), indent=2))
    except (ValueError, OSError) as exc:
        ap.exit(2, f'Export failed: {exc}\n')
