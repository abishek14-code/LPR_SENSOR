"""LPR 3D simulator: one local web page that drives the real pipeline.

Python is the backend. A Run request starts code/flowchart_pipeline.py, which
runs ElmerSolver, native LTspice and Darshan's unchanged P3 checks. The page
reads the files those programs write (Elmer field, LTspice channels, decision)
and shows them in 3D. Nothing is simulated in the browser: every number shown
comes from a pipeline run.

    .venv/bin/python webapp/server.py        then open http://127.0.0.1:8765
"""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from pathlib import Path
import csv, json, mimetypes, os, re, subprocess, sys, threading, webbrowser

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent
RUNS = ROOT/'evidence/flowchart_runs'
LOGS = RUNS/'.web_logs'
PYTHON = ROOT/'.venv/bin/python'
sys.path[:0] = [str(ROOT/'code'), str(ROOT/'project')]
import flowchart_pipeline as fp  # noqa: E402  (scenario list, input validation)

RANGES = {'conductivity': (0.05, 10.0), 'area': (2e-7, 2e-5), 'gap': tuple(fp.load_shared()['electrode_geometry']['supported_gap_range_m']),
          'reference_distance': (0.001, 0.047)}
NAME = re.compile(r'^[0-9]{8}T[0-9]{6}_[A-Za-z0-9_]+$')
JOB = {'proc': None}
LOCK = threading.Lock()


def read_json(path, default=None):
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return default


def run_dir(name):
    if not NAME.match(name or ''): raise KeyError(name)
    d = RUNS/name
    if not d.is_dir(): raise KeyError(name)
    return d


# ---------------------------------------------------------------- progress
def progress(d):
    """Which flowchart boxes have produced their output file so far."""
    c = d/'C_ltspice'
    def ok(p): return (d/p).exists()
    ltspice0 = read_json(c/'attempt_0/NATIVE_EXECUTION.json', {})
    steps = [
        dict(id='inputs', label='Shared inputs', done=ok('SHARED_INPUTS_USED.json')),
        dict(id='elmer', label="Danish: Elmer water-field solve", done=ok('A_elmer/nominal/extracted.json')),
        dict(id='ru', label='Effective Ru and uncertainty', done=ok('A_elmer/STAGE_A_RU.json')),
        dict(id='cell', label='Cell-interface parameters', done=ok('STAGE_B_CELL.json')),
        dict(id='ltspice', label='LTspice circuit and cell model', done='returncode' in ltspice0),
        dict(id='channels', label='Measured V, I and instrument channels', done=ok('C_ltspice/attempt_0/measured_channels.csv')),
        dict(id='darshan', label='Darshan: settling, faults, uncertainty', done=ok('C_ltspice/attempt_0/decision.json')),
    ]
    loop = []
    if c.is_dir():
        order = lambda p: (int(p.name.rsplit('_', 1)[-1]), 0 if p.name.startswith('reference') else 1)
        for sub in sorted((p for p in c.iterdir() if p.is_dir() and p.name != 'attempt_0'), key=order):
            done = (sub/'decision.json').exists() or (sub/'reference_result.json').exists()
            label = ('Reference check before attempt ' + sub.name.rsplit('_', 1)[-1]) if sub.name.startswith('reference') else 'LTspice repeat, ' + sub.name.replace('_', ' ')
            loop.append(dict(id=sub.name, label=label, done=done))
    status = read_json(d/'STATUS.json', {})
    result = read_json(d/'PIPELINE_RESULT.json')
    return dict(status=status.get('status', 'RUNNING'), error=status.get('error'), steps=steps, loop=loop,
                decision=(result or {}).get('final', {}).get('decision'))


