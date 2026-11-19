"""Actual PHALK artifact tests plus explicitly SYNTHETIC boundary/failure rows."""
from pathlib import Path
from dataclasses import replace
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from python.phalk_adapter import (PHALK_COLUMNS, NUMERIC_COLUMNS, LITHOLOGY_COLUMNS, GROUPS,
    PHALKValidationConfig, PHALKDataset, SUPPLIED_ARTIFACT, SUPPLIED_CSV_SHA256,
    category_memberships, load_phalk_csv, validate_phalk_frame, main)
from python.environmental_context import EnvironmentalContext, context_from_phalk, evaluate_with_environment
from python.config import AppConfig, AcquisitionConfig
from python.decision_engine import evaluate_measurement

FIXTURE = Path(__file__).parent/'fixtures/phalk/alkalinity_SurfaceWaters.csv'


@pytest.fixture(scope="module")
def actual_dataset():
    return load_phalk_csv(FIXTURE)


@pytest.fixture
def synthetic_record():
    """SYNTHETIC environmental record, not an observed water or corrosion value."""
    row = {name: 0 for name in PHALK_COLUMNS}
    row.update(HYBAS_ID=1120000000, PHALK_ID=1, site_latitude=45., site_longitude=10.,
        site_source="Other data", site_reference="SYNTHETIC isolated software fixture", value=350.,
        value_log10=np.log10(350.), Alkalinity_gr="b) 200-500", XX_su_U=100., slp_dg_uav=50)
    return pd.DataFrame([row])


def check(report, code):
    return next(c for c in report.checks if c.code == code)


def assert_failed(report, code):
    c = check(report, code)
    assert c.status == "FAIL" and c.affected_count > 0
    assert not report.validated and report.filtered_records == 0
    json.dumps(report.to_dict(), allow_nan=False)
    return c


def test_actual_artifact_schema_identity_and_preservation(actual_dataset):
    report = actual_dataset.report
    assert report.row_count == 50916 and report.column_count == 25
    assert report.columns == PHALK_COLUMNS
    assert report.source['sha256'] == SUPPLIED_CSV_SHA256
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == SUPPLIED_CSV_SHA256
    assert check(report,"SCHEMA").status == check(report,"ROW_COUNT").status == check(report,"ARTIFACT_SHA256").status == "PASS"
    assert len(actual_dataset.data) == 50916 and report.filtered_records == 0


def test_actual_nonpositive_values_and_log_quality(actual_dataset):
    d = actual_dataset.data
    r = actual_dataset.report
    assert r.statistics['negative_alkalinity_records'] == 43
    assert r.statistics['zero_alkalinity_records'] == 0
    assert d.loc[d.value <= 0,"value_log10"].eq(-1).all()
    assert d.loc[d.value < 0,"value"].min() == -47.15
    assert check(r,"LOG10_CONSISTENCY").status == "PASS"
    assert r.statistics['positive_log10_max_absolute_error'] < 1e-12
    # Do not claim original laboratory QA can be repeated with missing ions.
    assert any("Ionic-balance" in limitation for limitation in r.limitations)


def test_actual_category_anomalies_are_reported_not_fixed(actual_dataset):
    r = actual_dataset.report
    c = assert_failed(r,"ALKALINITY_CATEGORY")
    assert r.status == "ISSUES_FOUND" and c.affected_count == 5
    assert r.invalid_record_numbers == (3382,3749,4529,4907,6932)
    ids = {int(example['PHALK_ID']['value']) for example in c.examples}
    assert ids == {59209,49125,63252,58362,62214}
    rows = actual_dataset.data.value.eq(200)
    assert actual_dataset.data.loc[rows,'Alkalinity_gr'].eq("a) < 200").all()
    assert check(r,"INTERIOR_CATEGORY_BOUNDARY").affected_count == 55


