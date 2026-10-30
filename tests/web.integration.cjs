const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const {spawn} = require('node:child_process');
const {JSDOM, VirtualConsole} = require('jsdom');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function until(predicate, label) {
  for (let i = 0; i < 400; i++) {
    if (await predicate()) return;
    await delay(25);
  }
  throw Error('Timed out: ' + label);
}

test('guided form to real API: calculations, historical branches, stale edits, and resumed skips', {timeout:30000}, async () => {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'lifeev-workflow-'));
  const python = process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python';
  const child = spawn(python, ['-m','uvicorn','lifeev.api:app','--host','127.0.0.1','--port',String(port)], {
    windowsHide:true, stdio:'ignore', env:{...process.env,LIFEEV_DATA_DIR:temporary,LIFEEV_API_TOKEN:'',LIFEEV_MODEL_BASE_URL:''}});
  const base = `http://127.0.0.1:${port}`;
  let dom;
  try {
    await until(async () => { try {return (await fetch(base+'/api/health')).ok;} catch {return false;} }, 'server startup');
    const api = async (url, body) => {
      const response = await fetch(base+'/api'+url,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
      const value = await response.json();
      assert.equal(response.ok,true,JSON.stringify(value));
      return value;
    };
    const errors=[], requests=[];
    let html=fs.readFileSync('web/workspace.html','utf8').replace(/<script[^>]*><\/script>/g,'');
    for (const file of ['web/cards.js','web/guided.js','web/analysis.js','web/intake.js','web/field.js','web/dashboard.js','web/app.js','web/workspace.js']) html+='<script>'+fs.readFileSync(file,'utf8')+'</script>';
    const console = new VirtualConsole();
    console.on('jsdomError',error=>errors.push(error.message));
    dom = new JSDOM(html,{url:base,runScripts:'dangerously',virtualConsole:console,beforeParse(w){
      w.structuredClone=structuredClone;
      w.HTMLCanvasElement.prototype.getContext=()=>null;
      w.fetch=async(url,options)=>{const response=await fetch(new URL(url,base),options);requests.push({url,status:response.status});return response;};
    }});
    const d=dom.window.document;
    const click=selector=>{const element=d.querySelector(selector);assert.ok(element,selector);element.click();};
    const fill=(selector,value,scope=d)=>{const element=scope.querySelector(selector);assert.ok(element,selector);element.value=String(value);};
    await until(()=>requests.some(r=>r.url==='/api/sessions?summary=true'),'initial load');
    fill('#ask','girlfriend decision: should i just leave her or keep pursueing');click('#ask-run');
    await until(()=>d.querySelectorAll('.q-name').length===2,'options found');
    assert.deepEqual([...d.querySelectorAll('.q-name')].map(i=>i.value),['Just leave her','Keep pursueing']);
    d.querySelectorAll('.qopt').forEach((option,i)=>option.querySelectorAll('.qrow').forEach((row,j)=>{fill('.q-chance',[[60,30,10],[30,30,40]][i][j],row);fill('.q-value',[[5,1,-3],[8,0,-4]][i][j],row);}));
    click('#run-analysis');
    await until(()=>d.querySelectorAll('.dcard').length===2,'dashboard');
    assert.match(d.querySelectorAll('.dcard')[0].textContent,/Average outcome\+3score/);
    click('#dash-save');
    await until(()=>d.querySelector('#subtitle').textContent.startsWith('Version 1'),'quick save');
    assert.equal((await api('/nodes'))[0].document.options[1].outcomes[2].values.score,-4);
    click('#new');
    fill('#ask','Should I stay or go?\nStay: 100% same (0)\nGo: 50% better (+4), 50% worse (-2)');click('#ask-run');
    await until(()=>d.querySelectorAll('.dcard').length===2,'one-message dashboard');
    assert.match(d.querySelectorAll('.dcard')[1].textContent,/Average outcome\+1score/);
    assert.equal((await api('/nodes')).length,1,'analysis alone saves nothing');
    click('#new');click('[data-tab=inputs]');
    fill('#intake-text','Title: Career decision\nI am deciding between Stay and Study.\nBankroll: money=20000');
    click('#intake-preview');
    await until(()=>d.querySelector('#intake-apply'),'intake proposal');
    assert.equal(d.querySelector('#decision-title').value,'','preview must not apply');
    assert.equal((await api('/nodes')).length,1,'preview must not persist');
    click('#intake-apply');
    await until(()=>d.querySelector('#decision-title').value==='Career decision','intake applied');
    assert.equal(d.querySelectorAll('.option').length,2);
    fill('#intake-text','Cost Study: money=5000\nHorizon: 2 years');
    click('#intake-preview'); await until(()=>d.querySelector('#intake-apply'),'incremental proposal');
    fill('#decision-title','Manual edit'); click('#intake-apply');
    await until(()=>d.querySelector('#notice').textContent.includes('draft changed'),'stale proposal blocked');
    assert.equal(d.querySelector('#decision-title').value,'Manual edit');
    click('#intake-preview'); await until(()=>d.querySelector('#intake-apply'),'fresh proposal');
    click('#intake-apply'); await until(()=>d.querySelector('#horizon').value==='2 years','incremental apply');
    assert.equal(d.querySelectorAll('.option')[1].querySelector('[name=cost]').value,'5000');
    assert.equal((await api('/nodes')).length,1,'apply changes only the draft');
    click('#new');click('[data-tab=inputs]');
    fill('#decision-title','Guided workflow');fill('#horizon','6 months');fill('#reversibility','Fees cannot be recovered');fill('#weight-version','guided-v1');
    for (const dimension of ['money','time']) {
      const row=d.querySelector(`.dimension-row[data-dimension=${dimension}]`);
      fill('[name=weight]',dimension==='money'?1:0,row); fill('[name=bankroll]',1000,row);
    }
    fill('.dimension-row[data-dimension=money] [name=threshold]',-50);
    fill('#correlation-mode','none');
    click('#add-option'); await delay(20);
    const option=d.querySelector('.option');
    fill('[name=label]','Status quo',option); option.querySelector('[name=status]').checked=true;
    option.querySelectorAll('[name=cost]').forEach(input=>input.value='0');
    [...option.querySelectorAll('.outcome')].forEach((row,index)=>{
      fill('[name=probability]',[.2,.6,.2][index],row);
      fill('[name=probability_low]',0,row); fill('[name=probability_high]',1,row);
      fill('[data-dimension=money] [name=value]',[-100,0,200][index],row);
      fill('[data-dimension=time] [name=value]',0,row);fill('[name=black_swan]','false',row);
    });
    click('#save'); await until(()=>d.querySelector('#subtitle').textContent.startsWith('Version 1'),'first save');
    const original=(await api('/nodes')).find(n=>n.document.title==='Guided workflow');
    assert.equal(original.statistics_snapshot.options[0].dimensions.money.ev,20);
    assert.equal(original.document.weights.time,0);
    assert.deepEqual(original.document.correlations,[]);
    click('#analyze'); await until(()=>d.querySelector('table'),'statistics');
    assert.match(d.querySelector('table').textContent,/20/);
    assert.equal(d.querySelectorAll('#content svg').length,2,'one axis per dimension');
    assert.match(d.title,/LLess/);
    const details=original.statistics_snapshot.options[0].analysis_details.money;
    assert.equal(details.cost.break_even_total,20);
    assert.equal(details.risk.conditional_loss,100);
    assert.ok(Math.abs(details.probability_sensitivity[0].zero_ev_probability-1/3)<1e-12);
    fill('#analysis-extra-cost',25);
    d.querySelector('#analysis-extra-cost').dispatchEvent(new dom.window.Event('input'));
    assert.match(d.querySelector('#analysis-cost-preview').textContent,/-5/);
    assert.match(d.querySelector('#analysis-cost-preview').textContent,/-125/);
    fill('#analysis-probability',.4);
    d.querySelector('#analysis-probability').dispatchEvent(new dom.window.Event('input'));
    assert.equal(d.querySelector('#probability-ev').textContent,'-10');
    fill('#analysis-probability',1.1);
    d.querySelector('#analysis-probability').dispatchEvent(new dom.window.Event('input'));
    assert.match(d.querySelector('#analysis-probability-preview').textContent,/within the feasible range/);
    assert.deepEqual((await api(`/nodes/${original.id}`)).statistics_snapshot,original.statistics_snapshot,'what-if controls preserve frozen statistics');
    click('[data-tab=audit]'); click('#saved-stats');
    await until(()=>d.querySelector('#notice').textContent.includes('saved version 1'),'saved-version statistics');
    assert.match(d.querySelector('table').textContent,/20/);
    click('[data-tab=audit]'); click('#audit-log');
    await until(()=>d.querySelector('#audit-data table'),'audit table');
    assert.match(d.querySelector('#audit-data').textContent,/create/);
    assert.match(d.querySelector('#statusbar').textContent,/API ok/);
    assert.match(d.querySelector('#overview').textContent,/Versionv1/);
    click('[data-tab=inputs]'); fill('#decision-title','Version two'); click('#save');
    await until(()=>d.querySelector('#subtitle').textContent.startsWith('Version 2'),'revision');
    click('[data-tab=audit]');fill('#version',1);click('#load-version');
    await until(()=>d.querySelector('#subtitle').textContent.startsWith('Version 1'),'historical inspection');
    click('[data-tab=inputs]');click('#branch');click('#save');
    await until(async()=> (await api('/nodes')).length===3,'branch saved');
    const branch=(await api('/nodes')).find(n=>n.parent_id===original.id);
    assert.equal(branch.parent_version,1);
    assert.equal(branch.frozen_parent.document.title,'Guided workflow');
    await until(()=>d.querySelector('#subtitle').textContent.startsWith('Version 1'),'branch UI ready');
    await api(`/nodes/${branch.id}/versions`,{document:{...branch.document,title:'External revision'},source_version:1});
    fill('#decision-title','Keep my draft');click('#save');
    await until(()=>requests.some(r=>r.status===409),'stale save rejected');
    assert.equal(d.querySelector('#decision-title').value,'Keep my draft');
    assert.equal((await api(`/nodes/${branch.id}`)).version,2);
    click('#new');click('[data-tab=inputs]');fill('#decision-title','Questions can resume');click('#start');
    await until(()=>d.querySelector('[data-skip]'),'question batch');
    assert.equal(d.querySelector('#guided fieldset').disabled,true);
    const session=(await api('/sessions'))[0];
    click('[data-skip]');
    await until(async()=> (await api('/sessions/'+session.id)).history.length===1,'skip autosaved');
    click('#new');click('[data-tab=inputs]');fill('#sessions',session.id);click('#resume');
    await until(()=>d.querySelector('#questions').textContent.includes('explicit user skip'),'resumed provenance');
    assert.equal(d.querySelector('#guided fieldset').disabled,true);
    click('#simulation-load');
    await until(()=>d.querySelectorAll('#simulation-nodes option').length===3,'simulation nodes');
    d.querySelector(`#simulation-nodes option[value="${original.id}"]`).selected=true;
    fill('#simulation-samples',500); click('#simulation-run');
    await until(()=>d.querySelector('#simulation-result table'),'simulation table');
    assert.match(d.querySelector('#simulation-result table').textContent,/Status quo/);
    assert.equal(d.querySelectorAll('#simulation-result .field-labels li').length,1);
    assert.deepEqual(errors,[]);
  } finally {
    dom?.window.close();
    child.kill();
    await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));
    // Only the directory created by this test may be removed.
    if (path.dirname(temporary)===path.resolve(os.tmpdir()) && path.basename(temporary).startsWith('lifeev-workflow-')) fs.rmSync(temporary,{recursive:true,force:true,maxRetries:3,retryDelay:100});
  }
});