# ---------------------------------------------------------------- data
def field(d):
    """Elmer nominal solve: mesh, potential and current density, in the shared geometry."""
    case = d/'A_elmer/nominal'
    nodes = [line.split() for line in (case/'mesh.nodes').read_text().split('\n') if line.strip()]
    xy = [[float(n[2]), float(n[3])] for n in nodes]
    with (case/'nodal_fields.csv').open() as f: phi = [float(r['potential_V']) for r in csv.DictReader(f)]
    tri = [[int(v) for v in line.split()] for line in (case/'mesh.elements').read_text().split('\n') if line.strip()]
    with (case/'element_fields.csv').open() as f: J = [[float(r['Jx_A_m2']), float(r['Jy_A_m2'])] for r in csv.DictReader(f)]
    ex = read_json(case/'extracted.json')
    a = read_json(d/'A_elmer/STAGE_A_RU.json')
    return dict(nodes=xy, phi=phi, triangles=[[t[3]-1, t[4]-1, t[5]-1, t[1]] for t in tri], J=J,
                geometry=ex['geometry'], WE_potential_V=ex['WE_solution_potential_V'], RE_potential_V=ex['RE_solution_potential_V'],
                CE_potential_V=ex['CE_solution_potential_V'], elmer_current_A=ex['energy_current_A'], excitation_V=ex['delta_outer_electrode_V'],
                Ru_ohm=a['output']['Ru_ohm'], Ru_u_ohm=a['output']['standard_uncertainty_ohm'], solves=a['solves'],
                bodies={'1': 'working electrode (steel)', '2': 'water', '3': 'counter electrode'})


def waveform(d, attempt, limit=4000):
    path = d/f'C_ltspice/attempt_{int(attempt)}/measured_channels.csv'
    with path.open() as f: rows = list(csv.DictReader(f))
    segments = []
    for r in rows:
        t = float(r['time_s'])
        if segments and segments[-1]['state'] == r['state']: segments[-1]['end'] = t
        else: segments.append(dict(state=r['state'], start=t, end=t))
    step = max(1, len(rows)//limit)
    keep = [r for i, r in enumerate(rows) if i % step == 0 or i == len(rows)-1]
    cell_path = lambda r: r['drive_enabled'] == 'True' and r.get('sample_select', '1') == '1'
    return dict(attempt=int(attempt), samples=len(rows), stride=step, segments=segments,
                t=[round(float(r['time_s']), 4) for r in keep],
                v=[float(r['actual_voltage_v']) for r in keep], i=[float(r['current_a']) for r in keep],
                req=[float(r['requested_voltage_v']) for r in keep],
                path=['cell' if cell_path(r) else 'calibration' if r['drive_enabled'] == 'True' else 'open' for r in keep],
                state=[r['state'] for r in keep])


def run_summary(d):
    r = read_json(d/'PIPELINE_RESULT.json') or {}
    shared = read_json(d/'SHARED_INPUTS_USED.json', {})
    attempts = sorted(int(p.name.split('_')[1]) for p in (d/'C_ltspice').glob('attempt_*') if (p/'measured_channels.csv').exists())
    return dict(name=d.name, scenario=r.get('scenario') or d.name.split('_', 1)[1], progress=progress(d), result=r,
                inputs=shared.get('shared'), overrides=shared.get('overrides'), attempts=attempts,
                audit=read_json(d/'PARAMETER_AUDIT.json'))


def runs():
    out = []
    for d in sorted((p for p in RUNS.iterdir() if p.is_dir() and NAME.match(p.name)), reverse=True) if RUNS.is_dir() else []:
        r = read_json(d/'PIPELINE_RESULT.json') or {}
        out.append(dict(name=d.name, status=read_json(d/'STATUS.json', {}).get('status'),
                        decision=(r.get('final') or {}).get('decision'), scenario=r.get('scenario') or d.name.split('_', 1)[1]))
    return out


def config():
    reg = read_json(ROOT/'configs/PARAMETER_SOURCES.json')
    return dict(shared=fp.load_shared(), scenarios={k: v for k, v in sorted(fp.SCENARIOS.items())}, ranges=RANGES,
                parameters={k: dict(status=v['status'], value=v['value'], unit=v['unit']) for k, v in reg['parameters'].items()})


# ---------------------------------------------------------------- runs
def start(body):
    scenario = body.get('scenario', 'nominal_healthy')
    if scenario not in fp.SCENARIOS: raise ValueError('unknown scenario')
    ov = {}
    for key, (lo, hi) in RANGES.items():
        if body.get(key) not in (None, ''):
            v = float(body[key])
            if not lo <= v <= hi: raise ValueError(f'{key} must be between {lo} and {hi}')
            ov[key] = v
    fp.load_shared(**ov)  # same validation the pipeline applies (gap range, RE inside the gap)
    with LOCK:
        if JOB['proc'] is not None and JOB['proc'].poll() is None: raise RuntimeError('a run is already in progress')
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + scenario
        out = RUNS/name; LOGS.mkdir(parents=True, exist_ok=True)
        cmd = [str(PYTHON), str(ROOT/'code/flowchart_pipeline.py'), '--scenario', scenario, '--out', str(out), '--no-open']
        for key, v in ov.items(): cmd += ['--' + key.replace('_', '-'), repr(v)]
        if body.get('overlap'): cmd.append('--overlap')
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': f"{ROOT/'code'}:{ROOT/'project'}", 'GIT_CEILING_DIRECTORIES': str(Path.home())}
        log = (LOGS/(name + '.log')).open('w')
        JOB.update(proc=subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT), name=name, log=LOGS/(name+'.log'), cmd=cmd)
    return dict(name=name, command=' '.join(cmd))


