# Executor feedback and forecast clarification

After a JSON action is rejected, its structured validator feedback remains in
subsequent executor inputs while the executor reads schema or query tools. A tool
result, including a successful or cached result, does not correct that action.
The next JSON action clears the old feedback when accepted or replaces it with
the new rejection. This changes no evidence eligibility, binding authority,
completion rule, retry count, cycle stop, or phase budget.

`query.filters` contains the direct fields of the advertised root filter input.
For example, use `filters={"name": "Apollo"}`. The equivalent explicit root
argument is `arguments={"filter": {"name": "Apollo"}}`. Use one form at a time;
`filters={"filter": {"name": "Apollo"}}` is not an extra supported wrapper.
Nested filters still require the exact exposed input type. When a root has a
custom filter argument name, `arguments` must use that advertised name.

The public query tool still allows omitted `filters`. The Python `query` entry
point retains its required `filters` keyword; use `filters={}` with explicit
root `arguments`. No default or malformed-input normalization has changed.

Structured analytical clarification now also supports `forecast_method`,
`future_pricing`, and `reporting_currency` in English, German, and French.
These topics preserve the specific missing input instead of rendering a generic
criterion or unit question. Select only inputs that materially affect the
requested metric and remain unresolved by the user or an applicable definition.
A quantity forecast does not require future price assumptions. Topics do not
authorize a forecast, price assumption, currency conversion, or record claim.

The strict clarification response shape and source-bound choice context remain
the same. Existing topics and persisted questions remain valid. Newly rendered
questions use the same exact-text, role, user-quote, temporal and scope bindings;
an answered same-scope topic cannot be asked again under a new question ID.
Applications that enumerate allowed clarification topics outside the published
schema must add these three keys. No application-supplied free-text question or
result claim is accepted by this branch.
