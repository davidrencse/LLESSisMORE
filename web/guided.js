"use strict";

// Input rendering and serialization are kept separate from navigation and storage.
function decisionDimensions(value) {
  return [...new Set([
    ...(value.dimensions || []),
    ...Object.keys(value.weights || {}), ...Object.keys(value.bankroll || {}),
    ...Object.keys(value.thresholds || {}),
    ...(value.options || []).flatMap(o => [
      ...Object.keys(o.costs || {}),
      ...(o.outcomes || []).flatMap(r => Object.keys(r.values || {})),
    ]),
  ])].filter(d => typeof d === 'string');
}

function numericInput(label, name, value, bounds = '') {
  return `<label>${esc(label)}<input type="number" step="any" name="${esc(name)}" value="${esc(value)}" ${bounds}></label>`;
}

// Stores value under key, or removes the key when the value is unset (undefined or '').
function setOrDelete(target, key, value) {
  if (value === undefined || value === '') delete target[key]; else target[key] = value;
}
const sumsToOne = total => Math.abs(total - 1) < 1e-9;

function readNumber(input) {
  if (input.validity?.badInput) throw Error('Enter a valid number for ' + input.closest('label').textContent.trim());
  if (input.value.trim() === '') return undefined;
  const value = Number(input.value);
  if (!Number.isFinite(value) || !input.checkValidity()) {
    input.setAttribute('aria-invalid', 'true');
    input.focus();
    throw Error('Check the allowed range for ' + input.closest('label').textContent.trim());
  }
  input.removeAttribute('aria-invalid');
  return value;
}

function readDimensionMap(container, name) {
  return Object.fromEntries([...container.querySelectorAll(`[data-dimension] input[name="${name}"]`)]
    .map(input => [input.closest('[data-dimension]').dataset.dimension, readNumber(input)])
    .filter(([, value]) => value !== undefined));
}

function parseDecisionJSON(text) {
  const candidate = JSON.parse(text);
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) throw Error('Decision JSON must be an object.');
  if (candidate.options !== undefined && (!Array.isArray(candidate.options) || candidate.options.some(o => !o || typeof o !== 'object' || Array.isArray(o) || (o.outcomes !== undefined && (!Array.isArray(o.outcomes) || o.outcomes.some(r => !r || typeof r !== 'object' || Array.isArray(r))))))) throw Error('Options and outcomes must be arrays of objects.');
  for (const key of ['weights','bankroll','thresholds']) if (candidate[key] !== undefined && (!candidate[key] || typeof candidate[key] !== 'object' || Array.isArray(candidate[key]))) throw Error(key + ' must be an object.');
  if (candidate.dimensions !== undefined && (!Array.isArray(candidate.dimensions) || candidate.dimensions.some(d => typeof d !== 'string'))) throw Error('Dimensions must be a list of names.');
  if (candidate.correlations !== undefined && (!Array.isArray(candidate.correlations) || candidate.correlations.some(c => !c || typeof c !== 'object'))) throw Error('Correlations must be an array of objects.');
  return candidate;
}

function updateDoc() {
  if ($('#json')) { doc = parseDecisionJSON($('#json').value); return; }
  const form = $('#guided');
  if (!form) return;
  const next = structuredClone(doc);
  next.title = $('#decision-title').value;
  next.horizon = $('#horizon').value;
  next.reversibility = $('#reversibility').value;
  next.weight_version = $('#weight-version').value;
  const dimensions = [...form.querySelectorAll('.dimension-row')].map(row => row.dataset.dimension);
  next.dimensions = dimensions;
  for (const [key, name] of [['weights','weight'],['bankroll','bankroll'],['thresholds','threshold']]) {
    const values = readDimensionMap($('#dimension-settings'), name);
    if (Object.keys(values).length || (key === 'thresholds' && $('#no-thresholds').checked)) next[key] = values;
    else delete next[key];
  }
  next.options = [...form.querySelectorAll('.option')].map((el, i) => {
    const original = doc.options?.[i] || {};
    return {...original, id: original.id || crypto.randomUUID(), label: el.querySelector('[name=label]').value,
      status_quo: el.querySelector('[name=status]').checked,
      costs: readDimensionMap(el.querySelector('.option-costs'), 'cost'),
      outcomes: [...el.querySelectorAll('.outcome')].map((row, j) => {
        const outcome = {...original.outcomes?.[j], label: row.querySelector('[name=outcome]').value,
          values: readDimensionMap(row.querySelector('.outcome-values'), 'value')};
        for (const key of ['probability','probability_low','probability_high']) {
          setOrDelete(outcome, key, readNumber(row.querySelector(`[name="${key}"]`)));
        }
        const flag = row.querySelector('[name=black_swan]').value;
        setOrDelete(outcome, 'black_swan', flag && flag === 'true');
        return outcome;
      })};
  });
  const mode = $('#correlation-mode').value;
  if (mode === 'unknown') delete next.correlations;
  else if (mode === 'none') next.correlations = [];
  else next.correlations = [...form.querySelectorAll('.correlation-row')].map((row, i) => {
    const item = {...doc.correlations?.[i], node_id: row.querySelector('[name=linked-node]').value};
    for (const key of ['rho','exposure']) {
      setOrDelete(item, key, readNumber(row.querySelector(`[name="${key}"]`)));
    }
    setOrDelete(item, 'option_id', row.querySelector('[name=correlation-option]').value);
    setOrDelete(item, 'exposure_kind', row.querySelector('[name=exposure-kind]').value);
    return item;
  });
  doc = next;
  stats = null;
}

