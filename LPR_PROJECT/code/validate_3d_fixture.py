"""Native 3D cuboid verification; no claim about the undocumented specimen.

Uniform electrolyte, insulating side walls, full-face equipotential electrodes.
The analytic field is affine. Eight-node hexahedra reproduce it exactly;
field-affinity is explicitly checked before using the affine gradient integral.
"""
from pathlib import Path
import argparse, json
import numpy as np
from common import ROOT,write,sha
from p2_fem import execute
from interfaces import read_vtu

def solve(out,w,d,length,sigma,n):
    out.mkdir(parents=True,exist_ok=False)
    def idx(i,j,k):return 1+i+(n+1)*(j+(n+1)*k)
    xyz=np.array([[w*i/n,length*j/n,d*k/n] for k in range(n+1) for j in range(n+1) for i in range(n+1)])
    els=[];faces=[]
    for k in range(n):
        for j in range(n):
            for i in range(n):
                v=[idx(i,j,k),idx(i+1,j,k),idx(i+1,j+1,k),idx(i,j+1,k),idx(i,j,k+1),idx(i+1,j,k+1),idx(i+1,j+1,k+1),idx(i,j+1,k+1)]
                eid=len(els)+1;els.append([eid,1,808,*v])
                for yes,tag,positions in [(j==0,4,[0,1,5,4]),(j==n-1,5,[3,2,6,7])]:
                    if yes:faces.append([len(faces)+1,tag,eid,0,404,*[v[q] for q in positions]])
    np.savetxt(out/'mesh.nodes',np.c_[np.arange(1,len(xyz)+1),np.full(len(xyz),-1),xyz],fmt=['%d','%d','%.16g','%.16g','%.16g'])
    np.savetxt(out/'mesh.elements',els,fmt='%d');np.savetxt(out/'mesh.boundary',faces,fmt='%d')
    (out/'mesh.header').write_text(f'{len(xyz)} {len(els)} {len(faces)}\n2\n404 {len(faces)}\n808 {len(els)}\n')
    sif=f'''Header
 Mesh DB "." "."
End
Simulation
 Coordinate System = Cartesian 3D
 Simulation Type = Steady state
 Steady State Max Iterations = 1
 Post File = field.vtu
End
Body 1
 Equation = 1
 Material = 1
End
Equation 1
 Active Solvers(1) = 1
End
Solver 1
 Equation = Static Current Conduction
 Procedure = "StatCurrentSolve" "StatCurrentSolver"
 Variable = Potential
 Calculate Volume Current = True
 Exec Solver = Before Simulation
 Linear System Solver = Direct
 Linear System Direct Method = UMFPACK
End
Material 1
 Electric Conductivity = {sigma}
End
Boundary Condition 1
 Target Boundaries(1) = 4
 Potential = 0
End
Boundary Condition 2
 Target Boundaries(1) = 5
 Potential = 1
End
'''
    (out/'case.sif').write_text(sif)
    run=execute(['/opt/homebrew/bin/ElmerSolver','case.sif'],out,out/'solver.console.txt')
    field=read_vtu(out/'field_t0001.vtu');phi=field['potential']
    assert np.allclose(field['xyz'],xyz,atol=1e-12,rtol=0)
    affinity=float(np.max(abs(phi-xyz[:,1]/length)))
    assert affinity<1e-7,'Non-affine field: use general quadrature instead'
    gradients=[];energy=0
    for row in els:
        points=xyz[np.array(row[3:])-1];values=phi[np.array(row[3:])-1]
        coeff=np.linalg.lstsq(np.c_[np.ones(8),points],values,rcond=None)[0]
        grad=coeff[1:];gradients.append(grad);energy+=sigma*float(grad@grad)*w*d*length/n**3
    flux={4:0.,5:0.}
    for _,tag,parent,_,_,*nodes in faces:
        normal=np.array([0.,-1. if tag==4 else 1.,0.])
        flux[tag]+=float((-sigma*gradients[parent-1])@normal)*w*d/n**2
    ib=(abs(flux[4])+abs(flux[5]))/2
    # Nonloading RE at half-gap, interpolated from a containing cell's affine field.
    probe=np.array([w/2,length/2,d/2]);potential=None
    for row in els:
        points=xyz[np.array(row[3:])-1]
        if np.all(probe>=points.min(0)-1e-14) and np.all(probe<=points.max(0)+1e-14):
            coeff=np.linalg.lstsq(np.c_[np.ones(8),points],phi[np.array(row[3:])-1],rcond=None)[0]
            potential=float(np.r_[1,probe]@coeff);break
    ru=potential/energy;expected=length/(2*sigma*w*d)
    result=dict(scope='DECLARED 3D UNIFORM CUBOID SIMULATION FIXTURE ONLY',physical_specimen_qualified=False,
        width_m=w,depth_m=d,length_m=length,conductivity_S_m=sigma,divisions=n,nodes=len(xyz),hexahedra=len(els),
        reference_probe_m=probe.tolist(),Ru_ohm=ru,analytical_Ru_ohm=expected,relative_error=abs(ru/expected-1),
        field_affinity_max_V=affinity,power_W=energy,current_from_energy_A=energy,boundary_flux_A=flux,
        flux_imbalance_fraction=abs(sum(flux.values()))/ib,energy_flux_disagreement_fraction=abs(energy/ib-1),
        run=run,hashes={p.name:sha(p) for p in out.iterdir() if p.is_file()})
    assert max(result['relative_error'],result['flux_imbalance_fraction'],result['energy_flux_disagreement_fraction'])<.01
    write(out/'extracted.json',result);return result

def main(out):
    rows=[]
    for name,w,d,l,sigma in [('wide',.04,.001,.04,1.),('narrow',.02,.002,.06,.5),('conductive',.03,.0015,.04,2.)]:
        a=solve(out/(name+'_coarse'),w,d,l,sigma,4);b=solve(out/(name+'_fine'),w,d,l,sigma,8)
        change=abs(a['Ru_ohm']/b['Ru_ohm']-1);assert change<.01
        rows.append(dict(case=name,coarse=a,fine=b,mesh_change_fraction=change))
    result=dict(status='PASS',cases=rows,scope='Three declared 3D uniform cuboids, two meshes each; not the retained specimen',
        limitations='Full-face ideal electrodes; uniform conductivity; no spreading or finite electrode effects. Original specimen dimensions still unconfirmed.')
    write(out/'QUALIFICATION.json',result);print('3D fixture qualification PASS: six fresh Elmer solves')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('out',type=Path);main(p.parse_args().out)