def test_actual_provenance_identifiers_coordinates_predictors(actual_dataset):
    r = actual_dataset.report
    assert r.statistics['unique_phalk_ids'] == 50916
    assert r.statistics['unique_hybas_ids'] == 22089
    assert r.statistics['duplicate_extra_records'] == 0
    assert r.statistics['repeated_coordinate_extra_records'] == 4387
    assert r.statistics['potential_related_site_extra_records'] == 151
    assert check(r,'POTENTIAL_RELATED_SITE_RECORD').affected_count == 268
    assert check(r,'LITHOLOGY_SUM_NOT_100').status == 'WARN'
    assert r.statistics['lithology_sum_not_100_records'] == 13144
    assert r.statistics['lithology_sum_min_percent'] == pytest.approx(20.6)
    assert r.statistics['lithology_sum_max_percent'] == pytest.approx(100.3)
    for c in r.checks:
        if c.code != 'ALKALINITY_CATEGORY':
            assert c.status != 'FAIL'
    assert check(r,'UNRECOGNIZED_SOURCE').status == 'PASS'
    assert check(r,'SLOPE_ENCODING').status == 'PASS'
    assert check(r,'PREDICTOR_CONSISTENCY_BY_BASIN').status == 'PASS'
    assert r.statistics['basins_with_conflicting_predictors']==0


@pytest.mark.parametrize('column', PHALK_COLUMNS)
def test_missing_columns_are_not_invented(synthetic_record,column):
    r=validate_phalk_frame(synthetic_record.drop(columns=column))
    assert r.status=='INVALID_SCHEMA'
    assert_failed(r,'SCHEMA')


def test_unexpected_physical_column_and_duplicate_headers_reject(synthetic_record,tmp_path):
    synthetic_record['conductivity_s_per_m']=1
    assert_failed(validate_phalk_frame(synthetic_record),'SCHEMA')
    path=tmp_path/'synthetic.csv'
    path.write_text('PHALK_ID,PHALK_ID\n1,1\n')
    with pytest.raises(ValueError,match='duplicate'):
        load_phalk_csv(path,PHALKValidationConfig())


@pytest.mark.parametrize('column',NUMERIC_COLUMNS)
@pytest.mark.parametrize('bad,code',[(np.nan,'MISSING_VALUE'),(np.inf,'NONFINITE_VALUE'),('malformed','MALFORMED_NUMERIC'),(True,'INVALID_NUMERIC_TYPE')])
def test_every_numeric_channel_failure_is_explicit(synthetic_record,column,bad,code):
    synthetic_record[column]=synthetic_record[column].astype(object)
    synthetic_record.loc[0,column]=bad
    c=assert_failed(validate_phalk_frame(synthetic_record),code+'.'+column)
    assert c.examples and c.expected


@pytest.mark.parametrize('column,value,code',[
    ('HYBAS_ID',0,'IDENTIFIER_INTEGRITY.HYBAS_ID'),('HYBAS_ID',123,'IDENTIFIER_INTEGRITY.HYBAS_ID'),
    ('PHALK_ID',-1,'IDENTIFIER_INTEGRITY.PHALK_ID'),('PHALK_ID',1.5,'IDENTIFIER_INTEGRITY.PHALK_ID'),
    ('site_latitude',90.01,'COORDINATE_RANGE.site_latitude'),('site_latitude',-90.01,'COORDINATE_RANGE.site_latitude'),
    ('site_longitude',180.01,'COORDINATE_RANGE.site_longitude'),('site_longitude',-180.01,'COORDINATE_RANGE.site_longitude'),
    ('XX_su_U',100.1,'LITHOLOGY_PERCENTAGE.XX_su_U'),('XX_su_U',-.1,'LITHOLOGY_PERCENTAGE.XX_su_U'),
    ('slp_dg_uav',-9999,'SLOPE_ENCODING'),('slp_dg_uav',901,'SLOPE_ENCODING'),('slp_dg_uav',1.5,'SLOPE_ENCODING'),
])
def test_identifier_coordinate_and_predictor_bounds(synthetic_record,column,value,code):
    synthetic_record[column]=synthetic_record[column].astype(object)
    synthetic_record.loc[0,column]=value
    assert_failed(validate_phalk_frame(synthetic_record),code)


