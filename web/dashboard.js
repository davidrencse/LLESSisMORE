"use strict";
// Quick path: type the decision (numbers inline when you have them), analyze, read one dashboard.
// Text that lacks numbers falls back to a prefilled chance/outcome grid.
// Options keep input order; no card is styled or sorted as preferred.
// These labels are the ones the engine's completeness check recognizes.
const QUICK_SCENARIOS = ['Best case', 'Base case', 'Worst case'];
let composing = false, quick = null, dashBusy = false, dashFailure = '', dashDim = null;

function resetComposer() { quick = null; composing = false; dashFailure = ''; }
const blankOption = (label = '') => ({label, current: false, scenarios: QUICK_SCENARIOS.map(s => ({label: s, chance: '', value: ''}))});

// A document the grid can represent without dropping inputs: one dimension, point probabilities only.
function quickCompatible(value) {
  return decisionDimensions(value).length <= 1 && !(value.correlations || []).length &&
    Object.values(value.weights || {}).every(weight => weight === 1) &&
    (value.options || []).every(o => Object.values(o.costs || {}).every(cost => cost === 0)) &&
    (value.options || []).every(o => (o.outcomes || []).every(r => r.probability_low === undefined && r.probability_high === undefined));
}

function quickFromDoc(source = doc, text = source.intake_notes?.at(-1) || source.title || '') {
  const measure = decisionDimensions(source)[0] || 'score';
  const options = (source.options || []).map(o => ({label: o.label || '', current: o.status_quo === true,
    scenarios: o.outcomes?.length
      ? o.outcomes.map(r => ({label: r.label || '', chance: typeof r.probability === 'number' ? +(r.probability * 100).toFixed(6) : '', value: r.values?.[measure] ?? ''}))
      : blankOption().scenarios}));
  return {text, measure, horizon: source.horizon || '', options: options.length ? options : [blankOption(), blankOption()], info: []};
}

function renderDashboard() {
  if (composing || !doc.options?.length) { renderComposer(); return; }
  if (stats) { renderResults(); return; }
  if (dashFailure) {
    $('#content').innerHTML = `<div class="dash-root"><div class="panel empty"><h2>The engine could not run on these inputs.</h2><p class="muted">${esc(dashFailure)}</p><div class="actions dash-center"><button id="dash-edit">Edit scenarios</button><button id="dash-retry" class="primary">Try again</button></div></div></div>`;
    return;
  }
  $('#content').innerHTML = '<div class="dash-root"><div class="panel empty" aria-busy="true"><h2>Running the engine</h2><p class="muted">Calculating each option from your scenarios.</p></div></div>';
  computeDashboard();
}

function computeDashboard() {
  if (dashBusy) return;
  dashBusy = true;
  dashFailure = '';
  const source = JSON.stringify(doc);
  run(async () => {
    try {
      const result = await api('/quick', {document: doc});
      if (source !== JSON.stringify(doc)) return;
      if (!result.complete) throw Error(result.missing.join(' '));
      stats = result.analysis;
    }
    catch (error) { if (source === JSON.stringify(doc)) { dashFailure = error.message; throw error; } }
    finally { dashBusy = false; if (tab === 'dashboard') renderDashboard(); }
  });
}

const ASK_EXAMPLE = 'Should I take the new job or stay?\nTake the job: 60% it goes well (+6), 40% it doesn\'t (-3)\nStay: 80% nothing changes (0), 20% I get restless (-2)';

