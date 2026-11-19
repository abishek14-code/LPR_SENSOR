"""Native spatial FEM integration case and independently extracted WE-to-RE Ru."""
from common import ROOT,write,sha
from p4_reference import spatial
import numpy as np
import pandas as pd
from pathlib import Path
import subprocess,shutil,re,json
from interfaces import read_vtu

def execute(command,cwd,log):
    p=subprocess.run(command,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=180)
    log.write_text(p.stdout)
    assert p.returncode==0,(command,p.returncode)
    assert not re.search(r'FATAL|not converged',p.stdout,re.I),p.stdout[-2000:]
    return {'command':command,'returncode':p.returncode,'console_sha256':sha(log)}

def extract(case,spec,sigma):
    vtu=case/'conforming_case_t0001.vtu';a=read_vtu(vtu)
    nodes=np.loadtxt(case/'mesh.nodes');els=np.loadtxt(case/'mesh.elements',dtype=int)
    assert np.allclose(nodes[:,2:5],a['xyz'],rtol=0,atol=1e-12)
    ids={body:set(els[els[:,1]==body,3:].ravel()) for body in [1,2,3]}
    assert ids[1]&ids[2] and ids[2]&ids[3],'disconnected mesh'
    d=spec['depth_m'];grads={};energy=0.;rows=[];probe=None
    xyprobe=np.array(spec['reference_probe_m']);we_phi=[];ce_phi=[]
    for eid,body,typ,*n in els:
        assert typ==303
        xy=a['xyz'][np.array(n)-1,:2];v=a['potential'][np.array(n)-1]
        mat=np.column_stack([np.ones(3),xy]);coef=np.linalg.solve(mat,v)
        grad=coef[1:];s=sigma if body==2 else spec['electrode_conductivity_S_m']
        area=.5*abs(np.linalg.det(np.array([xy[1]-xy[0],xy[2]-xy[0]])))
        j=-s*grad;energy+=s*np.dot(grad,grad)*area*d;grads[eid]=j
        if body==2:
            bary=np.linalg.solve(mat.T,np.r_[1,xyprobe])
            if min(bary)>-1e-9:probe=float(np.r_[1,xyprobe]@coef)
        rows.append({'element':eid,'body':body,'x_m':xy[:,0].mean(),'y_m':xy[:,1].mean(),
                     'area_m2':area,'Jx_A_m2':j[0],'Jy_A_m2':j[1],'E_x_V_m':-grad[0],'E_y_V_m':-grad[1]})
    assert probe is not None,'RE point not in electrolyte'
    we_ids=np.array(sorted(ids[1]&ids[2]))-1;ce_ids=np.array(sorted(ids[2]&ids[3]))-1
    we=float(a['potential'][we_ids].mean());ce=float(a['potential'][ce_ids].mean())
    bottom=a['potential'][np.isclose(a['xyz'][:,1],0)];top=a['potential'][np.isclose(a['xyz'][:,1],.05)]
    delta=float(top.mean()-bottom.mean());ie=energy/delta
    boundary=np.loadtxt(case/'mesh.boundary',dtype=int);flux={}
    for bid,normal in [(4,np.array([0,-1])),(5,np.array([0,1]))]:
        total=0.
        for _,_,parent,_,typ,n1,n2 in boundary[boundary[:,1]==bid]:
            assert typ==202
            total+=float(grads[parent]@normal)*np.linalg.norm(a['xyz'][n1-1,:2]-a['xyz'][n2-1,:2])*d
        flux[str(bid)]=total
    ib=(abs(flux['4'])+abs(flux['5']))/2
    cells=pd.DataFrame(rows);elec=cells[cells.body==2];jy=abs(elec.Jy_A_m2)
    mean=float(np.average(jy,weights=elec.area_m2));cv=float(np.sqrt(np.average((jy-mean)**2,weights=elec.area_m2))/mean)
    ru=abs(probe-we)/abs(ie);prediction=spatial(spec,sigma)
    res={'case_id':case.name,'source_workstream':'P2','source_type':'NEW native Elmer spatial simulation',
      'geometry_id':spec['geometry_id'],'geometry':spec,'conductivity_S_m':sigma,'electrodes':{'WE':'bottom slab','CE':'top slab','RE':'nonloading electrolyte point observation'},
      'mesh_nodes':len(nodes),'triangles':len(els),'shared_WE_interface_nodes':len(we_ids),'shared_CE_interface_nodes':len(ce_ids),
      'delta_outer_electrode_V':delta,'WE_solution_potential_V':we,'RE_solution_potential_V':probe,
      'CE_solution_potential_V':ce,'delta_WE_RE_V':abs(probe-we),'power_W':energy,
      'integrated_outer_boundary_current_A':flux,'energy_current_A':ie,'mean_boundary_current_magnitude_A':ib,
      'boundary_imbalance_fraction':abs(sum(flux.values()))/max(abs(flux['4']),abs(flux['5'])),
      'Ru_ohm':ru,'Ru_boundary_flux_ohm':abs(probe-we)/ib,
      'full_outer_electrode_resistance_ohm':delta/ie,'bulk_solution_resistance_ohm':abs(ce-we)/ie,
      'counter_to_reference_ohm':abs(ce-probe)/ie,'current_density_mean_A_m2':mean,'current_density_CV':cv,
      'P4_independent':prediction,'relative_Ru_difference_P4':(ru-prediction['Ru_ohm'])/prediction['Ru_ohm'],
      'units':{'Ru':'ohm','current':'A including explicit 50um depth','potential':'V','coordinates':'m','current_density':'A/m^2'},
      'normalization':'none; physical planar SI scale and stated out-of-plane depth applied',
      'Ru_definition':'abs(phi_solution(RE)-mean(phi_solution at WE surface))/abs(total cell current)',
      'extraction':'linear triangle gradients + sigma|grad(phi)|^2 area*depth energy integral; independent boundary flux; barycentric nonloading RE potential',
      'primary_current_method':'energy identity I=P/deltaV; boundary current corroboration separately retained',
      'convergence':'native successful linear/nonlinear termination; console retained',
      'online_Ru_eligible':False,'physical_specimen_mapping':'NOT ESTABLISHED; new explicitly specified uniform-field test case',
      'hashes':{p.name:sha(p) for p in case.iterdir() if p.is_file()},'calculation_type':'independently calculated from raw potential fields'}
    res['mapping_validated']=abs(res['relative_Ru_difference_P4'])<.01 and res['boundary_imbalance_fraction']<.01 and cv<.01
    assert res['mapping_validated'],res
    cells.to_csv(case/'element_fields.csv',index=False)
    pd.DataFrame({'x_m':a['xyz'][:,0],'y_m':a['xyz'][:,1],'potential_V':a['potential'],
                  'Jx_native_A_m2':a['J'][:,0],'Jy_native_A_m2':a['J'][:,1]}).to_csv(case/'nodal_fields.csv',index=False)
    write(case/'extracted.json',res);return res

