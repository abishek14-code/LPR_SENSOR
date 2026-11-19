// LPR 3D simulator front end. All data comes from the Python backend, which
// reads files written by ElmerSolver, native LTspice and Darshan's P3 checks.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js';

const $ = (id) => document.getElementById(id);
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
};
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = (x, d = 4) => (x === null || x === undefined || Number.isNaN(x)) ? '—' : String(Number(Number(x).toPrecision(d)));
const fixed = (x, d) => (x === null || x === undefined) ? '—' : Number(x).toFixed(d);

const SCENARIO_TEXT = {
  nominal_healthy: 'Healthy cell and instrument.',
  gain_fault: 'Current gain error of 20 % in the measurement chain.',
  offset_fault: 'Current offset of 20 nA in the measurement chain.',
  reverse_gain_fault: 'Gain error that appears only on the reverse sweep.',
  moderate_noise: 'Measurement noise, 5 nA scale.',
  moderate_noise_seed7: 'Measurement noise, 5 nA scale, second noise seed.',
  moderate_noise_seed101: 'Measurement noise, 5 nA scale, third noise seed.',
  high_noise: 'Measurement noise, 100 nA scale.',
  recoverable: 'Slow interface (Cdl 0.3 mF): needs a longer observation.',
  persistent: 'Very slow interface (Cdl 2 mF): does not settle even on repeat.',
  timeout: 'First point overruns the point timeout.',
  reset: 'Instrument reset part-way through the first sweep.',
  conductivity_low: 'Healthy run (conductivity is set by the slider).',
  conductivity_high: 'Healthy run (conductivity is set by the slider).',
  reference_near: 'Healthy run (RE distance is set by the slider).',
  reference_far: 'Healthy run (RE distance is set by the slider).',
};
const STATE_GROUP = (s) => s.startsWith('CALIBRATION') ? ['Calibration resistor', '--s-cal']
  : s.startsWith('OCP') ? ['Open circuit (drive off)', '--s-ocp']
  : s === 'RECOVERY' ? ['Recovery', '--s-rec']
  : s === 'REFERENCE_WAIT' ? ['Reference wait', '--s-wait']
  : s.endsWith('_REVERSE') ? ['LPR steps, reverse', '--s-rev'] : ['LPR steps (A1, B, A2)', '--s-lpr'];

// ---------------------------------------------------------------- state
const S = { inputs: {}, config: null, run: null, field: null, wave: null, attempt: 0, t: 0, playing: false, polling: null };

// ---------------------------------------------------------------- inputs
const LOG = { conductivity: [0.05, 10], area: [2e-7, 2e-5] };
const toSlider = (key, v) => LOG[key] ? 1000 * Math.log(v / LOG[key][0]) / Math.log(LOG[key][1] / LOG[key][0]) : (key === 'gap' || key === 'reference_distance') ? v * 1000 : v;
const fromSlider = (key, s) => LOG[key] ? LOG[key][0] * Math.pow(LOG[key][1] / LOG[key][0], s / 1000) : s / 1000;
const SHOW = {
  conductivity: (v) => `${fmt(v, 3)} S/m`, area: (v) => `${fmt(v * 1e4, 3)} cm²`,
  gap: (v) => `${fixed(v * 1000, 0)} mm`, reference_distance: (v) => `${fixed(v * 1000, 1)} mm`,
};
function defaults() {
  const s = S.config.shared;
  return { conductivity: s.water.conductivity_S_m, area: s.steel.exposed_area_m2, gap: s.electrode_geometry.we_to_ce_gap_m,
           reference_distance: s.electrode_geometry.reference_distance_from_we_m };
}
function readInputs() { return { ...S.inputs }; }
function sliderMoved(key) {
  let x = fromSlider(key, Number($('s_' + key).value));
  S.inputs[key] = LOG[key] ? Number(x.toPrecision(3)) : x;
  syncInputs();
}
function syncInputs() {
  const v = readInputs(), d = defaults();
  $('s_reference_distance').max = Math.round(v.gap * 1000) - 1;
  if (v.reference_distance >= v.gap) { S.inputs.reference_distance = v.reference_distance = v.gap - 0.001; $('s_reference_distance').value = toSlider('reference_distance', v.reference_distance); }
  let changed = 0;
  for (const key of Object.keys(SHOW)) {
    $('o_' + key).textContent = SHOW[key](v[key]);
    const isChanged = Math.abs(v[key] / d[key] - 1) > 1e-3;
    $('s_' + key).closest('.slider').classList.toggle('changed', isChanged);
    changed += isChanged;
  }
  $('changedNote').textContent = changed ? 'Changed values are flagged CHANGED_NEEDS_SOURCE in the parameter audit, because the source register backs the defaults.' : 'All values are the sourced defaults from configs/shared_inputs.json.';
}
function setInputs(v) {
  S.inputs = { ...v };
  for (const key of Object.keys(SHOW)) $('s_' + key).value = toSlider(key, v[key]);
  syncInputs();
}