@pytest.mark.parametrize('value',[-47.15,-1,0,.1,10,199.999])
def test_negative_zero_positive_log_rule_preserves_observation(synthetic_record,value):
    synthetic_record.loc[0,'value']=value
    synthetic_record.loc[0,'value_log10']=np.log10(value) if value>0 else -1
    synthetic_record.loc[0,'Alkalinity_gr']='a) < 200'
    original=synthetic_record.copy(deep=True)
    report=validate_phalk_frame(synthetic_record)
    assert report.validated and check(report,'LOG10_CONSISTENCY').status=='PASS'
    pd.testing.assert_frame_equal(synthetic_record,original)


def test_inconsistent_log_is_reported(synthetic_record):
    synthetic_record.loc[0,'value_log10']+=.01
    c=assert_failed(validate_phalk_frame(synthetic_record),'LOG10_CONSISTENCY')
    assert c.examples[0]['value']['value']==350


@pytest.mark.parametrize('threshold,below,at,above',[
    (200,('a) < 200',),('b) 200-500',),('b) 200-500',)),
    (500,('b) 200-500',),('b) 200-500','c) 500-1000'),('c) 500-1000',)),
    (1000,('c) 500-1000',),('c) 500-1000','d) 1000-2000'),('d) 1000-2000',)),
    (2000,('d) 1000-2000',),('d) 1000-2000','e) 2000-3000'),('e) 2000-3000',)),
    (3000,('e) 2000-3000',),('e) 2000-3000','f) 3000-4000'),('f) 3000-4000',)),
    (4000,('f) 3000-4000',),('f) 3000-4000',),('g) >4000',)),
])
def test_paper_category_boundaries(threshold,below,at,above):
    assert category_memberships(threshold-.001)==below
    assert category_memberships(threshold)==at
    assert category_memberships(threshold+.001)==above


def test_outer_strict_label_and_ambiguous_interior(synthetic_record):
    synthetic_record.loc[0,'value']=200
    synthetic_record.loc[0,'value_log10']=np.log10(200)
    synthetic_record.loc[0,'Alkalinity_gr']='a) < 200'
    assert_failed(validate_phalk_frame(synthetic_record),'ALKALINITY_CATEGORY')
    synthetic_record.loc[0,'value']=500
    synthetic_record.loc[0,'value_log10']=np.log10(500)
    for label in ('b) 200-500','c) 500-1000'):
        synthetic_record.loc[0,'Alkalinity_gr']=label
        r=validate_phalk_frame(synthetic_record)
        assert r.validated and check(r,'INTERIOR_CATEGORY_BOUNDARY').status=='INFO'


def test_exact_duplicate_and_conflicting_identifier(synthetic_record):
    duplicate=pd.concat([synthetic_record,synthetic_record],ignore_index=True)
    assert_failed(validate_phalk_frame(duplicate),'EXACT_DUPLICATE_RECORD')
    assert_failed(validate_phalk_frame(duplicate),'DUPLICATE_PHALK_ID')
    duplicate.loc[1,'value']=351
    duplicate.loc[1,'value_log10']=np.log10(351)
    assert_failed(validate_phalk_frame(duplicate),'CONFLICTING_PHALK_ID')
    dataset=PHALKDataset(duplicate,validate_phalk_frame(duplicate))
    assert context_from_phalk(dataset,1).validation_status=='AMBIGUOUS_RECORD'