def main(out=None):
    spec=json.loads((ROOT/'configs/physical_integration.json').read_text())
    out=Path(out or ROOT/'evidence/P2');out.mkdir(parents=True,exist_ok=False);results=[]
    source=ROOT/'inputs/P2';siftxt=(source/'conforming_case.sif').read_text()
    # High conductivity contrast caused poor recovered boundary-flux balance
    # on refinement under the inherited CG tolerances. Use a native direct solve
    # for this new integration case; keep failed iterative evidence separately.
    siftxt=siftxt.replace('Linear System Solver = Iterative','Linear System Solver = Direct\n  Linear System Direct Method = UMFPACK')
    # Existing corrected mesh topology preserved, coordinates explicitly scaled to SI.
    for sigma in spec['conductivity_sweep_S_m']:
        case=out/f'sigma_{sigma:g}';case.mkdir()
        for p in source.glob('mesh.*'):shutil.copy2(p,case/p.name)
        n=np.loadtxt(case/'mesh.nodes');n[:,2:5]*=spec['coordinate_scale_m_per_original_unit']
        np.savetxt(case/'mesh.nodes',n,fmt=['%d','%d','%.16g','%.16g','%.16g'])
        sif=siftxt.replace('Electric Conductivity = 1.0\n',f'Electric Conductivity = {sigma}\n')
        (case/'conforming_case.sif').write_text(sif)
        run=execute(['/opt/homebrew/bin/ElmerSolver','conforming_case.sif'],case,case/'solver.console.txt')
        r=extract(case,spec,sigma);r['run']=run;results.append(r);print(case.name,r['Ru_ohm'],flush=True)
    # Correct connected geometry refinement, distinct from invalid historical disconnected mesh study.
    from mesh_refinement import refine
    previous=out/'sigma_1'
    for scale in [.5,.25]:
        case=out/f'refine_{scale:g}';case.mkdir()
        refine(previous,case)
        previous=case
        (case/'conforming_case.sif').write_text(siftxt)
        execute(['/opt/homebrew/bin/ElmerSolver','conforming_case.sif'],case,case/'solver.console.txt')
        r=extract(case,spec,1.);results.append(r);print(case.name,r['Ru_ohm'],flush=True)
    write(out/'manifest.json',{'physical_spec':spec,'results':results,'P4_equations':'code/p4_reference.py',
      'mesh_refinement_change_fraction':abs(results[-1]['Ru_ohm']-results[-2]['Ru_ohm'])/results[-1]['Ru_ohm'],
      'engine':{'path':'/opt/homebrew/bin/ElmerSolver','sha256':sha('/opt/homebrew/bin/ElmerSolver')},
      'physical_equivalence_scope':'confined uniform-field integration case with nonloading RE; not supplied specimen Ru'})
    return results

if __name__=='__main__':main()
