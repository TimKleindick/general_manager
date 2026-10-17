# Source quote indices and ranked-claim extraction

This follow-up clarifies two existing contracts from
[definition and choice binding](definition-choice-binding-r59.md). It does not
change the business meaning of a result or the authority of a source quote.

## Discovering the exact USER source

When a read planner requires the contextual envelope, its reference data now
publishes `user_sources`, the same representation stored in validated choice
context. Each entry contains an original USER message's `index` and `content`.
The index is its zero-based position in `conversation_context`, counting every
message role. It is not a USER-only ordinal.

For a visible system message followed by the current USER question, the USER
source index is `1`. A new definition binding or clarification must quote that
latest USER source. A decision about an earlier clarification must quote an
eligible USER message after the question it answers. Earlier identical question
text does not make an earlier index eligible as the latest source.

The existing removal of the separately supplied configured application context
happens before source indices are published. Historical schema projection keeps
message positions. The source helper publishes original USER text, and the
validator continues to check the actual original messages. Displayed indices
are source metadata, not evidence about records or business values.

The owning quote validator now returns the exact rejected JSON path, such as
`$.definition_bindings[0].scope_quote.index`, and the eligible USER indices.
An incorrect quote at a valid index instead identifies `.scope_quote.quote`.
The public `invalid_plan` error and existing attempt budgets remain unchanged.
No model output is renumbered or repaired automatically.

The existing defaults remain: a nonempty application definition source or
prior runtime clarification requires the read envelope; an ordinary first
read with neither retains the plain-plan contract. Standalone Python callers
without any USER message receive the existing appended original-request USER
source. A mismatching current USER message remains invalid. Write intent and
confirmation continue through their existing workflow.

Source binding proves that a model used an eligible quote. It does not prove
that a quoted storage unit resolves the requested reporting unit, or that an
application definition applies semantically to the user's task.

## Extracting a visibly asserted single winner

The blind judge already compares requested ranked results independently of
their factual values. Newly built reference `1.14` requests now clarify that an
explicit single winner or top-one claim in prose asserts a one-item
`ranked_ids` order. An ordered list or table still supplies visible order
without requiring a ranking word.

A mere mention, membership claim or unordered collection of names does not
assert rank. It keeps absent ranking support. Wrong winners, wrong order,
extra identities and unsupported claims remain exactly as asserted. The judge
must not infer a sort from tool values or fill an omitted order from the
reference. The deterministic oracle, scoring comparisons and expected facts
are unchanged.

The new field description is part of the hashed adjudication request. Only a
validated `1.14` request with that exact description selects the matching new
ranking paragraph. The remaining system instruction is unchanged. References
`1.10` through `1.13` retain their exact field descriptions and system text.
An archived `1.14` request with its original description also retains its
original system text under the new code. A non-ranking request keeps that
legacy system text.

Rebuilding an adjudication request produces a new request hash. It does not
rewrite an earlier request, judge response or score. Offline contract tests and
owner probes establish the structural behavior; they do not establish a new
model accuracy result. Any new model execution needs its own reviewed source,
selection and safe start admission.