@pytest.mark.parametrize('column',['site_source','site_reference','Alkalinity_gr'])
@pytest.mark.parametrize('value',['',None,True])
def test_missing_or_malformed_provenance_and_category(synthetic_record,column,value):
    synthetic_record[column]=synthetic_record[column].astype(object)
    synthetic_record.loc[0,column]=value
    assert_failed(validate_phalk_frame(synthetic_record),'MISSING_OR_INVALID_TEXT.'+column)


def test_artifact_count_hash_and_no_automatic_filtering(synthetic_record):
    original=synthetic_record.copy(deep=True)
    r=validate_phalk_frame(synthetic_record,SUPPLIED_ARTIFACT,source_sha256='0'*64)
    assert_failed(r,'ROW_COUNT')
    assert_failed(r,'ARTIFACT_SHA256')
    assert r.filtered_records==0 and len(synthetic_record)==1
    pd.testing.assert_frame_equal(original,synthetic_record)


def test_context_tracks_alkalinity_units_ids_and_roles(actual_dataset):
    context=context_from_phalk(actual_dataset,48763)
    assert context.available and context.validated
    assert context.observed_alkalinity_ueq_l==699.4
    assert context.site_provenance['site_source']=='CWSM'
    assert context.geographic_identifiers['HYBAS_ID']==1120012900
    assert context.geographic_identifiers['PHALK_ID']==48763
    assert len(context.environmental_predictors)==16
    assert all(not role['required_by_current_physical_model'] for role in context.field_roles.values())
    json.dumps(context.to_dict(),allow_nan=False)
    with pytest.raises(ValueError,match='alkalinity is not conductivity'):
        context.as_physical_inputs()


def test_bad_or_unavailable_context_does_not_fabricate_values(actual_dataset):
    bad=context_from_phalk(actual_dataset,59209)
    assert bad.available and not bad.validated
    assert bad.validation_checks and bad.observed_alkalinity_ueq_l == 200
    assert bad.alkalinity_group == "a) < 200"
    missing=context_from_phalk(actual_dataset,'does-not-exist')
    assert not missing.available and missing.observed_alkalinity_ueq_l is None
    negative=context_from_phalk(actual_dataset,49069)
    assert negative.validated and negative.observed_alkalinity_ueq_l==-6.79


@pytest.fixture
def circuit():
    """SYNTHETIC independent linear circuit observations, separate from PHALK."""
    cfg=AppConfig(acquisition=AcquisitionConfig(enable_reverse_check=False))
    frames={}
    for index,(name,amplitude) in enumerate([('A1',.005),('B',.01),('A2',.005)]):
        v=np.repeat(np.linspace(-amplitude,amplitude,5),300)
        frames[name]=pd.DataFrame({'time_s':61+index*80+np.arange(len(v))/20,'actual_voltage_v':v,
            'current_a':v/110000,'requested_voltage_v':v,'point_index':np.repeat(np.arange(5),300),
            'drive_enabled':True,'reverse':False,'block_id':name})
    evidence={'opening_check':{'passed':True,'source':'SYNTHETIC circuit check'},'closing_check':{'passed':True,'source':'SYNTHETIC circuit check'},
        'ocp_opening':pd.DataFrame({'time_s':np.arange(1,1201)/20,'ocp_voltage_v':0.}),
        'ocp_closing':pd.DataFrame({'time_s':300+np.arange(1,1201)/20,'ocp_voltage_v':0.})}
    return cfg,frames,evidence


def test_valid_environment_cannot_replace_missing_circuit(actual_dataset):
    context=context_from_phalk(actual_dataset,48763)
    result=evaluate_with_environment({},environmental_context=context)
    assert result.decision=='REJECT'
    assert result.decision_result.rp_estimate is None and result.decision_result.corrosion_estimate is None
    assert result.environmental_context.validated


def test_no_environment_and_invalid_optional_metadata_keep_valid_circuit(circuit):
    cfg,frames,evidence=circuit
    baseline=evaluate_measurement(frames,cfg,**evidence).to_dict()
    for optional in (None,EnvironmentalContext(),False):
        result=evaluate_with_environment(frames,cfg,environmental_context=optional,**evidence)
        assert result.decision=='ACCEPT' and result.decision_result.to_dict()==baseline
        assert not result.environmental_context.validated


