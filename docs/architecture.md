# Data and trust boundaries

HTTP input → deterministic validation → session/controller → deterministic validation → immutable encrypted node. Analysis reads the same frozen document. Each revision appends a new version; it never rewrites a historical document. Branch context captures the immediate parent, excluding recursively duplicated ancestry. Ancestors remain addressable by ID/version. Sessions capture the parent version when they begin.

The model owns wording only. Structured output cannot alter outcomes, probabilities, skips, or overrides. User text and recalled data are delimited untrusted input. A provider failure has no effect on calculations or persistence. Memory confirmation is separate from answering a question.

The cache hashes document content, upstream context and tier. Every request reruns routing before cache lookup. Higher-tier requests cannot reuse a lower-tier key. The cache and retrieval vectors are process-local and hold decrypted content in RAM; disk content is encrypted.

Simulation checks graph structure at the API boundary and local scope inside the engine. Two-node simulations require an exact parent-version relationship. Three or more child branches disable simulation. Simulation produces descriptive samples in original option order; no best-choice field exists.

Statistics use mutually exclusive exhaustive scenarios. Separate units stay separate until explicitly weighted. Missing fields produce null metrics and reasons; skipping does not turn unknown values into zero. Probability sums, bounds, finite values and input size are rechecked before calculations.
