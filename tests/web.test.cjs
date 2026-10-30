const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
const tick = () => new Promise(resolve => setTimeout(resolve, 20));

const summary = values => ({mean:values[0],median:values[1],standard_deviation:5,p05:values[2],p95:values[3],loss_probability:.25,
  distribution:[{value:values[2],count:25,probability:.25},{value:values[3],count:75,probability:.75}]});
const simulationFixture = {method:'seeded_discrete_monte_carlo',samples:100,seed:42,pairs:[],resource_budget:{},assumptions:['Options are not ranked or mixed.'],
  options:[{node_index:0,id:'a',label:'Stay',dimensions:{money:summary([10,20,-10,20]),time:summary([0,0,0,0]),weighted_utility:summary([10,20,-10,20])}},
           {node_index:0,id:'b',label:'Study',dimensions:{money:summary([5,15,-40,15]),time:summary([1,1,1,1]),weighted_utility:summary([5,15,-40,15])}}]};
const statsFixture = document => ({method:'exact discrete calculation',assumptions:[],confidence:{reason:'Heuristic only.'},
  assessment:{gaps:document.skipped?.includes('horizon') ? [] : [{field:'horizon',question:'Over what period does this decision play out?'}],skipped:document.skipped || []},
  options:[{label:'Stay',dimensions:{money:{ev:20,median:20,standard_deviation:5,p05:-10,p95:20}},analysis_details:{},probability_ev_bounds:{},unavailable:{}}]});

const dimOf = document => document.dimensions?.[0] || 'score';
const quickStats = document => ({method:'exact discrete calculation',assumptions:[],confidence:{reason:'Heuristic only.'},assessment:{gaps:[{field:'horizon',question:'Over what period does this decision play out?'}],skipped:[]},
  options:document.options.map(o => {
    const k = dimOf(document), values = o.outcomes.map(r => r.values[k]), ev = o.outcomes.reduce((s, r) => s + r.probability * r.values[k], 0);
    return {id:o.id,label:o.label,unavailable:{},dimensions:{[k]:{ev,p05:Math.min(...values),p95:Math.max(...values),standard_deviation:1,loss_probability:o.outcomes.filter(r=>r.values[k]<0).reduce((s,r)=>s+r.probability,0)}},
      analysis_details:{[k]:{risk:{minimum:Math.min(...values),maximum:Math.max(...values)},contributions:o.outcomes.map(r=>({label:r.label,probability:r.probability,net_value:r.values[k]}))}}};
  })});

const textDocument = {title:'Should I leave her or keep pursuing?',dimensions:['score'],weights:{score:1},options:[
  {id:'a',label:'Leave her',status_quo:false,costs:{score:0},outcomes:[{label:'I feel better',probability:.6,values:{score:5}},{label:'I regret it',probability:.4,values:{score:-3}}]},
  {id:'b',label:'Keep pursuing',status_quo:false,costs:{score:0},outcomes:[{label:'It works out',probability:.3,values:{score:8}},{label:'It does not',probability:.7,values:{score:-4}}]}]};
const quickResponse = body => body.document
  ? {complete:true,document:body.document,analysis:quickStats(body.document),missing:[],notes:[]}
  : body.text.includes('%')
    ? {complete:true,document:textDocument,analysis:quickStats(textDocument),missing:[],notes:[]}
    : {complete:false,document:{title:body.text,options:[{id:'a',label:'Leave her'},{id:'b',label:'Keep pursuing'}]},analysis:null,notes:[],
       missing:['“Leave her”: add chances and outcomes.','“Keep pursuing”: add chances and outcomes.']};

