"""Published parallel-plate geometry, explicitly reduced to ohmic conduction."""
from pathlib import Path
from common import ROOT,write
from validate_3d_fixture import solve

def main(out):
    a=solve(out/'coarse',.1,.1,.012,1.,4)
    b=solve(out/'fine',.1,.1,.012,1.,8)
    change=abs(a['Ru_ohm']/b['Ru_ohm']-1);assert change<.01
    record=dict(status='PASS',geometry_source='COMSOL Soluble Lead-Acid Redox Flow Battery model, CELL GEOMETRY AND MESH',
        source_url='https://doc.comsol.com/6.3/doc/com.comsol.help.models.battery.pb_flow_battery/pb_flow_battery.html',
        adopted_dimensions='two 10 cm square parallel electrodes, 12 mm separation',
        scope='Literature-dimensioned 3D parallel-plate ohmic benchmark; not replication of battery chemistry or experimental validation',
        declared_assumptions=dict(conductivity_S_m=1.,reference='nonloading midpoint, 6 mm from WE',
            sides='insulating; published inlet/outlet edge extensions omitted',electrodes='full-face equipotentials',
            chemistry='no reactions, transport, flow, or measured corrosion parameters'),
        physical_specimen_qualified=False,coarse=a,fine=b,mesh_change_fraction=change)
    write(out/'QUALIFICATION.json',record)
    return record



def electrical_check(out, qualification):
    import numpy as np
    from close_integration import adapter,case_run
    from publish_results import publish
    adapter.load_runtime()
    field=qualification['fine'];ru=field['Ru_ohm']
    spatial=dict(scope=qualification['scope'],geometry={k:field[k] for k in ['width_m','depth_m','length_m','reference_probe_m']},
        conductivity_S_m=1.,Ru_ohm=ru,counter_to_reference_ohm=ru,
        standard_uncertainty_ohm=ru*np.sqrt(4*.01**2/3),
        uncertainty_assumptions='Independent rectangular 1% engineering assumptions; not measured',
        field_sha256=field['hashes']['field_t0001.vtu'],physical_specimen_qualified=False)
    out.mkdir(parents=True,exist_ok=False)
    result=case_run(out,dict(id='literature_parallel_plate'),{'1.0':spatial})
    assert result['decision']=='ACCEPT'
    assert abs(result['offline_evaluation']['Rp_error_fraction'])<.05
    write(out/'manifest.json',dict(status='COMPLETED',results=[result]))
    publish(out)
    return result
