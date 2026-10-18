# ADR 0010: Planned chat reads with validated evidence

## Status

Accepted. Extends [ADR 0008](0008-llm-chat-endpoint.md).

## Context

A provider can emit valid tool calls yet answer from an incomplete population,
repeat a missing business question or calculate with unrelated values. A simple
call loop cannot express which observations are required, which tasks depend on
others or why the final answer is eligible. Multiple transports and evaluators
also need to exercise the same implementation rather than diverging read loops.

## Decision

Run all new reads through one transport-neutral planned scheduler. Validate a
structured task/dependency plan before application reads. Each task declares
schema, path, query or calculation requirements and explicit completion criteria.
Use one `executor` role, with `planner`, `synthesizer` and `fallback` profiles;
complexity metadata does not choose an executor model. All configured roles in a
turn share one declared trust group.

Capture immutable evidence with task ownership, canonical call identity and
source provenance. Requirement links and current schema observations determine
eligibility. Accept completion only after validating the selected evidence;
keep partial coverage visible. Compute declared arithmetic in Python from
observed leaves and validated bindings, retaining population, units and lineage.

Retain source-bound conversational choices separately from factual evidence.
Record-selector questions use actual complete query options. Analytical replies
and application definitions may resolve scope but cannot supply record facts or
write permissions. Pass the validated choice context through planning, execution
and synthesis so an answered same-scope question is not simply asked again.

Use a shared 90-second default planning/evidence stage and a separate 30-second
synthesis stage. Count provider rounds and observed usage. Normal planned reads
have no fixed cap on successful rounds; explicit evaluator dispatch ceilings
remain separate. Stop proven repeated validation cycles while allowing corrected
work and discovery within the deadline. Retain bounded planner/synthesis repair.

The installed evaluator uses a thin, read-only adapter around this production
scheduler. Deterministic role pins are explicit test fixtures. It does not inject
queries, repair arguments or manufacture successful answers.

## Alternatives considered

- **Separate read loops for each transport/evaluator:** rejected because they
  would measure or enforce different product behavior.
- **Route by a model's complexity label:** rejected because one executor contract
  and explicit role configuration are easier to reason about and validate.
- **Use a successful tool call as completion:** rejected because discovery,
  partial pages and incompatible schemas need not satisfy the user's task.
- **Allow model arithmetic without source bindings:** rejected because correct
  numerical output alone cannot establish units, population or provenance.
- **Treat a chosen analytical scope as authorization:** rejected because choices,
  native permissions and mutation confirmation have different owners.

## Consequences

Integrators must implement the structured role contracts when supplying custom
providers. Evidence validation adds a concrete boundary but cannot guarantee
semantic interpretation by a model. Plans and evidence remain turn-local and do
not resume after restart. Extended diagnostic deadlines or scripted passes must
not be presented as measured production performance or model accuracy.

See [architecture](../concepts/chat.md),
[evidence](../concepts/chat_evidence.md),
[definitions and choices](../concepts/chat_prompting.md) and
[evaluation](../concepts/chat_evaluation.md).
