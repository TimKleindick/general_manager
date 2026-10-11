# Complete grouped calculations

An executor can calculate every observed group of an already bound direct query
sum requirement without enumerating its operand paths. It must select the
requirement and the exact query evidence explicitly:

```json
{
  "action": "calculate_groups",
  "requests": [
    {"requirement_id": "annual_actuals", "query_evidence_id": "task:query:2"},
    {"requirement_id": "annual_plans", "query_evidence_id": "task:query:3"}
  ]
}
```

Each requirement appears once and retains its immutable binding. Its sole
source must be an earlier query requirement of the same task, and the selected
query must already be linked to that source. The runtime chooses no query,
field, unit or binding. Deferred bindings require an explicit successful
`bind_calculation` first. An ordinary queried scalar only needs an aggregate
when a declared requirement requests one.

The root `data` array must be nonempty, have `complete=true` and `has_more=false`,
and have an integer `total_count` equal to the number of rows. Group keys use
the existing declared scalar fields and explicit UTC-year transformations.
Groups keep first-observed order, and their generated operands keep original
row order. Each row contributes exactly once to its observed group. Typed
group values retain the existing JSON identity rules, including null values.

Generated ordinary `calculate` actions pass the existing calculator and scope
validator. They preserve each group's query identity, numeric leaf paths and
explicit unit. Unknown units remain unknown; incompatible units inside one
group fail. All groups of all requests are staged together. An invalid final
group, a failing later request or an expired deadline commits no calculations.
Distinct declared requirements bound the request count, while complete observed
rows bound the number of groups. This action does not introduce parallel backend
execution; independent tool calls in one provider round retain their supplied
execution order.

Only direct `sum` reductions are supported. Empty queries, unbound or derived
requirements, multiple sources, conversion and cross-query bindings are
rejected. Other reductions remain available through their existing actions.
`calculate_batch` retains its explicit operands and existing atomic behavior.

The executor still selects final evidence through `complete`. Every required
group must be covered, and selected calculations and source scope are
recomputed rather than trusted. Synthesis receives the selected results and
their verified sources. It renders fulfilled components that the user requested,
distinguishes actuals, existing plans and forecasts, and applies relevant supplied
definitions without asking an already resolved method again. Missing values and
unresolved business conventions remain unresolved.

Offline scripted tests establish the sequence and validation behavior, including
nine group sums from three explicitly selected queries in one calculation
round. They do not establish live model quality, runtime improvement or a new
evaluation score. Those require separate measurements against the final source.
