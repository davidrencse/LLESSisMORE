# LLess

A single-user decision notebook that preserves inputs before outcomes and computes transparent statistical profiles. Options stay in input order. There is no choice ranking or recommendation output.

For compatibility, the Python import path remains `lifeev`, existing database filenames and keys, `LIFEEV_*` environment variables, and pinned browser localStorage data remain unchanged.

## Run locally

Python 3.10+ is required. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
.\run.ps1
```

Open http://127.0.0.1:8000. API schema: http://127.0.0.1:8000/docs.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Start with a blank decision or explicitly load illustrative inputs. Record each outcome's benefits **before costs**, mutually exclusive outcome probabilities, and money/time units. Weights define utility conversion, not currency. Save creates an immutable version; branching freezes the parent's version and inputs. Clarification answers autosave; skips and the three-round cap remain in provenance. Drafts can contain absent inputs, but invalid numeric values cannot be saved.

The guided form supports point and range probabilities, money/time/custom dimensions, resource costs, utility weights, bankroll, thresholds, black-swan flags, and correlations without editing JSON. Empty numbers remain unknown, including an unassessed black-swan flag. Range and probability-total feedback appears beside each option. Dimension names cannot contain periods because question paths use periods as separators.

During a question session, its input form is read-only. Use **Edit a separate draft** to make independent changes; the saved session remains resumable. Historical branching preserves the version being viewed. Revision saves carry a source version; a concurrent edit returns a conflict and leaves the local form intact. Inspect the latest version before reapplying those edits.

## The text box (/)

http://127.0.0.1:8000 is a single text box. Type the decision and, for each option, a line of chances and outcomes; results appear underneath as you type, with no button. Text without numbers lists what is missing and can add fill-in lines with `__` blanks (Tab jumps to the next blank); it never supplies numbers. Scores may be `(+5)`, `+5`, `= 5` or a bare number ending the clause; amounts accept `$`, thousands commas and `k`/`m` (`$2.5k`). When some options are complete and others are not, cards appear for the complete ones and the rest are listed as unfinished; a clause with a chance that could not be read is quoted back rather than dropped. Each card lists the scenarios as read from your text. **Save** is the only write and needs every option complete; a saved decision opens in the full workspace at `/workspace.html?open=<id>`. The workspace (tree, inputs, statistics, provenance, Monte Carlo) is at `/workspace.html`.

### Two-player games

When another person's choice changes your outcome, type one line per pair of choices with your payoff first and theirs second:

```text
If I leave and she stays: -3, 2
If I leave and she moves on: 4, 3
If I stay and she stays: 5, 5
If I stay and she moves on: -6, 1
70% she stays
30% she moves on
```

The page shows the payoff table and states, from the payoffs as typed: each side's better reply to each of the other's choices, stable outcomes (pure-strategy Nash equilibria), choices that pay more whatever the other side does (strict dominance), your worst case per choice, outcomes neither side can improve without costing the other (Pareto efficiency), whether the game is constant-sum, and for 2×2 games the mixed balance point. Optional `70% she stays` lines give your average payoff per choice under your own chances. It assumes one simultaneous choice with known payoffs, never adds your payoffs to theirs, and does not say which choice to make. Missing lines or second payoffs are listed, never filled. `lifeev/game.py` does this arithmetic inside `/api/quick`; nothing else runs.

## Workspace dashboard

The workspace also opens on **Dashboard** with a text box. Type the decision and, for each option, a line of chances and outcomes, then choose **Analyze** (or Ctrl+Enter):

```text
Should I take the new job or stay?
Take the job: 60% it goes well (+6), 40% it doesn't (-3)
Stay: 80% nothing changes (0), 20% I get restless (-2)
```

Lines can also start with `If I stay, …`, or put the option on its own line followed by one scenario per line. Outcomes need a sign, brackets, or `=`; `$` amounts set the scale to money, otherwise it is a score. When every option's chances total 100%, the dashboard appears straight away: one card per option in your order with average outcome, the most likely scenario, chance of ending below 0, typical swing, worst and best case. Text without numbers opens a prefilled chance/outcome grid and says what is missing; nothing is guessed. Nothing is saved until you choose **Save decision**, and the page warns before you leave with unsaved work.

`POST /api/quick` takes `{"text": ...}` or `{"document": ...}`. It only parses and runs the exact discrete statistics: no model call, question session, retrieval, compute-tier routing, cache or storage write. The quick path records each option's separate cost as 0 because its outcomes already include costs; the dashboard lists that assumption. Decisions that need probability ranges, several dimensions or correlations use the full **Inputs** form. `GET /api/audit` accepts optional `action` and `limit` filters; the status bar asks only for the latest snapshot.

## Structure

| Module | Responsibility |
| --- | --- |
| `skeleton.py` | Deterministic validation, required context, prioritized gaps |
| `controller.py` | Resumable questions, round limits, memory review |
| `model_adapter.py` | Optional wording-only structured model output |
| `controls.py` | Tier routing, upstream-aware cache, incremental retrieval |
| `stats.py` | Exact discrete statistics and explicitly scoped proxies |
| `simulation.py` | Seeded sampling of one node or a directly linked pair |
| `storage.py` | Encrypted SQLite payloads, immutable revisions and statistics, snapshots |
| `maintenance.py` | Explicit usefulness-based archival and snapshot restoration |
| `api.py` | Validated HTTP boundary and local web client |
| `web/` | Dependency-free browser interface |

## Statistical interpretation

EV, quantiles and lower-tail CVaR describe the **entered discrete scenarios**. They do not estimate how complete the scenario model is. CVaR includes fractional probability at the tail boundary. Probability ranges must admit a normalized distribution; feasible EV bounds optimize within those ranges. These bounds and heuristic completeness scores are **not statistical confidence intervals**.

Skipped inputs remain visible. Heuristic sensitivity envelopes may widen to modeled support; unknown events outside that support remain unbounded, and no claimed coverage percentage is attached. Exact discrete tails remain exact for supplied scenarios even when Monte Carlo is disabled. This intentionally avoids relabeling exact arithmetic as an approximation or inventing calibrated confidence.

Kelly is a theoretical log-growth exposure calculation under proportional scaling and no leverage. The fraction scales the specified exposure; it is not automatically a bankroll allocation or a recommendation. Half and quarter values are arithmetic variants. Ruin/drawdown metrics are one-period proxies, not eventual ruin. Correlation-adjusted variance requires explicitly specified monetary standard-deviation exposure; arbitrary correlations cannot determine joint discrete tails. Pair simulation uses explicitly disclosed independence and rejects nonzero correlations. Analytical probability chains use log space and a depth cap of 12.

## Models and retrieval

The application runs offline by default. Questions come from the skeleton. Retrieval uses deterministic hashed vectors and lexical pair reranking, **not** a pretrained embedding model or a cross-encoder. Provider callbacks in `Retriever` allow replacements. The index is incremental in memory and reconstructed after restart; persisted node data stays encrypted.

Search and question sessions share one retrieval index. Synchronization streams database rows and tracks memory revisions with fixed-size content digests. Search retains a bounded shortlist plus explicit pins. The workspace requests `/api/nodes?summary=true` and `/api/sessions?summary=true` for navigation metadata; full records are loaded when opened. Omitting `summary` preserves the full listing API. Persistent index memory and navigation metadata still scale with the saved corpus.

Optional compatible model endpoint configuration:

```text
LIFEEV_MODEL_BASE_URL=https://your-provider.example/v1
LIFEEV_MODEL_API_KEY=...
LIFEEV_MODEL_SMALL=...
LIFEEV_MODEL_MEDIUM=...
LIFEEV_MODEL_LARGE=...
```

For clarification questions, configured models receive the current decision, bounded retrieved context and confirmed memory. They may only rephrase skeleton questions. Field IDs, counts and structured output are validated; errors fall back to deterministic wording. Routing tiers are a policy, not a claim that a provider was called. No provider was contacted in automated tests. Provisional memory is excluded from future context, and conflicting confirmations require reviewed entry IDs.

## Describe a decision or add details

The Inputs view includes a **Describe your decision** panel. Preview extraction, inspect current and proposed values, then apply the reviewed proposal to the draft. Save a version to persist it, or select **Explore missing inputs** to start the existing bounded clarification flow. Later messages can add options, costs and outcomes; each message is retained verbatim in `intake_notes`. Existing saved versions are never modified by extraction. Use the existing Branch action to attach a new decision to a historical parent; intake does not invent branch relationships.

Offline extraction recognizes phrases such as `I am deciding between Stay and Study` and labeled details, one per line:

```text
Title: Career decision
Options: Stay; Study
Bankroll: money=20000
Cost Study: money=5000
Outcome Study / Upside: probability=30%, money=15000
Horizon: 2 years
```

Arbitrary prose that the parser cannot structure stays in notes. Missing probabilities, outcomes, costs and status quo remain gaps rather than fabricated defaults. A structurally invalid extraction retains the original draft and records the message with a warning.

Selecting **Use configured AI provider** sends the message and current draft to the configured endpoint using the first available large, medium or small model. This optional extraction path proposes structured updates, preserves existing list order and omitted details, and cannot change skips, overrides or memory. Local validation checks every response; failures fall back to offline extraction. Validation checks structure, not factual accuracy: the user must review AI proposals. The provider path is covered with mocked responses, not a live provider evaluation.

`POST /api/intake/preview` accepts `text`, an optional `document`, and boolean `use_model` (default false). It returns a proposed document, changes, gaps, method, provenance and warnings without writing decisions or sessions. Messages are limited to 20,000 characters, notes to 100 entries / 100,000 characters, and concurrent intake to one request. Unapplied proposals and draft edits are page-local; only saved versions or started question sessions persist across reloads.

## Persistence and deployment

Default state is in ignored `data/`. Fernet authenticated encryption covers sensitive SQLite payloads and entire snapshot files. SQLite IDs, version numbers, file sizes and timing metadata are not hidden. The default local key is `data/.key`; back it up separately. This protects database copies without the key, not an attacker who can read the entire user account. Prefer `LIFEEV_ENCRYPTION_KEY` supplied by a secret manager for deployed use. Losing the key makes data unrecoverable.

Daily snapshots occur before the first write of the day and through an hourly scheduler while the app runs. No process can take scheduled snapshots while stopped. Archive takes a pre-prune snapshot; restoration is available for seven days. Hard purge requires an expired recovery window and a successful new snapshot. Snapshots are retained until explicitly removed, so retention is at least seven days. No automatic destructive pruning is enabled.

Resource limits protect the single-process application:

- The analysis cache uses a thread-safe LRU capped at 128 entries and 16 MiB of serialized payloads. Oversized results are returned normally but not cached; digest/container overhead is separately bounded by the entry cap.
- Simulation rejects requests exceeding 3,000,000 summarized values or a conservative 100,000 output distribution points before sampling. Its budgets include all option marginals and Cartesian option pairs across three output dimensions. Requested samples are never silently reduced. One simulation may run at a time; another receives HTTP 429 with `Retry-After`.
- Missing or already archived nodes create no backup on an archive request. A real archive still requires a successful pre-prune backup.
- Snapshots are capped at 256 files, 512 MiB combined, and 64 MiB per encrypted file. Manual snapshots have a persistent 60-second cooldown (HTTP 429). Capacity failures return HTTP 507 and abort archive/purge operations. No existing backups are automatically removed. Move older backups to secure offline storage, preserving their retention and encryption key, before retrying. Store constructor limits can be adjusted deliberately if needed.
- Reads and startup remain available if a daily backup cannot fit; the server logs the failure. Writes requiring a new daily backup fail until capacity is restored. Limits are intended for the supported single-process deployment, not independent workers sharing the same data directory.

The default server binds loopback only. Remote exposure requires a TLS reverse proxy, an explicit allowed-host configuration and authentication. Set `LIFEEV_API_TOKEN` to require Bearer auth on API routes. TLS is not provided by the development server. Do not expose it directly to the Internet. The API blocks cross-origin writes and sets a restrictive content security policy.

This is a runnable local MVP. Learned retrieval, external-provider quality/cost evaluation, automatic usefulness-based archival, and production deployment operations remain extension points. These limitations are not represented as completed services.

Run a single application process. Session and memory mutations are serialized within its Store instance; separate worker processes would require database-level session compare-and-swap. Immutable node revisions already check their source version inside a database transaction. The `lifeev-discrete-v2` engine fixes Kelly solvent-range edge cases and preserves tiny quantile/CVaR boundary mass; previous snapshots retain their original results and calculation version.

Optional browser-client DOM tests (Node 22+): run npm ci, then npm test. These check form behavior and draft state without verifying visual layout. Snapshot recovery is available through lifeev.maintenance.restore_snapshot(snapshot_path, destination, key); it only creates a new destination and never overwrites an existing database.


A TLS reverse-proxy template is in `deploy/Caddyfile`. Supply your domain, set `LIFEEV_ALLOWED_HOSTS` and `LIFEEV_API_TOKEN`, and provision Caddy separately. It has not been deployed. `requirements.lock` records the dependency versions used for verification.

The `lless-discrete-v3` engine adds per-outcome EV contributions, loss decomposition, cost break-even, and proportional-mixture probability sensitivity. The dashboard exposes temporary cost and probability scenarios without changing saved inputs or historical snapshots. Probability sensitivity respects all entered bounds; these bounds are feasibility constraints, not confidence intervals. Tail tables show signed lower-tail CVaR and positive loss magnitudes separately.
"# LLESSisMORE" 