async function setup() {
  const calls = [], errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', e => errors.push(e.message));
  let html = fs.readFileSync('web/workspace.html', 'utf8').replace(/<script[^>]*><\/script>/g, '');
  for (const file of ['web/cards.js','web/guided.js','web/analysis.js','web/intake.js','web/field.js','web/dashboard.js','web/app.js','web/workspace.js']) html += '<script>' + fs.readFileSync(file,'utf8') + '</script>';
  const dom = new JSDOM(html, {url:'http://localhost',runScripts:'dangerously',virtualConsole,
    beforeParse(window) {
      window.structuredClone = structuredClone;
      window.HTMLCanvasElement.prototype.getContext = () => null;
      window.fetch = async (url, options) => {
        const body = options.body ? JSON.parse(options.body) : undefined;
        calls.push({url,body});
        let value = [];
        if (url === '/api/nodes') value = {id:'node-1',version:1,document:body.document,created_at:new Date().toISOString()};
        if (url === '/api/profiles' && body) value = {...body,id:'profile-1'};
        if (url === '/api/profiles' && !body) value = [{id:'profile-1',weight_version:'v1',bankroll:{money:100},weights:{money:1}}];
        if (url === '/api/health') value = {status:'ok',mode:'single-user',agent:'deterministic offline controller',retrieval:'hashed vector + lexical rerank fallback'};
        if (url.startsWith('/api/audit')) value = [{action:'create',id:'node-1',created_at:'2026-10-29T09:00:00+00:00'},{action:'snapshot',id:null,created_at:'2026-10-29T10:00:00+00:00',reason:'daily'}];
        if (url === '/api/analyze') value = statsFixture(body.document);
        if (url === '/api/quick') value = quickResponse(body);
        if (url === '/api/overrides') value = {...body.document,skipped:[body.field],override_acceptances:[{field:body.field,accepted:true}]};
        if (url === '/api/simulate') value = simulationFixture;
        return {ok:true,json:async()=>value};
      };
    }});
  await tick();
  dom.window.document.querySelector('[data-tab=inputs]').click();
  await tick();
  return {dom,document:dom.window.document,calls,errors};
}

test('quick editing sends nonzero costs and custom weights to the full form', async () => {
  const {dom} = await setup();
  try {
    const value = structuredClone(textDocument);
    assert.equal(dom.window.quickCompatible(value), true);
    value.options[0].costs.score = 2;
    assert.equal(dom.window.quickCompatible(value), false);
    value.options[0].costs.score = 0;
    value.weights.score = 3;
    assert.equal(dom.window.quickCompatible(value), false);
  } finally { dom.window.close(); }
});

test('dashboard discards results from a document replaced during analysis', async () => {
  const {dom} = await setup();
  try {
    let resolve;
    dom.window.fetch = () => new Promise(r => { resolve = r; });
    dom.window.eval('doc = {title:"Old", options:[]}; stats = null; computeDashboard();');
    dom.window.eval('doc = {title:"New", options:[]};');
    resolve({ok:true,json:async () => ({complete:true,analysis:{options:[{label:'Old'}]}})});
    await tick();
    assert.equal(dom.window.eval('stats'), null);
  } finally { dom.window.close(); }
});