function renderInputs() {
  intakeProposal = null;
  const dimensions = decisionDimensions(doc);
  if (!dimensions.length) dimensions.push('money', 'time');
  const activeSession = activeSessionOpen();
  const correlationMode = doc.correlations === undefined ? 'unknown' : doc.correlations.length ? 'defined' : 'none';
  $('#content').innerHTML = `<div class="panel">
    <h2>${node ? 'Decision inputs' : 'Create a decision'}</h2>
    <p class="muted">Record benefits before costs. Blank inputs stay unknown. Probabilities use 0 to 1; point probabilities must total 1 for each option.</p>
    ${activeSession ? '<p class="notice">This session autosaves question answers. Its inputs are shown read-only here.</p><button id="leave-session">Edit a separate draft</button>' : ''}
    ${renderIntake(activeSession)}
    <form id="guided"><fieldset class="guided-fields" ${activeSession ? 'disabled' : ''}><legend class="sr-only">Decision inputs</legend>
    <div class="fields"><label class="wide">Decision title<input id="decision-title" value="${esc(doc.title)}"></label>
    <label>Time horizon<input id="horizon" value="${esc(doc.horizon)}" placeholder="e.g. 6 months"></label>
    <label>Reversibility<input id="reversibility" value="${esc(doc.reversibility)}" placeholder="What can be reversed, and at what cost?"></label></div>
    <details class="input-section" open><summary>Resources, utility weights & thresholds</summary>
      <p class="muted">Use consistent units within each dimension. Weights convert benefits into utility; they are not probabilities. Bankroll is the total resource available. A threshold counts net outcomes at or below that value.</p>
      <label>Weight version<input id="weight-version" value="${esc(doc.weight_version)}" placeholder="e.g. personal-v1"></label>
      <div id="dimension-settings">${dimensions.map(d => `<div class="dimension-row" data-dimension="${esc(d)}"><h3>${esc(d)}</h3><div class="fields three">
        ${numericInput('Utility weight','weight',doc.weights?.[d],'min="0"')}
        ${numericInput('Available bankroll','bankroll',doc.bankroll?.[d],'min="0"')}
        ${numericInput('Net-value threshold','threshold',doc.thresholds?.[d])}
      </div></div>`).join('')}</div>
      <label class="check"><input id="no-thresholds" type="checkbox" ${doc.thresholds && !Object.keys(doc.thresholds).length ? 'checked' : ''}>If threshold fields are blank, I have no thresholds to record</label>
      <div class="dimension-add"><label>Additional dimension<input id="dimension-name" maxlength="80" placeholder="e.g. opportunity or health"></label><button id="add-dimension" type="button">Add dimension</button></div>
    </details>
    <div id="options">${(doc.options || []).map((option, i) => `<section class="option">
      <h3>Option ${i + 1}</h3><label>Option name<input name="label" value="${esc(option.label)}"></label>
      <label class="check"><input type="radio" name="status" ${option.status_quo ? 'checked' : ''}>This is the status quo</label>
      <h4>Costs per dimension</h4><div class="fields option-costs">${dimensions.map(d => `<div data-dimension="${esc(d)}">${numericInput(d + ' cost','cost',option.costs?.[d])}</div>`).join('')}</div>
      ${(option.outcomes || []).map((outcome, j) => `<fieldset class="outcome"><legend>Outcome ${j + 1}</legend>
        <label>Scenario name<input name="outcome" value="${esc(outcome.label)}" placeholder="Base, upside, downside, or another scenario"></label>
        <div class="fields three probabilities">${numericInput('Point probability','probability',outcome.probability,'min="0" max="1"')}${numericInput('Lower probability','probability_low',outcome.probability_low,'min="0" max="1"')}${numericInput('Upper probability','probability_high',outcome.probability_high,'min="0" max="1"')}</div>
        <p class="probability-feedback muted" aria-live="polite"></p>
        <div class="fields outcome-values">${dimensions.map(d => `<div data-dimension="${esc(d)}">${numericInput(d + ' benefit before costs','value',outcome.values?.[d])}</div>`).join('')}</div>
        <label>Explicit black-swan scenario<select name="black_swan"><option value="">Not assessed</option><option value="false" ${outcome.black_swan === false ? 'selected' : ''}>No</option><option value="true" ${outcome.black_swan === true ? 'selected' : ''}>Yes</option></select></label>
        <button type="button" data-remove="${i},${j}">Remove outcome ${j + 1}</button>
      </fieldset>`).join('')}
      <p class="probability-total muted" aria-live="polite"></p>
      <div class="actions"><button type="button" data-outcome="${i}">Add outcome</button><button type="button" data-delete-option="${i}">Remove option</button></div>
    </section>`).join('')}</div>
    <div class="actions"><button id="add-option" type="button">Add option</button></div>
    <details class="input-section"><summary>Correlations to other decisions</summary><p class="muted">Correlation alone does not identify joint tail risk. Combined monetary variance requires exposure explicitly measured as a monetary standard deviation.</p>
      <label>Correlation context<select id="correlation-mode"><option value="unknown" ${correlationMode === 'unknown' ? 'selected' : ''}>Not assessed</option><option value="none" ${correlationMode === 'none' ? 'selected' : ''}>No linked correlations to record</option><option value="defined" ${correlationMode === 'defined' ? 'selected' : ''}>Record correlations below</option></select></label>
      <div id="correlation-rows">${(doc.correlations || []).map((c, i) => `<fieldset class="correlation-row"><legend>Correlation ${i + 1}</legend><label>Linked decision ID<input name="linked-node" list="decision-ids" value="${esc(c.node_id)}"></label><div class="fields">${numericInput('Correlation coefficient','rho',c.rho,'min="-1" max="1"')}${numericInput('Exposure','exposure',c.exposure)}</div><label>Applies to option<select name="correlation-option"><option value="">Not option-specific (variance unavailable)</option>${(doc.options || []).map(o => `<option value="${esc(o.id)}" ${c.option_id === o.id ? 'selected' : ''}>${esc(o.label || o.id)}</option>`).join('')}</select></label><label>Exposure meaning<select name="exposure-kind"><option value="">Unspecified (variance unavailable)</option><option value="monetary_standard_deviation" ${c.exposure_kind === 'monetary_standard_deviation' ? 'selected' : ''}>Monetary standard deviation</option></select></label><button type="button" data-remove-correlation="${i}">Remove correlation</button></fieldset>`).join('')}</div>
      <datalist id="decision-ids">${nodes.map(n => `<option value="${esc(n.id)}">${esc(n.document.title)}</option>`).join('')}</datalist><button id="add-correlation" type="button">Add correlation</button>
    </details></fieldset></form>
    ${activeSession ? '' : '<details id="advanced"><summary>Advanced document JSON</summary><p class="muted">Opening this editor copies the guided inputs. Close it to return to the form.</p><div id="editor-slot"></div></details>'}
    <div class="actions">${activeSession ? '' : `<button id="save" class="primary">${node ? 'Save new version' : 'Save decision'}</button><button id="start">Explore missing inputs</button>`}<button id="analyze">Calculate statistics</button>${node && !activeSession ? '<button id="branch">Create branch</button><button id="archive">' + (node.archived_at ? 'Restore decision' : 'Archive decision') + '</button>' : ''}</div>
    <p class="muted">Saved versions retain their exact inputs and statistics. Unknowns and skipped fields remain visible.</p>
  </div>`;
  $('#guided').addEventListener('input', showProbabilityFeedback);
  $('#advanced')?.addEventListener('toggle', () => {
    try {
      if ($('#advanced').open && !$('#json')) {
        updateDoc();
        $('#editor-slot').innerHTML = '<label>Decision document<textarea id="json" spellcheck="false"></textarea></label>';
        $('#json').value = JSON.stringify(doc, null, 2);
        $('#guided fieldset').disabled = true;
      } else if (!$('#advanced').open && $('#json')) {
        const candidate = parseDecisionJSON($('#json').value);
        doc = candidate;
        stats = null;
        renderInputs();
      }
      renderOverview();
    } catch (error) { notice(error.message, true); if ($('#advanced')) $('#advanced').open = true; }
  });
  showProbabilityFeedback();
}

