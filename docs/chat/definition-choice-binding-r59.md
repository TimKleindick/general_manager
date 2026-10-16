# Application meanings and first-turn clarification

When `GENERAL_MANAGER.CHAT.system_prompt` supplies application context, read
planning uses the contextual envelope on the first turn as well as after a
clarification. The envelope contains `plan`, `choices`, `clarification_requests`
and `definition_bindings`. Each definition binding names a closed analytical
topic and quotes both the actual application context and the latest user scope.
The planner assesses applicability. The runtime checks source membership,
unique topics and consistency: a definition-resolved topic cannot also be an
open clarification. Record selection and customer identity cannot be resolved
by these application-meaning bindings.

A first concrete record ambiguity may be discovered only by the executor's
current query. Version 2 therefore retains the existing query-witness selector
path until a record-selector question has occurred. This is explicit in
`query_witness_record_selector_allowed`; it grants no analytical default or
identity fact. The actual complete-query witness, options and scope checks
still belong to the existing selector owner. Once that question exists, its
source-bound choices and answered-question restrictions remain authoritative.

This closes a gap where a first-turn schema-only clarification plan could be
accepted without any bound assessment of already supplied business meanings.
For example, an application-defined quantity forecast and plan comparison
should lead to the corresponding data work when it matches the request. A
definition for a different metric does not decide the user's choice. Genuine
ambiguity, conflicting definitions and unresolved units remain valid reasons
to ask. No language-specific keyword router infers these meanings.

Native storage units or available year ranges alone do not choose the user's
reporting measure or horizon; an actual applicable request convention is needed.

The same immutable choice context constrains synthesis. Its version 2 binds
the exact configured application context by hash; a changed context invalidates
the binding. A rejected repeated question uses the existing bounded synthesis
fallback, with the existing deadline and round budget. Definition interpretation
does not supply record facts, evidence, concrete identity choices or mutation
permission. Semantic applicability remains a model assessment and must still
be evaluated against real questions and evidence.

## Entry-point migration

`plan_request` defaults to the configured `system_prompt`; supplying its
`configured_context` explicitly selects that exact source. With nonempty context,
custom planner providers must return the contextual envelope even without prior
questions. `choices` is empty if no prior runtime question exists;
`definition_bindings` and `clarification_requests` may be empty when appropriate.
The published request schema is authoritative. The standalone Python entry point
adds the original request as a user quote source when no user message was supplied.
Existing user history is not rewritten to hide a mismatching request.

With empty application context, the previous first-turn plan schema remains;
the previous contextual envelope is still required after a runtime question.
Explicit write requests keep their existing mutation handoff. No new model call,
automatic JSON repair or transport retry is introduced. Offline infrastructure
probe providers emit the new envelope where requested; their replies are not
model-quality measurements.

## Nested executor actions

Executor requests include a nested conversion-binding example serialized by
the existing `CalculationBinding` and `ConversionBinding` types. Every field and
identifier in this example is a placeholder, not observed evidence. Replace it
with the current task's observed paths and exact schema witnesses. Binding,
snapshot, source and completion validators remain authoritative.

Malformed action JSON is rejected. Feedback includes the parser's message and
line, column and character position without echoing the rejected input. The
next already-budgeted round must emit valid JSON itself. Duplicate keys,
trailing content and invalid action contracts remain rejected.

## Evaluation reference 1.14

The current public catalog, oracle, CLI and harness selectors default to 1.14.
The default references imported by adjudication/scoring retain the selected
version in requests and extraction metadata; a missing or different extraction
version fails validation. Explicit 1.10, 1.11, 1.12 and 1.13 selectors retain all
149 original contracts and blind extraction shapes. Saved results are not
rescored or replaced.

Only E086 business semantics change: its unchanged question asks for projects
using Copper and supplied through parts by supplier Nord. Neither the question
nor visible application definitions require supplier approval. Reference 1.14
therefore requires the actual supplier/part/project/material relation without
an implicit approval filter. Approval is an obligation only when explicitly
requested or specified by an applicable visible business rule. The versioned
correction records the question, visible rule and expected selection together;
the historical catalog bytes remain intact. Wrong suppliers, materials,
projects, additional narrowing and missing evidence still fail. Previous
approval-required references remain strict and unchanged.

For an explicitly approval-restricted reference question or visible rule, the
oracle's reviewed case declaration `supplier_approval_required=True` retains the
approval predicate. It accepts an exact boolean, never inferred supplier status
or a language keyword. The current E086 declaration has no such restriction.
Changing a real catalog case still requires the normal versioned catalog review;
counterfactual unit controls do not change its frozen question or source rows.

E072 inherits the separately reviewed positive-material extraction binding from
1.13. This revision alone makes no live pass, latency or aggregate-rate claim.