// ---------------------------------------------------------------- flowchart progress
function renderFlow(progress, running) {
  const ol = $('flow'); ol.innerHTML = '';
  if (!progress) return;
  let firstPending = true;
  const add = (label, cls) => { const li = document.createElement('li'); li.className = cls; li.innerHTML = '<span class="dot"></span><span></span>'; li.lastChild.textContent = label; ol.appendChild(li); };
  for (const s of progress.steps) {
    let cls = s.done ? 'done' : '';
    if (!s.done && running && firstPending) { cls = 'running'; firstPending = false; }
    if (!s.done && progress.status === 'FAILED' && firstPending) { cls = 'failed'; firstPending = false; }
    add(s.label, cls);
  }
  for (const s of progress.loop) {
    let cls = 'loop ' + (s.done ? 'done' : running ? 'running' : progress.status === 'FAILED' ? 'failed' : '');
    add('↻ ' + s.label, cls);
  }
  const branch = { ACCEPT: 'Accept → Rp and conditional corrosion estimate', REJECT: 'Reject → failure reason, numeric result withheld', WAIT_OR_REPEAT: 'Wait or repeat → no repeat left, result withheld' };
  if (progress.decision) add('Decision: ' + branch[progress.decision], 'final done');
  else if (progress.status === 'FAILED') add('Run failed: ' + (progress.error || ''), 'final failed');
  else add('Decision', 'final' + (running && firstPending ? ' running' : ''));
}

// ---------------------------------------------------------------- results panel
function renderResults() {
  const r = S.run.result, f = r.final;
  if (!f) { $('decision').innerHTML = '<p class="small">No decision yet.</p>'; return; }
  const kv = (pairs) => '<dl class="kv">' + pairs.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('') + '</dl>';
  let html = `<span class="badge ${f.decision}">${f.decision.replace(/_/g, ' ')}</span><p class="small">${S.run.scenario.replace(/_/g, ' ')} · ${f.branch}</p>`;
  html += kv([['Rapp', f.Rapp_ohm ? fmt(f.Rapp_ohm, 7) + ' Ω' : '—'], ['Ru (Elmer)', `${fmt(f.Ru_ohm, 6)} ± ${fmt(f.Ru_standard_uncertainty_ohm, 3)} Ω`],
    ['Rp', f.Rp_ohm ? `${fmt(f.Rp_ohm, 7)} ± ${fmt(f.Rp_standard_uncertainty_ohm, 3)} Ω` : 'withheld']]);
  if (f.corrosion) {
    const c = f.corrosion;
    html += kv([['i<sub>corr</sub>', `${fmt(c.icorr_uA_cm2, 4)} ± ${fmt(c.icorr_standard_uncertainty_uA_cm2, 2)} µA/cm²`],
      ['Corrosion rate', `${fmt(c.corrosion_rate_mm_per_year, 3)} ± ${fmt(c.corrosion_rate_standard_uncertainty_mm_per_year, 2)} mm/yr`],
      ['95 % (k=2)', `${fmt(c.corrosion_rate_expanded_interval_k2_mm_per_year[0], 3)} – ${fmt(c.corrosion_rate_expanded_interval_k2_mm_per_year[1], 3)} mm/yr`]]);
    html += '<p class="small">Conditional on: ' + c.conditional_on.slice(0, 3).join('; ') + '.</p>';
  } else {
    const reasons = (f.failure_reasons || []).filter((x, i, a) => a.findIndex(y => y.reason_code === x.reason_code && y.check === x.check) === i).slice(0, 6);
    html += '<p class="small">Numeric corrosion result withheld. Reasons:</p><ul class="reasons">' + reasons.map(x => `<li><b>${x.reason_code}</b>${x.check ? ' · ' + x.check : ''}</li>`).join('') + '</ul>';
    html += `<p class="small">${f.recommended_action}</p>`;
  }
  const loop = r.stage_C_D_loop || [];
  html += '<p class="small">Loop: ' + loop.map(s => s.plan ? `attempt ${s.plan.attempt} (${s.plan.observation_time_per_point_s} s/point) → ${s.decision}` : `reference check ${s.passed ? 'passed' : 'failed'}`).join(' · ') + '</p>';
  $('decision').innerHTML = html;
  const A = r.stage_A;
  $('ru').innerHTML = kv([['Ru', `${fmt(A.Ru_ohm, 6)} Ω`], ['Std. uncertainty', `${fmt(A.standard_uncertainty_ohm, 3)} Ω`]].concat(
    Object.entries(A.uncertainty_budget_ohm).map(([k, v]) => [k.replace(/_/g, ' '), fmt(v, 3) + ' Ω']))) +
    `<p class="small">${Object.keys(S.field ? S.field.solves : {}).length} fresh ElmerSolver runs; closed-form check within ${fmt(Math.max(...Object.values(S.field ? S.field.solves : {a:{P4_relative_difference:0}}).map(s => Math.abs(s.P4_relative_difference))) * 100, 2)} %.</p>`;
  const audit = S.run.audit;
  if (audit) {
    $('audit').innerHTML = '<div class="chips">' + Object.entries(audit.counts).map(([k, v]) => `<span class="chip">${k.replace(/_/g, ' ').toLowerCase()}: ${v}</span>`).join('') + '</div>' +
      `<p class="small">Assumed or flagged: ${audit.flagged.join(', ')}. Every citation is in configs/PARAMETER_SOURCES.json.</p>`;
  }
  $('reportLink').href = `/report/${S.run.name}/`;
}