function showProbabilityFeedback() {
  document.querySelectorAll('.option').forEach(option => {
    const inputs = [...option.querySelectorAll('[name=probability]')];
    const entered = inputs.filter(input => input.value !== '');
    const sum = entered.reduce((total, input) => total + Number(input.value), 0);
    const label = option.querySelector('.probability-total');
    label.textContent = `Point probability total: ${fmt(sum)}. ${entered.length < inputs.length ? 'Some probabilities are unknown.' : sumsToOne(sum) ? 'Totals 1.' : 'Complete point probabilities must total 1.'}`;
    label.classList.toggle('input-warning', entered.length === inputs.length && !sumsToOne(sum));
    option.querySelectorAll('.outcome').forEach(row => {
      const [p, lo, hi] = ['probability','probability_low','probability_high'].map(k => row.querySelector(`[name=${k}]`).value);
      const mismatch = (lo !== '' && hi !== '' && +lo > +hi) || (p !== '' && lo !== '' && +p < +lo) || (p !== '' && hi !== '' && +p > +hi);
      const message = row.querySelector('.probability-feedback');
      message.textContent = mismatch ? 'The point probability must be between the lower and upper probabilities.' : '';
      message.classList.toggle('input-warning', mismatch);
    });
  });
  renderOverview();
}
