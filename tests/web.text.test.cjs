const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM, VirtualConsole} = require('jsdom');
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
// Real /api/quick output for a two-player game, generated from lifeev.quick.quick_text.
const gameResult = JSON.parse(fs.readFileSync('tests/fixtures_game.json', 'utf8'));

const complete = {complete:true,missing:[],notes:[],method:'offline text parser + exact discrete statistics',
  document:{title:'Stay or go?',options:[{id:'a',label:'Stay'},{id:'b',label:'Go'}]},
  analysis:{options:[
    {label:'Stay',dimensions:{score:{ev:0,p05:0,p95:0,standard_deviation:0,loss_probability:0}},analysis_details:{score:{risk:{minimum:0,maximum:0},contributions:[{label:'Same',probability:1,net_value:0}]}}},
    {label:'Go',dimensions:{score:{ev:1,p05:-2,p95:4,standard_deviation:3,loss_probability:.5}},analysis_details:{score:{risk:{minimum:-2,maximum:4},contributions:[{label:'Better',probability:.5,net_value:4},{label:'Worse',probability:.5,net_value:-2}]}}}]}};
const incomplete = {complete:false,analysis:null,notes:[],method:'offline text parser + exact discrete statistics',
  document:{title:'Should I move to Lisbon or stay in Toronto?',options:[{id:'a',label:'Move to Lisbon'},{id:'b',label:'Stay in Toronto'}]},
  missing:['“Move to Lisbon” needs chances and outcomes.','“Stay in Toronto” needs chances and outcomes.']};

async function setup(intercept) {
  const calls = [], errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', e => errors.push(e.message));
  const html = fs.readFileSync('web/index.html', 'utf8').replace(/<script[^>]*><\/script>/g, '') + ['web/cards.js', 'web/text.js'].map(file => '<script>' + fs.readFileSync(file, 'utf8') + '</script>').join('');
  const dom = new JSDOM(html, {url:'http://localhost', runScripts:'dangerously', virtualConsole, beforeParse(window) {
    window.fetch = async (url, options) => {
      const body = JSON.parse(options.body);
      calls.push({url, body});
      if (intercept) {
        const response = await intercept(url, body, options);
        if (response) return response;
      }
      if (body.text?.includes('OFFLINE')) throw new TypeError('Failed to fetch');
      const partial = {...complete, complete:false, partial:true, missing:['“Go”: the chances add up to 50%. They need to total 100%.'], notes:[],
        analysis:{options:[complete.analysis.options[0]]}};
      const value = url === '/api/quick' ? (body.text.includes('If I') ? gameResult : body.text.includes('PARTIAL') ? partial : body.text.includes('%') ? complete : incomplete) : {id:'node-1', version:1, document:body.document};
      return {ok:true, status:200, json:async () => value};
    };
  }});
  const d = dom.window.document;
  const type = text => { d.querySelector('#text').value = text; d.querySelector('#text').dispatchEvent(new dom.window.Event('input')); };
  return {dom, d, calls, errors, type};
}

test('the page is one text box and results follow typing without a button', async () => {
  const {dom, d, calls, errors, type} = await setup();
  assert.equal(d.querySelectorAll('textarea').length, 1);
  assert.equal(d.querySelectorAll('button').length, 0);
  type('Should I stay or go?\nStay: 100% same (0)\nGo: 50% better (+4), 50% worse (-2)');
  type('Should I stay or go?\nStay: 100% same (0)\nGo: 50% better (+4), 50% worse (-2) ');
  await wait(450);
  assert.equal(calls.filter(c => c.url === '/api/quick').length, 1, 'typing is debounced into one request');
  assert.equal(calls.filter(c => c.url !== '/api/quick').length, 0, 'analysis writes nothing');
  assert.equal(d.querySelectorAll('.dcard').length, 2);
  assert.match(d.querySelectorAll('.dcard')[1].textContent, /Average outcome\+1score/);
  assert.match(d.querySelectorAll('.dcard')[1].textContent, /Most likely: Better, 50% chance, \+4/);
  d.querySelector('#save').click(); await wait(20);
  assert.deepEqual(calls.at(-1), {url:'/api/nodes', body:{document:complete.document}});
  assert.match(d.querySelector('.solo-actions').textContent, /Saved as version 1/);
  assert.equal(d.querySelector('.solo-actions a').getAttribute('href'), '/workspace.html?open=node-1');
  assert.deepEqual(errors, []);
  dom.window.close();
});

test('editing cancels pending analysis and blocks stale saves during debounce', async () => {
  let resolve, signal;
  const {dom, d, type, calls} = await setup((url, body, options) => {
    if (body.text === 'slow') {
      signal = options.signal;
      return new Promise(r => { resolve = r; });
    }
  });
  type('slow'); await wait(450);
  type('changed');
  assert.equal(signal.aborted, true);
  resolve({ok:true, status:200, json:async () => complete});
  await wait(10);
  assert.equal(d.querySelector('#save'), null);
  type('100% complete'); await wait(450);
  assert.ok(d.querySelector('#save'));
  type('new unsent edits');
  assert.equal(d.querySelector('#save'), null);
  assert.equal(calls.filter(c => c.url === '/api/nodes').length, 0);
  dom.window.close();
});