// ---------------------------------------------------------------- 3D scene
const view = $('three');
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
view.appendChild(renderer.domElement);
const labels = new CSS2DRenderer();
labels.domElement.style.position = 'absolute'; labels.domElement.style.inset = '0'; labels.domElement.style.pointerEvents = 'none';
view.appendChild(labels.domElement);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(40, 1, 0.5, 2000);
camera.position.set(95, 75, 120);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(18, 25, 0); controls.enableDamping = true;
scene.add(new THREE.HemisphereLight(0xdfeaf5, 0x1a2630, 1.1));
const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(60, 120, 80); scene.add(sun);
const grid = new THREE.GridHelper(260, 26, 0x3a5060, 0x23323d); grid.position.y = -0.01; scene.add(grid);
let rig = new THREE.Group(); scene.add(rig);
const anim = { slice: null, sliceColors: null, rel: null, waterMask: null, particles: null, pdata: null, screen: null, led: null, geom: null, Jgrid: null };

function fitCamera() {
  const f = anim.frame; if (!f) return;
  const fov = camera.fov * Math.PI / 180, dist = f.size / 2 / Math.tan(fov / 2) / Math.min(1, camera.aspect) * 1.3;
  const dir = new THREE.Vector3(0.55, 0.42, 0.72).normalize();
  controls.target.set(f.cx, f.cy, 0); camera.position.set(f.cx + dir.x * dist, f.cy + dir.y * dist, dir.z * dist); controls.update();
}
function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h); labels.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix();
  drawCharts();
}
new ResizeObserver(resize).observe(view);

function tag(text, pos, anchor = 'center') {
  // anchor 'start' puts the label's left edge at the point, so labels on the left of the cell stay on screen.
  const el = document.createElement('div'), inner = document.createElement('div');
  inner.className = 'tag'; inner.textContent = text; el.appendChild(inner);
  if (anchor === 'start') inner.style.transform = 'translateX(50%)';
  const o = new CSS2DObject(el); o.position.copy(pos); o.userData.label = true; return o;
}
function tube(points, color, radius = 0.6) {
  const curve = new THREE.CatmullRomCurve3(points.map(p => new THREE.Vector3(...p)));
  return new THREE.Mesh(new THREE.TubeGeometry(curve, 64, radius, 8, false), new THREE.MeshStandardMaterial({ color, roughness: .5 }));
}