def job():
    p = JOB['proc']
    if p is None: return dict(running=False)
    rc = p.poll(); name = JOB['name']
    tail = [l for l in JOB['log'].read_text(errors='replace').splitlines() if not l.startswith('fatal: not a git repository')][-8:] if JOB['log'].exists() else []
    d = RUNS/name
    return dict(running=rc is None, returncode=rc, name=name, log=tail, progress=progress(d) if d.exists() else None)


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, kind='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body, allow_nan=False).encode()
        self.send_response(code); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store'); self.end_headers(); self.wfile.write(data)

    def log_message(self, fmt, *args):
        if '/api/job' not in (args[0] if args else ''): sys.stderr.write('%s\n' % (fmt % args))

    def do_GET(self):
        path, _, query = self.path.partition('?')
        q = dict(p.split('=', 1) for p in query.split('&') if '=' in p)
        try:
            if path == '/api/config': return self.send(200, config())
            if path == '/api/runs': return self.send(200, runs())
            if path == '/api/job': return self.send(200, job())
            m = re.match(r'^/api/run/([^/]+)(?:/(field|waveform))?$', path)
            if m:
                d = run_dir(m[1])
                if m[2] == 'field': return self.send(200, field(d))
                if m[2] == 'waveform': return self.send(200, waveform(d, q.get('attempt', 0)))
                return self.send(200, run_summary(d))
            if path.startswith('/report/'):
                name, _, rest = path[len('/report/'):].partition('/')
                f = (run_dir(name)/(rest or 'index.html')).resolve()
                if not f.is_relative_to(run_dir(name).resolve()) or not f.is_file(): raise KeyError(path)
                return self.send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
            rel = 'index.html' if path in ('/', '') else path.lstrip('/')
            f = (WEB/rel).resolve()
            if not (f.is_relative_to(WEB/'static') or f.is_relative_to(WEB/'vendor') or f == WEB/'index.html') or not f.is_file(): raise KeyError(path)
            kind = 'text/javascript' if f.suffix == '.js' else mimetypes.guess_type(f.name)[0] or 'application/octet-stream'
            return self.send(200, f.read_bytes(), kind + ('; charset=utf-8' if kind.startswith('text') else ''))
        except KeyError: return self.send(404, dict(error='not found'))
        except (OSError, ValueError) as e: return self.send(500, dict(error=str(e)))

    def do_POST(self):
        if self.path != '/api/run': return self.send(404, dict(error='not found'))
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0) or b'{}')
            return self.send(200, start(body))
        except RuntimeError as e: return self.send(409, dict(error=str(e)))
        except (ValueError, TypeError) as e: return self.send(400, dict(error=str(e)))


def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument('--port', type=int, default=8765); p.add_argument('--no-open', action='store_true')
    a = p.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    url = f'http://127.0.0.1:{a.port}/'
    print('LPR 3D simulator running at', url, '(Ctrl+C to stop)', flush=True)
    if not a.no_open: threading.Timer(.8, lambda: webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass


if __name__ == '__main__':
    main()
