"""Publish actual native decisions and equal-input baseline calculations offline."""
from pathlib import Path
import html,json,sys
from common import ROOT,write,sha

def publish(batch):
    batch=Path(batch).resolve();manifest=json.loads((batch/'manifest.json').read_text())
    if manifest['status']!='COMPLETED':
        (batch/'index.html').write_text('<!doctype html><h1>INCOMPLETE — no completed batch result</h1>')
        raise ValueError('Cannot publish an incomplete batch as completed')
    rows=manifest['results'];comparison=[];decisions=[]
    for row in rows:
        case=batch/row.get('case_directory',row['case']);attempts=sorted(case.glob('attempt_*/decision.json'))
        for path in attempts:
            data=json.loads(path.read_text());truth=data['offline_evaluation']['Rapp_expected_ohm']
            for method,value in data.get('baselines',{}).items():
                comparison.append(dict(case=row['case'],attempt=path.parent.name,method=method,
                    estimate_ohm=value,relative_error=None if value is None else value/truth-1,
                    released=value is not None and value>0,qualification='unconditional numerical baseline, no quality gates'))
            value=data['controller_result']['measurements'].get('Rapp_ohm')
            comparison.append(dict(case=row['case'],attempt=path.parent.name,method='P3_gated_last_quarter_OLS',
                estimate_ohm=value,relative_error=None if value is None else value/truth-1,
                released=data['decision']=='ACCEPT',qualification='unchanged P3 policy'))
            decisions.append(data)
    metrics={}
    for method in sorted({x['method'] for x in comparison}):
        subset=[x for x in comparison if x['method']==method and x['relative_error'] is not None]
        released=[x for x in subset if x['released']];bad=[x for x in subset if abs(x['relative_error'])>.05]
        good=[x for x in subset if abs(x['relative_error'])<=.05]
        metrics[method]=dict(evaluable_attempts=len(subset),released=len(released),coverage=len(released)/len(subset) if subset else None,
            released_with_error_over_5_percent=sum(abs(x['relative_error'])>.05 for x in released),
            numerical_false_acceptance_denominator=len(bad),numerical_false_acceptance_numerator=sum(x['released'] for x in bad),
            withheld_with_error_at_most_5_percent=sum(not x['released'] for x in good),numerically_good_denominator=len(good),
            definition='Numerical error criterion only, not ground truth for calibration/recovery/timeouts. A small slope error can coexist with a real quality fault.')
    write(batch/'BASELINE_COMPARISON.json',dict(scope='small deterministic pilot, not a statistical superiority claim',
        input='identical native A1 observations per attempt; all truth joined after the decision',
        numerical_error_criterion=.05,metrics=metrics,rows=comparison))
    counts={key:sum(x['decision']==key for x in rows) for key in ['ACCEPT','REJECT','WAIT_OR_REPEAT']}
    native=[p for row in rows for p in (batch/row.get('case_directory',row['case'])).rglob('NATIVE_EXECUTION.json')]
    native_records=[json.loads(p.read_text()) for p in native]
    assert all(x['fresh_pair'] and x['returncode']==0 for x in native_records)
    esc=lambda x:html.escape(str(x))
    cell=lambda x:'WITHHELD' if x is None else f'{x:,.3f}' if isinstance(x,(float,int)) else esc(x)
    table=''.join('<tr>'+''.join('<td>'+x+'</td>' for x in [f'<a href="{esc(r["report"])}">{esc(r["case"])}</a>',esc(r['decision']),str(r['actual_retries']),cell(r['Rapp_ohm']),cell(r['Rp_ohm']),cell(r['Rp_standard_uncertainty_ohm']),esc(', '.join(r['reason_codes']) or 'All implemented checks passed')])+'</tr>' for r in rows)
    baseline=''.join('<tr>'+''.join('<td>'+esc(x)+'</td>' for x in [name,m['evaluable_attempts'],m['released'],m['released_with_error_over_5_percent'],m['withheld_with_error_at_most_5_percent']])+'</tr>' for name,m in metrics.items())
    summary=dict(batch=str(batch.relative_to(ROOT)),counts=counts,native_executions=len(native),
        native_wall_time_s=sum((r['finished_ns']-r['started_ns'])/1e9 for r in native_records),
        actual_reacquisitions=sum(r['actual_retries'] for r in rows),
        P3_changes=False,scope='declared planar simulation integration fixture',
        retained_3D_specimen_qualified=False,physical_release=False,baseline_metrics=metrics)
    write(batch/'VALIDATION_SUMMARY.json',summary)
    doc=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LPR simulation integration results</title><style>
    body{{font:16px/1.5 system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 20px;color:#173145}}h1{{font-size:32px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid #ccd7df;padding:9px;text-align:left;overflow-wrap:anywhere}}th{{background:#edf4f8}}.note{{background:#fff5db;padding:16px;border-left:5px solid #a8781d}}a{{color:#095b92}}.scroll{{overflow:auto}}svg{{width:100%;max-height:180px}}@media print{{body{{font-size:11px}}tr{{break-inside:avoid}}a{{color:inherit}}}}</style></head><body>
    <h1>Native simulation integration — actual results</h1><p><strong>{counts['ACCEPT']} ACCEPT · {counts['REJECT']} REJECT · {counts['WAIT_OR_REPEAT']} WAIT_OR_REPEAT</strong></p>
    <p>{len(rows)} cases · {len(native)} fresh LTspice executions · {summary['actual_reacquisitions']} executed reacquisitions · unchanged P3 source.</p>
    <div class="note"><strong>Scope:</strong> integration is demonstrated for the explicitly declared planar FEM fixture. The retained specimen-shaped 3D model still fails its qualification gates and is excluded. Calibration and uncertainty are simulation evidence and declared engineering assumptions; no physical certification or corrosion-rate release is claimed.</div>
    <svg viewBox="0 0 1100 140" role="img" aria-label="Fresh FEM to native LTspice to unchanged P3 to decision; repeat feedback">
    <defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0L8 4L0 8z" fill="#235a77"/></marker></defs>
    <g fill="#e5f1f7" stroke="#235a77"><rect x="5" y="15" width="230" height="55" rx="9"/><rect x="290" y="15" width="230" height="55" rx="9"/><rect x="575" y="15" width="230" height="55" rx="9"/><rect x="860" y="15" width="230" height="55" rx="9"/></g>
    <g font-size="16" text-anchor="middle" fill="#173145"><text x="120" y="47">Qualified planar FEM Ru</text><text x="405" y="47">Fresh native LTspice</text><text x="690" y="47">Unchanged P3 checks</text><text x="975" y="47">Decision + eligible Rp</text></g>
    <g stroke="#235a77" stroke-width="2" fill="none" marker-end="url(#arrow)"><path d="M235 42H285"/><path d="M520 42H570"/><path d="M805 42H855"/><path d="M690 70V113H405V76"/></g><text x="550" y="136" text-anchor="middle" font-size="13">Repeat only after fresh independent reference verification; maximum one</text></svg>
    <h2>Results</h2><div class="scroll"><table><tr><th>Case / detailed report</th><th>Decision</th><th>Retries</th><th>Rapp (Ω)</th><th>Rp (Ω)</th><th>u(Rp), simulation (Ω)</th><th>Reason</th></tr>{table}</table></div>
    <h2>Equal-input baseline pilot</h2><p>All methods use the same native A1 observations. Numerical error uses offline circuit truth only after diagnostics. The 5% criterion is the project's initial evaluation criterion. These counts are not estimates of population performance.</p>
    <div class="scroll"><table><tr><th>Method</th><th>Evaluable attempts</th><th>Released</th><th>Released error &gt;5%</th><th>Withheld error ≤5%</th></tr>{baseline}</table></div>
    <p>A low numerical slope error does not prove calibration, reference integrity, timely acquisition or recovery. Accordingly, “withheld error ≤5%” is not automatically an incorrect rejection.</p>
    <h2>What was added</h2><ul><li>Fresh Elmer conductivity and mesh verification with a hash-bound simulation Ru interface.</li><li>Independent switched-standard training, holdout and positive reference measurements at both ends.</li><li>Existing P3 correction API supplied only with qualified simulation Ru and declared uncertainty.</li><li>Decision-driven native reference verification and 45-second reacquisition, with exact prefix equivalence checks.</li><li>Native fault matrix and three ungated baseline estimators evaluated on identical data.</li></ul>
    <h2>Limits that remain visible</h2><p>The 3D specimen's flux/mesh qualification is unresolved. The simulated reference uses an ideal nonloading point. Geometry and conductivity uncertainty are assumed distributions, not measured tolerances. P3's settling and heuristic uncertainty limitations remain. This pilot uses one noise seed per case. No physical instrument, physical calibration or material corrosion-rate validation occurred.</p>
    <p><a href="manifest.json">Case manifest</a> · <a href="BASELINE_COMPARISON.json">Exact comparison calculations</a> · <a href="VALIDATION_SUMMARY.json">Validation summary</a></p></body></html>'''
    (batch/'index.html').write_text(doc)
    return summary

if __name__=='__main__':
    summary=publish(Path(sys.argv[1]));print(json.dumps(summary,indent=2))
