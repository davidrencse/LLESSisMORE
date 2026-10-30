"use strict";
const $ = (s) => document.querySelector(s);
let apiToken = "";
let nodes = [],
  node = null,
  doc = { title: "", options: [] },
  tab = "dashboard",
  session = null,
  branch = null,
  stats = null,
  pins = [],
  sessionList = [];
try { const saved = JSON.parse(localStorage.getItem("lifeev-pins") || "[]"); if (Array.isArray(saved)) pins = saved.filter(p => typeof p === "string"); } catch {}
async function api(path, body) {
  const r = await fetch("/api" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      ...(apiToken ? { Authorization: "Bearer " + apiToken } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let d = await r.json();
  if (!r.ok)
    throw Error(
      typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail || d),
    );
  return d;
}
function notice(text, error = false) {
  $("#notice").className = text ? "notice " + (error ? "error" : "success") : "";
  $("#notice").textContent = text;
}
async function run(fn) {
  try {
    await fn();
  } catch (e) {
    notice(e.message, true);
  }
}
// run() for a button: disabled and aria-busy until it finishes (unless the click re-rendered it away).
function withBusy(button, fn) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  return run(fn).finally(() => {
    if (button.isConnected) { button.disabled = false; button.removeAttribute("aria-busy"); }
  });
}
function activeSessionOpen() { return session && session.status !== "finalized"; }
const analyzeDraft = () => api("/analyze", { document: doc, force_tier: $("#tier").value || null });
const when = (v) =>
  v
    ? new Date(v).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
    : "—";
const shortId = (id) => String(id ?? "").slice(0, 8);
const dl = (pairs) =>
  pairs.map(([k, v, cls]) => `<dt>${esc(k)}</dt><dd${cls ? ` class="${cls}"` : ""}>${v}</dd>`).join("");
function setTab(t) {
  tab = t;
  document
    .querySelectorAll("[data-tab]")
    .forEach((b) => b.setAttribute("aria-selected", b.dataset.tab === t));
  render();
}
async function refresh() {
  // Independent requests run together; rendering happens afterwards in a fixed order.
  [nodes, sessionList] = await Promise.all([
    api("/nodes?summary=true&include_archived=" + $("#archived").checked),
    api("/sessions?summary=true"),
    refreshStatus(),
  ]);
  const sessions = sessionList;
  renderTree();
  renderSystem();
  $("#sessions").innerHTML =
    '<option value="">Choose a saved session</option>' +
    sessions
      .map(
        (s) =>
          `<option value="${esc(s.id)}">${esc(s.document?.title || "Untitled")} · ${esc(s.status)}</option>`,
      )
      .join("");
}
function renderTree() {
  const ids = new Set(nodes.map(n => n.id));
  const childrenByParent = new Map();
  for (const n of nodes) {
    if (!childrenByParent.has(n.parent_id)) childrenByParent.set(n.parent_id, []);
    childrenByParent.get(n.parent_id).push(n);
  }
  const seen = new Set();
  function item(n) {
    if (seen.has(n.id)) return "";
    seen.add(n.id);
    const children = childrenByParent.get(n.id) || [];
    return `<li><div class="node ${node?.id === n.id ? "selected" : ""}"><button data-open="${esc(n.id)}" title="${esc(n.document.title || "Untitled decision")}">${esc(n.document.title || "Untitled decision")}</button><small>v${n.version}${n.archived_at ? " · archived" : ""}${n.parent_id ? " · branch" : ""}</small><button class="pin" data-pin="${esc(n.id)}" aria-pressed="${pins.includes(n.id)}" title="Pinned decisions stay in related-context search">${pins.includes(n.id) ? "Unpin" : "Pin"}</button></div>${children.length ? '<ul>' + children.map(item).join('') + '</ul>' : ''}</li>`;
  }
  const roots = nodes.filter(n => !n.parent_id || !ids.has(n.parent_id));
  $('#tree').innerHTML = nodes.length ? '<ul>' + roots.map(item).join('') + nodes.filter(n => !seen.has(n.id)).map(item).join('') + '</ul>' : '<p>Saved decisions appear here. Save a draft to start the tree.</p>';
  $('#tree-count').textContent = nodes.length || '';
}
// Sidebar counts come from the lists the server already returned.
function renderSystem() {
  const active = nodes.filter(n => !n.archived_at);
  $("#system").innerHTML = dl([
    ["Decisions", active.length],
    ["Saved versions", active.reduce((sum, n) => sum + (n.version || 0), 0)],
    ["Open sessions", sessionList.filter(s => s.status !== "finalized").length],
    ["Pinned context", pins.filter(p => nodes.some(n => n.id === p)).length],
  ]);
}
// Short labels for the server's method strings; the exact string stays in the tooltip.
const STATUS_LABELS = { "deterministic offline controller": "offline rules", "hashed vector + lexical rerank fallback": "offline fallback" };
const plainStatus = (value) =>
  value ? `<b title="${esc(value)}">${esc(STATUS_LABELS[value] || value)}</b>` : "<b>—</b>";