function renderComposer() {
  const editing = composing && doc.options?.length;
  $('#content').innerHTML = `<div class="dash-root"><section class="panel composer" aria-labelledby="ask-label">
    <label id="ask-label" class="ask" for="ask">What are you deciding?</label>
    <textarea id="ask" rows="${quick ? 3 : 6}" maxlength="20000" placeholder="${esc(ASK_EXAMPLE)}" aria-describedby="ask-help">${esc(quick?.text ?? (doc.options?.length ? '' : doc.intake_notes?.at(-1) || doc.title || ''))}</textarea>
    <p id="ask-help" class="muted">${quick ? 'Edit the text and analyze again, or fill in the grid below.' : 'Write the question, then one line per option with each outcome\'s chance and score. With numbers you get results straight away; without them, you fill them in next.'}</p>
    ${quick ? '' : `<details class="ask-howto"><summary>How to write chances and outcomes</summary><ul class="notes">
      <li>Start each option's line with its name and a colon, <code>Stay: …</code>, or with <code>If I stay, …</code></li>
      <li>Write each outcome as a chance, a few words and a signed score: <code>60% it goes well (+5)</code></li>
      <li>One option's chances must total 100%. Use <code>(0)</code> for no change and <code>$</code> for money.</li>
    </ul></details>`}
    <div class="actions ask-actions"><button id="ask-run" class="${quick ? '' : 'primary run'}">${quick ? 'Analyze text again' : 'Analyze'}</button><span class="muted">Ctrl+Enter</span></div>
    ${quick ? renderBuilder(editing) : ''}
    ${node && !quick && !node.archived_at ? '<div class="composer-foot"><button id="dash-archive" class="quiet-button">Archive this saved decision</button></div>' : ''}
  </section></div>`;
  updateQuickSums();
  renderOverview();
  if (!quick) $('#ask').focus();
}

function renderBuilder(editing) {
  return `<div class="builder">
    <div class="measure-row"><label>Measure outcomes as<input id="measure" maxlength="40" value="${esc(quick.measure)}"></label>
      <label><span>Over what time? <span class="optional">optional</span></span><input id="q-horizon" maxlength="80" placeholder="e.g. the next 6 months" value="${esc(quick.horizon || '')}"></label>
      <p class="muted">Use one scale for every option, for example a score from −10 to +10, dollars, or months. Below 0 means worse off than today.</p></div>
    <div id="q-info" aria-live="polite">${quick.info?.length ? `<ul class="notes">${quick.info.map(m => `<li>${esc(m)}</li>`).join('')}</ul>` : ''}</div>
    ${quick.options.map((o, i) => `<fieldset class="qopt">
      <legend>Option ${i + 1}</legend>
      <div class="qopt-head"><label class="sr-only" for="qname-${i}">Option ${i + 1} name</label><input id="qname-${i}" class="q-name" value="${esc(o.label)}" placeholder="Name this option">
        ${quick.options.length > 1 ? `<button type="button" class="quiet-button" data-q-remove-option="${i}">Remove option</button>` : ''}</div>
      <label class="check q-current"><input type="radio" name="q-current" class="q-is-current" ${o.current ? 'checked' : ''}>This is what I'm doing now</label>
      <div class="qgrid">
        <div class="qgrid-head" aria-hidden="true"><span>Scenario</span><span>Chance</span><span>Outcome</span><span></span></div>
        ${o.scenarios.map((s, j) => `<div class="qrow">
          <input class="q-label" aria-label="Option ${i + 1}, scenario ${j + 1} name" value="${esc(s.label)}" placeholder="Scenario ${j + 1}">
          <input class="q-chance" type="number" min="0" max="100" step="any" inputmode="decimal" aria-label="Option ${i + 1}, scenario ${j + 1} chance in percent" value="${esc(s.chance)}" placeholder="%">
          <input class="q-value" type="number" step="any" inputmode="decimal" aria-label="Option ${i + 1}, scenario ${j + 1} outcome" value="${esc(s.value)}" placeholder="+/−">
          <button type="button" class="icon-button" data-q-remove-scenario="${i},${j}" aria-label="Remove option ${i + 1}, scenario ${j + 1}" title="Remove scenario"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg></button>
        </div>`).join('')}
      </div>
      <div class="qopt-foot"><button type="button" data-q-add-scenario="${i}">Add scenario</button><span class="q-sum" aria-live="polite"></span></div>
    </fieldset>`).join('')}
    <div class="actions"><button type="button" id="q-add-option">Add option</button></div>
    <p id="q-error" class="notice error" role="alert" hidden></p>
    <div class="run-row"><button id="run-analysis" class="primary run">Run analysis</button>${editing ? '<button id="q-cancel">Cancel</button>' : ''}<span class="muted">Nothing is saved until you choose Save.</span></div>
  </div>`;
}

