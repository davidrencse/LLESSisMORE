"use strict";
// Shared by both pages and loaded first: formatting helpers and the result cards.
// Options are drawn on one shared scale; none is styled or sorted as preferred.
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'})[c]);
const numberFormatter = new Intl.NumberFormat(undefined, {maximumFractionDigits: 2});
const percentFormatter = new Intl.NumberFormat(undefined, {style: 'percent', maximumFractionDigits: 2});
const fmt = v => typeof v === 'number' ? numberFormatter.format(v) : 'Unknown';
const percent = v => Number.isFinite(v)
  ? (v > 0 && v < 0.0001 ? '<0.01%' : percentFormatter.format(v)) : 'Unknown';
const signed = v => typeof v === 'number' ? (v > 0 ? '+' : '') + fmt(v) : 'Unknown';

// Returns the cards for `options` in input order, measured on dimension `dim`.
// opts.empty: text for an option without enough numbers; opts.reasons: also list why;
// opts.scenarios: accessible name for the scenario list.
function cardsHtml(options, dim, opts = {}) {
  const rows = options.map(o => ({o, m: o.dimensions?.[dim], d: o.analysis_details?.[dim]}));
  const points = rows.flatMap(({m, d}) => [d?.risk.minimum, d?.risk.maximum, m?.p05, m?.p95, m?.ev]).filter(Number.isFinite);
  let lo = Math.min(0, ...points), hi = Math.max(0, ...points);
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = v => 12 + (v - lo) / (hi - lo) * 276;
  const card = ({o, m, d}) => {
    const title = `<h3>${esc(o.label || 'Unnamed option')}</h3>`;
    if (!m || !d) return `<article class="dcard">${title}<p class="muted">${esc(opts.empty)}</p>${opts.reasons
      ? `<ul class="notes">${Object.values(o.unavailable || {}).slice(0, 3).map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}</article>`;
    // The single highest-probability scenario; the first one wins a tie.
    const likely = d.contributions.reduce((best, c) => (!best || c.probability > best.probability ? c : best), null);
    const {minimum, maximum} = d.risk;
    return `<article class="dcard">
      ${title}
      <p class="dcard-ev"><span>Average outcome</span><b>${esc(signed(m.ev))}</b><small>${esc(dim)}</small></p>
      ${likely ? `<p class="dcard-likely">Most likely: ${esc(likely.label)}, ${esc(percent(likely.probability))} chance, ${esc(signed(likely.net_value))}</p>` : ''}
      <svg class="dcard-range" viewBox="0 0 300 40" role="img" aria-label="Outcomes from ${esc(fmt(minimum))} to ${esc(fmt(maximum))}, average ${esc(fmt(m.ev))}">
        <line class="axis" x1="12" x2="288" y1="20" y2="20"/>
        ${lo < 0 && hi > 0 ? `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="4" y2="36"/>` : ''}
        <line class="span" x1="${x(minimum)}" x2="${x(maximum)}" y1="20" y2="20"/>
        <line class="mid" x1="${x(m.p05)}" x2="${x(m.p95)}" y1="20" y2="20"/>
        <circle cx="${x(m.ev)}" cy="20" r="5"/>
      </svg>
      <dl class="dcard-stats">
        <div><dt>Chance of ending below 0</dt><dd>${esc(percent(m.loss_probability))}</dd></div>
        <div><dt>Typical swing</dt><dd>±${esc(fmt(m.standard_deviation))}</dd></div>
        <div><dt>Worst case</dt><dd>${esc(signed(minimum))}</dd></div>
        <div><dt>Best case</dt><dd>${esc(signed(maximum))}</dd></div>
      </dl>
      <ol class="dcard-scen"${opts.scenarios ? ` aria-label="${esc(opts.scenarios)}"` : ''}>${d.contributions.map(c => `<li><span class="sl">${esc(c.label)}</span><span class="sb"><i data-w="${c.probability}"></i></span><span class="sp">${esc(percent(c.probability))}</span><span class="sv">${esc(signed(c.net_value))}</span></li>`).join('')}</ol>
    </article>`;
  };
  return `<div class="dcards">${rows.map(card).join('')}</div>`;
}

// Scenario bars carry their share in data-w (CSP forbids style attributes in markup).
function sizeBars(root) {
  root.querySelectorAll('.dcard-scen i[data-w]').forEach(i => { i.style.width = Number(i.dataset.w) * 100 + '%'; });
}
