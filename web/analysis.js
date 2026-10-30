"use strict";

function analysisTable(caption, headings, rows) {
  return `<div class="table-wrap"><table><caption>${esc(caption)}</caption><thead><tr>${headings.map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map((cell,i) => i === 0 ? `<th scope="row">${esc(cell)}</th>` : `<td>${esc(cell)}</td>`).join('')}</tr>`).join('') || `<tr><td colspan="${headings.length}">No complete outcome dimensions are available.</td></tr>`}</tbody></table></div>`;
}

function renderStats() {
  if (!stats) {
    $('#content').innerHTML = '<div class="panel empty"><h2>Explore the statistical profile.</h2><p class="muted">Calculate statistics from your recorded inputs. Unknown values remain visible.</p><button id="calculate-current" class="primary">Calculate statistics</button></div>';
    return;
  }
  const rows = stats.options.flatMap(o => Object.entries(o.dimensions).map(([dimension, metrics]) => ({o, dimension, metrics, detail:o.analysis_details?.[dimension]})));
  const dimensions = [...new Set(rows.map(row => row.dimension))];
  const overview = rows.map(({o,dimension,metrics:m}) => [o.label+' / '+dimension, ...['ev','median','standard_deviation','p05','p95'].map(key => fmt(m?.[key]))]);
  const tails = rows.map(({o,dimension,metrics:m,detail}) => [o.label+' / '+dimension, percent(m?.loss_probability), fmt(detail?.risk.conditional_loss), fmt(m?.expected_drawdown), fmt(m?.cvar_lower_05), fmt(m?.cvar_lower_01), percent(m?.threshold_probability)]);
  const gaps = stats.assessment?.gaps || [], skipped = stats.assessment?.skipped || [];
  $('#content').innerHTML = `<div class="panel analysis-panel">
    <h2>Statistical profile</h2><p class="muted">${esc(stats.method)}. Options remain in your input order. Values use each named dimension's units.</p>
    ${analysisTable('Distribution overview',['Option / dimension','Net EV','Median','Std. dev.','P05','P95'],overview)}
    <h3>Tail risk</h3><p class="muted">Conditional loss is the mean loss size when a loss occurs. Expected loss averages loss size over every outcome; both use positive magnitudes. CVaR is the signed average of the worst 5% or 1% of outcomes, with fractional probability at the boundary.</p>
    ${analysisTable('Loss and tail profile',['Option / dimension','P(loss)','Mean loss if loss','Expected loss','CVaR 5%','CVaR 1%','P(at/below threshold)'],tails)}
    <h3>Sensitivity & break-even</h3><p class="muted">Inspect one option and dimension at a time. Break-even means zero net EV, not a preference between choices. What-if changes below are temporary and are never saved.</p>
    <div class="fields analysis-selectors"><label>Inspect option<select id="analysis-option">${stats.options.map((o,i) => `<option value="${i}">${esc(o.label || 'Unnamed option')}</option>`).join('')}</select></label><label>Dimension<select id="analysis-dimension">${dimensions.map(d => `<option value="${esc(d)}">${esc(d)}</option>`).join('')}</select></label></div>
    <div id="analysis-detail"></div>
    <h3>Net-value intervals</h3><p class="muted">Each line spans P05 to P95 and its dot marks EV. These are distribution quantiles, not confidence intervals. Each dimension has its own axis.</p>
    <div class="chart-wrap">${dimensions.map(d => '<h4>'+esc(d)+'</h4>'+chart(rows.filter(r => r.dimension === d).map(r=>({o:r.o,d:r.dimension,m:r.metrics})))).join('')}</div>
    <h3>Uncertainty & missing context</h3><p class="muted">${esc(stats.confidence?.reason || 'No calibrated confidence model.')}</p>
    ${analysisTable('Probability-range EV bounds',['Option / dimension','Low EV','High EV'],rows.map(({o,dimension})=>[o.label+' / '+dimension,fmt(o.probability_ev_bounds?.[dimension]?.low),fmt(o.probability_ev_bounds?.[dimension]?.high)]))}
    <p class="muted">These are feasible bounds within entered probability ranges, not confidence intervals. They do not cover unmodeled events.</p>
    <p>${gaps.length} unresolved context fields; ${skipped.length} explicitly skipped fields.</p>
    ${gaps.length ? `<ul class="gap-list">${gaps.map(g=>`<li><span><code>${esc(g.field)}</code><br>${esc(g.question)}</span>${activeSessionOpen() ? '' : `<button data-accept-gap="${esc(g.field)}">Accept as unknown</button>`}</li>`).join('')}</ul>
    <p class="muted">Accepting records the field as an explicit skip with provenance in this draft. The value stays unknown and is never filled with zero.</p>` : ''}
    ${skipped.length ? `<details><summary>Skipped fields (${skipped.length})</summary><ul class="notes">${skipped.map(field=>`<li>${esc(field)}</li>`).join('')}</ul></details>` : ''}
    <details><summary>Assumptions and unavailable metrics</summary><ul class="notes">${(stats.assumptions||[]).map(a=>`<li>${esc(a)}</li>`).join('')}${stats.options.flatMap(o=>Object.entries(o.unavailable||{}).map(([key,reason])=>`<li>${esc(o.label)} / ${esc(key)}: ${esc(reason)}</li>`)).join('')}</ul></details>
    <details><summary>All metrics, computation version & routing</summary><pre>${esc(JSON.stringify(stats,null,2))}</pre></details>
  </div>`;
  $('#analysis-option').addEventListener('change', renderAnalysisDetail);
  $('#analysis-dimension').addEventListener('change', renderAnalysisDetail);
  renderAnalysisDetail();
}

function selectedAnalysis() {
  const option = stats?.options[Number($('#analysis-option')?.value)];
  const dimension = $('#analysis-dimension')?.value;
  return {option, dimension, metrics:option?.dimensions[dimension], detail:option?.analysis_details?.[dimension]};
}

function renderAnalysisDetail() {
  const {option,dimension,metrics,detail} = selectedAnalysis();
  if (!detail || !metrics) {
    $('#analysis-detail').innerHTML = '<p class="notice">Sensitivity requires explicit costs, outcome values and complete point probabilities for this dimension.</p>';
    return;
  }
  const bounds = detail.probability_sensitivity;
  $('#analysis-detail').innerHTML = `
    <h4>Cost break-even / ${esc(dimension)}</h4>
    ${analysisTable('Cost sensitivity in '+dimension,['Current cost','Total cost at zero EV','Additional cost to zero EV','EV change per cost unit'],[[fmt(detail.cost.current),fmt(detail.cost.break_even_total),fmt(detail.cost.additional_to_zero_ev),fmt(detail.cost.ev_change_per_cost_unit)]] )}
    <p class="muted">Outcome benefits and probabilities are held fixed. A negative additional-cost value means the cost must decrease to reach zero EV. A negative total break-even cost implies a subsidy under this model.</p>
    <label class="analysis-input">What-if additional cost (${esc(dimension)})<input id="analysis-extra-cost" type="number" step="any" value="0"></label>
    <div id="analysis-cost-preview" aria-live="polite"></div>
    <h4>Probability sensitivity</h4><p class="muted">Change one outcome's probability and rescale all other probabilities proportionally to their original mixture. The feasible range respects every entered probability bound. This is one sensitivity path, not the full EV envelope.</p>
    ${analysisTable('Probability break-even by scenario',['Scenario','Feasible probability','EV at low','EV at high','Probability at zero EV','Within bounds'],bounds.map(row=>[row.label,Number.isFinite(row.feasible_probability_low)?percent(row.feasible_probability_low)+' to '+percent(row.feasible_probability_high):'Unavailable',fmt(row.ev_at_feasible_low),fmt(row.ev_at_feasible_high),row.zero_ev_probability === null ? 'No unique feasible value' : percent(row.zero_ev_probability),row.break_even_within_bounds === null ? 'Unknown' : row.break_even_within_bounds ? 'Yes' : 'No']))}
    <details><summary>Probability sensitivity qualifications</summary><ul class="notes">${bounds.map(row=>`<li>${esc(row.label)}: ${esc(row.reason || 'Proportional-mixture calculation within recorded bounds.')}</li>`).join('')}</ul></details>
    <label>What-if scenario<select id="analysis-scenario">${bounds.map((row,i)=>`<option value="${i}">${esc(row.label)}</option>`).join('')}</select></label>
    <div id="analysis-probability-controls"></div>
    <h4>Outcome contributions to EV</h4>
    ${analysisTable('Outcome contributions in '+dimension,['Scenario','Probability','Net outcome','EV contribution','Black-swan flag'],detail.contributions.map(row=>[row.label,percent(row.probability),fmt(row.net_value),fmt(row.ev_contribution),row.black_swan === null?'Unassessed':row.black_swan?'Yes':'No']))}
    <p class="muted">Contributions sum to net EV. A black-swan flag identifies explicitly entered cases; it does not estimate unknown-event probability.</p>
    ${analysisTable('Additional tail measures in '+dimension,['Metric','Value'],[
      ['Gain probability',percent(detail.risk.gain_probability)],['Zero-change probability',percent(detail.risk.zero_probability)],
      ['Mean gain when gain occurs',fmt(detail.risk.conditional_gain)],['Downside deviation from zero',fmt(detail.risk.downside_deviation)],
      ['Minimum / maximum with positive probability',fmt(detail.risk.minimum)+' / '+fmt(detail.risk.maximum)],
      ['Explicit black-swan probability',percent(metrics.black_swan_probability)],['Black-swan EV contribution',fmt(metrics.black_swan_ev_contribution)],
      ['Maximum one-period loss',fmt(metrics.max_drawdown)],
    ])}
    <p class="muted">All tail figures describe the supplied one-period scenarios, not eventual ruin or peak-to-trough path drawdown.</p>`;
  $('#analysis-extra-cost').addEventListener('input', updateCostPreview);
  $('#analysis-scenario').addEventListener('change', renderProbabilityControls);
  updateCostPreview();
  renderProbabilityControls();
}

function updateCostPreview() {
  const {dimension, metrics, detail} = selectedAnalysis();
  const input = $('#analysis-extra-cost'), delta = Number(input.value);
  const shifted = detail.contributions.map(row=>row.net_value-delta);
  const ev = metrics.ev-delta, total = detail.cost.current+delta;
  if (input.value === '' || !Number.isFinite(delta) || !Number.isFinite(ev) || !Number.isFinite(total) || shifted.some(value=>!Number.isFinite(value))) {
    $('#analysis-cost-preview').textContent = 'Enter a finite cost change to inspect this scenario.'; return;
  }
  const loss = detail.contributions.reduce((sum,row,i)=>sum+(shifted[i]<0?row.probability:0),0);
  $('#analysis-cost-preview').innerHTML = analysisTable('Temporary cost scenario / '+dimension,['Total cost','Net EV','P(loss)','CVaR 5%'],[[fmt(total),fmt(ev),percent(loss),fmt(metrics.cvar_lower_05-delta)]]);
}

function renderProbabilityControls() {
  const {detail} = selectedAnalysis();
  const row = detail.probability_sensitivity[Number($('#analysis-scenario').value)];
  if (!row || !Number.isFinite(row.feasible_probability_low) || !Number.isFinite(row.feasible_probability_high) || row.probability === 1) {
    $('#analysis-probability-controls').innerHTML = '<p class="muted">No defined proportional remainder is available for this scenario.</p>'; return;
  }
  $('#analysis-probability-controls').innerHTML = `<label class="analysis-input">Scenario probability (0 to 1)<input id="analysis-probability" type="number" step="any" min="${row.feasible_probability_low}" max="${row.feasible_probability_high}" value="${row.probability}"></label><div id="analysis-probability-preview" aria-live="polite"></div>`;
  $('#analysis-probability').addEventListener('input', updateProbabilityPreview);
  updateProbabilityPreview();
}

function updateProbabilityPreview() {
  const {dimension,detail} = selectedAnalysis();
  const i = Number($('#analysis-scenario').value), row = detail.probability_sensitivity[i];
  const input = $('#analysis-probability'), probability = Number(input.value);
  if (input.value === '' || !Number.isFinite(probability) || probability < row.feasible_probability_low || probability > row.feasible_probability_high) {
    $('#analysis-probability-preview').textContent = 'Enter a probability within the feasible range: '+percent(row.feasible_probability_low)+' to '+percent(row.feasible_probability_high)+'.'; return;
  }
  const remainder = detail.contributions.reduce((sum,o,j)=>sum+(j===i?0:o.probability),0);
  const probabilities = detail.contributions.map((o,j)=>j===i?probability:(o.probability/remainder)*(1-probability));
  const ev = detail.contributions.reduce((sum,o,j)=>sum+o.net_value*probabilities[j],0);
  if (!Number.isFinite(ev)) {
    $('#analysis-probability-preview').textContent = 'This scenario exceeds finite numeric limits.'; return;
  }
  $('#analysis-probability-preview').innerHTML = `<p>Temporary net EV (${esc(dimension)}): <output id="probability-ev">${esc(fmt(ev))}</output></p>` + analysisTable('Rescaled probability mixture',['Scenario','Probability'],detail.contributions.map((o,j)=>[o.label,percent(probabilities[j])]));
}
