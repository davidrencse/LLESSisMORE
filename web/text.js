"use strict";
// The whole app as one text box. Results follow the text as you type, through
// /api/quick only (parse + exact statistics, no writes); Save is the one write.
// Options keep the order they were typed; no card is styled or sorted as preferred.
const box = document.getElementById('text'), out = document.getElementById('output');
let seq = 0, timer = 0, last = null, saved = null, savedText = '';
let analyzedText = null, pending = null, saving = false;

async function post(path, body, signal) {
  let response;
  try { response = await fetch('/api' + path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal}); }
  catch { throw Error('Can’t reach the LLess server. Start it with run.ps1, then keep typing.'); }
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) throw Error('This server requires an access token. Use the Workspace to enter it.');
  if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'The server could not read this text.');
  return data;
}

function autosize() {
  box.style.height = 'auto';
  box.style.height = Math.max(box.scrollHeight + 2, 160) + 'px';
}

function schedule() {
  autosize();
  clearTimeout(timer);
  ++seq;
  pending?.abort();
  pending = null;
  last = null;
  analyzedText = null;
  out.querySelector('#save')?.remove();
  out.classList.remove('busy');
  if (!box.value.trim()) { out.innerHTML = ''; return; }
  timer = setTimeout(analyze, 350);
}

async function analyze() {
  const source = box.value, text = source.trim(), id = ++seq;
  pending?.abort();
  if (!text) { last = null; out.innerHTML = ''; return; }
  out.classList.add('busy');
  const request = pending = new AbortController();
  try {
    const result = await post('/quick', {text}, request.signal);
    if (id !== seq) return;
    last = result;
    analyzedText = source;
    render();
  } catch (error) {
    if (id === seq) out.innerHTML = `<p class="solo-msg">${esc(error.message)}</p>`;
  } finally {
    if (id === seq) { out.classList.remove('busy'); pending = null; }
  }
}

function render() {
  const {document: doc, analysis, game} = last;
  const current = saved && box.value === savedText;
  let html = game ? gameHtml(game) : '';
  if (analysis) {
    const dim = Object.keys(analysis.options[0]?.dimensions || {})[0];
    html += cardsHtml(analysis.options, dim, {empty: 'Not enough numbers to describe this option.', scenarios: 'How your text was read'}) + `
    <p class="solo-note">All cards share one scale: the thin line runs from worst to best case, the thick line covers the middle 90%, the circle is the average and the dashed line is 0. Each option's outcomes are taken as already including its costs.</p>`;
  }
  html += last.complete
    ? `<div class="solo-actions">${current
      ? `<span>Saved as version ${saved.version}.</span><a href="/workspace.html?open=${encodeURIComponent(saved.id)}">Open in workspace</a>`
      : `<button id="save" type="button">${saved ? 'Save new version' : 'Save'}</button><span class="muted">Nothing is saved until you choose Save.</span>`}</div>`
    : game && !doc.options?.length ? gameMissingHtml() : missingHtml(doc);
  out.innerHTML = html;
  if (saving && out.querySelector('#save')) out.querySelector('#save').disabled = true;
  sizeBars(out);
}

