"""Assemble a bounded, evidence-backed delivery without changing native sources."""
from pathlib import Path
import argparse,json,shutil,html,hashlib,zipfile
from common import ROOT,write,sha
from publish_results import publish
from validate_release import validate

def main(batch):
    batch=Path(batch).resolve();verification=validate(batch)
    manifest=json.loads((batch/'manifest.json').read_text())
    rows=[]
    for r in manifest['results']:
        row=dict(r);row['case_directory']=str((batch/r['case']).relative_to(ROOT))
        row['report']=str((batch/r['report']).relative_to(ROOT));rows.append(row)
    write(ROOT/'manifest.json',dict(status='COMPLETED',source_batch=str(batch.relative_to(ROOT)),results=rows))
    summary=publish(ROOT)
    shutil.copy2(ROOT/'index.html',ROOT/'decision_report.html')
    logs=ROOT/'evidence/verification_logs';logs.mkdir(exist_ok=True)
    work=ROOT.parents[1]/'work/SIMULATION_CLOSURE_20261021'
    for p in work.glob('*.log'):shutil.copy2(p,logs/p.name)
    assert '184 passed' in (logs/'p3_tests.log').read_text()
    assert '16 passed' in (logs/'interface_tests_release.log').read_text()
    # Retain exact field/audit evidence for why the separate 3D model stays excluded.
    source=ROOT.parents[1]/'outputs/FEM_DOCUMENTATION_20261019'
    for name in ['ALL_CASE_CALCULATIONS.json','QUALIFICATION_DECISION.json','USED_SOURCE_FIELD_SHA256.json']:
        shutil.copy2(source/name,ROOT/'evidence'/('RETAINED_3D_'+name))
    p2=json.loads((ROOT/'evidence/P2/manifest.json').read_text())
    nominal=next(x for x in rows if x['case']=='nominal_healthy')
    tests=dict(existing_P3_tests_passed=184,new_interface_tests_passed=16,total=200,
        note='Selected existing P3 regression suite plus new interface tests; not a rerun of every historical campaign.')
    summary.update(tests=tests,source_preservation=verification['unchanged_supplied_project_files'],
        native_original_preservation=verification['original_successful_nominal_hashes'])
    write(ROOT/'VALIDATION_SUMMARY.json',summary)
    gap_rows=[
        ('Qualified P2 → P1/P3 Ru transfer','CLOSED FOR PLANAR SIMULATION FIXTURE','Five fresh native Elmer cases, conductivity and reference-position extraction, flux/energy agreement and mesh gates. Separate 3D specimen remains NOT QUALIFIED.'),
        ('Reference and calibration evidence','DEMONSTRATED IN NATIVE SIMULATION','Independent switched resistor, signed training levels, independent holdouts and positive reference probes. Observable drive-off OCP remains a separate measurement.'),
        ('Corrected Rp output','DEMONSTRATED UNDER DECLARED MODEL',f'Nominal Rapp {nominal["Rapp_ohm"]:.6f} Ω; Ru 10000 Ω; Rp {nominal["Rp_ohm"]:.6f} Ω. Unchanged P3 correction gate.'),
        ('End-to-end repeat feedback','DEMONSTRATED IN NATIVE SIMULATION','P3 request → fresh independent reference → fresh continuous-prefix simulation with 45 s/point retry. One-retry bound and exact prefix comparison enforced.'),
        ('Integrated condition validation','DEMONSTRATED IN BOUNDED MATRIX','16 declared cases, 22 fresh native executions in the final matrix, healthy/fault/noise/conductivity/reference-position/timeout/reset coverage.'),
        ('Comparative novelty evidence','PRELIMINARY EQUAL-INPUT PILOT','Three simple ungated estimators compared with P3 on identical native A1 observations. This is not proof of superiority over published instruments or algorithms.')]
    write(ROOT/'GAP_STATUS.json',[dict(item=a,status=b,evidence=c) for a,b,c in gap_rows])
    md=['# Simulation integration implementation and validation','',
        'Completed 21 October 2026. Scope: declared planar simulation fixture. P3 logic and thresholds unchanged.','',
        '| Requested item | Status | Evidence |','|---|---|---|']
    md += ['| '+' | '.join(x)+' |' for x in gap_rows]
    md += ['', '## Final native results','',
        f"{summary['counts']['ACCEPT']} ACCEPT, {summary['counts']['REJECT']} REJECT, {summary['counts']['WAIT_OR_REPEAT']} WAIT_OR_REPEAT; {summary['native_executions']} fresh native executions; {summary['actual_reacquisitions']} executed reacquisitions.",
        '', '| Case | Decision | Retries | Rapp (Ω) | Rp (Ω) |','|---|---|---:|---:|---:|']
    md += [f"| {r['case']} | {r['decision']} | {r['actual_retries']} | {r['Rapp_ohm']} | {r['Rp_ohm']} |" for r in rows]
    md += ['', '## Exact interface change','',
        'The new adapter supplies the existing P3 reference-check fields (`passed`, `source`, expected/measured current and requested/measured voltage) from the independent resistor phase, and supplies qualified simulation Ru through the existing `EvaluationContext`. No check is authorized by an OCP or calibration label alone. Raw observations, not the acquisition plan, determine the frames and switch context.',
        '', 'The scientific cell/amplifier topology is unchanged. The new reference protocol uses −1/+1 mV training, −0.5/+0.5 mV holdouts and a +1 mV check, all through the existing 10 kΩ switched resistor. The final protocol observes 10 s per level and analyzes the final 100 native clock samples. The source circuit had ±22 mV calibration-labelled levels without an authorized adapter contract.',
        '', 'The first new 1 s protocol passed nominal but failed reference checks for moderate-noise seeds 7 and 101. Those runs remain archived. The uniform 10 s acquisition revision passes the three tested moderate-noise seeds without changing P3’s 5% reference rule. This is not proof that every noise realization will pass.',
        '', '## Spatial qualification','',
        f"The five fresh planar solves have a last-two-mesh Ru change fraction of {p2['mesh_refinement_change_fraction']:.9g}. At σ = 0.5/1/2 S/m, midgap WE–RE Ru = 20/10/5 kΩ. Reference distance 10/20/30 mm is extracted from the fresh potential fields. Geometry, material, field and extraction hashes bind the model to P1 and P3.",
        '', 'The separate 3D specimen-shaped model remains excluded: its retained raw-gradient boundary flux is imbalanced and its mesh-change gate fails. Weak reaction balance does not by itself close that qualification. This implementation does not relabel the 3D model as integrated or physically validated.',
        '', '## Retry continuity','',
        'The adapter uses P3’s actual `repeat_permitted` decision and one-retry budget. It first simulates an independent reference. If that passes, it appends the 45 s acquisition to a fresh simulation of the entire continuous prefix. Prefix samples match exactly. This costs extra native solve time but preserves the simulated specimen state without passing its internal capacitor voltage to P3. It is not a live instrument pause/resume mechanism.',
        '', '## Tests and evidence integrity','',
        '184 selected existing P3 tests and 16 new interface tests passed (200 total). Tests cover missing/invalid reference evidence, independent holdout failure, truth-channel removal, scenario-label independence, source mutation protection, corrected-Rp prerequisites, altered provenance and stale-report prevention. All 185 supplied scientific files and the original successful native RAW/LOG/circuit hashes were verified unchanged.',
        '', '## Comparative interpretation','',
        'Last-sample, whole-point-mean and last-quarter OLS estimators use the same native A1 data as the gated P3 estimator. Error is calculated offline against Ru + Rp. All evaluable attempts, including pre-retry attempts, remain in the comparison. A numerical error ≤5% does not establish valid calibration, recovery or timing; such rejections are not automatically false. This small simulation pilot is preliminary novelty evidence, not a statistically established superiority claim.',
        '', '## Remaining limits','',
        'Actual specimen-shaped 3D qualification is unresolved. The qualified fixture assumes a planar uniform field and ideal nonloading reference. Uncertainty distributions are declared engineering assumptions, not measured tolerances or physical coverage guarantees. P3’s existing settling-estimator limitations remain. Physical corrosion-rate output is withheld. Existing publication manuscripts have not been silently rewritten; this report records the newer implementation evidence.',
        '', '## Reproduction','',
        'Run `RUN_SIMULATION.command` for a new native nominal case, or add `--matrix` for the final 16-case matrix. Each run produces a new HTML report and preserves older outputs. See README.md for dependencies, runtime and exact commands.']
    (ROOT/'IMPLEMENTATION_AND_VALIDATION.md').write_text('\n'.join(md)+'\n')
    # Static HTML QA uses no network or browser automation.
    from html.parser import HTMLParser
    class Links(HTMLParser):
        def __init__(self):super().__init__();self.links=[];self.external=[]
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs)
            if tag=='a' and 'href' in attrs:self.links.append(attrs['href'])
            if tag in ['script','img','iframe','link']:
                for key in ['src','href']:
                    if attrs.get(key,'').startswith(('http:','https:','//')):self.external.append(attrs[key])
    qa=[]
    for path in [ROOT/'decision_report.html',*[ROOT/r['report'] for r in rows]]:
        parser=Links();parser.feed(path.read_text());assert not parser.external
        for link in parser.links:
            if not link.startswith('#'):assert (path.parent/link).exists(),(path,link)
        qa.append(dict(file=str(path.relative_to(ROOT)),links_verified=len(parser.links),external_resources=0))
    write(ROOT/'evidence/HTML_QA.json',dict(status='STATIC_CHECKS_PASSED',reports=qa,
        visual_browser_QA='NOT PERFORMED: browser tool security policy blocks file:// URLs; no browser workaround attempted'))
    files=[p for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name not in ['PACKAGE_SHA256.json','ARCHIVE_VERIFICATION.json']]
    manifest={str(p.relative_to(ROOT)):sha(p) for p in files}
    write(ROOT/'PACKAGE_SHA256.json',manifest)
    print('Final report:',ROOT/'decision_report.html')
    print('Payload files:',len(manifest))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('batch');a=p.parse_args();main(a.batch)
