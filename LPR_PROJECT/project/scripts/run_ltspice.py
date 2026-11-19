"""Optional local batch runner. Requires YOUR installed LTspice executable.

Uses -b on macOS and -b -ascii on Windows. macOS output is binary RAW. GUI instructions are the fallback if a
particular OS/version does not support these flags. Never substitutes Python.
"""
from pathlib import Path
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--exe', required=True, type=Path, help='Absolute path to LTspice binary, not its .app folder')
    selection = ap.add_mutually_exclusive_group(required=True)
    selection.add_argument('--case', help='Case stem, e.g. 00_cell_analytical')
    selection.add_argument('--all', action='store_true')
    ap.add_argument('--timeout', type=int, default=900)
    args = ap.parse_args()
    if not args.exe.is_file():
        ap.error('LTspice executable does not exist at the supplied path')
    cases = sorted((ROOT/'ltspice/circuits').glob('*.cir'))
    if not args.all:
        cases = [p for p in cases if p.stem == args.case]
        if not cases:
            ap.error('Unknown --case; inspect ltspice/circuits')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8]
    run_dir = ROOT/'results/electrical/native_runs'/run_id
    shutil.copytree(ROOT/'ltspice', run_dir/'ltspice')
    manifest = dict(run_id=run_id, executable=str(args.exe.resolve()),
                    source_hashes={str(p.relative_to(ROOT/'ltspice')): hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (ROOT/'ltspice').rglob('*') if p.is_file()}, runs=[])
    for original in cases:
        deck = run_dir/'ltspice/circuits'/original.name
        command = ([str(args.exe.resolve()), '-b', deck.name] if sys.platform == 'darwin'
                   else [str(args.exe.resolve()), '-b', '-ascii', deck.name])
        record = dict(case=deck.stem, command=command, status='FAILED')
        print(f'Running {deck.stem}', flush=True)
        try:
            process = subprocess.run(command, cwd=deck.parent, capture_output=True,
                                     text=True, timeout=args.timeout, errors='replace')
            (deck.parent/(deck.stem+'.console.txt')).write_text(process.stdout+'\n'+process.stderr)
            record['returncode'] = process.returncode
            raw, log = deck.with_suffix('.raw'), deck.with_suffix('.log')
            logtext = ''
            if log.exists():
                from export_trace import load_text
                logtext = load_text(log)
            has_error = bool(re.search(r'fatal error|unknown (?:parameter|device|subcircuit)|singular matrix|timestep too small|could not open', logtext, re.I))
            if process.returncode == 0 and raw.exists() and raw.stat().st_size > 0 and log.exists() and not has_error:
                record['status'] = 'ENGINE_OUTPUT_CREATED_REVIEW_REQUIRED'
                record['raw_file'] = str(raw.relative_to(run_dir))
                record['log_file'] = str(log.relative_to(run_dir))
            else:
                record['error'] = 'Nonzero return, missing raw/log, or known error in log. Inspect console and log.'
        except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError) as exc:
            record['error'] = str(exc)
        manifest['runs'].append(record)
        (run_dir/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
        print(record['status'], flush=True)
    print(f'Run directory: {run_dir}')
    # Creating engine output is not the same as passing numerical validation.
    return 1 if any(r['status']=='FAILED' for r in manifest['runs']) else 0


if __name__ == '__main__':
    sys.exit(main())
