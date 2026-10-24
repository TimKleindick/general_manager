# Chat business definitions and conversational choices

The same numerical question can have different legitimate meanings: gross or
net revenue, a calendar or fiscal year, known values or all competitors, pieces
or mass. Define application conventions once, preserve the user's scope and ask
only for choices that remain open. Tool/schema evidence still supplies record
facts. See [architecture](chat.md) for the complete request flow.

## Supply business definitions

`GENERAL_MANAGER["CHAT"]["system_prompt"]` adds application context. Describe
what a metric means, which manager/fields it applies to, relevant calendars,
handling of missing values and genuine defaults. Avoid rules that falsely imply
all data is known or override native permissions. For example:

```python
GENERAL_MANAGER["CHAT"]["system_prompt"] = """
For quantity comparisons, use pieces unless the user asks for another unit.
Our reporting year is a calendar year. Missing prices remain unknown, not zero.
A quantity forecast does not require a future price assumption.
""".strip()
```

This text is a business convention for the example application, not an automatic
framework default. A different application's definitions must match its actual
data and reporting rules. A definition for one metric does not resolve another.
Storage units and available years alone do not select a requested unit or horizon.

With nonempty configured context, a read planner returns a contextual envelope
containing `plan`, `choices`, `clarification_requests` and `definition_bindings`,
including on the first turn. Each definition binding quotes the actual application
context and latest user scope for a closed analytical topic. The validator checks
source membership, unique topics and consistency with open questions. Applicability
is still a model judgment; a valid quote alone does not prove the interpretation.

`plan_request()` defaults to configured application context. Custom callers may
select an explicit `configured_context`; the response must follow the published
schema for that input. Without application context or previous runtime questions,
the ordinary first-turn plan remains valid. Explicit write requests retain their
mutation intent and confirmation path.

## Preserve original quote sources

The planner's `user_sources` contains each eligible original USER message's
`index` and `content`. Indices refer to the complete conversation context,
counting all roles, rather than a USER-only ordinal. A definition or new question
must quote the latest eligible user source. A reply to an earlier question must
quote a real user message after that question.

Do not rewrite indices or invent source text in a custom adapter. Standalone
callers without any user message receive the original request as a user source;
a mismatching current user message is not silently replaced. Tool observations,
assistant objectives and application instructions cannot impersonate user quotes.

## Ask analytical questions

The analytical clarification branch accepts a closed JSON shape:

```json
{
  "clarification": {
    "language": "en",
    "requirements": ["forecast_method", "reporting_currency"]
  }
}
```

Supported topics are `criterion`, `metric`, `horizon`, `population`,
`customer_identity`, `record_selector`, `unit`, `forecast_method`, `future_pricing`
and `reporting_currency`. Languages are `en`, `de` and `fr`. The runtime renders
fixed questions. This branch accepts no arbitrary question prose, numbers,
entity names, factual claims or evidence IDs. Mixed answers and questions belong
to the grounded answer branch and still need eligible evidence.

Use `criterion` for the missing standard of an evaluative question and `metric`
for the missing measurement of an already defined analysis. Ask for future
pricing only if it affects the requested metric; a quantity forecast does not
need it. Definitions may resolve analytical topics, but cannot decide concrete
customer/record identity or create mutation permission.

Persisted question metadata binds the exact rendered assistant question, topic,
original user request and history position. The next planner classifies every
relevant question as `answered`, `open` or `conflicting`, with `same_scope`,
`changed_scope` or `unrelated` applicability. It must use eligible original user
quotes. Partial replies do not automatically close a choice.

An answered same-scope topic cannot be asked again under a new question ID. A
changed entity, period or analysis may need a new source-bound choice. Validated
choice context reaches execution and synthesis; synthesis cannot reopen a topic
outside its permitted schema. A rejected response uses the existing bounded
fallback and does not create another round/deadline allowance.

## Ask concrete record questions

When a query finds ambiguous records, `clarify_selector` selects one current
same-task query requirement and its actually linked evidence. The renderer
requires 2–20 complete rows, an exact integer `total_count`, `complete=true`,
`has_more=false` and distinct nonempty labels from a supported identity field.
Every option comes from that query. The executor must first query the relevant
population; the scheduler does not filter a broader saved population for it.

The task waits for clarification rather than pretending to complete. Dependent
work pauses and the terminal coverage lists `clarification_required`, including
when no task resolved. A failed question-persistence callback produces a failure
rather than publishing an unbound question. User selection is conversational
scope; the next executor re-queries the current identity before dependent reads.

Only exact supported runtime question renderings/metadata are restored from
history. Quoted examples, arbitrary assistant prose or summarized-away choices
are not reconstructed by assumption. Decisions remain turn-local; the persisted
question is not a cross-conversation cache of permissions or facts.

## Population and dates

An unqualified ranking with a clear metric, period and entity uses accessible
records of that entity. Explicit current and unchanged prior user filters remain
binding. That rule does not resolve an ambiguous metric or entity. Named targets
require eligible identity-query evidence, including a correctly empty result.
Explanatory comparison entities do not replace selected result entities.

Explicit periods and later corrections take precedence over a completed-calendar-
year default. The current year remains queryable. If the supplied business clock
shows an included calendar year is still running, identify it as incomplete.
Keep application fiscal calendars distinct from calendar-year conventions.

## Validate changes

Review definitions, exact source roles, question rendering and multi-turn scope
with deterministic tests. Then evaluate representative whole conversations with
the actual provider/model and source revision. Structural validation cannot prove
that a model interprets a definition or partial reply correctly. Keep historical
requests and scores unchanged; see [evaluation concepts](chat_evaluation.md) and
[the evaluation how-to](../howto/run_chat_evals.md).
