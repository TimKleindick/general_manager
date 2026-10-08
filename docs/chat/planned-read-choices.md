# Source-bound clarification decisions

When visible history includes a runtime-rendered analytical question, the planner
returns a contextual envelope with `plan`, `choices` and `clarification_requests`.
Ordinary plan JSON remains the inner plan. Explicit write requests retain their
existing mutation plan and confirmation workflow.

Each question ID binds its topic, exact assistant text and history position,
and the preceding user request's text and position. The planner assesses every
question as `answered`, `open` or `conflicting`, with applicability `same_scope`,
`changed_scope` or `unrelated`. Answered/conflicting states require verbatim quotes
from actual user messages after the question. Changed/unrelated scope requires a
quote from the latest user message. Empty or partial replies do not automatically
close a choice; assistant objectives and tool results cannot supply user quotes.

Clarification requests must name an open/conflicting question in the same scope.
A new topic or changed scope uses a new request bound to the latest user quote.
An answered topic in the current scope blocks repetition even under a different
question ID. A selected analytical basis can answer generic criterion and metric
questions; the runtime does not equate those topics or infer answers by keywords.
A new forecast, entity or period can still require its own choice.

The immutable validated `ChoiceContext` passes through `PlanningResult` and
`PreparedPlannedTurn` to execution and synthesis. Its digest binds the same user,
assistant and tool messages; application instructions are carried separately.
Synthesis exposes only requested remaining topics in its response schema and
checks them before rendering. Contradictory clarification uses the existing one
fallback with a stable rejection code. Planner repair retains its existing
correction and fallback. No new role, router, retry loop or phase budget is added.

## Python defaults and persistence

| Entry point | Default and migration |
| --- | --- |
| `plan_request` | Ordinary plan JSON without prior runtime questions; require a contextual envelope with them. The current reply must be in history and equal `user_text`. Explicit write requests retain ordinary mutation JSON. |
| `PreparedPlannedTurn.for_plan` | `choice_context=None` for plans without prior analytical questions. Callers supplying such history also supply a validated context. |
| `synthesize_answer` | `choice_context=None` is valid without prior runtime questions. With them, missing or changed context fails before a provider call. |
| `provider_messages_from_context` | Restores separately persisted assistant-question metadata; never promotes it to tool evidence. |

Published analytical questions store topics, an exact question hash and their user
request through the existing assistant `tool_result` JSON column, without a DB
migration. This is internal metadata, not a toolcall, factual evidence, mutation
permission or instruction. Legacy questions migrate only when the entire assistant
string equals a valid runtime rendering. Prefixes, quoted examples, user and tool
text do not become question sources. Concrete record selectors retain their
existing query-witness contract.

Decisions are turn-local, not a cross-conversation cache. Only published question
metadata is persisted; classifications remain in current provider/audit inputs.
Summarized-away questions or answers are never reconstructed by assumption.

## Verification boundary

Validation proves source roles, exact quotes, temporal order, complete decisions,
question/current-source identity and consistency of requested clarifications.
Semantic classification of a reply or scope change remains model work. A model
can still misclassify an answered or partial reply; offline tests do not establish
a live semantic pass. Wholecase E019/E042 testing requires independent review and
a version-bound release. Reference data and historical scores remain unchanged.