function buildScene() {
  scene.remove(rig); rig.traverse(o => { o.geometry?.dispose?.(); }); rig = new THREE.Group(); scene.add(rig);
  for (const o of [...scene.children]) if (o.userData.label) scene.remove(o);
  const F = S.field, g = F.geometry;
  const w = g.width_m * 1000, gap = g.electrolyte_length_m * 1000, t = g.electrode_thickness_m * 1000, r = g.reference_distance_m * 1000;
  const D = Math.max(20, w);  // display depth; the model is a planar cross-section
  const X0 = -w / 2;
  anim.geom = { w, gap, t, r, D, X0 };
  const steel = new THREE.MeshStandardMaterial({ color: 0x9aa3ab, metalness: .85, roughness: .32 });
  const ceMat = new THREE.MeshStandardMaterial({ color: 0x5d6970, metalness: .6, roughness: .45 });
  const we = new THREE.Mesh(new THREE.BoxGeometry(w, t, D), steel); we.position.set(0, t / 2, 0); rig.add(we);
  const ce = new THREE.Mesh(new THREE.BoxGeometry(w, t, D), ceMat); ce.position.set(0, t + gap + t / 2, 0); rig.add(ce);
  const water = new THREE.Mesh(new THREE.BoxGeometry(w, gap, D), new THREE.MeshPhysicalMaterial({ color: 0x3f8fd0, transparent: true, opacity: .16, roughness: .1, depthWrite: false }));
  water.position.set(0, t + gap / 2, 0); rig.add(water);
  const walls = new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(w + .6, gap + 2 * t + 4, D + .6)), new THREE.LineBasicMaterial({ color: 0xbfd6e6, transparent: true, opacity: .55 }));
  walls.position.set(0, t + gap / 2, 0); rig.add(walls);

  // Elmer slice at the mid-depth plane: water triangles only, vertex colours updated each frame.
  const pos = new Float32Array(F.nodes.length * 3);
  F.nodes.forEach(([x, y], i) => { pos[3 * i] = x * 1000 + X0; pos[3 * i + 1] = y * 1000; pos[3 * i + 2] = 0; });
  const idx = []; F.triangles.forEach(([a, b, c, body]) => { if (body === 2) idx.push(a, b, c); });
  const sg = new THREE.BufferGeometry(); sg.setAttribute('position', new THREE.BufferAttribute(pos, 3)); sg.setIndex(idx);
  anim.sliceColors = new Float32Array(F.nodes.length * 3); sg.setAttribute('color', new THREE.BufferAttribute(anim.sliceColors, 3));
  anim.slice = new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, transparent: true, opacity: .92 }));
  rig.add(anim.slice);
  anim.rel = F.phi.map(p => p - F.WE_potential_V);   // Elmer potential relative to the WE surface, for the 1 V Elmer excitation
  anim.slice.add(contours(F, X0));

  // Reference electrode: glass capillary entering through the side wall, tip at the non-loading RE point.
  const reY = t + r, zOut = D / 2 + 22;
  const glass = new THREE.Mesh(new THREE.CylinderGeometry(1.3, 1.3, zOut, 20), new THREE.MeshPhysicalMaterial({ color: 0xe8f2f8, transparent: true, opacity: .55, roughness: .05 }));
  glass.rotation.x = Math.PI / 2; glass.position.set(0, reY, zOut / 2); rig.add(glass);
  const tip = new THREE.Mesh(new THREE.SphereGeometry(1.1, 20, 12), new THREE.MeshStandardMaterial({ color: 0x6b5a3c, roughness: .7 })); tip.position.set(0, reY, 0); rig.add(tip);

  // Potentiostat box (the LTspice circuit) with live screen and decision LED.
  const px = w / 2 + 48, py = 20, pz = 0;
  const box = new THREE.Mesh(new THREE.BoxGeometry(38, 26, 30), new THREE.MeshStandardMaterial({ color: 0x26323b, roughness: .6, metalness: .2 })); box.position.set(px, py, pz); rig.add(box);
  const cv = document.createElement('canvas'); cv.width = 512; cv.height = 256; anim.screen = { cv, ctx: cv.getContext('2d'), tex: new THREE.CanvasTexture(cv) };
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(30, 15), new THREE.MeshBasicMaterial({ map: anim.screen.tex })); scr.position.set(px, py + 3, pz + 15.05); rig.add(scr);
  anim.led = new THREE.Mesh(new THREE.SphereGeometry(1.6, 16, 10), new THREE.MeshBasicMaterial({ color: 0x777777 })); anim.led.position.set(px + 13, py - 9, pz + 15.2); rig.add(anim.led);
  // Leads: green WE, white RE, red CE (common potentiostat colour convention).
  rig.add(tube([[w / 2, t / 2, 0], [w / 2 + 14, t / 2 - 2, 4], [px - 19, py - 6, 10]], 0x2fa35a));
  rig.add(tube([[0, reY, zOut], [8, reY + 4, zOut + 6], [px - 10, py - 2, pz + 15]], 0xf2f2f2));
  rig.add(tube([[w / 2, t + gap + t / 2, 0], [w / 2 + 16, t + gap + 6, 4], [px - 19, py + 6, 10]], 0xd0453b));

  // Current particles in the water.
  const N = 900, pp = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) { pp[3 * i] = X0 + Math.random() * w; pp[3 * i + 1] = t + Math.random() * gap; pp[3 * i + 2] = (Math.random() - .5) * D; }
  const pg = new THREE.BufferGeometry(); pg.setAttribute('position', new THREE.BufferAttribute(pp, 3));
  anim.particles = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0xffd34d, size: .9, transparent: true, opacity: .9, depthWrite: false }));
  rig.add(anim.particles);
  anim.Jgrid = jGrid(F, X0, w, t, gap);

  const A = S.run.result.stage_A, shared = S.run.inputs;
  const labelsOn = $('t_labels').checked;
  const L = [
    tag(`Steel working electrode (WE) · ${fmt(shared.steel.exposed_area_m2 * 1e4, 3)} cm²`, new THREE.Vector3(-w / 2, t / 2, D / 2), 'start'),
    tag(`Counter electrode (CE)`, new THREE.Vector3(-w / 2, t + gap + t / 2, D / 2), 'start'),
    tag(`Reference electrode (RE) · ${fixed(r, 1)} mm from WE`, new THREE.Vector3(0, reY + 4, zOut), 'start'),
    tag(`Water · σ = ${fmt(shared.water.conductivity_S_m, 3)} S/m · Ru = ${fmt(A.Ru_ohm, 5)} ± ${fmt(A.standard_uncertainty_ohm, 2)} Ω`, new THREE.Vector3(0, t + gap + t + 8, 0)),
    tag('Potentiostat · your LTspice circuit', new THREE.Vector3(px, py + 17, pz)),
  ];
  L.forEach(o => { o.visible = labelsOn; scene.add(o); });
  anim.frame = { cx: (X0 + px + 19) / 2 - 6, cy: (2 * t + gap) / 2, size: Math.max(px + 19 - X0, D + 22) + 30 };
  fitCamera();
  $('scaleNote').textContent = `Elmer solves the ${fixed(w, 0)} mm × ${fixed(gap, 0)} mm cross-section shown as the coloured slice. Model depth is area ÷ width = ${fmt(g.depth_m * 1000, 3)} mm; depth is drawn as ${fixed(D, 0)} mm so the cell is visible. The planar field is uniform along depth.`;
}

