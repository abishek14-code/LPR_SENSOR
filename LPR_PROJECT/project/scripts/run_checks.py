"""Retain reproducible software/analytical/static check evidence, not native results."""
from pathlib import Path
from datetime import datetime, timezone
import json
import platform
import subprocess
import sys
from reference_checks import validate
from check_sources import check

ROOT = Path(__file__).resolve().parents[1]


if __name__ == '__main__':
    output = ROOT/'results/electrical'
    output.mkdir(parents=True, exist_ok=True)
    reference = validate()
    sources = check()
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                            cwd=ROOT, capture_output=True, text=True)
    text = 'SOFTWARE/SYNTHETIC-FIXTURE TESTS ONLY — NOT LTSPICE RUNS\n\n'+result.stdout+result.stderr
    (output/'software_tests.txt').write_text(text)
    (output/'reference_checks.json').write_text(json.dumps(reference, indent=2)+'\n')
    (output/'source_checks.json').write_text(json.dumps(sources, indent=2)+'\n')
    status = dict(checked_utc=datetime.now(timezone.utc).isoformat(),
                  python_version=platform.python_version(), os=platform.system(),
                  test_exit_code=result.returncode, native_ltspice_executed=False,
                  native_waveform_validation='PENDING',
                  warning='These are software, source-dependency and mathematical checks only.')
    (output/'check_environment.json').write_text(json.dumps(status, indent=2)+'\n')
    print(text)
    sys.exit(result.returncode)