function readComposer() {
  if (!$('.composer')) return;
  quick ||= {text: '', measure: 'score', options: [], info: []};
  quick.text = $('#ask').value;
  if (!$('#measure')) return;
  quick.measure = $('#measure').value;
  quick.horizon = $('#q-horizon').value;
  quick.options = [...document.querySelectorAll('.qopt')].map(el => ({label: el.querySelector('.q-name').value, current: el.querySelector('.q-is-current').checked,
    scenarios: [...el.querySelectorAll('.qrow')].map(row => ({label: row.querySelector('.q-label').value, chance: row.querySelector('.q-chance').value, value: row.querySelector('.q-value').value}))}));
}

function updateQuickSums() {
  document.querySelectorAll('.qopt').forEach(el => {
    const chances = [...el.querySelectorAll('.q-chance')].map(i => i.value).filter(v => v !== '').map(Number);
    const total = chances.reduce((s, v) => s + v, 0), out = el.querySelector('.q-sum');
    out.textContent = chances.length ? `Chances total ${fmt(total)}%${Math.abs(total - 100) < 1e-6 ? '' : ', need 100%'}` : 'Chances must total 100%';
    out.classList.toggle('off', chances.length > 0 && Math.abs(total - 100) >= 1e-6);
  });
}

function quickError(message) {
  const el = $('#q-error');
  el.textContent = message;
  el.hidden = false;
  el.scrollIntoView({block: 'center', behavior: 'smooth'});
}

// Builds the decision document from the grid, or explains the first problem in plain words.
function buildQuickDocument() {
  const measure = quick.measure.trim();
  if (!measure || measure.includes('.') || ['__proto__', 'constructor', 'prototype'].includes(measure)) return 'Name the outcome scale without periods, for example “score”.';
  const options = [];
  for (const [i, o] of quick.options.entries()) {
    const rows = o.scenarios.filter(s => s.chance !== '' || s.value !== '');
    if (!o.label.trim() && !rows.length) continue;
    const name = o.label.trim() || `Option ${i + 1}`;
    if (!o.label.trim()) return `Give option ${i + 1} a name.`;
    if (!rows.length) return `Add at least one scenario with a chance and an outcome for “${name}”.`;
    const outcomes = [];
    for (const [j, s] of rows.entries()) {
      const label = s.label.trim() || `Scenario ${j + 1}`;
      if (s.chance === '' || s.value === '') return `“${name}”, ${label}: enter both a chance and an outcome.`;
      const chance = Number(s.chance), value = Number(s.value);
      if (!Number.isFinite(chance) || chance < 0 || chance > 100) return `“${name}”, ${label}: the chance must be between 0 and 100%.`;
      if (!Number.isFinite(value)) return `“${name}”, ${label}: the outcome must be a number.`;
      outcomes.push({label, probability: chance / 100, values: {[measure]: value}});
    }
    const total = outcomes.reduce((s, r) => s + r.probability, 0);
    if (Math.abs(total - 1) > 1e-6) return `The chances for “${name}” add up to ${fmt(total * 100)}%. They need to total 100%.`;
    const original = composing ? doc.options?.[i] : undefined;
    options.push({id: original?.id || crypto.randomUUID(), label: name, status_quo: o.current === true, costs: {[measure]: 0}, outcomes});
  }
  if (!options.length) return 'Add at least one option.';
  const base = composing ? doc : {};
  const sameScale = decisionDimensions(base)[0] === measure;
  const text = quick.text.trim();
  const next = {...structuredClone(base), title: text.split('\n')[0].slice(0, 200) || base.title || 'Untitled decision',
    dimensions: [measure], weights: {[measure]: 1}, options};
  if (text && !(next.intake_notes || []).includes(text)) next.intake_notes = [...(next.intake_notes || []), text];
  for (const key of ['bankroll', 'thresholds']) if (!sameScale) delete next[key];
  if (quick.horizon?.trim()) next.horizon = quick.horizon.trim();
  return next;
}