function contours(F, X0) {
  // Equipotential lines from the Elmer solution (marching triangles), 10 % steps of the WE→CE drop.
  const segs = [], span = F.CE_potential_V - F.WE_potential_V;
  for (const [a, b, c, body] of F.triangles) {
    if (body !== 2) continue;
    const P = [a, b, c].map(i => [F.nodes[i][0] * 1000 + X0, F.nodes[i][1] * 1000, (F.phi[i] - F.WE_potential_V) / span]);
    for (let k = 1; k < 10; k++) {
      const lv = k / 10, cut = [];
      for (let e = 0; e < 3; e++) {
        const p = P[e], q = P[(e + 1) % 3];
        if ((p[2] - lv) * (q[2] - lv) < 0) { const s = (lv - p[2]) / (q[2] - p[2]); cut.push([p[0] + s * (q[0] - p[0]), p[1] + s * (q[1] - p[1])]); }
      }
      if (cut.length === 2) segs.push(cut[0][0], cut[0][1], .05, cut[1][0], cut[1][1], .05, cut[0][0], cut[0][1], -.05, cut[1][0], cut[1][1], -.05);
    }
  }
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(segs, 3));
  return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0x0b1a24, transparent: true, opacity: .45 }));
}

function jGrid(F, X0, w, t, gap) {
  // Element current density from Elmer, binned on a coarse grid for particle motion.
  const nx = 16, ny = 24, sum = Array.from({ length: nx * ny }, () => [0, 0, 0]);
  F.triangles.forEach(([a, b, c, body], e) => {
    if (body !== 2) return;
    const x = (F.nodes[a][0] + F.nodes[b][0] + F.nodes[c][0]) / 3 * 1000 + X0, y = (F.nodes[a][1] + F.nodes[b][1] + F.nodes[c][1]) / 3 * 1000;
    const i = Math.min(nx - 1, Math.max(0, Math.floor((x - X0) / w * nx))), j = Math.min(ny - 1, Math.max(0, Math.floor((y - t) / gap * ny)));
    const s = sum[j * nx + i]; s[0] += F.J[e][0]; s[1] += F.J[e][1]; s[2]++;
  });
  let mean = 0, n = 0;
  const cells = sum.map(s => { const v = s[2] ? [s[0] / s[2], s[1] / s[2]] : [0, 0]; if (s[2]) { mean += Math.hypot(...v); n++; } return v; });
  mean /= Math.max(n, 1);
  return { nx, ny, cells: cells.map(v => [v[0] / mean, v[1] / mean]) };
}

function diverging(u) {   // u in [-1, 1]: blue → near white → red
  const a = Math.min(1, Math.abs(u));
  const end = u < 0 ? [0.16, 0.42, 0.80] : [0.80, 0.24, 0.18], mid = [0.93, 0.94, 0.95];
  return mid.map((m, k) => m + (end[k] - m) * a);
}
function drawColorbar() {
  const c = $('cbcanvas'), x = c.getContext('2d');
  for (let i = 0; i < c.width; i++) { const [r, g, b] = diverging(2 * i / (c.width - 1) - 1); x.fillStyle = `rgb(${r * 255},${g * 255},${b * 255})`; x.fillRect(i, 0, 1, c.height); }
}
drawColorbar();

// ---------------------------------------------------------------- waveform lookup
function sampleAt(t) {
  const W = S.wave; if (!W) return null;
  let lo = 0, hi = W.t.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; (W.t[m] <= t ? (lo = m) : (hi = m)); }
  return { i: lo, t: W.t[lo], v: W.v[lo], I: W.i[lo], req: W.req[lo], state: W.state[lo], path: W.path[lo] };
}
function cellCurrent(s) { return s && s.path === 'cell' ? s.I : 0; }

