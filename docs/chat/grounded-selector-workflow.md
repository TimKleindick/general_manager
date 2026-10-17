# Grounded identity questions before dependent reads

The planned read executor can pause on an ambiguous identity using a closed
`clarify_selector` action:

```json
{
  "action": "clarify_selector",
  "requirement_id": "identity",
  "language": "en",
  "selector": {"evidence_id": "identity-query", "field": "code"}
}
```

The requirement must be a query requirement of the current task. Its evidence
must be an actually linked current query with canonical call identity, matching
manager/tool provenance and the observed identity field (`id`, `code`, `name`
or `designation`). The existing renderer accepts 2–20 complete rows with exact
integer `total_count`, `complete=true`, `has_more=false` and distinct nonempty
labels. Languages are English, German and French. Every option comes from the
query. The action cannot add options, an answer, filters, arithmetic or claims.

The executor must first query the population relevant to the user's request.
The scheduler does not select matching rows from a broader saved population.
Choosing filters remains model work. Ambiguous identity fields, incomplete or
unlinked queries and foreign evidence are rejected through ordinary executor
feedback. The action grants no read, mutation or completion permission.

The task enters the private `awaiting_clarification` state. Its requirements
remain open, with `clarification_required` in the normal done event's unresolved
list and truthful coverage, including zero resolved tasks. Child questions pause
their owner; dependent roots do not run. The question is rendered directly and
does not require synthesizer claims or a falsely completed task.

Questions persist through the existing `tool_result.gm_clarification` seam using
version 2 and `kind=record_selector`. The metadata contains the exact original
user scope, question hash and query source witnesses. History ingestion verifies
the source hashes and re-renders all labels before admitting conversational
choice bindings. Missing or unsupported metadata cannot turn arbitrary concrete
assistant prose into a source-bound question. Existing version 1 analytical
questions and unannotated legacy history retain their existing behavior.

The next planner sees a source-bound question ID and must bind its choice
assessment to real user quotes. An answered same-scope selector cannot be asked
again. A quote is conversational choice, never record evidence: the executor
must re-query the current identity before reading related business data.

Question persistence fails closed on every ordinary adapter callback exception:
the scheduler audits and emits `provider_failed`, without a question or done.
Cancellation propagates. Deadline and provider failures keep their original
semantics; a rejected selector creates no waiting question. No schema-tool or
Python-entry default changes, arithmetic changes, Gold changes or confirmation
workflow changes are introduced.