test('edits during a save remain unsaved and clearing during a save is safe', async () => {
  let resolve;
  const {dom, d, type, errors} = await setup(url => url === '/api/nodes'
    ? new Promise(r => { resolve = r; }) : undefined);
  type('100% original'); await wait(450);
  d.querySelector('#save').click();
  type('');
  resolve({ok:true, status:200, json:async () => ({id:'n', version:1})});
  await wait(10);
  assert.equal(d.querySelector('#output').innerHTML, '');
  type('100% edited'); await wait(450);
  assert.match(d.querySelector('#save').textContent, /Save new version/);
  assert.deepEqual(errors, []);
  dom.window.close();
});

test('text without numbers says what is missing and adds blanks, never numbers', async () => {
  const {dom, d, errors, type} = await setup();
  type('Should I move to Lisbon or stay in Toronto?');
  await wait(450);
  assert.match(d.querySelector('#output').textContent, /Found Move to Lisbon, Stay in Toronto/);
  assert.match(d.querySelector('#output').textContent, /needs chances and outcomes/);
  d.querySelector('#scaffold').click();
  const lines = d.querySelector('#text').value.split('\n');
  assert.equal(lines[1], "Move to Lisbon: __% it goes well (+__), __% it doesn't (-__)");
  assert.equal(lines.length, 3);
  assert.doesNotMatch(lines.slice(1).join(''), /\d/);
  assert.equal(d.querySelector('#text').selectionEnd - d.querySelector('#text').selectionStart, 2);
  assert.deepEqual(errors, []);
  dom.window.close();
});

test('clearing the box clears the output', async () => {
  const {dom, d, type} = await setup();
  type('Stay: 100% same (0)'); await wait(450);
  assert.ok(d.querySelector('.dcard'));
  type('   '); await wait(450);
  assert.equal(d.querySelector('#output').innerHTML, '');
  dom.window.close();
});

test('complete options show while others are unfinished, and only complete text can be saved', async () => {
  const {dom, d, errors, type} = await setup();
  type('PARTIAL Stay: 100% same (0)\nGo: 50% better (+4)'); await wait(450);
  assert.equal(d.querySelectorAll('.dcard').length, 1);
  assert.match(d.querySelector('.dcard .dcard-scen').textContent, /Same100%0/);
  assert.match(d.querySelector('.solo-msg').textContent, /add up to 50%/);
  assert.equal(d.querySelector('#save'), null);
  assert.deepEqual(errors, []);
  dom.window.close();
});

test('an unreachable server gets a plain message', async () => {
  const {dom, d, type} = await setup();
  type('OFFLINE should I stay or go'); await wait(450);
  assert.match(d.querySelector('#output').textContent, /Can’t reach the LLess server/);
  dom.window.close();
});

test('Tab moves between fill-in blanks', async () => {
  const {dom, d} = await setup();
  const box = d.querySelector('#text');
  box.value = 'A: __% x (+__)';
  box.setSelectionRange(3, 5);
  box.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key:'Tab', cancelable:true}));
  assert.deepEqual([box.selectionStart, box.selectionEnd], [11, 13]);
  box.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key:'Tab', cancelable:true}));
  assert.deepEqual([box.selectionStart, box.selectionEnd], [3, 5], 'wraps to the first blank');
  dom.window.close();
});

test('a typed game shows the payoff table, stable outcomes and replies without picking a choice', async () => {
  const {dom, d, calls, errors, type} = await setup();
  type('If I leave and she stays: -3, 2'); await wait(450);
  const table = d.querySelector('.g-table');
  assert.equal(table.querySelectorAll('tbody tr').length, 2);
  assert.equal(table.querySelectorAll('td.g-stable').length, 2);
  assert.match(table.querySelector('thead').textContent, /She stays/);
  const facts = d.querySelector('.g-facts').textContent;
  assert.match(facts, /Stable outcomes: you leave and she moves on \(\+4, \+3\); you stay and she stays \(\+5, \+5\)/);
  assert.match(facts, /If she stays, your higher payoff comes from stay \(\+5\)/);
  assert.match(facts, /both of her choices average \+2\.6 for her/);
  assert.match(facts, /your average payoff: leave -0\.9, stay \+1\.7/);
  assert.doesNotMatch(d.querySelector('#output').textContent, /you should|recommend|best choice/i);
  d.querySelector('#save').click(); await wait(20);
  assert.deepEqual(calls.at(-1).body.document.game.payoffs[0][0], [-3, 2]);
  assert.deepEqual(errors, []);
  dom.window.close();
});