function updateFrame(dt) {
  const s = sampleAt(S.t); if (!s || !anim.slice) return;
  const F = S.field, Icell = cellCurrent(s);
  // Ohmic water field is linear in current: phi(t) = phi_Elmer * I(t) / I_Elmer (Elmer: 1 V, current CE→WE).
  const k = -Icell / F.elmer_current_A * 1000;        // mV per volt of Elmer solution
  const vmax = S.vmax || 1e-9;
  const col = anim.sliceColors;
  for (let n = 0; n < anim.rel.length; n++) { const [r, g, b] = diverging(anim.rel[n] * k / vmax); col[3 * n] = r; col[3 * n + 1] = g; col[3 * n + 2] = b; }
  anim.slice.geometry.attributes.color.needsUpdate = true;
  $('cbmin').textContent = '−' + fmt(vmax, 2); $('cbmax').textContent = '+' + fmt(vmax, 2);
  // Particles move along Elmer J, scaled by the instantaneous LTspice cell current.
  const G = anim.geom, J = anim.Jgrid, p = anim.particles.geometry.attributes.position.array;
  const speed = (S.playing ? 1 : 0.25) * (Icell / (S.imax || 1)) * G.gap / 2.5 * dt;
  for (let n = 0; n < p.length; n += 3) {
    const i = Math.min(J.nx - 1, Math.max(0, Math.floor((p[n] - G.X0) / G.w * J.nx))), j = Math.min(J.ny - 1, Math.max(0, Math.floor((p[n + 1] - G.t) / G.gap * J.ny)));
    const [jx, jy] = J.cells[j * J.nx + i];
    p[n] += -jx * speed; p[n + 1] += -jy * speed;   // Elmer J points CE→WE; LTspice positive current flows WE→water
    if (p[n + 1] > G.t + G.gap) p[n + 1] -= G.gap; if (p[n + 1] < G.t) p[n + 1] += G.gap;
    if (p[n] < G.X0) p[n] += G.w; if (p[n] > G.X0 + G.w) p[n] -= G.w;
  }
  anim.particles.geometry.attributes.position.needsUpdate = true;
  anim.particles.material.opacity = s.path === 'cell' ? .9 : .25;
  // Readout and the potentiostat screen.
  const pathText = { cell: 'Current path: through the cell', calibration: 'Current path: 10 kΩ calibration resistor (cell bypassed)', open: 'Drive off: open circuit' }[s.path];
  $('readout').innerHTML = `<b>t = ${fixed(s.t, 2)} s</b> · ${s.state.replace(/_/g, ' ')}<br>E requested ${fixed(s.req * 1000, 3)} mV · measured ${fixed(s.v * 1000, 3)} mV<br>I measured ${fixed(s.I * 1e9, 2)} nA<br>iR drop at RE ${fixed(Icell * F.Ru_ohm * 1000, 3)} mV<br><span style="color:#b9c7d2">${pathText}</span>`;
  const sc = anim.screen, x = sc.ctx; x.fillStyle = '#0b2a1d'; x.fillRect(0, 0, 512, 256); x.fillStyle = '#8ff0b5'; x.font = '600 34px ui-monospace, Menlo, monospace';
  x.fillText(`E ${fixed(s.v * 1000, 3)} mV`, 22, 62); x.fillText(`I ${fixed(s.I * 1e9, 2)} nA`, 22, 118);
  x.font = '26px ui-monospace, Menlo, monospace'; x.fillText(s.state.replace(/_/g, ' ').slice(0, 26), 22, 172); x.fillText(`t ${fixed(s.t, 1)} s  att ${S.attempt}`, 22, 222);
  sc.tex.needsUpdate = true;
  $('timeOut').textContent = `${fixed(s.t, 1)} s`;
}

let last = performance.now();
function loop(now) {
  const dt = Math.min(.05, (now - last) / 1000); last = now;
  if (S.playing && S.wave) {
    S.t += dt * Number($('speed').value);
    const end = S.wave.t[S.wave.t.length - 1];
    if (S.t > end) S.t = S.wave.t[0];
    $('timeSlider').value = 1000 * (S.t - S.wave.t[0]) / (end - S.wave.t[0]);
    drawPlayheads();
  }
  updateFrame(dt);
  controls.update(); renderer.render(scene, camera); labels.render(scene, camera);
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);

