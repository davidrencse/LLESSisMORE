"use strict";
let intakeProposal = null;

function renderIntake(disabled) {
  return `<section class="intake-panel" aria-labelledby="intake-heading">
    <h3 id="intake-heading">Describe your decision</h3>
    <p class="muted">Start in your own words, or add details to this draft. Review the proposed changes before applying them. Missing numbers stay unknown.</p>
    <label for="intake-text">Decision or additional details</label>
    <textarea id="intake-text" rows="4" maxlength="20000" ${disabled?'disabled':''} placeholder="I am deciding between staying in my job and studying full time."></textarea>
    <details><summary>Examples the offline parser understands</summary><p>Use one detail per line. Match option names when adding costs or outcomes.</p><pre>Options: Stay; Study
Horizon: 2 years
Bankroll: money=20000
Cost Study: money=5000
Outcome Study / Upside: probability=0.3, money=15000</pre><p class="muted">Other text is retained as context notes. Numeric extraction uses explicit labels; a configured AI model can interpret broader phrasing.</p></details>
    <label class="check"><input id="intake-use-model" type="checkbox" ${disabled?'disabled':''}>Use configured AI provider for this message</label>
    <p class="muted">When selected, your message and current draft are sent to your configured provider. Without a provider, extraction stays offline.</p>
    <button id="intake-preview" type="button" ${disabled?'disabled':''}>Preview extracted details</button>
    <div id="intake-review" aria-live="polite"></div>
    ${doc.intake_notes?.length ? `<details><summary>Recorded intake notes (${doc.intake_notes.length})</summary><pre>${esc(JSON.stringify(doc.intake_notes,null,2))}</pre></details>`:''}
  </section>`;
}

function intakeContext() { return JSON.stringify({doc,node:node?.id,version:node?.version,session:session?.id,branch}); }

document.addEventListener('click', event => {
  const button = event.target.closest('.intake-panel button');
  if (!button || button.disabled) return;
  withBusy(button, async () => {
    if (button.id === 'intake-preview') {
      updateDoc();
      const base = intakeContext(), target = $('#intake-review');
      intakeProposal = null;
      target.textContent = 'Extracting details for review…';
      let result;
      try { result = await api('/intake/preview',{text:$('#intake-text').value,document:doc,use_model:$('#intake-use-model').checked}); }
      catch (error) { if (target.isConnected) target.textContent='Extraction failed. Your text is still available; correct it or try again.'; throw error; }
      if (!target.isConnected) return;
      updateDoc();
      if (base !== intakeContext()) { target.textContent='The draft changed during extraction. Preview again to use the latest inputs.'; return; }
      intakeProposal = {base,result};
      target.innerHTML = `<h4>Review proposed changes</h4><p class="muted">Source: ${esc(result.method)}. These details have not been applied or saved.</p>
        <ul class="notes">${result.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>
        ${analysisTable('Proposed changes',['Field','Current value','Proposed value'],result.changes.map(c=>[c.field,JSON.stringify(c.before)??'Unknown',JSON.stringify(c.after)??'Unknown']))}
        <details><summary>Decision structure preview</summary><ul>${(result.document.options||[]).map(o=>`<li>${esc(o.label)}${o.status_quo?' (status quo)':''}<ul>${(o.outcomes||[]).map(r=>`<li>${esc(r.label)} — probability ${r.probability===undefined?'unknown':esc(r.probability)}</li>`).join('')}</ul></li>`).join('')}</ul></details>
        <p>${result.gaps.length} missing context fields remain. After applying, use “Explore missing inputs” to continue.</p>
        <button id="intake-apply" type="button">Apply reviewed changes to draft</button><button id="intake-discard" type="button">Discard proposal</button>`;
    } else if (button.id === 'intake-apply') {
      if (!intakeProposal) return;
      updateDoc();
      if (intakeContext() !== intakeProposal.base) throw Error('The draft changed. Preview extraction again before applying.');
      doc = structuredClone(intakeProposal.result.document);
      stats = null;
      render();
      notice('Reviewed details applied to the draft. Save a version to preserve them, or explore missing inputs.');
    } else if (button.id === 'intake-discard') {
      intakeProposal = null;
      $('#intake-review').textContent = 'Proposal discarded. Your draft is unchanged.';
    }
  });
});
