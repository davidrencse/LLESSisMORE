"use strict";
// Monte Carlo sample field: one WebGL point per sampled outcome, rows in input order.
// Points spread around their exact value only so dense values stay visible.
const FIELD_ROW = 56, FIELD_PAD = 12, FIELD_POINT_CAP = 400000, FIELD_MS = 900;

const FIELD_VERTEX = `
attribute vec2 a_value;
attribute vec3 a_spread;
uniform vec2 u_size;
uniform float u_row, u_pad, u_dx, u_dy, u_time, u_dpr;
varying float v_alpha;
void main() {
  float t = clamp((u_time - a_spread.z * 0.55) / 0.45, 0.0, 1.0);
  float e = 1.0 - pow(2.0, -10.0 * t);
  float x = u_pad + a_value.x * (u_size.x - 2.0 * u_pad) + a_spread.x * u_dx;
  float y = (a_value.y + 0.5) * u_row + a_spread.y * u_dy * e;
  gl_Position = vec4(x / u_size.x * 2.0 - 1.0, 1.0 - y / u_size.y * 2.0, 0.0, 1.0);
  gl_PointSize = 2.0 * u_dpr;
  v_alpha = e;
}`;
const FIELD_FRAGMENT = `
precision mediump float;
uniform vec3 u_ink;
uniform float u_alpha;
varying float v_alpha;
void main() {
  vec2 c = gl_PointCoord - 0.5;
  if (dot(c, c) > 0.25) discard;
  float a = u_alpha * v_alpha;
  gl_FragColor = vec4(u_ink * a, a);
}`;

function fieldRows(result, dimension) {
  const label = (id, index) => result.options.find(o => o.id === id && o.node_index === index)?.label || 'Unnamed option';
  const several = new Set(result.options.map(o => o.node_index)).size > 1;
  return [
    ...result.options.map(o => ({label: (o.label || 'Unnamed option') + (several ? ` · decision ${o.node_index + 1}` : ''), summary: o.dimensions?.[dimension]})),
    ...(result.pairs || []).map(p => ({label: `${label(p.option_ids[0], 0)} + ${label(p.option_ids[1], 1)}`, summary: p.dimensions?.[dimension]})),
  ].filter(row => row.summary?.distribution?.length);
}

function fieldRandom(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function fieldProgram(gl) {
  const shaders = [];
  const compile = (type, source) => {
    const shader = gl.createShader(type);
    shaders.push(shader);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw Error(gl.getShaderInfoLog(shader) || 'Shader compilation failed');
    return shader;
  };
  const program = gl.createProgram();
  try {
  gl.attachShader(program, compile(gl.VERTEX_SHADER, FIELD_VERTEX));
  gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FIELD_FRAGMENT));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw Error(gl.getProgramInfoLog(program) || 'Shader linking failed');
  return program;
  } catch (error) {
    gl.deleteProgram(program);
    throw error;
  } finally {
    shaders.forEach(shader => gl.deleteShader(shader));
  }
}

