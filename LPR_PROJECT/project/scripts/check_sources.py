"""Static package checks, not a SPICE syntax parser or engine execution."""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def check():
    decks = sorted((ROOT/'ltspice/circuits').glob('*.cir'))
    if len(decks) != 21:
        raise AssertionError(f'Expected 21 circuits, found {len(decks)}')
    report = []
    for deck in decks:
        visited = set()
        def inspect(path):
            path = path.resolve()
            if path in visited:
                return
            visited.add(path)
            text = path.read_text()
            for dependency in re.findall(r'(?im)^\s*\.include\s+(\S+)', text):
                target = (deck.parent/dependency.strip('"')).resolve()
                if not target.is_file():
                    raise AssertionError(f'Missing include: {target}')
                inspect(target)
            for dependency in re.findall(r'(?i)PWL\s+FILE=(\S+)', text):
                if not (deck.parent/dependency.strip('"')).resolve().is_file():
                    raise AssertionError(f'Missing stimulus: {dependency}')
        inspect(deck)
        if not re.search(r'(?im)^\.end\s*$', deck.read_text()):
            raise AssertionError(f'Missing .end: {deck.name}')
        report.append(dict(circuit=deck.name, dependency_check='PASS',
                           ltspice_execution='NOT_RUN', waveform_validation='PENDING'))
    return dict(provenance='STATIC_SOURCE_CHECK_ONLY', cases=report)


if __name__ == '__main__':
    result = check()
    output = ROOT/'results/electrical/source_checks.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2)+'\n')
    print(f"PASS: {len(result['cases'])} circuit source/dependency checks. LTspice execution NOT RUN.")
