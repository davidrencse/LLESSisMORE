'use strict';
// Explicit workspace operations; option order and decision merits are untouched.
const toolsPanel = document.createElement('section');
toolsPanel.className = 'workspace-tools panel';
toolsPanel.innerHTML = `<h2>Workspace records</h2>
<details><summary>Bankroll profiles & weights</summary>
<label>New version (JSON)<textarea id="profile-input">{"weight_version":"v1","bankroll":{"money":10000,"time":100},"weights":{"money":1,"time":0}}</textarea></label>
<div class="actions"><button id="profile-save">Save profile version</button><button id="profiles-load">Load profiles</button></div>
<label>Saved version<select id="profile-list"><option value="">Load profiles first</option></select></label><button id="profile-apply">Copy profile into draft</button></details>
<details><summary>Memory proposals & confirmation</summary><p class="muted">Only confirmed entries enter future questions. Conflicts require explicit review.</p>
<div class="fields"><label>Key<input id="memory-key"></label><label>Value<input id="memory-value"></label></div>
<label class="check"><input type="checkbox" id="memory-confirm">I confirm this value for future context</label>
<div class="actions"><button id="memory-save">Save memory entry</button><button id="memory-load">Review memory</button></div><div id="memory-records"></div></details>
<details><summary>Restricted Monte Carlo</summary><p class="muted">One node or a directly linked pair only. Three or more branches disable simulation. Pair sampling assumes independence.</p>
<button id="simulation-load">Load saved nodes</button><label>Nodes (Ctrl or Command to select two)<select id="simulation-nodes" multiple size="4"></select></label>
<div class="fields"><label>Samples<input id="simulation-samples" type="number" min="100" max="100000" value="10000"></label><label>Seed<input id="simulation-seed" type="number" value="42"></label></div><button id="simulation-run">Run simulation</button>
<div id="simulation-result" aria-live="polite"></div></details>
<details><summary>Archive maintenance</summary><p class="muted">Usefulness describes storage activity, never the merits of a choice. Archiving creates a snapshot and allows recovery for seven days.</p><button id="maintenance-load">Inspect archive candidates</button><div id="maintenance-records"></div><button id="maintenance-archive">Archive selected records</button></details>
<pre id="workspace-result" aria-live="polite"></pre>`;
document.querySelector('footer').before(toolsPanel);
let savedProfiles = [], lastSimulation = null;
const outputWorkspace = value => { $('#workspace-result').textContent = JSON.stringify(value, null, 2); };
const SIMULATION_DIMENSIONS = {money:'Money', time:'Time', weighted_utility:'Weighted utility'};
function renderSimulation(dimension = 'money') {
  const result = lastSimulation, target = $('#simulation-result');
  $('#simulation-field')?._fieldCleanup?.();
  const labelFor = (id, index) => result.options.find(o => o.id === id && o.node_index === index)?.label || 'Unnamed option';
  const rows = [...result.options.map(o => [o.label || 'Unnamed option', o.dimensions[dimension]]),
    ...result.pairs.map(p => [labelFor(p.option_ids[0], 0) + ' + ' + labelFor(p.option_ids[1], 1), p.dimensions[dimension]])];
  target.innerHTML = `<h3>Simulation · ${result.samples.toLocaleString()} samples, seed ${esc(result.seed)}</h3>
    <label class="analysis-input">Dimension<select id="simulation-dimension">${Object.entries(SIMULATION_DIMENSIONS).map(([key, label]) => `<option value="${key}" ${key === dimension ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
    ${analysisTable('Sampled ' + SIMULATION_DIMENSIONS[dimension].toLowerCase() + ', options in input order', ['Option', 'Mean', 'Median', 'Std. dev.', 'P05', 'P95', 'P(loss)'],
      rows.map(([label, s]) => [label, fmt(s?.mean), fmt(s?.median), fmt(s?.standard_deviation), fmt(s?.p05), fmt(s?.p95), percent(s?.loss_probability)]))}
    <div id="simulation-field"></div>
    <details><summary>Assumptions</summary><ul class="notes">${result.assumptions.map(a => `<li>${esc(a)}</li>`).join('')}</ul></details>
    <details><summary>Full simulation output</summary><pre>${esc(JSON.stringify(result, null, 2))}</pre></details>`;
  renderSampleField($('#simulation-field'), result, dimension);
  $('#simulation-dimension').addEventListener('change', event => renderSimulation(event.target.value));
}
toolsPanel.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button) return;
  run(async () => {
    switch (button.id) {
      case 'profile-save': outputWorkspace(await api('/profiles', JSON.parse($('#profile-input').value))); notice('New profile version saved. Existing decisions retain recorded weights.'); break;
      case 'profiles-load':
        savedProfiles = await api('/profiles');
        $('#profile-list').innerHTML = '<option value="">Choose a version</option>' + savedProfiles.map((p,i) => `<option value="${i}">${esc(p.weight_version)}</option>`).join('');
        outputWorkspace(savedProfiles); break;
      case 'profile-apply': {
        if (activeSessionOpen()) throw Error('Choose Edit a separate draft before applying a profile to session inputs.');
        const choice = $('#profile-list').value;
        if (choice === '') throw Error('Choose a saved profile.');
        updateDoc();
        for (const key of ['bankroll','weights','weight_version']) doc[key] = structuredClone(savedProfiles[Number(choice)][key]);
        stats = null; setTab('inputs'); notice('Profile copied into draft. Save to create an immutable version.'); break;
      }
      case 'memory-load': {
        const records = await api('/memory');
        $('#memory-records').innerHTML = records.map(m => `<label class="check"><input type="checkbox" data-review-memory="${esc(m.id)}">Reviewed conflict: ${esc(m.key)} = ${esc(JSON.stringify(m.value))} (${m.confirmed ? 'confirmed' : 'provisional'})</label>`).join('') || '<p>No entries.</p>';
        outputWorkspace(records); break;
      }
      case 'memory-save':
        outputWorkspace(await api('/memory', {key:$('#memory-key').value,value:$('#memory-value').value,confirmed:$('#memory-confirm').checked,reviewed_conflicts:[...document.querySelectorAll('[data-review-memory]:checked')].map(e=>e.dataset.reviewMemory)}));
        $('#memory-confirm').checked=false; $('#memory-records').innerHTML=''; notice('Memory and review provenance saved.'); break;
      case 'simulation-load': {
        const records=await api('/nodes?summary=true');
        $('#simulation-nodes').innerHTML=records.map(n=>`<option value="${esc(n.id)}">${esc(n.document.title||'Untitled')} / v${n.version}</option>`).join(''); break;
      }
      case 'simulation-run':
        lastSimulation = await api('/simulate',{node_ids:[...$('#simulation-nodes').selectedOptions].map(o=>o.value),samples:Number($('#simulation-samples').value),seed:Number($('#simulation-seed').value)});
        $('#workspace-result').textContent = '';
        renderSimulation($('#simulation-dimension')?.value || 'money'); break;
      case 'maintenance-load': {
        const records=await api('/maintenance/candidates');
        $('#maintenance-records').innerHTML=records.map(r=>`<label class="check"><input type="checkbox" data-archive-node="${esc(r.id)}">${esc(nodes.find(n=>n.id===r.id)?.document.title||r.id)} / ${esc(r.reason)} / usefulness ${r.score}</label>`).join(''); break;
      }
      case 'maintenance-archive':
        outputWorkspace(await api('/maintenance/archive',{ids:[...document.querySelectorAll('[data-archive-node]:checked')].map(e=>e.dataset.archiveNode),pins}));
        await refresh(); $('#maintenance-records').innerHTML=''; notice('Records archived after snapshots. Show archived in the tree to restore.'); break;
    }
  });
});