// ---------------------------------------------------------------- charts
const charts = {};
function setupChart(id, series, unit, scale) {
  const c = $(id), dpr = devicePixelRatio || 1, w = c.clientWidth, h = c.clientHeight;
  c.width = w * dpr; c.height = h * dpr;
  const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = S.wave, pad = { l: 58, r: 12, t: 16, b: 22 };
  const t0 = W.t[0], t1 = W.t[W.t.length - 1];
  let lo = Infinity, hi = -Infinity;
  series.forEach(s => s.data.forEach(v => { lo = Math.min(lo, v * scale); hi = Math.max(hi, v * scale); }));
  const m = (hi - lo) * .08 || 1; lo -= m; hi += m;
  const X = (t) => pad.l + (t - t0) / (t1 - t0) * (w - pad.l - pad.r), Y = (v) => pad.t + (hi - v) / (hi - lo) * (h - pad.t - pad.b);
  charts[id] = { X, t0, t1, pad, w, h, draw: () => {
    x.clearRect(0, 0, w, h);
    for (const seg of W.segments) { x.fillStyle = css(STATE_GROUP(seg.state)[1]); x.fillRect(X(seg.start), pad.t, Math.max(1, X(seg.end) - X(seg.start)), h - pad.t - pad.b); }
    x.strokeStyle = css('--line'); x.fillStyle = css('--muted'); x.font = '11px system-ui'; x.lineWidth = 1;
    for (let k = 0; k <= 4; k++) { const v = lo + (hi - lo) * k / 4, y = Y(v); x.beginPath(); x.moveTo(pad.l, y); x.lineTo(w - pad.r, y); x.stroke(); x.fillText(fmt(v, 3), 4, y + 4); }
    for (let k = 0; k <= 6; k++) { const t = t0 + (t1 - t0) * k / 6; x.fillText(fixed(t, 0) + ' s', X(t) - 12, h - 6); }
    x.fillStyle = css('--ink'); x.font = '600 12px system-ui'; x.fillText(unit, pad.l + 4, 12);
    series.forEach(s => { x.strokeStyle = css(s.color); x.lineWidth = s.width || 1.4; x.beginPath(); s.data.forEach((v, i) => { const px = X(W.t[i]), py = Y(v * scale); i ? x.lineTo(px, py) : x.moveTo(px, py); }); x.stroke(); });
    x.strokeStyle = css('--playhead'); x.lineWidth = 1.5; x.beginPath(); x.moveTo(X(S.t), pad.t); x.lineTo(X(S.t), h - pad.b); x.stroke();
  }};
  c.onclick = (ev) => { const r = c.getBoundingClientRect(), C = charts[id]; S.t = Math.min(C.t1, Math.max(C.t0, C.t0 + (ev.clientX - r.left - C.pad.l) / (C.w - C.pad.l - C.pad.r) * (C.t1 - C.t0))); syncSlider(); drawPlayheads(); };
}
function drawCharts() {
  if (!S.wave) return;
  setupChart('chartV', [{ data: S.wave.req, color: '--trace-req', width: 1 }, { data: S.wave.v, color: '--trace-v' }], 'Voltage WE−RE (mV): requested grey, measured blue', 1000);
  setupChart('chartI', [{ data: S.wave.i, color: '--trace-i' }], 'Measured current (nA)', 1e9);
  drawPlayheads();
  const groups = {}; S.wave.segments.forEach(s => { const [n, c] = STATE_GROUP(s.state); groups[n] = c; });
  $('legend').innerHTML = Object.entries(groups).map(([n, c]) => `<span style="--c:var(${c})">${n}</span>`).join('');
}
function drawPlayheads() { Object.values(charts).forEach(c => c.draw()); }
function syncSlider() { const W = S.wave; $('timeSlider').value = 1000 * (S.t - W.t[0]) / (W.t[W.t.length - 1] - W.t[0]); }
$('timeSlider').oninput = (e) => { const W = S.wave; if (!W) return; S.t = W.t[0] + e.target.value / 1000 * (W.t[W.t.length - 1] - W.t[0]); drawPlayheads(); };
$('playBtn').onclick = () => { S.playing = !S.playing; $('playBtn').textContent = S.playing ? '❚❚' : '▶'; $('playBtn').setAttribute('aria-label', S.playing ? 'Pause' : 'Play'); };
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', drawCharts);