// Groups the engine's field-level gaps into plain categories, keeping impact order.
const GAP_GROUPS = [
  [/^status_quo$/, 'Which option is what you are doing now'],
  [/.outcomes$/, 'Best, base and worst cases for each option'],
  [/probability_(low|high)$/, 'How sure you are about each chance (a low–high range)'],
  [/black_swan$/, 'Whether any scenario is a rare, extreme event'],
  [/^horizon$/, 'Time horizon'],
  [/^reversibility$/, 'Whether the decision can be undone'],
  [/^(bankroll|weights|weight_version|thresholds)$/, 'Resources at stake and limits'],
  [/^correlations/, 'Links to other decisions'],
  [/costs/, 'Costs'],
  [/values/, 'Outcome values'],
];
function gapGroups(gaps) {
  const groups = new Map();
  for (const g of gaps) {
    const label = GAP_GROUPS.find(([pattern]) => pattern.test(g.field))?.[1] || 'Other details';
    groups.set(label, (groups.get(label) || 0) + 1);
  }
  return [...groups];
}

function renderResults() {
  const dims = Object.keys(stats.options[0]?.dimensions || {});
  if (!dims.includes(dashDim)) dashDim = dims[0];
  const changed = node && JSON.stringify(node.document) !== JSON.stringify(doc);
  const gaps = stats.assessment?.gaps || [];
  const zeroCosts = (doc.options || []).every(o => o.costs?.[dashDim] === 0);
  $('#content').innerHTML = `<div class="dash-root">
    <div class="dash-head">
      <div><h2>Results</h2><p class="muted">${stats.options.length} option${stats.options.length === 1 ? '' : 's'}, in the order you entered them · ${node ? (changed ? `edited from saved version ${node.version}, not saved` : `saved as version ${node.version}`) : 'not saved yet'}</p></div>
      <div class="actions">${activeSessionOpen() ? '<button id="dash-session">Back to questions</button>' : `${!node || changed ? `<button id="dash-save" class="primary">${node ? 'Save new version' : 'Save decision'}</button>` : ''}<button id="dash-edit">Edit scenarios</button>`}<button id="dash-details">Detailed statistics</button>${node && !changed && !node.archived_at && !activeSessionOpen() ? '<button id="dash-archive" class="quiet-button">Archive</button>' : ''}</div>
    </div>
    ${dims.length > 1 ? `<div class="segmented" role="group" aria-label="Outcome scale">${dims.map(d => `<button data-dash-dim="${esc(d)}" aria-pressed="${d === dashDim}">${esc(d === 'weighted_utility' ? 'weighted total' : d)}</button>`).join('')}</div>` : ''}
    ${cardsHtml(stats.options, dashDim, {empty: 'Not enough inputs to calculate this option yet.', reasons: true})}
    <p class="dash-legend muted">All cards share one scale. Thin line: worst to best case. Thick line: the middle 90% of outcomes. Circle: average. Dashed line: 0.</p>
    <div class="dash-notes">
      <section><h3>What the engine does not know yet</h3>
        ${gaps.length ? `<p>${gaps.length} unanswered detail${gaps.length === 1 ? '' : 's'}, most important first:</p><ul class="gap-groups">${gapGroups(gaps).map(([label, count]) => `<li>${esc(label)}${count > 1 ? `<span>${count}</span>` : ''}</li>`).join('')}</ul>
        ${activeSessionOpen() ? '' : '<button id="dash-questions">Answer follow-up questions</button>'}` : '<p>No open questions for these inputs.</p>'}
      </section>
      <section><h3>Assumptions</h3><ul class="notes">
        <li>Each option's scenarios cover everything that could happen, and their chances total 100%.</li>
        ${zeroCosts ? '<li>Outcomes already include any costs, so each option\'s separate cost is recorded as 0.</li>' : ''}
        <li>${esc(stats.confidence?.reason || 'Completeness scores are heuristics, not statistical confidence.')}</li>
      </ul></section>
    </div>
    <p class="muted dash-foot">LLess shows the shape of each option. It does not pick one for you.</p>
  </div>`;
  sizeBars($('#content'));
}