test('scripts render workspace without runtime errors and save blanks as unknown', async () => {
  const {dom,document:d,calls,errors}=await setup();
  assert.ok(d.querySelector('#memory-save'));
  d.querySelector('#add-option').click(); await tick();
  d.querySelector('#decision-title').value='My decision';
  d.querySelector('[name=label]').value='Status quo';
  d.querySelector('[name=status]').checked=true;
  d.querySelector('#save').click(); await tick();
  const saved=calls.find(c=>c.url==='/api/nodes'&&c.body);
  assert.equal(saved.body.document.title,'My decision');
  assert.equal('money' in saved.body.document.options[0].costs,false);
  assert.equal('probability' in saved.body.document.options[0].outcomes[0],false);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('profile copying updates draft without saving a decision', async () => {
  const {dom,document:d,calls,errors}=await setup();
  d.querySelector('#profiles-load').click(); await tick();
  d.querySelector('#profile-list').value='0';
  d.querySelector('#profile-apply').click(); await tick();
  assert.match(d.querySelector('#notice').textContent,/copied into draft/);
  assert.equal(calls.filter(c=>c.url==='/api/nodes'&&c.body).length,0);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('advanced JSON roundtrip restores guided values', async () => {
  const {dom,document:d,errors}=await setup();
  d.querySelector('#advanced').open=true; await tick();
  const value=JSON.parse(d.querySelector('#json').value);
  value.title='Edited through JSON';
  d.querySelector('#json').value=JSON.stringify(value);
  d.querySelector('#advanced').open=false; await tick();
  assert.equal(d.querySelector('#decision-title').value,'Edited through JSON');
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('guided custom dimensions, ranges, and correlations serialize without JSON', async () => {
  const {dom,document:d,calls,errors}=await setup();
  d.querySelector('#dimension-name').value='health';
  d.querySelector('#add-dimension').click(); await tick();
  d.querySelector('[data-dimension=health] [name=weight]').value='2';
  d.querySelector('[data-dimension=health] [name=bankroll]').value='100';
  d.querySelector('[data-dimension=health] [name=threshold]').value='-5';
  d.querySelector('#weight-version').value='custom-v1';
  d.querySelector('#add-option').click(); await tick();
  const option=d.querySelector('.option');
  option.querySelector('[name=label]').value='Custom option';
  option.querySelector('.option-costs [data-dimension=health] input').value='4';
  const outcome=option.querySelector('.outcome');
  outcome.querySelector('[name=probability]').value='.2';
  outcome.querySelector('[name=probability_low]').value='.1';
  outcome.querySelector('[name=probability_high]').value='.3';
  outcome.querySelector('[name=black_swan]').value='true';
  outcome.querySelector('[data-dimension=health] input').value='-20';
  d.querySelector('#add-correlation').click(); await tick();
  d.querySelector('[name=linked-node]').value='related-id';
  d.querySelector('[name=rho]').value='.4';
  d.querySelector('[name=exposure]').value='50';
  d.querySelector('[name=exposure-kind]').value='monetary_standard_deviation';
  d.querySelector('#save').click(); await tick();
  const saved=calls.find(c=>c.url==='/api/nodes'&&c.body).body.document;
  assert.equal(saved.weights.health,2);
  assert.equal(saved.bankroll.health,100);
  assert.equal(saved.thresholds.health,-5);
  assert.equal(saved.options[0].costs.health,4);
  assert.equal(saved.options[0].outcomes[0].values.health,-20);
  assert.equal(saved.options[0].outcomes[0].probability_low,.1);
  assert.equal(saved.options[0].outcomes[0].black_swan,true);
  assert.equal(saved.correlations[0].rho,.4);
  assert.equal(saved.correlations[0].exposure_kind,'monetary_standard_deviation');
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('invalid guided probabilities preserve inputs and prevent API writes', async () => {
  const {dom,document:d,calls,errors}=await setup();
  d.querySelector('#add-option').click(); await tick();
  d.querySelector('[name=probability]').value='1.2';
  d.querySelector('#save').click(); await tick();
  assert.equal(calls.filter(c=>c.url==='/api/nodes'&&c.body).length,0);
  assert.equal(d.querySelector('[name=probability]').value,'1.2');
  assert.match(d.querySelector('#notice').textContent,/allowed range/);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('malformed advanced document stays editable without breaking form', async () => {
  const {dom,document:d,errors}=await setup();
  d.querySelector('#advanced').open=true; await tick();
  d.querySelector('#json').value='{"options":null}';
  d.querySelector('#advanced').open=false; await tick();
  assert.equal(d.querySelector('#advanced').open,true);
  assert.equal(d.querySelector('#json').value,'{"options":null}');
  assert.match(d.querySelector('#notice').textContent,/arrays of objects/);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('status panels show server health, counts and the last snapshot', async () => {
  const {dom,document:d,errors}=await setup();
  assert.match(d.querySelector('#server-status').textContent,/Online/);
  assert.match(d.querySelector('#statusbar').textContent,/API ok/);
  assert.match(d.querySelector('#statusbar').textContent,/daily/);
  assert.match(d.querySelector('#system').textContent,/Decisions0/);
  assert.match(d.querySelector('#overview').textContent,/StateDraft/);
  d.querySelector('#add-option').click(); await tick();
  assert.match(d.querySelector('#overview').textContent,/Options1/);
  assert.match(d.querySelector('#overview').textContent,/Scenarios3/);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('accepting a gap records an explicit override without saving', async () => {
  const {dom,document:d,calls,errors}=await setup();
  d.querySelector('#analyze').click(); await tick();
  const accept=d.querySelector('[data-accept-gap=horizon]');
  assert.ok(accept);
  accept.click(); await tick();
  const override=calls.find(c=>c.url==='/api/overrides');
  assert.equal(override.body.field,'horizon');
  assert.equal(override.body.accepted,true);
  assert.match(d.querySelector('#notice').textContent,/accepted unknown/);
  assert.equal(d.querySelector('[data-accept-gap]'),null);
  assert.equal(calls.filter(c=>c.url==='/api/nodes'&&c.body).length,0);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('simulation results render as a table with a WebGL fallback note', async () => {
  const {dom,document:d,errors}=await setup();
  d.querySelector('#simulation-run').click(); await tick();
  const result=d.querySelector('#simulation-result');
  assert.match(result.querySelector('table').textContent,/Study/);
  assert.match(result.querySelector('table').textContent,/25%/);
  assert.equal(result.querySelectorAll('.field-labels li').length,2);
  assert.match(result.textContent,/WebGL is unavailable/);
  d.querySelector('#simulation-dimension').value='time';
  d.querySelector('#simulation-dimension').dispatchEvent(new dom.window.Event('change'));
  assert.match(result.querySelector('caption').textContent,/time/);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

async function quickSetup() {
  const context = await setup();
  context.document.querySelector('[data-tab=dashboard]').click(); await tick();
  return context;
}
const fillQuick = (d, rows) => d.querySelectorAll('.qopt').forEach((option, i) => option.querySelectorAll('.qrow').forEach((row, j) => {
  row.querySelector('.q-chance').value = rows[i][j][0];
  row.querySelector('.q-value').value = rows[i][j][1];
}));

test('quick path: question to options to dashboard without saving', async () => {
  const {dom,document:d,calls,errors}=await quickSetup();
  d.querySelector('#ask').value='Should I leave her or keep pursuing?';
  d.querySelector('#ask-run').click(); await tick();
  assert.deepEqual([...d.querySelectorAll('.q-name')].map(i=>i.value),['Leave her','Keep pursuing']);
  assert.match(d.querySelector('#q-info').textContent,/add chances and outcomes/);
  fillQuick(d,[[[60,5],[30,1],[10,-3]],[[30,8],[30,0],[40,-4]]]);
  d.querySelector('#run-analysis').click(); await tick(); await tick();
  const analyzed=calls.filter(c=>c.url==='/api/quick'&&c.body.document).at(-1).body.document;
  assert.equal(analyzed.options[0].outcomes[0].probability,.6);
  assert.deepEqual(analyzed.options[1].costs,{score:0});
  assert.equal(d.querySelectorAll('.dcard').length,2);
  assert.match(d.querySelectorAll('.dcard')[0].textContent,/Leave her/);
  assert.match(d.querySelector('.dash-head').textContent,/not saved yet/);
  assert.equal(calls.filter(c=>c.url==='/api/nodes'&&c.body).length,0);
  d.querySelector('#dash-save').click(); await tick();
  assert.equal(calls.filter(c=>c.url==='/api/nodes'&&c.body).length,1);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('quick path explains chances that do not total 100%', async () => {
  const {dom,document:d,calls,errors}=await quickSetup();
  d.querySelector('#ask').value='Should I leave her or keep pursuing?';
  d.querySelector('#ask-run').click(); await tick();
  fillQuick(d,[[[60,5],[30,1],[20,-3]],[[30,8],[30,0],[40,-4]]]);
  d.querySelector('.q-chance').dispatchEvent(new dom.window.Event('input',{bubbles:true}));
  assert.match(d.querySelector('.q-sum').textContent,/110%, need 100%/);
  d.querySelector('#run-analysis').click(); await tick();
  assert.match(d.querySelector('#q-error').textContent,/add up to 110%/);
  assert.equal(calls.filter(c=>c.url==='/api/quick'&&c.body.document).length,0);
  assert.deepEqual(errors,[]);
  dom.window.close();
});

test('one message with numbers goes straight to the dashboard', async () => {
  const {dom,document:d,calls,errors}=await quickSetup();
  d.querySelector('#ask').value='Should I leave her or keep pursuing?\nLeave her: 60% I feel better (+5), 40% I regret it (-3)';
  d.querySelector('#ask-run').click(); await tick();
  assert.equal(calls.filter(c=>c.url==='/api/quick').length,1);
  assert.equal(calls.filter(c=>['/api/analyze','/api/intake/preview','/api/sessions'].includes(c.url)&&c.body).length,0);
  assert.equal(d.querySelectorAll('.dcard').length,2);
  assert.match(d.querySelectorAll('.dcard')[0].textContent,/Most likely: I feel better, 60% chance, \+5/);
  assert.equal(d.querySelector('.qopt'),null);
  assert.deepEqual(errors,[]);
  dom.window.close();
});