// Two-player game: payoff table plus plain statements about the payoffs as typed; it never names a choice to make.
function gameHtml(g) {
  const you = choice => `you ${choice}`, they = choice => `${g.subject} ${choice}`;
  const or = list => list.length > 1 ? list.slice(0, -1).join(', ') + ' or ' + list.at(-1) : list[0];
  const pair = ([i, j]) => `${you(g.rows[i])} and ${they(g.cols[j])} (${signed(g.payoffs[i][j][0])}, ${signed(g.payoffs[i][j][1])})`;
  const stable = (i, j) => g.nash?.some(([a, b]) => a === i && b === j);
  const cell = (i, j) => {
    const c = g.payoffs[i][j];
    if (!c) return '<td class="g-missing">—</td>';
    const mine = g.best_responses.me?.[j]?.includes(i), theirs = g.best_responses.them?.[i]?.includes(j);
    return `<td${stable(i, j) ? ' class="g-stable"' : ''}><span${mine ? ' class="g-best"' : ''}>${esc(signed(c[0]))}</span>, <span${theirs ? ' class="g-best"' : ''}>${c[1] === null ? '?' : esc(signed(c[1]))}</span>${stable(i, j) ? '<small>stable</small>' : ''}</td>`;
  };
  const facts = [];
  if (g.nash) facts.push(g.nash.length
    ? `Stable outcome${g.nash.length > 1 ? 's' : ''}: ${g.nash.map(pair).join('; ')}. Neither side gains by changing alone.`
    : 'No stable outcome among single choices: wherever you both land, one side gains by switching.');
  if (g.mixed) facts.push(`Balance point: if ${they(g.cols[0])} ${percent(g.mixed.them[0])} of the time, both your choices average ${signed(g.mixed.payoffs[0])}; if you ${g.rows[0]} ${percent(g.mixed.me[0])} of the time, both of ${g.possessive} choices average ${signed(g.mixed.payoffs[1])} for ${g.object}.`);
  if (g.best_responses.me) facts.push(...g.cols.map((c, j) => `If ${they(c)}, your higher payoff comes from ${or(g.best_responses.me[j].map(i => g.rows[i]))} (${signed(g.payoffs[g.best_responses.me[j][0]][j][0])}).`));
  if (g.best_responses.them) facts.push(...g.rows.map((r, i) => `If ${you(r)}, ${g.possessive} higher payoff comes from ${or(g.best_responses.them[i].map(j => g.cols[j]))} (${signed(g.payoffs[i][g.best_responses.them[i][0]][1])}).`));
  if (g.dominant.me !== null) facts.push(`“${g.rows[g.dominant.me]}” pays you more than your other choices whatever ${g.subject} does.`);
  if (g.dominant.them !== null) facts.push(`“${g.cols[g.dominant.them]}” pays ${g.object} more whatever you do.`);
  if (g.worst_case.me) facts.push(`Your worst case for each choice: ${g.rows.map((r, i) => `${r} ${signed(g.worst_case.me[i])}`).join(', ')}.`);
  if (g.expected) facts.push(`With your chances (${g.cols.map((c, j) => `${c} ${percent(g.beliefs[j])}`).join(', ')}), your average payoff: ${g.rows.map((r, i) => `${r} ${signed(g.expected[i])}`).join(', ')}.`);
  if (g.pareto?.length) facts.push(`Outcomes neither side can improve without costing the other: ${g.pareto.map(pair).join('; ')}.`);
  if (g.constant_sum) facts.push('Your payoffs and theirs always add up to the same total: one side’s gain is the other’s loss.');
  return `<section class="game" aria-label="Game between you and ${esc(g.player)}">
    <div class="table-wrap"><table class="g-table">
      <caption>First number: you. Second: ${esc(g.object)}. Underlined: the better reply to the other side’s choice. Boxed: stable outcomes.</caption>
      <thead><tr><th scope="col"></th>${g.cols.map(c => `<th scope="col">${esc(g.player)} ${esc(c)}</th>`).join('')}</tr></thead>
      <tbody>${g.rows.map((r, i) => `<tr><th scope="row">You ${esc(r)}</th>${g.cols.map((_, j) => cell(i, j)).join('')}</tr>`).join('')}</tbody>
    </table></div>
    ${facts.length ? `<ul class="g-facts">${facts.map(f => `<li>${esc(f)}</li>`).join('')}</ul>` : ''}
    ${last.complete && last.notes.length ? `<ul class="notes">${last.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    <p class="solo-note">${g.assumptions.map(esc).join(' ')}</p>
  </section>`;
}

function gameMissingHtml() {
  return `<div class="solo-msg"><ul>${[...last.missing, ...last.notes].map(m => `<li>${esc(m)}</li>`).join('')}</ul>
    <p class="muted">Write one line for each pair of choices, your payoff first: <code>If I leave and she stays: -3, 2</code></p></div>`;
}

function missingHtml(doc) {
  const options = doc.options || [];
  const open = options.filter(o => !o.outcomes?.length);
  return `<div class="solo-msg${last.partial ? ' partial' : ''}">
    ${last.partial
      ? '<p>The cards above cover the options that are complete. To finish:</p>'
      : options.length
      ? `<p>Found ${options.map(o => `<b>${esc(o.label)}</b>`).join(', ')}.</p>`
      : '<p>Name the choices, like “Should I take the job or stay?”, or start a line with an option and a colon.</p>'}
    <ul>${[...last.missing, ...last.notes].map(m => `<li>${esc(m)}</li>`).join('')}</ul>
    <p class="muted">Write each outcome as a chance, a few words and a signed score, for example <code>60% it goes well (+5)</code>. One option's chances must total 100%.</p>
    ${box.value.includes('__') ? '<p>Replace each <code>__</code> in the box with your numbers.</p>' : open.length ? '<button id="scaffold" type="button">Add a line for each option</button>' : ''}
  </div>`;
}

// Appends fill-in lines for options without numbers and selects the first blank; it never supplies numbers.
function scaffold() {
  const open = (last?.document.options || []).filter(o => !o.outcomes?.length);
  if (!open.length) return;
  const start = box.value.replace(/\s+$/, '').length;
  box.value = box.value.replace(/\s+$/, '') + '\n' + open.map(o => `${o.label}: __% it goes well (+__), __% it doesn't (-__)`).join('\n');
  const blank = box.value.indexOf('__', start);
  box.focus();
  box.setSelectionRange(blank, blank + 2);
  schedule();
}

async function save(button) {
  if (saving || !last?.complete || analyzedText !== box.value) return;
  const document = last.document, source = analyzedText;
  saving = true;
  button.disabled = true;
  try {
    saved = saved
      ? await post(`/nodes/${encodeURIComponent(saved.id)}/versions`, {document, source_version: saved.version})
      : await post('/nodes', {document});
    savedText = source;
  } catch (error) {
    button.disabled = false;
    button.insertAdjacentHTML('afterend', `<span class="solo-error">${esc(error.message)}</span>`);
  } finally {
    saving = false;
    if (last && analyzedText === box.value && savedText === source) render();
    else if (out.querySelector('#save')) out.querySelector('#save').disabled = false;
  }
}

box.addEventListener('input', schedule);
// While fill-in blanks remain, Tab moves to the next "__" instead of leaving the box.
box.addEventListener('keydown', event => {
  if (event.key !== 'Tab' || event.shiftKey || !box.value.includes('__')) return;
  const next = box.value.indexOf('__', box.selectionEnd), blank = next >= 0 ? next : box.value.indexOf('__');
  event.preventDefault();
  box.setSelectionRange(blank, blank + 2);
});
out.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (button?.id === 'scaffold') scaffold();
  if (button?.id === 'save') save(button);
});
window.addEventListener('beforeunload', event => {
  if (!box.value.trim() || box.value === savedText) return;
  event.preventDefault();
  event.returnValue = '';
});
window.addEventListener('pagehide', () => {
  clearTimeout(timer);
  ++seq;
  pending?.abort();
  pending = null;
});
autosize();
if (box.value.trim()) analyze();