def test_valid_bad_and_negative_environment_never_change_circuit_semantics(circuit,actual_dataset):
    cfg,frames,evidence=circuit
    baseline=evaluate_measurement(frames,cfg,**evidence).to_dict()
    for record_id in (48763,59209,49069):
        result=evaluate_with_environment(frames,cfg,environmental_context=context_from_phalk(actual_dataset,record_id),**evidence)
        assert result.decision_result.to_dict()==baseline
        assert result.decision_result.corrosion_estimate is None and result.decision_result.rp_estimate is None
    evidence['opening_check']['passed']=False
    result=evaluate_with_environment(frames,cfg,environmental_context=context_from_phalk(actual_dataset,48763),**evidence)
    assert result.decision=='REJECT' and 'OPENING_CHECK_FAILURE' in result.decision_result.reason_codes


def test_phalk_columns_passed_as_circuit_are_rejected(actual_dataset):
    result=evaluate_with_environment({'A1':actual_dataset.data.iloc[:5]},environmental_context=context_from_phalk(actual_dataset,48763))
    assert result.decision=='REJECT' and 'INVALID_INPUT' in result.decision_result.reason_codes


def test_validator_cli_reports_actual_anomalies_without_rewriting(tmp_path,capsys):
    before=hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert main([str(FIXTURE),'--output-dir',str(tmp_path)])==1
    data=json.loads((tmp_path/'phalk-validation.json').read_text())
    assert data['row_count']==50916 and data['status']=='ISSUES_FOUND'
    assert data['filtered_records']==0 and len(data['invalid_record_numbers'])==5
    assert 'ISSUES_FOUND' in capsys.readouterr().out
    assert before==hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


def test_context_keeps_numeric_observation_with_bad_predictor(synthetic_record):
    synthetic_record.loc[0,'XX_su_U']=-1
    data=PHALKDataset(synthetic_record,validate_phalk_frame(synthetic_record))
    context=context_from_phalk(data,1)
    assert context.available and not context.validated
    assert context.observed_alkalinity_ueq_l==350
    assert context.environmental_predictors['XX_su_U']['value']==-1
    assert context.validation_checks


def test_context_nonfinite_observation_stays_unavailable(synthetic_record):
    synthetic_record.loc[0,'value']=np.inf
    data=PHALKDataset(synthetic_record,validate_phalk_frame(synthetic_record))
    context=context_from_phalk(data,1)
    assert context.available and not context.validated
    assert context.observed_alkalinity_ueq_l is None
    json.dumps(context.to_dict(),allow_nan=False)


def test_context_normalizes_identifier_without_first_match(synthetic_record):
    duplicate=pd.concat([synthetic_record,synthetic_record],ignore_index=True)
    duplicate['PHALK_ID']=duplicate.PHALK_ID.astype(object)
    duplicate.loc[1,'PHALK_ID']='01'
    data=PHALKDataset(duplicate,validate_phalk_frame(duplicate))
    assert context_from_phalk(data,1).validation_status=='AMBIGUOUS_RECORD'


def test_conflicting_basin_predictors_are_reviewed_not_rewritten(synthetic_record):
    rows=pd.concat([synthetic_record,synthetic_record],ignore_index=True)
    rows.loc[1,'PHALK_ID']=2
    rows.loc[1,'XX_su_U']=99
    rows.loc[1,'XX_sc_U']=1
    original=rows.copy(deep=True)
    report=validate_phalk_frame(rows)
    c=check(report,'PREDICTOR_CONSISTENCY_BY_BASIN')
    assert c.status=='WARN' and c.affected_count==2
    assert report.statistics['basins_with_conflicting_predictors']==1
    pd.testing.assert_frame_equal(rows,original)
