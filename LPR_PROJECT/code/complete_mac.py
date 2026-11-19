"""Run fresh planar FEM, native fault campaign and declared 3D benchmarks."""
from pathlib import Path
from datetime import datetime, timezone
import argparse,json,subprocess,sys,webbrowser
from common import ROOT,write

def summary(out,batch,fixtures,literature,electrical):
    validation=json.loads((batch/'RELEASE_VERIFICATION.json').read_text())
    counts=json.loads((batch/'VALIDATION_SUMMARY.json').read_text())
    record=dict(status='PASS',planar_campaign=str(batch.relative_to(ROOT)),
        declared_3d=str(fixtures.relative_to(ROOT)),literature_geometry=str(literature.relative_to(ROOT)),
        literature_electrical=str(electrical.relative_to(ROOT)),physical_specimen_qualified=False,
        scientific_source_files_verified=validation['unchanged_supplied_project_files'])
    write(out/'COMPLETION.json',record)
    import os,html
    link=lambda p,label:f'<li><a href="{html.escape(os.path.relpath(p,out))}">{label}</a></li>'
    doc='''<!doctype html><meta charset="utf-8"><title>Mac integrated simulation completion</title><style>body{font:17px system-ui;max-width:900px;margin:50px auto;padding:24px;line-height:1.6}h1{color:#176345}li{margin:14px 0}</style><h1>PASS — integrated simulation on this Mac</h1><p>Fresh Elmer → extracted solution resistance → native LTspice → Python decisions → report. The supplied scientific Python sources remain unchanged.</p><ul>'''
    doc+=link(batch/'index.html','16-case native campaign and detailed decisions')+link(batch/'RELEASE_VERIFICATION.json','Independent release checks')+link(fixtures/'QUALIFICATION.json','Three declared 3D cuboids: six Elmer solves')+link(literature/'QUALIFICATION.json','Published parallel-plate dimensions: two-mesh 3D verification')+link(electrical/'index.html','Literature-dimensioned FEM → LTspice → Python result')
    doc+='''</ul><h2>Qualification scope</h2><p>These are verified simulation fixtures. The literature model adopts 10 cm square plates and a 12 mm gap from <a href="https://doc.comsol.com/6.3/doc/com.comsol.help.models.battery.pb_flow_battery/pb_flow_battery.html">COMSOL's published soluble lead–acid flow battery model</a>. The ohmic reduction uses declared conductivity, a nonloading midpoint reference and insulating sides; it does not reproduce battery chemistry, flow or edge extensions.</p><p>The original physical specimen remains unqualified. Published dimensions do not establish the dimensions, conductivity, electrode placement, or measured response of a real specimen. No hardware or physical corrosion-rate validation occurred.</p>'''
    (out/'index.html').write_text(doc)
    return out/'index.html'

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-open',action='store_true');args=parser.parse_args()
    out=ROOT/'evidence/mac_completion'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    out.mkdir(parents=True,exist_ok=False)
    write(out/'COMPLETION.json',dict(status='RUNNING',physical_specimen_qualified=False))
    try:
        batch=out/'native'
        subprocess.run([sys.executable,str(ROOT/'run_simulation.py'),'--matrix','--fresh-fem','--no-open','--batch',str(batch)],check=True)
        from validate_release import validate
        validate(batch)
        from validate_3d_fixture import main as three_d
        from literature_fixture import main as literature_main,electrical_check
        three_d(out/'declared_3d')
        q=literature_main(out/'literature_geometry')
        electrical_check(out/'literature_electrical',q)
        page=summary(out,batch,out/'declared_3d',out/'literature_geometry',out/'literature_electrical')
    except Exception as error:
        write(out/'COMPLETION.json',dict(status='FAILED',error=repr(error)));raise
    print('Completed:',page)
    if not args.no_open:webbrowser.open(page.as_uri())

if __name__=='__main__':main()
