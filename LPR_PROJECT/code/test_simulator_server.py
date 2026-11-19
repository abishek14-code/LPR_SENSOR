"""3D simulator backend: reads real run files and validates run requests (no solver run)."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'webapp'))
import server  # noqa: E402


def completed_run():
    for r in server.runs():
        if r['status'] == 'COMPLETED':
            return server.run_dir(r['name'])
    pytest.skip('no completed flowchart run on disk')


def test_field_is_the_elmer_solution():
    f = server.field(completed_run())
    assert len(f['phi']) == len(f['nodes']) and len(f['J']) == len(f['triangles'])
    assert {t[3] for t in f['triangles']} == {1, 2, 3}
    assert f['Ru_ohm'] > 0 and f['Ru_u_ohm'] > 0


def test_waveform_marks_current_path():
    w = server.waveform(completed_run(), 0)
    assert len(w['t']) == len(w['i']) == len(w['path']) and w['segments'][0]['state'].startswith('CALIBRATION')
    assert set(w['path']) <= {'cell', 'calibration', 'open'} and 'cell' in w['path']


def test_progress_reports_all_flowchart_boxes():
    p = server.progress(completed_run())
    assert p['status'] == 'COMPLETED' and all(s['done'] for s in p['steps']) and p['decision']


def test_bad_requests_are_refused():
    with pytest.raises(ValueError): server.start({'scenario': 'not_a_case'})
    with pytest.raises(ValueError): server.start({'conductivity': 100})
    with pytest.raises(ValueError): server.start({'gap': 0.02, 'reference_distance': 0.03})
    with pytest.raises(KeyError): server.run_dir('../../etc')