async function refreshStatus() {
  let health = null, events = null, failure = "";
  try { [health, events] = await Promise.all([api("/health"), api("/audit?action=snapshot&limit=1")]); }
  catch (error) { failure = error instanceof TypeError ? "Unreachable" : /token/i.test(error.message) ? "Token required" : "Error"; }
  const online = health?.status === "ok";
  const snapshot = Array.isArray(events) ? events.filter(e => e.action === "snapshot").at(-1) : undefined;
  $("#server-status").innerHTML = "<dl>" + dl([
    ["Server", `<span class="dot${online ? "" : " off"}"></span>${online ? "Online" : esc(failure || "Unknown")}`],
    ["Host", esc(location.host || "local file")],
    ["Mode", esc(health?.mode || "—")],
  ]) + "</dl>";
  $("#statusbar").innerHTML = [
    `API <b>${online ? "ok" : esc((failure || "unavailable").toLowerCase())}</b>`,
    `Questions ${plainStatus(health?.agent)}`,
    `Model provider <b>${esc(health?.model_provider || "—")}</b>`,
    `Search ${plainStatus(health?.retrieval)}`,
    `Last snapshot <b>${snapshot ? esc(when(snapshot.created_at) + " · " + (snapshot.reason || "manual")) : events ? "none recorded" : "—"}</b>`,
  ].map(s => `<span>${s}</span>`).join("");
}
function draftCounts() {
  const form = $("#guided");
  if (form && !$("#json")) {
    const options = [...form.querySelectorAll(".option")];
    return {
      options: options.length,
      scenarios: form.querySelectorAll(".outcome").length,
      dimensions: form.querySelectorAll(".dimension-row").length,
      names: [...form.querySelectorAll(".dimension-row")].map(row => row.dataset.dimension),
      complete: options.filter(o => {
        const p = [...o.querySelectorAll("[name=probability]")];
        return p.length && p.every(i => i.value !== "") && sumsToOne(p.reduce((s, i) => s + Number(i.value), 0));
      }).length,
    };
  }
  const options = doc.options || [];
  return {
    options: options.length,
    scenarios: options.reduce((s, o) => s + (o.outcomes?.length || 0), 0),
    dimensions: decisionDimensions(doc).length,
    names: decisionDimensions(doc),
    complete: options.filter(o => o.outcomes?.length && o.outcomes.every(r => typeof r.probability === "number") && sumsToOne(o.outcomes.reduce((s, r) => s + r.probability, 0))).length,
  };
}
function recordState() {
  if (activeSessionOpen()) return ["Session", `Round ${session.round} of ${session.round_cap}`];
  if (node) return [node.archived_at ? "Archived" : "Saved", "Immutable version"];
  if (branch) return ["Branch draft", `From version ${branch.version}`];
  return ["Draft", "Not saved"];
}
function renderOverview() {
  const c = draftCounts(), [state, stateNote] = recordState();
  $("#overview").hidden = c.options === 0 && !node;
  // Nothing to reset on an untouched draft, and the heading already says "New decision".
  $("#new").hidden = c.options === 0 && !node && !branch && !session && !quick;
  const cell = (label, value, note) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}<small>${esc(note)}</small></dd></div>`;
  $("#overview").innerHTML = [
    cell("Options", c.options, "in your order"),
    cell("Scenarios", c.scenarios, "across all options"),
    cell("Dimensions", c.dimensions, c.names.join(", ") || "none yet"),
    cell("Probabilities", `${c.complete} of ${c.options}`, "options sum to 1"),
    cell("Version", node ? "v" + node.version : "—", node ? "of " + (nodes.find(n => n.id === node.id)?.version ?? node.version) : "unsaved"),
    cell("State", state, stateNote),
  ].join("");
}
function renderRecord() {
  const active = activeSessionOpen();
  const latest = node && nodes.find(n => n.id === node.id)?.version;
  $("#record").innerHTML = active
    ? dl([["Session ID", esc(session.id), "id"], ["Type", "Question session"], ["Round", `${session.round} of ${session.round_cap}`], ["Status", esc(session.status)], ["Parent", session.parent_id ? `${esc(shortId(session.parent_id))} · v${esc(session.parent_version)}` : "None"]])
    : node
      ? dl([["Decision ID", esc(node.id), "id"], ["Type", node.parent_id ? "Branch" : "Root decision"], ["Version", `${node.version}${latest ? " of " + latest : ""}`], ["Status", node.archived_at ? "Archived" : "Saved"], ["Parent", node.parent_id ? `${esc(shortId(node.parent_id))} · v${esc(node.parent_version)}` : "None"], ["Created", esc(when(node.created_at))]])
      : dl([["Decision ID", "Assigned on first save", "id"], ["Type", branch ? "Branch draft" : "Draft"], ["Parent", branch ? `${esc(shortId(branch.id))} · v${esc(branch.version)}` : "None"]]);
}
function renderChrome() {
  renderOverview();
  renderRecord();
}
async function openNode(id, version) {
  node = await api("/nodes/" + id + (version ? "?version=" + version : ""));
  resetComposer();
  doc = structuredClone(node.document);
  stats = null;
  session = null;
  branch = null;
  renderTree();
  render();
  renderQuestions();
}
function render() {
  document.body.dataset.tab = tab;
  $("#title").textContent = doc.title || "New decision";
  $("#subtitle").textContent = node
    ? `Version ${node.version} · ${node.archived_at ? "Archived · " : ""}${node.parent_id ? "Branch with frozen parent context" : "Root decision"} · ${when(node.created_at)}`
    : branch
      ? "New branch · Parent context will be frozen when saved"
      : "Draft · not saved yet";
  renderChrome();
  if (tab === "dashboard") {
    renderDashboard();
    return;
  }
  if (tab === "statistics") {
    renderStats();
    return;
  }
  if (tab === "audit") {
    renderAudit();
    return;
  }
  renderInputs();
}
async function saveDoc() {
  node = await api(
    node ? "/nodes/" + node.id + "/versions" : "/nodes",
    { document: doc, parent_id: branch?.id, parent_version: branch?.version, source_version: node?.version },
  );
  branch = null;
  await refresh();
  render();
  notice(`Saved as version ${node.version}. Saved versions never change.`);
}
async function startSession() {
  session = await api("/sessions", {
    document: doc,
    parent_id: branch?.id,
    parent_version: branch?.version,
    pins,
    force_tier: $("#tier").value || null,
  });
  setTab("inputs");
  renderQuestions();
  await refresh();
}
async function calculate() {
  updateDoc();
  stats = await analyzeDraft();
  setTab("statistics");
}
function chart(rows) {
  const valid = rows.filter((r) => r.m),
    vs = valid.flatMap((r) => [r.m.p05, r.m.p95, r.m.ev]);
  if (!vs.length)
    return '<p class="muted">Complete probabilities, costs and outcome values are needed for this chart.</p>';
  let lo = Math.min(...vs),
    hi = Math.max(...vs);
  if (lo === hi) {
    lo -= 1;
    hi += 1;
  }
  const x = (v) => 200 + ((v - lo) / (hi - lo)) * 390,
    h = valid.length * 68 + 42;
  const zero = lo < 0 && hi > 0 ? `<line class="zero" x1="${x(0)}" x2="${x(0)}" y1="8" y2="${h - 25}" stroke="#c9c9c6" stroke-dasharray="3 3"/><text x="${x(0)}" text-anchor="middle" y="${h - 6}">0</text>` : "";
  return `<svg viewBox="0 0 640 ${h}" role="img" aria-label="Net-value quantile intervals and expected values"><line x1="200" x2="590" y1="${h - 25}" y2="${h - 25}" stroke="#0a0a0a"/>${zero}${valid
    .map((r, i) => {
      let y = 25 + i * 68;
      return `<text x="0" y="${y + 4}" fill="#0a0a0a">${esc(String(r.o.label).slice(0, 24))}</text><line x1="${x(r.m.p05)}" x2="${x(r.m.p95)}" y1="${y}" y2="${y}" stroke="#0a0a0a" stroke-width="2"/><line x1="${x(r.m.p05)}" x2="${x(r.m.p05)}" y1="${y - 6}" y2="${y + 6}" stroke="#0a0a0a"/><line x1="${x(r.m.p95)}" x2="${x(r.m.p95)}" y1="${y - 6}" y2="${y + 6}" stroke="#0a0a0a"/><circle cx="${x(r.m.ev)}" cy="${y}" r="5" fill="#ffffff" stroke="#0a0a0a" stroke-width="2"/><text x="200" y="${y + 24}">P05 ${fmt(r.m.p05)} · EV ${fmt(r.m.ev)} · P95 ${fmt(r.m.p95)}</text>`;
    })
    .join(
      "",
    )}<text x="200" y="${h - 6}">${fmt(lo)}</text><text x="590" text-anchor="end" y="${h - 6}">${fmt(hi)}</text></svg>`;
}
async function renderAudit() {
  const latest = node && (nodes.find(n => n.id === node.id)?.version || node.version);
  $("#content").innerHTML =
    `<div class="panel"><h2>Version provenance</h2>${node ? `<dl class="provenance">${dl([["Decision ID", esc(node.id)], ["Immutable version", `${node.version} of ${latest}`], ["Created", `${esc(when(node.created_at))} <small>${esc(node.created_at)}</small>`], ["Parent", node.parent_id ? `${esc(node.parent_id)} · version ${esc(node.parent_version ?? "—")}` : "None (root decision)"], ["Archived", node.archived_at ? esc(when(node.archived_at)) : "No"]])}</dl><div class="fields"><label>Inspect version<input id="version" type="number" min="1" max="${latest}" value="${node.version}"></label></div><div class="actions"><button id="load-version">Load version</button><button id="saved-stats">Calculate statistics for version ${node.version}</button></div><details><summary>Frozen parent context</summary><pre>${esc(JSON.stringify(node.frozen_parent, null, 2))}</pre></details>` : '<p class="muted">Save this decision to preserve its first immutable version.</p>'}<details><summary>Frozen statistics at save time</summary><pre>${esc(JSON.stringify(node?.statistics_snapshot ?? null, null, 2))}</pre></details><details open><summary>Recorded document</summary><pre>${esc(JSON.stringify(doc, null, 2))}</pre></details><h3>Workspace audit</h3><p class="muted">The log records decision saves and revisions, saved sessions, profiles and memory, archives, restores, purges and snapshots. Snapshot files are encrypted with the same key as the database.</p><div class="actions"><button id="audit-log">Load audit log</button><button id="snapshot">Create encrypted snapshot</button></div><div id="audit-data" aria-live="polite"></div></div>`;
}
function auditTable(events) {
  const label = id => {
    const n = nodes.find(x => x.id === id);
    return id ? (n ? `${n.document.title || "Untitled decision"} · ${shortId(id)}` : shortId(id)) : "—";
  };
  const recent = events.slice().reverse().slice(0, 200);
  return analysisTable(`Audit log · ${events.length} events${events.length > 200 ? ", newest 200 shown" : ""}`, ["Event", "Time", "Record", "Reason"],
    recent.map(e => [String(e.action || "").replaceAll("_", " "), when(e.created_at), label(e.id), e.reason || "—"]));
}
function renderQuestions() {
  const qs = $("#questions");
  if (!session) {
    qs.innerHTML =
      '<p class="muted">No active question session. Choose “Explore missing inputs” to begin.</p>';
    renderChrome();
    return;
  }
  qs.innerHTML = `<p class="muted">Round ${session.round} / ${session.round_cap} · ${esc(session.status)}<br>Question source: ${esc(JSON.stringify(session.question_provenance || session.question_method || "deterministic offline fallback"))}.</p>${(session.questions || []).map((q, i) => `<div class="question"><label for="answer-${i}">${esc(q.question)}<small>${esc(q.field)} · ${esc(q.impact)}</small></label><textarea id="answer-${i}" placeholder="Enter text, a number, or JSON"></textarea><div class="actions"><button data-answer="${i}">Save answer</button><button data-skip="${i}">Skip explicitly</button></div></div>`).join("")}<button id="finalize" class="primary" ${session.status !== "ready" ? "disabled" : String()}>Save session as decision</button><details><summary>Session history & skips</summary><pre>${esc(JSON.stringify(session.history, null, 2))}</pre></details>`;
  renderChrome();
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled || b.closest(".workspace-tools") || b.closest('.intake-panel') || b.closest('.dash-root')) return;
  withBusy(b, async () => {
    if (b.dataset.tab) {
      if (tab === "inputs") updateDoc();
      setTab(b.dataset.tab);
    } else if (b.dataset.open) await openNode(b.dataset.open);
    else if (b.dataset.pin) {
      pins = pins.includes(b.dataset.pin)
        ? pins.filter((p) => p !== b.dataset.pin)
        : [...pins, b.dataset.pin];
      localStorage.setItem("lifeev-pins", JSON.stringify(pins));
      renderTree();
      renderSystem();
    } else if (b.dataset.acceptGap !== undefined) {
      const field = b.dataset.acceptGap;
      doc = await api("/overrides", { document: doc, field, accepted: true });
      stats = await analyzeDraft();
      renderStats();
      renderChrome();
      notice(`“${field}” is recorded as an accepted unknown in this draft. Save a version to keep it.`);
    } else if (b.dataset.outcome !== undefined) {
      updateDoc();
      (doc.options[+b.dataset.outcome].outcomes ||= []).push({ label: "", values: {} });
      renderInputs();
    } else if (b.dataset.remove) {
      updateDoc();
      const [i, j] = b.dataset.remove.split(",").map(Number);
      doc.options[i].outcomes.splice(j, 1);
      renderInputs();
    } else if (b.dataset.deleteOption !== undefined) {
      updateDoc();
      doc.options.splice(+b.dataset.deleteOption, 1);
      renderInputs();
    } else if (b.dataset.removeCorrelation !== undefined) {
      updateDoc();
      doc.correlations?.splice(+b.dataset.removeCorrelation, 1);
      renderInputs();
    } else if (b.dataset.answer !== undefined || b.dataset.skip !== undefined) {
      const i = +(b.dataset.answer ?? b.dataset.skip),
        raw = $("#answer-" + i).value;
      let value = raw;
      try {
        value = JSON.parse(raw);
      } catch {}
      session = await api("/sessions/" + session.id + "/answers", {
        field: session.questions[i].field,
        value,
        skip: b.dataset.skip !== undefined,
      });
      doc = structuredClone(session.document);
      render();
      renderQuestions();
      await refresh();
    } else
      switch (b.id) {
        case "new":
          node = null;
          branch = null;
          session = null;
          stats = null;
          doc = { title: "", options: [] };
          resetComposer();
          setTab("dashboard");
          renderQuestions();
          renderTree();
          break;
        case "example":
          doc = await api("/example");
          node = null;
          renderTree();
          branch = null;
          stats = null;
          session = null;
          renderQuestions();
          setTab("dashboard");
          notice(
            "Illustrative example loaded. These inputs are editable and have not been saved.",
          );
          break;
        case "leave-session":
          session = null;
          node = null;
          renderInputs(); renderQuestions();
          notice("Editing a separate draft. The saved question session remains available to resume.");
          break;
        case "add-dimension": {
          updateDoc();
          const name = $("#dimension-name").value.trim();
          if (!name || name.includes('.') || ['__proto__','constructor','prototype'].includes(name)) throw Error('Use a dimension name without periods or reserved object names.');
          const dimensions = decisionDimensions(doc);
          if (dimensions.includes(name)) throw Error('That dimension already exists.');
          doc.dimensions = [...dimensions, name];
          renderInputs();
          break;
        }
        case "add-correlation":
          updateDoc();
          (doc.correlations ||= []).push({});
          renderInputs();
          $("#correlation-mode").closest('details').open = true;
          break;
        case "add-option":
          updateDoc();
          doc.options.push({
            id: crypto.randomUUID(),
            label: "",
            status_quo: false,
            costs: {},
            outcomes: ["Downside", "Base", "Upside"].map(label => ({label, values: {}})),
          });
          renderInputs();
          break;
        case "save":
          updateDoc();
          await saveDoc();
          break;
        case "analyze":
          await calculate();
          break;
        case "calculate-current":
          stats = await analyzeDraft();
          renderStats();
          break;
        case "start":
          updateDoc();
          await startSession();
          break;
        case "resume":
          if ($("#sessions").value) {
            session = await api("/sessions/" + $("#sessions").value);
            doc = structuredClone(session.document);
            branch = session.parent_id ? {id:session.parent_id, version:session.parent_version} : null;
            node = null;
            stats = null;
            setTab("inputs");
            renderQuestions();
          }
          break;
        case "finalize":
          node = await api("/sessions/" + session.id + "/finalize", {});
          doc = structuredClone(node.document);
          session = null;
          branch = null;
          await refresh();
          render();
          renderQuestions();
          notice("Session saved with explicit uncertainty provenance.");
          break;
        case "branch":
          updateDoc();
          branch = {id: node.id, version: node.version};
          node = null;
          doc.title += " · branch";
          stats = null;
          setTab("inputs");
          break;
        case "archive":
          await api(
            "/nodes/" + node.id + (node.archived_at ? "/restore" : "/archive"),
            {},
          );
          node = await api("/nodes/" + node.id);
          await refresh();
          render();
          break;
        case "load-version":
          await openNode(node.id, +$("#version").value);
          break;
        case "audit-log":
          $("#audit-data").innerHTML = auditTable(await api("/audit"));
          break;
        case "snapshot":
          await api("/snapshots", {});
          await refreshStatus();
          notice("Encrypted snapshot created.");
          break;
        case "saved-stats":
          stats = await api(
            `/nodes/${encodeURIComponent(node.id)}/stats?version=${node.version}` +
              ($("#tier").value ? "&force_tier=" + encodeURIComponent($("#tier").value) : ""),
          );
          setTab("statistics");
          notice(`Statistics calculated from saved version ${node.version}, not from unsaved edits.`);
          break;
        case "retrieve": {
          const r = await api("/retrieve", { query: $("#search").value, pins });
          $("#retrieval").innerHTML = r.length
            ? `<ol class="matches">${r.map(m => {
                const n = nodes.find(x => x.id === m.id);
                return `<li><b>${esc(n ? n.document.title || "Untitled decision" : "Memory entry")}</b><small>${m.pinned ? "Pinned · " : ""}${m.reranking_method === "lexical_pair_fallback" ? "term overlap" : "rerank score"} ${fmt(m.score)} · vector ${fmt(m.vector_score)}</small>${n ? `<button data-open="${esc(m.id)}">Open</button>` : ""}</li>`;
              }).join("")}</ol><p class="muted">${esc(r[0].embedding_method)} · ${esc(r[0].reranking_method)}</p>`
            : '<p class="muted">No recorded context matches this search.</p>';
          break;
        }
      }
  });
});
$("#archived").addEventListener("change", () => run(refresh));
$("#tier").addEventListener("change", () => {
  stats = null;
  if (tab === "statistics") renderStats();
  if (tab === "dashboard") renderDashboard();
});
// On phones the tree starts collapsed so the composer is in the first screen.
if (window.matchMedia?.("(max-width: 760px)").matches) $("#side-tree").open = false;
render();
renderQuestions();
run(refresh);
// The text page links saved decisions here as /workspace.html?open=<id>.
const openFromLink = new URLSearchParams(location.search).get("open");
if (openFromLink) run(() => openNode(openFromLink));

$("#token").addEventListener("change", () => {
  apiToken = $("#token").value;
  run(refresh);
});


document.addEventListener('submit', event => event.preventDefault());