function renderSampleField(container, result, dimension) {
  container._fieldCleanup?.();
  const rows = fieldRows(result, dimension);
  if (!rows.length) {
    container.innerHTML = '<p class="muted">This dimension has no sampled values to plot.</p>';
    return;
  }
  const values = rows.flatMap(r => r.summary.distribution.map(d => d.value));
  let lo = Math.min(...values), hi = Math.max(...values);
  if (lo === hi) { lo -= 1; hi += 1; }
  const norm = v => (v - lo) / (hi - lo);
  const total = rows.reduce((sum, r) => sum + r.summary.distribution.reduce((s, d) => s + d.count, 0), 0);
  const per = Math.max(1, Math.ceil(total / FIELD_POINT_CAP));
  const distinct = [...new Set(values)].sort((a, b) => a - b);
  const minGap = distinct.slice(1).reduce((m, v, i) => Math.min(m, norm(v) - norm(distinct[i])), 1);

  container.innerHTML = `<div class="field">
    <div class="field-plot">
      <ol class="field-labels">${rows.map(r => `<li>${esc(r.label)}</li>`).join('')}</ol>
      <div class="field-stage"><canvas role="img" aria-label="Sampled ${esc(dimension)} values for ${rows.length} rows. Exact figures are in the table above."></canvas></div>
      <div class="field-axis" aria-hidden="true"><span>${esc(fmt(lo))}</span>${lo < 0 && hi > 0 ? `<span data-x="${norm(0)}">0</span>` : ''}<span>${esc(fmt(hi))}</span></div>
    </div>
    <p class="field-note muted">Each point is ${per === 1 ? 'one sample' : `${per.toLocaleString()} samples`}. Clusters sit at the exact sampled values, and each cluster's height is proportional to how often that value was drawn. Scatter inside a cluster only keeps points visible. The vertical line marks the median; the rule beneath each row spans P05 to P95.</p>
  </div>`;
  const stage = container.querySelector('.field-stage'), canvas = stage.querySelector('canvas');
  const height = rows.length * FIELD_ROW;
  canvas.style.height = height + 'px';
  const place = (el, x) => el.style.setProperty('left', `calc(${FIELD_PAD}px + (100% - ${2 * FIELD_PAD}px) * ${x})`);
  container.querySelectorAll('.field-axis [data-x]').forEach(el => place(el, el.dataset.x));
  rows.forEach((r, i) => {
    const median = document.createElement('span'), range = document.createElement('span');
    median.className = 'field-mark median';
    range.className = 'field-mark range';
    place(median, norm(r.summary.median));
    median.style.top = i * FIELD_ROW + 10 + 'px';
    median.style.height = FIELD_ROW - 20 + 'px';
    place(range, norm(r.summary.p05));
    range.style.width = `calc((100% - ${2 * FIELD_PAD}px) * ${norm(r.summary.p95) - norm(r.summary.p05)})`;
    range.style.top = (i + 1) * FIELD_ROW - 6 + 'px';
    stage.append(median, range);
  });
  if (lo < 0 && hi > 0) {
    const zero = document.createElement('span');
    zero.className = 'field-mark zero';
    place(zero, norm(0));
    stage.append(zero);
  }

  const gl = canvas.getContext('webgl', {antialias: true, premultipliedAlpha: true, alpha: true});
  if (!gl) {
    canvas.replaceWith(Object.assign(document.createElement('p'), {className: 'muted', textContent: 'WebGL is unavailable in this browser, so the sample field is not drawn. The table above summarizes the same samples.'}));
    return;
  }
  let program = null, gpuBuffer = null, observer = null;
  let width = 0, frame = 0, start = 0, disposed = false;
  container._fieldCleanup = () => {
    if (disposed) return;
    disposed = true;
    cancelAnimationFrame(frame);
    observer?.disconnect();
    if (gpuBuffer) gl.deleteBuffer(gpuBuffer);
    if (program) gl.deleteProgram(program);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    container._fieldCleanup = null;
  };
  try {
  const random = fieldRandom(Number(result.seed) || 1);
  const densest = Math.max(...rows.flatMap(r => r.summary.distribution.map(d => d.count)));
  const pointCount = rows.reduce((sum, r) => sum + r.summary.distribution.reduce(
    (s, d) => s + Math.max(1, Math.round(d.count / per)), 0), 0);
  let buffer = new Float32Array(pointCount * 5), offset = 0;
  // Cluster height is proportional to how often a value was drawn, so each cluster reads as a histogram bar.
  rows.forEach((r, row) => r.summary.distribution.forEach(d => {
    const height = Math.max(0.08, d.count / densest);
    for (let k = 0, n = Math.max(1, Math.round(d.count / per)); k < n; k++) {
      buffer[offset++] = norm(d.value);
      buffer[offset++] = row;
      buffer[offset++] = random() * 2 - 1;
      buffer[offset++] = (random() * 2 - 1) * height;
      buffer[offset++] = random();
    }
  }));
  program = fieldProgram(gl);
  gl.useProgram(program);
  gpuBuffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, gpuBuffer);
  gl.bufferData(gl.ARRAY_BUFFER, buffer, gl.STATIC_DRAW);
  buffer = null; // The GPU owns a copy; resize/animation closures need only the count.
  const value = gl.getAttribLocation(program, 'a_value'), spread = gl.getAttribLocation(program, 'a_spread');
  gl.enableVertexAttribArray(value);
  gl.vertexAttribPointer(value, 2, gl.FLOAT, false, 20, 0);
  gl.enableVertexAttribArray(spread);
  gl.vertexAttribPointer(spread, 3, gl.FLOAT, false, 20, 8);
  gl.enable(gl.BLEND);
  gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
  const u = name => gl.getUniformLocation(program, name);
  const ink = (getComputedStyle(container).color.match(/\d+(\.\d+)?/g) || [10, 10, 10]).slice(0, 3).map(c => c / 255);
  gl.uniform3fv(u('u_ink'), ink);
  gl.uniform1f(u('u_alpha'), 0.4);

  const draw = time => {
    if (disposed) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(u('u_size'), width, height);
    gl.uniform1f(u('u_row'), FIELD_ROW);
    gl.uniform1f(u('u_pad'), FIELD_PAD);
    gl.uniform1f(u('u_dx'), Math.max(1.5, Math.min(7, minGap * (width - 2 * FIELD_PAD) * 0.3)));
    gl.uniform1f(u('u_dy'), FIELD_ROW * 0.38);
    gl.uniform1f(u('u_time'), time);
    gl.uniform1f(u('u_dpr'), dpr);
    gl.drawArrays(gl.POINTS, 0, pointCount);
  };
  const resize = () => {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    width = stage.clientWidth;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    if (!frame) draw(1);
  };
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const tick = now => {
    if (disposed) return;
    start ||= now;
    const t = Math.min(1, (now - start) / FIELD_MS);
    draw(t);
    frame = t < 1 ? requestAnimationFrame(tick) : 0;
  };
  observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
  if (!reduced) frame = requestAnimationFrame(tick);
  observer?.observe(stage);
  resize();
  } catch {
    container._fieldCleanup?.();
    canvas.replaceWith(Object.assign(document.createElement('p'), {className: 'muted', textContent: 'The sample field could not be drawn. The table above summarizes the same samples.'}));
  }
}