// ---------------------------------------------------------------- loading runs
async function loadAttempt(n) {
  S.attempt = n; S.wave = await api(`/api/run/${S.run.name}/waveform?attempt=${n}`);
  const cellI = S.wave.i.filter((_, k) => S.wave.path[k] === 'cell').map(Math.abs);
  // Colour scale: 95th percentile of the cell current, so short step transients do not wash out the slice.
  const sorted = [...cellI].sort((a, b) => a - b);
  S.imax = Math.max(sorted[Math.floor(.95 * (sorted.length - 1))] || 0, 1e-15);
  const relMax = Math.max(...anim.rel.map(Math.abs));
  S.vmax = S.imax / S.field.elmer_current_A * relMax * 1000;
  const firstLpr = S.wave.segments.find(s => s.state === 'A1');
  S.t = firstLpr ? firstLpr.start + 1 : S.wave.t[0];
  [...$('attemptTabs').children].forEach(b => b.setAttribute('aria-selected', String(Number(b.dataset.n) === n)));
  drawCharts(); syncSlider();
}
async function loadRun(name) {
  S.run = await api('/api/run/' + name);
  $('runSelect').value = name;
  renderFlow(S.run.progress, false);
  if (!S.run.result.final) { $('decision').innerHTML = `<p class="small">This run did not finish (${S.run.progress.status}).</p>`; return; }
  S.field = await api(`/api/run/${name}/field`);
  buildScene(); renderResults();
  const led = { ACCEPT: 0x2fd17a, REJECT: 0xff4d47, WAIT_OR_REPEAT: 0xffb02e }[S.run.result.final.decision];
  anim.led.material.color.setHex(led || 0x777777);
  $('attemptTabs').innerHTML = S.run.attempts.map(n => `<button role="tab" data-n="${n}">Attempt ${n}</button>`).join('');
  [...$('attemptTabs').children].forEach(b => b.onclick = () => loadAttempt(Number(b.dataset.n)));
  await loadAttempt(S.run.attempts[S.run.attempts.length - 1] ?? 0);
}
async function refreshRuns(select) {
  const list = await api('/api/runs');
  $('runSelect').innerHTML = list.map(r => `<option value="${r.name}">${r.name.slice(0, 15).replace('T', ' ')} · ${r.scenario.replace(/_/g, ' ')} · ${r.decision || r.status || ''}</option>`).join('');
  if (select) $('runSelect').value = select;
  return list;
}
$('runSelect').onchange = (e) => loadRun(e.target.value);

// ---------------------------------------------------------------- running
async function startRun() {
  $('runError').textContent = '';
  const body = { scenario: $('scenario').value, overlap: $('overlap').checked, ...readInputs() };
  try {
    $('runBtn').disabled = true; $('runBtn').textContent = 'Running…';
    const r = await api('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    $('log').hidden = false; $('log').textContent = r.command;
    poll(r.name);
  } catch (e) { $('runError').textContent = e.message; $('runBtn').disabled = false; $('runBtn').textContent = 'Run Elmer → LTspice → Darshan'; }
}
function poll(name) {
  clearInterval(S.polling);
  S.polling = setInterval(async () => {
    const j = await api('/api/job').catch(() => null); if (!j) return;
    if (j.progress) renderFlow(j.progress, j.running);
    $('log').textContent = j.log.join('\n');
    if (!j.running) {
      clearInterval(S.polling); $('runBtn').disabled = false; $('runBtn').textContent = 'Run Elmer → LTspice → Darshan';
      if (j.returncode !== 0) $('runError').textContent = 'The run stopped with an error. The log above shows the last lines.';
      await refreshRuns(j.name);
      if (j.returncode === 0) await loadRun(j.name);
    }
  }, 800);
}
$('runBtn').onclick = startRun;
$('resetBtn').onclick = () => setInputs(defaults());
for (const key of Object.keys(SHOW)) $('s_' + key).oninput = () => sliderMoved(key);
$('scenario').onchange = () => { $('scenarioHint').textContent = SCENARIO_TEXT[$('scenario').value] || ''; };
$('t_labels').onchange = (e) => scene.children.forEach(o => { if (o.userData.label) o.visible = e.target.checked; });
$('t_field').onchange = (e) => { if (anim.slice) anim.slice.visible = e.target.checked; };
$('t_flow').onchange = (e) => { if (anim.particles) anim.particles.visible = e.target.checked; };

// ---------------------------------------------------------------- start
(async function init() {
  if (innerWidth < 700) $('t_labels').checked = false;  // labels crowd a phone-width view
  S.config = await api('/api/config');
  const order = ['nominal_healthy', 'gain_fault', 'recoverable', 'persistent', 'reset', 'high_noise', 'moderate_noise', 'offset_fault', 'reverse_gain_fault', 'timeout'];
  const names = [...order.filter(n => n in S.config.scenarios), ...Object.keys(S.config.scenarios).filter(n => !order.includes(n) && !/conductivity_|reference_/.test(n))];
  $('scenario').innerHTML = names.map(n => `<option value="${n}">${n.replace(/_/g, ' ')}</option>`).join('');
  $('scenarioHint').textContent = SCENARIO_TEXT.nominal_healthy;
  setInputs(defaults());
  const list = await refreshRuns();
  const job = await api('/api/job');
  if (job.running) { $('runBtn').disabled = true; $('runBtn').textContent = 'Running…'; poll(job.name); }
  const done = list.find(r => r.status === 'COMPLETED' && r.scenario === 'recoverable') || list.find(r => r.status === 'COMPLETED');
  if (done) await loadRun(done.name);
  else $('decision').innerHTML = '<p class="small">No runs yet. Press Run to start the first one.</p>';
})().catch(e => { $('runError').textContent = 'Could not reach the Python backend: ' + e.message; });