document.getElementById('content').addEventListener('input', event => {
  if (!event.target.closest('.composer')) return;
  readComposer();
  updateQuickSums();
});

document.getElementById('content').addEventListener('keydown', event => {
  if (event.target.id === 'ask' && event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    $('#ask-run').click();
  }
});

document.addEventListener('click', event => {
  const b = event.target.closest('.dash-root button');
  if (!b || b.disabled) return;
  withBusy(b, async () => {
    readComposer();
    if (b.dataset.qRemoveOption !== undefined) { quick.options.splice(+b.dataset.qRemoveOption, 1); renderComposer(); }
    else if (b.dataset.qAddScenario !== undefined) { quick.options[+b.dataset.qAddScenario].scenarios.push({label: '', chance: '', value: ''}); renderComposer(); }
    else if (b.dataset.qRemoveScenario !== undefined) { const [i, j] = b.dataset.qRemoveScenario.split(',').map(Number); quick.options[i].scenarios.splice(j, 1); renderComposer(); }
    else if (b.dataset.dashDim !== undefined) { dashDim = b.dataset.dashDim; renderResults(); }
    else switch (b.id) {
      case 'ask-run': {
        const text = $('#ask').value.trim();
        if (!text) { notice('Type your decision in the box first.', true); $('#ask').focus(); break; }
        const result = await api('/quick', {text});
        if (result.complete) {
          doc = result.document;
          stats = result.analysis;
          composing = false;
          quick = null;
          notice(result.notes.join(' '));
          render();
          $('.dash-head')?.scrollIntoView({block: 'start'});
        } else {
          quick = quickFromDoc(result.document, text);
          quick.info = [...result.missing, ...result.notes];
          renderComposer();
          [...document.querySelectorAll('.q-chance, .q-value')].find(i => i.value === '')?.focus();
        }
        break;
      }
      case 'q-add-option': quick.options.push(blankOption()); renderComposer(); break;
      case 'q-cancel': composing = false; quick = null; renderDashboard(); break;
      case 'run-analysis': {
        const next = buildQuickDocument();
        if (typeof next === 'string') { quickError(next); return; }
        doc = next;
        stats = null;
        composing = false;
        quick = null;
        notice('');
        render();
        break;
      }
      case 'dash-retry': dashFailure = ''; renderDashboard(); break;
      case 'dash-edit':
        if (!quickCompatible(doc)) { setTab('inputs'); notice('These inputs use more than the quick grid supports, so they open in the full Inputs form.'); break; }
        composing = true;
        quick = quickFromDoc(doc);
        renderComposer();
        break;
      case 'dash-save': await saveDoc(); break;
      case 'dash-archive':
        await api('/nodes/' + node.id + '/archive', {});
        node = null;
        doc = {title: '', options: []};
        stats = null;
        resetComposer();
        await refresh();
        render();
        notice('Decision archived. Turn on “Show archived” in the tree to restore it within seven days.');
        break;
      case 'dash-questions': await startSession(); break;
      case 'dash-session': setTab('inputs'); break;
      case 'dash-details': setTab('statistics'); break;
    }
  });
});

// Typed text, grid entries or an unsaved result would be lost on reload; ask first.
function hasUnsavedWork() {
  if (activeSessionOpen()) return false;
  if ($('#ask')?.value.trim() && !doc.options?.length) return true;
  if (quick && quick.options.some(o => o.scenarios.some(s => s.chance !== '' || s.value !== ''))) return true;
  if (!doc.options?.length) return false;
  return !node || JSON.stringify(node.document) !== JSON.stringify(doc);
}
window.addEventListener('beforeunload', event => {
  if (!hasUnsavedWork()) return;
  event.preventDefault();
  event.returnValue = '';
});
