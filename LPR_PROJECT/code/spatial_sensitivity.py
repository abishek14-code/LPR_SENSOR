"""Extract RE-position sensitivity from fresh fields, plus declared model uncertainty."""
from pathlib import Path
import json,sys
import numpy as np
from common import ROOT,write,sha
from interfaces import read_vtu

def main(p2_root=None, output=None):
    p2_root=Path(p2_root or ROOT/'evidence/P2')
    rows=[]
    for sigma in [.5,1.,2.]:
        case=p2_root/f'sigma_{sigma:g}'
        r=json.loads((case/'extracted.json').read_text())
        data=read_vtu(case/'conforming_case_t0001.vtu')
        xyz=data['xyz'][:,:2];phi=data['potential']
        elements=np.loadtxt(case/'mesh.elements',dtype=int)
        ids=elements[elements[:,1]==2,3:]-1;x=xyz[ids]
        for distance in [.01,.02,.03]:
            point=np.array([.02,.005+distance])
            candidates=np.flatnonzero(((x.min(axis=1)<=point+1e-12)&(x.max(axis=1)>=point-1e-12)).all(axis=1))
            potential=None
            for idx in candidates:
                weights=np.linalg.solve(np.vstack([np.ones(3),x[idx].T]),np.r_[1.,point])
                if min(weights)>=-1e-8:
                    potential=float(weights@phi[ids[idx]]);break
            assert potential is not None
            ru=(potential-r['WE_solution_potential_V'])/r['energy_current_A']
            expected=distance/(sigma*.04*50e-6)
            rows.append(dict(conductivity_S_m=sigma,reference_distance_m=distance,
                RE_potential_V=potential,WE_potential_V=r['WE_solution_potential_V'],current_A=r['energy_current_A'],
                Ru_ohm=ru,P4_Ru_ohm=expected,relative_error=ru/expected-1,
                field_sha256=sha(case/'conforming_case_t0001.vtu')))
    rng=np.random.default_rng(20261021)
    factors=rng.uniform(.99,1.01,size=(100000,4))
    ratios=factors[:,0]/(factors[:,1]*factors[:,2]*factors[:,3])
    budget=dict(scope='ENGINEERING SIMULATION UNCERTAINTY ASSUMPTIONS ONLY',samples=len(ratios),seed=20261021,
        reference_distance_sigma_width_depth='independent rectangular ±1% factors',
        relative_standard_uncertainty_linear=float(np.sqrt(4*.01**2/3)),
        relative_standard_deviation_MC=float(np.std(ratios,ddof=1)),
        relative_quantiles_MC=np.quantile(ratios,[.025,.5,.975]).tolist(),
        not_physical_confidence_interval=True)
    write(output or ROOT/'evidence/SPATIAL_SENSITIVITY.json',dict(results=rows,uncertainty=budget))
    print('Verified 9 conductivity/reference-position combinations from fresh FEM fields.')

if __name__=='__main__':main()
