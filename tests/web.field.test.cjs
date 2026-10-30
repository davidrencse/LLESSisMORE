const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');

function setup(failure) {
  const resources = {shaders: new Set(), programs: new Set(), buffers: new Set(), observers: new Set(), frames: new Set()};
  let serial = 0;
  const create = kind => { const id = ++serial; resources[kind].add(id); return id; };
  const gl = {
    createShader: () => create('shaders'), deleteShader: id => resources.shaders.delete(id),
    createProgram: () => create('programs'), deleteProgram: id => resources.programs.delete(id),
    createBuffer: () => create('buffers'), deleteBuffer: id => resources.buffers.delete(id),
    getShaderParameter: () => failure !== 'compile', getProgramParameter: () => failure !== 'link',
    getShaderInfoLog: () => 'compile failed', getProgramInfoLog: () => 'link failed',
    getExtension: () => null, // Cleanup must work without WEBGL_lose_context.
    bufferData: () => { if (failure === 'upload') throw Error('upload failed'); },
  };
  for (const name of ['shaderSource','compileShader','attachShader','linkProgram','useProgram','bindBuffer',
    'getAttribLocation','enableVertexAttribArray','vertexAttribPointer','enable','blendFunc','getUniformLocation',
    'uniform3fv','uniform1f','uniform2f','viewport','clearColor','clear','drawArrays']) gl[name] = () => {};
  const dom = new JSDOM('<div id="field"></div>', {runScripts:'outside-only'});
  const w = dom.window;
  w.HTMLCanvasElement.prototype.getContext = () => gl;
  w.requestAnimationFrame = () => create('frames');
  w.cancelAnimationFrame = id => resources.frames.delete(id);
  w.ResizeObserver = class {
    observe() { resources.observers.add(this); }
    disconnect() { resources.observers.delete(this); }
  };
  w.esc = String; w.fmt = String;
  w.eval(fs.readFileSync('web/field.js', 'utf8') + '\nwindow.renderSampleField = renderSampleField;');
  const container = w.document.querySelector('#field');
  const summary = {median:1, p05:0, p95:2, distribution:[{value:0,count:50},{value:2,count:50}]};
  const result = {seed:42, options:[{id:'a',label:'A',node_index:0,dimensions:{money:summary}}],pairs:[]};
  return {dom, resources, container, render: () => w.renderSampleField(container, result, 'money')};
}

test('100 sample-field redraws retain only the current GPU resources and observer', () => {
  const {dom, resources, container, render} = setup();
  try {
    for (let i = 0; i < 100; i++) {
      render();
      assert.equal(resources.shaders.size, 0);
      for (const kind of ['programs','buffers','observers','frames']) assert.equal(resources[kind].size, 1, kind);
    }
    const cleanup = container._fieldCleanup;
    cleanup(); cleanup();
    for (const [kind, values] of Object.entries(resources)) assert.equal(values.size, 0, kind);
  } finally { dom.window.close(); }
});

for (const failure of ['compile','link','upload']) test(`${failure} failure releases partial WebGL allocations`, () => {
  const {dom, resources, container, render} = setup(failure);
  try {
    render();
    assert.match(container.textContent, /could not be drawn/);
    for (const [kind, values] of Object.entries(resources)) assert.equal(values.size, 0, kind);
  } finally { dom.window.close(); }
});
