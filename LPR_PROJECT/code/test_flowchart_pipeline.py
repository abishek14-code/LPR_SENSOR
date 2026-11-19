"""Fast checks for the flowchart wiring (no Elmer or LTspice run needed)."""
import json, math
from pathlib import Path
import numpy as np
import pytest
import flowchart_pipeline as fp
from python.config import AppConfig
import close_integration as ci


def shared(**kw):
    return fp.load_shared(**kw)


def cell(tmp_path):
    return fp.stage_b_cell(shared(), tmp_path)[0]


def test_fem_spec_derives_depth_from_steel_area():
    s = shared(area=4e-6)
    spec = fp.fem_spec(s)
    assert spec['depth_m'] == pytest.approx(4e-6/0.04)
    assert spec['reference_probe_m'][1] == pytest.approx(spec['electrode_thickness_m'] + 0.02)


def test_mesh_mapping_keeps_regions(tmp_path):
    spec = fp.fem_spec(shared(gap=0.03))
    fp.map_mesh(fp.ROOT/'inputs/P2', tmp_path, spec)
    n = np.loadtxt(tmp_path/'mesh.nodes')
    assert n[:, 3].min() == pytest.approx(0) and n[:, 3].max() == pytest.approx(0.05)
    assert n[:, 2].max() == pytest.approx(0.04)


def test_invalid_geometry_refused():
    with pytest.raises(ValueError): shared(gap=0.06)
    with pytest.raises(ValueError): shared(reference_distance=0.05)


def test_assumed_cell_is_labelled(tmp_path):
    c = cell(tmp_path)
    assert c['status'] == 'EXPLICITLY_ASSUMED' and c['B_standard_uncertainty_V'] == pytest.approx(0.007/math.sqrt(3))


def test_calibrated_cell_needs_complete_file(tmp_path):
    s = shared(); s['cell_interface'].update(mode='calibrated', calibration_file=str(tmp_path/'cal.json'))
    (tmp_path/'cal.json').write_text(json.dumps(dict(Rp_ohm=1., Cdl_F=1.)))
    with pytest.raises(ValueError): fp.stage_b_cell(s, tmp_path)


def test_corrosion_conversion_matches_astm_g102(tmp_path):
    c = fp.corrosion_estimate(100000., 100., shared(), cell(tmp_path))
    # icorr = 0.026 V / (1e5 ohm * 0.02 cm2) = 13 uA/cm2; CR = 3.27e-3*13*27.92/7.87
    assert c['icorr_uA_cm2'] == pytest.approx(13.0)
    assert c['corrosion_rate_mm_per_year'] == pytest.approx(3.27e-3*13*27.92/7.87)
    assert c['rp_uncertainty_included']


def test_reject_withholds_numbers(tmp_path):
    result = dict(decision='REJECT', reason_codes=['OPENING_CHECK_FAILURE'], controller_result=dict(
        measurements={'Rapp_ohm': 9e4}, rp_estimate=None, recommended_action='stop', primary_reason='OPENING_CHECK_FAILURE',
        checks=[dict(name='opening', passed=False, reason_code='OPENING_CHECK_FAILURE', explanation='x', recommended_action='y')]))
    out = fp.outcome(result, shared(), cell(tmp_path), dict(Ru_ohm=1e4, standard_uncertainty_ohm=100.))
    assert out['Rp_ohm'] is None and out['corrosion'] is None and out['failure_reasons'][0]['reason_code'] == 'OPENING_CHECK_FAILURE'


def test_repeat_plan_follows_p3_retry_plan():
    cfg = AppConfig()
    res = dict(reason_codes=['NOT_SETTLED'], controller_result=dict(retry_plan=dict(
        repeat_permitted=True, reference_required=True, remaining_observation_s=0., retries_used=0, max_retries=1)))
    plan = fp.repeat_plan(res, cfg)
    assert plan['observation_time_per_point_s'] == cfg.acquisition.reacquisition_dwell_s and plan['attempt'] == 1
    events, _ = ci.schedule(dwell=plan['observation_time_per_point_s'], attempt=1)
    fp.check_schedule(events, plan)
    res['controller_result']['retry_plan']['repeat_permitted'] = False
    assert fp.repeat_plan(res, cfg) is None


def test_every_used_parameter_has_a_source_entry(tmp_path):
    audit = fp.parameter_audit(shared(), cell(tmp_path), AppConfig())
    reg = json.loads(fp.SOURCES.read_text())
    for row in audit['parameters']:
        assert row['status'] in reg['status_legend'] or row['status'].startswith('CALIBRATED')
        if row['status'] in {'LITERATURE', 'ESTABLISHED_CONCEPT'}: assert row['sources']
    assert 'cell.Cdl_specific_F_per_cm2' not in audit['flagged']
    assert not [r for r in audit['parameters'] if r['status'] == 'CONFLICTS_WITH_LITERATURE']


def test_cdl_scales_with_steel_area(tmp_path):
    c = cell(tmp_path)
    assert c['Cdl_F'] == pytest.approx(40e-6*0.02)  # 40 uF/cm2 x 0.02 cm2 = 0.8 uF
    big = fp.stage_b_cell(shared(area=4e-6), tmp_path)[0]
    assert big['Cdl_F'] == pytest.approx(2*c['Cdl_F'])


def test_changed_value_is_flagged(tmp_path):
    audit = fp.parameter_audit(shared(conductivity=5.), cell(tmp_path), AppConfig())
    row = next(r for r in audit['parameters'] if r['parameter'] == 'water.conductivity_S_m')
    assert row['status'] == 'CHANGED_NEEDS_SOURCE'
