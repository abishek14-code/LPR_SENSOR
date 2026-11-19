"""Offline HTML presentation of actual P3 results; never a decision policy."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import html
import io
import json
import math
import os
import re
import tempfile

REASON_TEXT = {
 'MISSING_OPENING_CHECK': 'No qualifying opening reference check was supplied. An OCP window is not an independent reference check.',
 'MISSING_CLOSING_CHECK': 'No qualifying closing reference check was supplied. An OCP window is not an independent reference check.',
 'MISSING_OCP_EVIDENCE': 'The required drive-disconnected opening and closing OCP observations were not established.',
 'NOT_SETTLED': 'At least one point did not meet the implemented stationarity criteria. This does not identify a physical fault cause.',
 'RETRY_LIMIT_EXHAUSTED': 'The configured bounded reacquisition budget was exhausted while required criteria remained unresolved.',
 'TIMEOUT': 'An observation exceeded the configured point timeout. This acquisition must stop.',
 'RESET': 'The run was interrupted by reset. This incomplete acquisition cannot release a result or automatically resume.',
 'ACQUISITION_CHAIN_SUSPECT': 'The supplied reference response did not meet the configured acquisition-chain comparison.',
 'REFERENCE_REPLAY_FAILURE': 'The executed software reference replay failed its checks.',
 'REPEATABILITY_FAILURE': 'The A1 and A2 apparent resistances exceeded the configured consistency tolerance.',
 'SLOPE_INCONSISTENCY': 'The available sweep slopes failed an implemented consistency comparison.',
 'EXCESSIVE_UNCERTAINTY': 'An implemented signal-to-uncertainty check failed. The default uncertainty remains an engineering heuristic.',
 'INVALID_INPUT': 'The supplied input could not be read or did not satisfy the supported interface contract.',
 'INCOMPATIBLE_SAMPLING': 'Recorded point sampling is incompatible with P3. Samples were not silently resampled.',
 'OPENING_CHECK_FAILURE': 'The supplied opening check failed; the controller did not authorize measurement release.',
 'CLOSING_CHECK_FAILURE': 'The supplied closing check failed.',
 'LOW_SNR': 'The measured signal-to-noise ratio was below its configured rule.',
 'EXCESSIVE_NOISE': 'Measured within-tail noise exceeded the configured signal-relative rule.',
 'OCP_DRIFT': 'The observed OCP drift exceeded the configured rule.',
 'OCP_RECOVERY_FAILURE': 'The observed opening-to-closing OCP shift exceeded the configured rule.',
 'INVALID_FIT': 'An implemented resistance-fit criterion was not satisfied.',
 'MISSING_DATA': 'One or more required measurement groups or point observations are missing.',
 'INVALID_OPERATIONAL_CONTEXT': 'Observed acquisition context does not match the declared point sequence or timing contract.',
}

CSS = '''
:root{color-scheme:light;--ink:#182c3a;--muted:#536773;--line:#d5e0e6;--navy:#173d51;--paper:#fff;--bg:#edf2f5;--red:#a52c32;--green:#17694b;--amber:#865600}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}main{max-width:1190px;margin:30px auto;background:var(--paper);padding:38px 44px;border:1px solid var(--line)}h1{font-size:30px;line-height:1.2;margin:6px 0 12px}h2{font-size:21px;margin:30px 0 12px}h3{font-size:16px;margin:20px 0 8px}p{margin:8px 0 13px}.eyebrow{font-size:12px;font-weight:700;letter-spacing:.11em;text-transform:uppercase;color:var(--muted)}.decision{font-size:43px;line-height:1.15;margin:12px 0 8px;font-weight:750}.reject{color:var(--red)}.accept{color:var(--green)}.wait_or_repeat,.running,.execution_failed{color:var(--amber)}.meaning{max-width:900px;font-size:18px}.scope{color:var(--muted);font-size:13px}.meta{display:grid;grid-template-columns:1fr 1fr;gap:8px 28px;border-block:1px solid var(--line);padding:15px 0;margin:20px 0}.meta strong{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.03em}.mono,code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px;overflow-wrap:anywhere}.metric-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:22px;margin:18px 0}.metric{border-top:3px solid var(--navy);padding-top:10px}.metric-label{font-size:12px;text-transform:uppercase;font-weight:700}.metric-value{font-size:24px;line-height:1.3;font-weight:650}.metric p{font-size:13px;color:var(--muted)}table{border-collapse:collapse;width:100%;font-size:12px;margin:10px 0 16px}th,td{border:1px solid var(--line);padding:8px 10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#e7eef2;color:#163b4d;font-weight:700}tbody tr:nth-child(even){background:#f7f9fa}.diagnostics th:first-child{width:22%}.diagnostics th:nth-child(2){width:23%}.diagnostics th:nth-child(3){width:26%}.diagnostics th:nth-child(4){width:10%}.pass{color:var(--green);font-weight:700}.fail,.missing{color:var(--red);font-weight:700}.unknown{color:var(--amber);font-weight:700}.tools{display:flex;gap:9px;flex-wrap:wrap;align-items:center;margin:12px 0}button,input,select{font:inherit;font-size:13px;padding:8px 11px;border:1px solid #a7b9c4;border-radius:5px;background:white;color:var(--ink)}button{cursor:pointer}button:hover{background:#eaf1f5}.svg-wrap{width:100%;overflow:auto}.svg-wrap svg{width:100%;height:auto;display:block}.scroll{overflow-x:auto}.note{font-size:13px;color:var(--muted)}.reason-list{padding-left:22px}.reason-list li{padding:4px 0}a{color:#145f82;text-underline-offset:2px}footer{margin-top:28px;border-top:1px solid var(--line);padding-top:14px;color:var(--muted);font-size:12px}details{margin:12px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}figure{margin:15px 0 22px}figcaption{font-size:12px;color:var(--muted);margin:6px 0}.anchor{scroll-margin-top:20px}.error-message{white-space:pre-wrap}.pill{display:inline-block;font-size:12px;font-weight:650;padding:3px 9px;background:#edf2f5;border:1px solid var(--line);border-radius:3px}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:28px}@media(max-width:800px){main{margin:0;padding:22px 16px;border:0}.metric-grid,.meta,.two-col{grid-template-columns:1fr}h1{font-size:25px}.decision{font-size:36px}table{font-size:11px}th,td{padding:6px}}
@media screen and (max-width:800px){.scroll table{min-width:850px}}
@page{size:A4;margin:14mm}@media print{body{background:white;font-size:10pt}main{margin:0;padding:0;border:0;max-width:none}h1{font-size:22pt}h2{font-size:15pt;break-after:avoid}.decision{font-size:30pt}.tools,.no-print{display:none!important}table{font-size:8pt}thead{display:table-header-group}tr,figure,.metric{break-inside:avoid}tr[hidden]{display:table-row!important}.meta{font-size:8.5pt}.metric-value{font-size:17pt}.svg-wrap{overflow:visible}details{display:none!important}a{color:inherit}footer{font-size:8pt}.print-notice{display:block}.metric-grid{gap:12px}}
'''


def esc(value):
    return html.escape(str(value), quote=True)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def fmt(value):
    if value is None:
        return 'Not supplied / unavailable'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return format(value, '.7g') if math.isfinite(value) else 'Unavailable (nonfinite internal metric)'
    if isinstance(value, dict):
        return '; '.join(f'{k}: {fmt(v)}' for k, v in value.items()) or 'None'
    if isinstance(value, (list, tuple)):
        return ', '.join(fmt(x) for x in value) or 'None'
    return str(value)


def atomic_write(path, content):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def document(title, body, data=None, script=''):
    payload = ''
    if data is not None:
        encoded = json.dumps(data, allow_nan=False, separators=(',', ':')).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
        payload = '<script type="application/json" id="run-data">' + encoded + '</script>'
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Cache-Control" content="no-store"><title>' + esc(title) + '</title><style>' + CSS + '</style></head><body><main>' + body + '</main>' + payload + ('<script>' + script + '</script>' if script else '') + '</body></html>'


def status_page(run, status, message):
    label = {'RUNNING': 'DECISION PENDING', 'EXECUTION_FAILED': 'NO MEASUREMENT DECISION'}.get(status, status)
    body = '<div class="eyebrow">LPR decision report · current execution</div><h1>Measurement validation</h1><div id="decision" class="decision ' + status.lower() + '">' + esc(label) + '</div>'
    body += '<p class="meaning">' + esc(message) + '</p><p><strong>No measurement result is authorized.</strong> Previous run decisions and quantities are not reused.</p>'
    body += '<div class="meta"><div><strong>Run ID</strong><span class="mono">' + esc(run['run_id']) + '</span></div><div><strong>Started UTC</strong>' + esc(run['started_at_utc']) + '</div></div>'
    body += '<p>No controller check is shown as passed because no completed measurement decision is available for this execution.</p>'
    body += '<footer>Hardware commands: none. This file represents the identified invocation only. If execution is interrupted, pending is not acceptance.</footer>'
    script = 'setTimeout(()=>location.reload(),2500);' if status == 'RUNNING' else ''
    return document('LPR ' + label, body, {**run, 'execution_status': status, 'decision': None, 'error': message if status != 'RUNNING' else None}, script)


def make_trace_payload(frames, record, diagnostics):
    """Read already acquired samples; reconstruct exact slice indices for display.

    Tail/settling slices follow lpr_analysis.analyze_block/estimate_settling.
    Plotting and slice bookkeeping never call the decision engine or plant.
    """
    import numpy as np
    observations = []
    windows = []
    block_summary = []
    history = record.attempt_history
    for frame in frames:
        if frame is None or frame.empty:
            continue
        times = frame.time_s.to_numpy(dtype=float)
        attempt_index = 0
        if history:
            for index, attempt in enumerate(history):
                bounds = attempt.get('windows', [])
                if bounds and bounds[0]['start_time_s'] - 1e-8 <= times[0] <= bounds[-1]['end_time_s'] + 1e-8:
                    attempt_index = index
                    break
        frame_indices = []
        for row in frame.to_dict('records'):
            voltage = row.get('actual_voltage_v', row.get('ocp_voltage_v'))
            current = row.get('current_a')
            point = row.get('point_index')
            observations.append({'time_s': float(row['time_s']), 'voltage_v': float(voltage) if finite(voltage) else None,
                                 'current_a': float(current) if finite(current) else None,
                                 'state': str(row.get('state', row.get('block_id', 'UNLABELLED'))),
                                 'block': str(row.get('block_id', row.get('state', 'UNLABELLED'))),
                                 'point_index': int(point) if finite(point) else None,
                                 'attempt_index': attempt_index,
                                 'drive_enabled': bool(row['drive_enabled']) if 'drive_enabled' in row else None})
            frame_indices.append(len(observations) - 1)
        block = str(frame.get('block_id', frame.get('state')).iloc[0])
        if block not in {'A1', 'B', 'A2', 'A1_REVERSE', 'B_REVERSE', 'A2_REVERSE'} or 'point_index' not in frame:
            continue
        # Only display an analysis selection if this attempt actually ran that block analysis.
        if history:
            diag = (history[attempt_index].get('diagnostics') or {})
            analyzed = block.removesuffix('_REVERSE') in (diag.get('measured_values', {}).get('reverse_blocks', {}) if block.endswith('_REVERSE') else diag.get('blocks', {}))
        else:
            analyzed = record.diagnostics is not None and (block.removesuffix('_REVERSE') in (record.diagnostics.reverse_blocks or {}) if block.endswith('_REVERSE') else block in record.diagnostics.blocks)
        if not analyzed:
            continue
        for point_index, point in frame.groupby('point_index', sort=True):
            position = np.flatnonzero(frame.point_index.to_numpy() == point_index)
            indices = [frame_indices[int(p)] for p in position]
            n = len(point)
            if n < diagnostics.min_samples_per_point:
                continue
            tail_n = max(2, n // 4)
            t = point.time_s.to_numpy(dtype=float)
            dt = float(np.median(np.diff(t))) if n >= 2 else None
            settling = None
            dwell = float(t[-1] - t[0] + dt) if dt is not None else None
            # estimate_settling returns before selecting windows on these conditions.
            if dt and n >= 4 and dwell <= record.decision_result.thresholds['point_timeout_s'] + 1e-8 and dwell + 1e-9 >= diagnostics.settling_observation_s:
                window_n = max(2, min(n, int(round(diagnostics.settling_window_s / dt))))
                if n >= 2 * window_n:
                    settling = {'previous': indices[-2 * window_n:-window_n], 'final': indices[-window_n:]}
            windows.append({'id': f'window-{attempt_index}-{block}-{int(point_index)}', 'attempt_index': attempt_index,
                            'block': block, 'point_index': int(point_index), 'observations': indices,
                            'start_s': float(t[0]), 'end_s': float(t[-1]), 'fit_tail': indices[-tail_n:],
                            'fit_samples': tail_n, 'settling': settling})
    if record.diagnostics is not None:
        for name, block in {**record.diagnostics.blocks, **{k + '_REVERSE': v for k, v in (record.diagnostics.reverse_blocks or {}).items()}}.items():
            block_summary.append({'block': name, 'status': block.status, 'resistance_ohm': block.resistance.value_ohm,
                                  'r_squared': block.r_squared, 'noise_std_a': block.noise_std_a,
                                  'snr_db': block.snr_db, 'reason_codes': list(block.reason_codes)})
    return {'observations': observations, 'analysis_windows': windows, 'block_summary': block_summary,
            'selection_method': 'Exact last max(2,n//4) point samples for fit; previous/final settling windows use the controller rounded window_s/median_dt slice. No smoothing or interpolation.',
            'missing_current_note': 'Missing current observations, including synthetic OCP intervals without a recorded current channel, remain gaps; zero is not fabricated.'}


def svg_plot(payload, detail=False):
    cache = Path(tempfile.gettempdir()) / "lpr-report-plot-cache"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(cache))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    rows = payload.get('observations', [])
    windows = payload.get('analysis_windows', [])
    if not rows:
        return '<p>No acquired voltage/current samples are available for this run.</p>'
    selected = None
    if detail:
        candidates = [w for w in windows if w['block'] == 'A1' and w['point_index'] == 0]
        if not candidates:
            return '<p>No analyzed A1 point window is available; no replacement window has been invented.</p>'
        selected = candidates[-1]
        rows = [payload['observations'][i] for i in selected['observations']]
    with plt.rc_context({'font.family': 'DejaVu Sans', 'font.size': 10.5, 'path.simplify': False, 'svg.fonttype': 'none'}):
        fig, axes = plt.subplots(2, 1, figsize=(10, 4.4), sharex=True, layout='constrained')
        time = np.asarray([r['time_s'] for r in rows])
        for ax, key, scale, color, label in zip(axes, ['voltage_v', 'current_a'], [1e3, 1e6], ['#1c5774', '#973d24'], ['Measured voltage (mV)', 'Measured current (µA)']):
            values = np.asarray([r[key] if r[key] is not None else np.nan for r in rows]) * scale
            ax.plot(time, values, color=color, linewidth=.85, marker='.' if detail else None, markersize=2)
            ax.set_ylabel(label)
            ax.grid(alpha=.2)
            ax.spines[['top', 'right']].set_visible(False)
            if selected:
                settling = selected.get('settling')
                if settling:
                    for indices, color2, label2 in [(settling['previous'], '#e5c174', 'Previous settling window'), (settling['final'], '#a9b9d7', 'Final settling window')]:
                        obs = payload['observations']
                        ax.axvspan(obs[indices[0]]['time_s'], obs[indices[-1]]['time_s'], color=color2, alpha=.38, label=label2)
                indices = selected['fit_tail']
                obs = payload['observations']
                ax.axvspan(obs[indices[0]]['time_s'], obs[indices[-1]]['time_s'], color='#6eb69a', alpha=.35, label='Fit tail (last 25%)')
            else:
                for window in windows:
                    indices = window['fit_tail'];obs=payload['observations']
                    ax.axvspan(obs[indices[0]]['time_s'], obs[indices[-1]]['time_s'], color='#64ad91', alpha=.18)
        axes[-1].set_xlabel('Native / synthetic acquisition time (s)')
        if selected:
            axes[0].legend(loc='best', fontsize=9.5)
            axes[0].set_title(f"Actual analyzed window · attempt {selected['attempt_index'] + 1} · {selected['block']} point {selected['point_index']}", loc='left', fontsize=11)
        else:
            axes[0].set_title('Acquired observations · green shading marks actual point-fit tails', loc='left', fontsize=11)
        buffer = io.StringIO()
        fig.savefig(buffer, format='svg', metadata={'Date': None})
        plt.close(fig)
    raw = buffer.getvalue()
    return raw[raw.index('<svg'):]


def source_label(report):
    mode = report.get('execution_mode', '')
    if mode.startswith('NATIVE'):
        return 'Native LTspice offline replay · no new simulation or live instrument'
    if mode.startswith('SYNTHETIC'):
        return 'Synthetic controller execution · software plant, not physical measurements'
    return 'Source not established · this runner has no recorded-hardware acquisition mode'


def reason_html(result):
    codes = result.get('reason_codes', [])
    if not codes:
        return '<p>All mandatory implemented checks passed for this execution. Unimplemented or unavailable physical checks remain separate below.</p>'
    return '<ul class="reason-list">' + ''.join('<li><strong>' + esc(code) + '</strong> — ' + esc(REASON_TEXT.get(code, 'See the exact implemented check and its observation in the diagnostic table below.')) + '</li>' for code in codes) + '</ul>'


def check_rows(report):
    rows = []
    checks = report['controller_result'].get('checks', [])
    windows = report.get('trace_payload', {}).get('analysis_windows', [])
    final_attempt = max((w['attempt_index'] for w in windows), default=0)
    for index, check in enumerate(checks):
        code = check.get('reason_code')
        passed = check.get('passed') is True
        outcome = 'PASS' if passed else 'MISSING' if code and code.startswith('MISSING_') else 'FAIL'
        name = check.get('name', 'Unnamed implemented check')
        match = re.match(r'^(A[12](?:_REVERSE)?|B(?:_REVERSE)?)\.point_(\d+)', name)
        link = None
        if match:
            wanted = f'window-{final_attempt}-{match[1]}-{int(match[2])}'
            if any(w['id'] == wanted for w in windows):
                link = wanted
        name_html = '<a href="#' + link + '">' + esc(name) + '</a>' if link else esc(name)
        reason = (code + ': ' if code else '') + check.get('explanation', '')
        rows.append('<tr data-outcome="' + outcome.lower() + '" id="check-' + str(index) + '"><td class="mono">' + name_html + '</td><td>' + esc(fmt(check.get('observed'))) + '</td><td>' + esc(fmt(check.get('expected'))) + '</td><td class="' + outcome.lower() + '">' + outcome + '</td><td>' + esc(reason) + '</td></tr>')
    return ''.join(rows)


def evidence_table(report):
    native = report.get('execution_mode', '').startswith('NATIVE')
    prov = report.get('provenance', {})
    offline = report.get('offline_comparisons', {})
    p2 = offline.get('spatial_FEM', {})
    p4 = offline.get('analytical_reference', {})
    electrical = (str(prov.get('raw_path', 'Unavailable')) + '; RAW SHA-256 ' + str(prov.get('raw_sha256', 'Unavailable')) + f"; {prov.get('native_rows', 'unknown')} native rows / {prov.get('observations', 'unknown')} clock observations. LOG: " + str(prov.get('log', {}).get('version', 'unavailable'))) if native else 'No native P1 RAW was used. Voltage/current came from the existing P3 synthetic plant.'
    items = [
      ('P1 electrical', electrical, 'Measured helper channels and acquisition context feed P3. Simulator-only truth channels are excluded.' if native else 'Synthetic electrical model only; no physical instrument was controlled.'),
      ('P2 spatial FEM', p2.get('status', 'Spatial summary unavailable for this run'), 'Retained qualification summary only; no field solve in this execution and no P2 value supplied to P3. A full two-electrode path resistance is not verified three-electrode WE–RE Ru.'),
      ('P3 controller', str(sum(name.startswith('python/') for name in report.get('source_hashes', {}))) + ' supplied Python modules checked against source hashes; canonical decision and lifecycle executed.', 'Actual implemented checks, thresholds and bounded retry policy; unchanged controller code.'),
      ('P4 analytical', 'Supplied models/analytical_checks.py executed after the decision; offline check status: ' + p4.get('status', 'UNAVAILABLE'), 'Reference equations only; not independent calibration or specimen truth. Original member authorship is not established by file location.')]
    return '<table><thead><tr><th>Workstream</th><th>Evidence actually used or read</th><th>Role and limitation</th></tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(x) + '</td>' for x in row) + '</tr>' for row in items) + '</tbody></table>'


def render_report(report):
    result = report['controller_result']
    decision = result['decision']
    if decision != report['decision'] or decision not in {'ACCEPT', 'REJECT', 'WAIT_OR_REPEAT'}:
        raise ValueError('Report/controller decision mismatch')
    interrupted = report.get('legacy_terminal_decision') == 'INCOMPLETE' or result.get('result_status') == 'INTERRUPTED'
    meaning = {'ACCEPT': 'The measurement passed the implemented validation checks. ACCEPT does not mean that the material is corrosion-free or safe.',
               'REJECT': 'The controller did not authorize this measurement result. Resolve the failed or missing evidence before starting a new measurement.',
               'WAIT_OR_REPEAT': 'The controller has not accepted this measurement. Follow the stated bounded observation or repeat action; a recommendation is not an executed retry.'}[decision]
    if interrupted:
        meaning = 'This acquisition was interrupted and is INCOMPLETE. No measurement result is released. Start a fresh run explicitly; this execution cannot resume automatically.'
    mode = source_label(report)
    prov = report.get('provenance', {})
    source = prov.get('raw_path') or ('Synthetic scenario: ' + str(prov.get('scenario', 'not established')) if report.get('execution_mode', '').startswith('SYNTHETIC') else 'Input unavailable')
    timestamp = report.get('evaluated_at_utc', 'Not established')
    body = '<div class="eyebrow">LPR measurement decision · actual execution</div><h1>Decision and supporting evidence</h1><span class="pill">' + esc(mode) + '</span>'
    body += '<div class="decision ' + decision.lower() + '" id="decision">' + decision + '</div><p class="meaning">' + esc(meaning) + '</p>'
    if interrupted:
        body += '<p><strong>Execution status: INCOMPLETE / INTERRUPTED.</strong> The canonical result above is WAIT_OR_REPEAT, not ACCEPT.</p>'
    if report.get('input_error'):
        body += '<p class="error-message"><strong>Input rejected:</strong> ' + esc(report['input_error']) + '</p>'
    body += '<div class="tools"><button onclick="window.print()">Print or save as PDF</button><button id="download-json">Download exact run data</button><a href="#diagnostics">Diagnostic checks</a><a href="#observations">Traces and windows</a><a href="#evidence">Evidence scope</a></div>'
    body += '<div class="meta"><div><strong>Run timestamp UTC</strong>' + esc(timestamp) + '<br><span class="note">Evaluation time, not the original acquisition date.</span></div><div><strong>Run ID</strong><span class="mono">' + esc(report.get('run_id')) + '</span></div><div><strong>Input source</strong><span class="mono">' + esc(source) + '</span></div><div><strong>Execution mode</strong>' + esc(report.get('execution_mode')) + '<br>Hardware commands sent: <strong>NONE</strong></div></div>'
    body += '<h2>Why the controller reached this decision</h2>' + reason_html(result)
    body += '<h3>Recommended next action</h3><p>' + esc(result['recommended_action']) + '</p>'
    if 'MISSING_OPENING_CHECK' in result.get('reason_codes', []) or 'MISSING_CLOSING_CHECK' in result.get('reason_codes', []):
        body += '<p class="note">Provide genuine independent reference-check evidence through an implemented, supported acquisition interface. Do not relabel OCP windows or calibration phases as passing checks. This RAW runner does not authenticate external reference-check sidecars.</p>'
    retry = result.get('retry_plan', {})
    body += '<p><strong>Retries actually executed:</strong> ' + esc(report.get('reacquisition_count', 0)) + ' / ' + esc(retry.get('max_retries', 'not established')) + '. <strong>Repeat currently permitted by P3:</strong> ' + esc(fmt(retry.get('repeat_permitted'))) + '. <strong>Public automatic retry recommendation:</strong> ' + esc(fmt(retry.get('automatic_retry'))) + '.</p>'
    attempts = report.get('attempt_decisions', [])
    if attempts:
        body += '<table><thead><tr><th>Executed attempt</th><th>Actual decision</th><th>Reasons</th></tr></thead><tbody>' + ''.join('<tr><td class="mono">' + esc(a['attempt_id']) + '</td><td>' + esc(a.get('decision')) + '</td><td>' + esc(', '.join(a.get('reason_codes', [])) or 'None') + '</td></tr>' for a in attempts) + '</tbody></table>'
    body += '<p class="note">Synthetic retry was executed only when shown in the attempt history. Native offline replay cannot command a new acquisition. Legacy terminal result: ' + esc(report.get('legacy_terminal_decision', 'Not available')) + '.</p>'
    measurements = result.get('measurements', {})
    rapp = measurements.get('Rapp_ohm')
    rp = result.get('rp_estimate')
    corrosion = result.get('corrosion_estimate')
    rp_eligible = decision == 'ACCEPT' and not interrupted and finite(rp) and rp > 0 and finite(rapp) and finite(measurements.get('Ru_ohm')) and 0 < measurements['Ru_ohm'] < rapp and result.get('estimate_status', {}).get('rp') == 'AVAILABLE_UNDER_DECLARED_SIMPLIFIED_MODEL'
    body += '<h2>Calculated quantities and eligibility</h2><div class="metric-grid">'
    quantities = [('Apparent resistance Rapp', fmt(rapp) + ' Ω' if finite(rapp) else 'Unavailable', 'Diagnostic fit of voltage against current. ' + ('Accepted under software policy only.' if decision == 'ACCEPT' else 'Not an authorized released measurement.')),
                  ('Corrected polarization resistance Rp', fmt(rp) + ' Ω' if rp_eligible else 'Withheld', result.get('estimate_status', {}).get('rp', 'Required inputs and model assumptions not established.')),
                  ('Corrosion-rate estimate', 'Unavailable', result.get('estimate_status', {}).get('corrosion', 'No supported calibrated corrosion conversion.'))]
    # The supplied P3 always reports corrosion_estimate=None and defines no physical rate unit.
    # Never attach an invented unit or call a raw number a corrosion rate.
    for label, value, note in quantities:
        body += '<div class="metric"><div class="metric-label">' + esc(label) + '</div><div class="metric-value">' + esc(value) + '</div><p>' + esc(note) + '</p></div>'
    body += '</div><p><strong>Independent online Ru:</strong> ' + (esc(fmt(measurements.get('Ru_ohm'))) + ' Ω' if finite(measurements.get('Ru_ohm')) else 'Not supplied / specimen not qualified') + '. Rapp, Rp and area-normalized resistance are different quantities; no area normalization is invented.</p>'
    uncertainty = result.get('uncertainty', {}).get('measurement', {})
    body += '<p><strong>Reported uncertainty status:</strong> ' + esc(uncertainty.get('status', 'UNAVAILABLE')) + '. '
    if finite(uncertainty.get('heuristic_ohm')):
        body += 'Implemented heuristic: ' + esc(fmt(uncertainty['heuristic_ohm'])) + ' Ω. '
    body += esc(uncertainty.get('method', 'No uncertainty value available.')) + ' No confidence coverage is claimed.</p>'
    blocks = report.get('trace_payload', {}).get('block_summary', [])
    if blocks:
        body += '<table><thead><tr><th>Analyzed block</th><th>Status</th><th>Apparent resistance (Ω)</th><th>R²</th><th>Noise std (A)</th><th>SNR (dB)</th></tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(fmt(row.get(key))) + '</td>' for key in ['block', 'status', 'resistance_ohm', 'r_squared', 'noise_std_a', 'snr_db']) + '</tr>' for row in blocks) + '</tbody></table>'
    checks = result.get('checks', [])
    pass_n = sum(c.get('passed') is True for c in checks)
    body += '<h2 id="diagnostics" class="anchor">Implemented diagnostic checks</h2><p>' + str(len(checks)) + ' checks reported by P3: ' + str(pass_n) + ' pass, ' + str(len(checks) - pass_n) + ' fail or missing. The table contains the actual observed values and configured rule dictionaries; display rounding does not change a rule. Exact values are embedded in the downloadable run data.</p>'
    body += '<div class="tools"><label>Show <select id="check-filter"><option value="attention">Failed or missing</option><option value="all">All implemented checks</option><option value="pass">Passing checks</option></select></label><input id="check-search" aria-label="Search diagnostic checks" placeholder="Search block, point or reason"><span id="visible-count" class="note"></span></div><div class="scroll"><table class="diagnostics"><thead><tr><th>Check</th><th>Observed value</th><th>Configured acceptance rule</th><th>Outcome</th><th>Reason</th></tr></thead><tbody id="check-rows">' + check_rows(report) + '</tbody></table></div>'
    body += '<h3>Missing evidence and checks not established</h3><p class="note">The following are evidence limitations, not invented passing controller checks.</p><table><thead><tr><th>Evidence or capability</th><th>Observed availability</th><th>Implemented rule or limitation</th><th>Outcome</th></tr></thead><tbody>'
    limits = [('Validated physical calibration', 'Not established', 'No physical calibration authentication gate in supplied P3; unavailable calibration is a warning.', 'NOT ESTABLISHED'),
              ('Voltage/current instrument limits', fmt(result.get('thresholds', {}).get('voltage_limit_v')) + ' V; ' + fmt(result.get('thresholds', {}).get('current_limit_a')) + ' A', 'No saturation test is established on a channel without its actual declared limit.', 'NOT PROVIDED'),
              ('Independent specimen WE–RE Ru', fmt(measurements.get('Ru_ohm')), 'No qualified P2 specimen Ru supplied for correction; two-electrode path resistance is not substituted.', 'NOT QUALIFIED'),
              ('Physical corrosion conversion', 'No supported rate or rate unit', 'Verified electrochemical/material/area calibration and conversion are absent.', 'NOT IMPLEMENTED'),
              ('Live instrument acquisition / native repeat', 'No hardware commands sent', 'This entry point supports native offline replay and explicitly synthetic controller execution, not recorded hardware acquisition.', 'NOT IMPLEMENTED')]
    body += ''.join('<tr>' + ''.join('<td>' + esc(x) + '</td>' for x in row) + '</tr>' for row in limits) + '</tbody></table>'
    body += '<h2 id="observations" class="anchor">Supporting voltage and current observations</h2>'
    payload = report.get('trace_payload', {})
    obs = payload.get('observations', [])
    body += '<p>' + str(len(obs)) + ' acquired observations available for display. Native mode displays the existing falling-clock samples, not an interpolated version of the adaptive RAW. Synthetic mode displays samples retained by the actual executed plant.</p>'
    try:
        body += '<figure><div class="svg-wrap">' + svg_plot(payload) + '</div><figcaption>Actual recorded sample values in this execution. ' + esc(payload.get('missing_current_note', '')) + '</figcaption></figure>'
        if payload.get('analysis_windows'):
            body += '<figure><div class="svg-wrap">' + svg_plot(payload, detail=True) + '</div><figcaption>Selected detail uses A1 point 0 from the last analyzed attempt. Shading follows exact sample-index slices used by the implemented estimator, not a visually chosen steady region.</figcaption></figure>'
    except Exception as exc:
        # A plot failure must be visible; it may not reuse another run's image.
        body += '<p><strong>Trace rendering unavailable for this execution:</strong> ' + esc(str(exc)) + '. No previous plot was reused. The embedded run data retain acquired observations.</p>'
        report.setdefault('presentation_warnings', []).append('Trace rendering failed: ' + str(exc))
    windows = payload.get('analysis_windows', [])
    if windows:
        body += '<p class="note">' + esc(payload.get('selection_method', '')) + '</p><div class="scroll"><table><thead><tr><th>Attempt / block / point</th><th>Acquired interval (s)</th><th>Fit-tail interval (s)</th><th>Fit samples</th><th>Previous / final settling intervals (s)</th></tr></thead><tbody>'
        def span(indices):
            return fmt(obs[indices[0]]['time_s']) + ' – ' + fmt(obs[indices[-1]]['time_s'])
        for window in windows:
            settling = window.get('settling')
            body += '<tr class="anchor" id="' + window['id'] + '"><td>' + esc(str(window['attempt_index'] + 1) + ' / ' + window['block'] + ' / ' + str(window['point_index'])) + '</td><td>' + esc(fmt(window['start_s']) + ' – ' + fmt(window['end_s'])) + '</td><td>' + esc(span(window['fit_tail'])) + '</td><td>' + str(window['fit_samples']) + '</td><td>' + esc((span(settling['previous']) + ' / ' + span(settling['final'])) if settling else 'Not selected by estimator before early return') + '</td></tr>'
        body += '</tbody></table></div>'
    elif obs:
        body += '<p>No completed block analysis selected a fit/settling window in this run. Partial acquisition samples are shown without inventing an analysis window.</p>'
    body += '<h2 id="evidence" class="anchor">Evidence used and integration limits</h2>' + evidence_table(report)
    body += '<p><strong>Simulator-only information:</strong> ' + ('RAW truth channels were excluded from diagnostics: ' + esc(', '.join(prov.get('ignored_channels', []))) if report.get('execution_mode', '').startswith('NATIVE') else 'Synthetic voltage/current and software reference probes are generated by the supplied model. Fault scenario config selects the test stimulus; fault labels and known sample parameters are not measurement-diagnostic inputs.') + ' Analytical truth and spatial summaries are offline context only, calculated or read after the controller decision.</p>'
    p4 = report.get('offline_comparisons', {}).get('analytical_reference', {})
    if p4.get('actual'):
        body += '<h3>Offline analytical reference only</h3><p>Rdc = ' + esc(fmt(p4['actual'].get('dc_resistance_ohm'))) + ' Ω; τ = ' + esc(fmt(p4['actual'].get('tau_s'))) + ' s; initial current = ' + esc(fmt(p4['actual'].get('initial_current_a'))) + ' A; steady current = ' + esc(fmt(p4['actual'].get('steady_current_a'))) + ' A. These are reference-case calculations, not specimen measurements or calibration evidence.</p>'
    if report.get('offline_comparisons', {}).get('status') == 'UNAVAILABLE':
        body += '<p>Offline comparison unavailable: ' + esc(report['offline_comparisons'].get('error')) + '. Controller decision preserved.</p>'
    body += '<p><strong>Meaning of ACCEPT:</strong> The measurement passed the implemented validation checks. It does not mean the material is corrosion-free or safe. A synthetic ACCEPT validates software behavior, not a tested physical material.</p>'
    body += '<h3>Controller warnings</h3><ul>' + ''.join('<li>' + esc(w) + '</li>' for w in result.get('warnings', [])) + '</ul>'
    body += '<details><summary>Exact controller decision and provenance</summary><div><pre>' + esc(json.dumps({k: v for k, v in report.items() if k != 'trace_payload'}, indent=2, allow_nan=False)) + '</pre></div></details>'
    body += '<footer>One execution only · run <span class="mono">' + esc(report.get('run_id')) + '</span>. No live instrument was controlled. The controller logic and configured thresholds are unchanged. A software ACCEPT is not a declaration of material safety, corrosion absence, authenticated calibration or full physical integration. Print includes all implemented diagnostic rows regardless of screen filtering.</footer>'
    script = '''
const rows=Array.from(document.querySelectorAll('#check-rows tr'));
function filterChecks(){const mode=document.querySelector('#check-filter').value;const q=document.querySelector('#check-search').value.toLowerCase();let n=0;rows.forEach(r=>{const outcome=r.dataset.outcome;const show=(mode==='all'||(mode==='pass'?outcome==='pass':outcome!=='pass'))&&r.textContent.toLowerCase().includes(q);r.hidden=!show;if(show)n++;});document.querySelector('#visible-count').textContent=n+' of '+rows.length+' checks shown';}
document.querySelector('#check-filter').addEventListener('change',filterChecks);document.querySelector('#check-search').addEventListener('input',filterChecks);filterChecks();
document.querySelector('#download-json').addEventListener('click',()=>{const a=document.createElement('a');const blob=new Blob([document.getElementById('run-data').textContent],{type:'application/json'});a.href=URL.createObjectURL(blob);a.download='lpr-decision-'+JSON.parse(document.getElementById('run-data').textContent).run_id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);});
'''
    return document('LPR decision ' + decision, body, report, script)
