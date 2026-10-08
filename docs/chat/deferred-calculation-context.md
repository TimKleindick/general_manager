# Deferred calculation work in executor context

An explicit calculation requirement with `binding=null` is valid pending work.
It must acquire an exact binding through `bind_calculation` before calculation
or completion. The executor context now includes `deferred_calculations` and
`deferred_calculation_context_version=gm.deferred-calculations/1` while such work
exists.

Each entry identifies the declared requirement and operation, the required
binding action, and earlier same-task query/calculation requirements with their
actually linked evidence IDs. These are possible source requirements, not
chosen or automatically compatible sources. `binding_validated=false` gives no
field, population, group, unit or completion authority. Full original evidence
remains in `task_evidence`; the executor must inspect it, gather missing reads,
choose the appropriate binding and pass the unchanged validator.

After binding, the executor still must request the declared calculation and
complete using eligible linked evidence covering every requirement. The runtime
does not auto-bind or calculate, select quantity fields or units, or prevent a
legitimate block. Tasks without explicit deferred binding receive the same
messages as before; legacy requirements without binding obligation remain
unchanged. This default context addition applies to planned Python, HTTP and
WebSocket paths through the shared scheduler and makes no new business claim.

The context clarifies available work. Scripted tests prove framework sequences;
improved model motivation or task quality requires a separate version-bound
live evaluation. Historical failures and Gold stay unchanged.
