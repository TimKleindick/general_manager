# Planned Chat Orchestration

All new read requests use separate planning, evidence gathering, and grounded
synthesis through the existing HTTP, SSE, and WebSocket chat routes. Mutations continue through
the legacy allow-list and confirmation flow.

## Configure one local profile

Install the provider extra and expose the managers that the catalog names:

```bash
python -m pip install "GeneralManager[chat-ollama]"
```

In the Django settings module:

```python
GENERAL_MANAGER = {
    "CHAT": {
        "enabled": True,
        "provider": "general_manager.chat.providers.OllamaProvider",
        "provider_config": {"model": "gemma4:e4b"},
        "planned": {
            "enabled": True,
            "catalog": "myproject.chat.catalog.catalog",
        },
    }
}
```

With no `provider_profiles`, planned chat assigns the legacy provider to all
four roles through the implicit `default` profile. For separate role models,
use the explicit profile mapping in the [rollout how-to](../howto/run_chat_evals.md#5-roll-out-planned-chat-safely).

Create the catalog callable named by `planned.catalog`:

```python
# myproject/chat/catalog.py
def catalog():
    return {
        "PartManager": {
            "domain": "manufacturing",
            "aliases": ["part", "component"],
            "use_when": "The question concerns designed or purchased components.",
            "distinguish_from": ["MaterialManager"],
        },
        "MaterialManager": {
            "domain": "manufacturing materials",
            "aliases": ["material", "substance"],
            "use_when": "The question concerns material definitions.",
            "distinguish_from": ["PartManager"],
        },
    }
```

The manager names must already be present in the chat-exposed schema. The
catalog helps rank candidates; it does not grant visibility or bypass
permissions. Run `python manage.py check` before sending traffic.

## Send a read over SSE

The stream endpoint is a POST endpoint, so use a streaming client rather than
the browser's GET-only `EventSource` constructor:

```bash
curl -N -X POST http://localhost:8000/chat/stream/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -H 'Cookie: csrftoken=<csrf-token>' \
  -H 'X-CSRFToken: <csrf-token>' \
  --data '{"text":"Which parts use aluminum?"}'
```

A representative successful stream keeps the normal event vocabulary while
adding the owning task ID to actual tool events:

```text
data: {"type":"tool_call","task_id":"<task-id>","id":"<call-id>","name":"query","args":{...}}

data: {"type":"tool_result","task_id":"<task-id>","id":"<call-id>","name":"query","result":{...}}

data: {"type":"text_chunk","content":"Aluminum is used by ..."}

data: {"type":"done","usage":{"input_tokens":12,"output_tokens":8},"orchestration":{"status":"complete","coverage":{"resolved":1,"total":1},"unresolved":[]}}
```

Independent roots may produce partial coverage. In that case `done.orchestration.status`
is `partial`, `coverage.resolved` is less than `coverage.total`, and
`unresolved` lists only stable task IDs and reasons. If no root resolves, the
stream ends with one error event and no synthesized answer. The stable planned
error codes are `invalid_plan`, `manager_unresolved`, `dependency_blocked`,
`budget_exhausted`, `deadline_exceeded`, `provider_failed`, and
`synthesis_failed`; rate limiting also uses `rate_limited`.

HTTP returns the same ordered events in an `{events, answer}` JSON envelope,
and WebSocket clients receive the same planned event shapes. Planned execution
is held in memory for one request; disconnects and process restarts do not
resume it. See the [concept model](../concepts/chat_prompting.md#planned-orchestration-bounds-and-grounding)
and [API reference](../api/chat.md#planned-read-orchestration) for the bounds,
settings, signatures, and exact error messages.

## Planner contract and bounded repair

The planner receives one shared contract from `chat.planned.contract`: the JSON
schema, supported requirement kinds and calculation operations, routing rules,
and valid query, calculation, and evidence-backed clarification examples. The
strict validator uses that same vocabulary and routing derivation. Provider
requests continue to use ordinary JSON text; the provider interface does not
promise native schema enforcement.

For `schema`, `path`, and `query` requirements, `operation` must be `null`.
Calculation requirements must choose `count`, `sum`, `average`, `minimum`,
`maximum`, `difference`, `ratio`, or `percentage`. A task's `completion_criteria`
contains exactly its own requirement IDs, each once. `routing_features` follows
the task structure: dependencies, a calculation requirement, and more than one
query requirement imply `has_dependency`, `requires_calculation`, and
`multiple_queries`, respectively. Read plans retain the existing one-to-six
root limit and one-edge dependency depth; mutation plans contain no tasks.

If a planner response is rejected, the next attempt receives its unchanged text,
the precise JSON field path, and the expected form in untrusted reference data.
There is one planner correction and then one fallback attempt, within the same
round and deadline budgets. No invalid fields are silently normalized. Clients
still receive only the stable `invalid_plan` error when all attempts fail.

Clarification is not a separate intent or action. A clarification task must gather
relevant schema, path, or query evidence before the current executor and synthesis
flow can produce a grounded question. An empty-requirement task is structurally
accepted but cannot complete that evidence-based flow.

Each executor attempt also receives its task's prior tool calls and results as
paired provider-neutral messages. The call ID, tool name, structured result, and
order survive a provider or fallback change. Discovery results, schema reads
outside the plan, cached results, and tool errors remain available as operational
feedback. Only eligible evidence linked to the task can satisfy requirements or
support completion. Repeating the same validation diagnosis after correction
feedback stops a diagnosed error cycle, even when the rejected raw output differs.

This history lasts only for the task within its current turn. Existing deadline
and provider input limits remain in force, without truncating successful
results. A locally rejected call with non-JSON arguments is represented by an
untrusted rejection record containing its ID, name, and error, rather than a native
call with invented replacement arguments. Tool exceptions retain their sanitized
error codes.

The executor reference includes each requirement's complete validated description
alongside its kind and operation. Missing evidence directs the executor to gather
it with the available read tools. A `block` action is reserved for an obstacle
that prevents continuing and must use an existing stable reason listed above;
every supported block remains immediately terminal.

Each executor receives `required_action_schema` with the exact `complete`,
`block`, `calculate`, and `spawn_children` structures. Dynamic children use the
same six required task fields, requirement kinds, operation vocabulary,
completion criteria, and routing derivation as the planner. For example:

```json
{
  "action": "spawn_children",
  "children": [{
    "task_id": "child_read",
    "objective": "Read the requested records",
    "depends_on": [],
    "requirements": [{
      "requirement_id": "read_query",
      "kind": "query",
      "description": "Read the requested records",
      "operation": null
    }],
    "completion_criteria": ["read_query"],
    "routing_features": []
  }]
}
```

Child dependencies follow the child graph rules: they may name the owning root
or its existing or proposed sibling children, including later siblings. They
cannot name themselves or another subtree, or form a cycle. Only a root can
spawn children, with at most two children in total across all its proposals.
Task IDs must remain globally unique. Do not supply `parent_id`; ownership is
assigned internally. The current task reference includes all its validated fields
and its read-only `parent_id` so an executor can identify its ownership; that
context field is excluded from accepted child payloads. Accepting a child does
not provide evidence or satisfy completion. Root-only dependency ordering and
depth rules do not apply to this child graph.

An executor response may contain multiple read-tool calls. The scheduler validates
the complete response before executing any call, then runs the calls serially in
the supplied order. IDs must be unique within the response, arguments must be
JSON-compatible, and tool calls cannot be mixed with a text action. Each call
keeps the existing allow-list, permissions, query validation, cache, and evidence
checks. A failed or unauthorized call provides its own error feedback; other
legitimate calls can still run. Cancellation or an expired deadline stops later
calls, including cache hits.

The following executor request receives one assistant message declaring the
ordered calls, followed by their results matched by call ID. Tool history remains
local to the task and turn. A batch consumes one provider round and records its
reported token usage once. It does not extend deadlines or explicitly configured
round limits; planner
and synthesis responses still cannot call tools.

Invalid executor output remains rejected. The next existing attempt receives
the latest task-local `action_validation_error`, containing a validation `code`,
`path`, and `expected` form, with diagnostic `detail` when available. Dynamic
child rejections preserve the actual validator's fields, including failures of
completion criteria, routing features, and graph constraints. Diagnostic string
fields longer than 1,024 characters are explicitly excerpted with `truncated:
true`; the raw rejected action is never replayed. These fields remain untrusted
reference data and cannot introduce instructions or satisfy evidence requirements.

Malformed action objects, unsupported block reasons, invalid completion evidence,
calculation failures, and invalid provider-round shapes also receive guidance.
Unknown IDs are not invented or replaced, missing child fields are not filled,
and failed calculations never create evidence. Every supported block remains
immediately terminal. An accepted action or tool output clears prior action
feedback; tool results retain their separate history.

Feedback lasts only for the task in its current turn. It does not count as new
progress or extend planner/synthesis fallback attempts, deadlines, or provider
input limits. Normal planned turns have no fixed round cap or generic no-progress
stop; successful discovery can continue even before declared evidence is added.
Transport failures and repeated invalid-operation cycles remain explicit errors.
Public error codes and strict validation remain unchanged.


### Corrected queries and requirement links

Successful distinct queries and pages remain immutable evidence even after a
requirement has an earlier result. Empty successful results are retained too.
The executor can complete with the later corrected evidence IDs, and synthesis
receives only the selected evidence and verified calculation ancestors, including exact query identity and filters. Declared dependents receive the selected successful predecessor evidence as reference data; unrelated tasks do not.

When several requirements share an evidence kind, pass `requirement_id` on the
read-tool call to select the intended requirement. A sole matching requirement
is linked automatically. Ambiguous results are stored without a requirement
link; repeating the call with its intended `requirement_id` links the cached
result without another backend read. Invalid or foreign IDs are rejected before
execution. The selector is scheduler metadata and is excluded from backend
arguments and cache identity. `task_evidence` lists each record's
`requirement_ids`; completion still requires evidence linked to every declared
requirement.


### Verified arithmetic and executor context

An executor can reference the `value` of an earlier framework calculation. The
calculator re-evaluates its entire derivation from immutable query evidence and
checks the stored result and canonical call identity. Every source must belong
to the same task and, in scheduled execution, be linked to a declared requirement.
Missing, cyclic, foreign, unlinked or forged derivations are rejected. This
validates arithmetic lineage; it does not infer population or unit semantics
from a requirement's prose.

Executor requests include a full linked payload once in `task_evidence`. An exact
copy in native tool history can become `{"evidence_ref":"..."}`. Original history,
call IDs and ordering remain intact. Errors and unmatched or unlinked results
remain full. This reduces duplicated context without truncating evidence or
raising request limits.

Private execution results and sanitized audit events distinguish the stable
public error reason from `reason_origin`: `model_declared_block`,
`provider_exception`, `scheduler`, or `synthesizer`. Only runtime code assigns
this field; a model-selected `synthesis_failed` block is not a synthesizer failure.

Planner and synthesis instructions require clarification when an analytical
metric, period or population is materially unspecified. They preserve choices
already supplied by the user or applicable context. Explicit source requests
require source attribution in answer prose, beyond internal evidence IDs.
Offline prompt/contract validation does not establish future model compliance.


### Population-bound calculations

Model-authored calculation requirements include `binding`, naming earlier
same-task source requirements, a row-relative `value_path`, `group_by` field
paths and an optional explicit `unit_path`. A query reduction consumes every row
of exactly one group from one complete query result. Completion requires all
its groups. Partial pages, duplicate operands, mixed groups and incompatible
explicit units are rejected. Unknown units stay unknown; no conversion is
inferred from a field name. A derived operation names earlier calculation
requirements with `value_path: null`, `group_by: []`, and `unit_path: null`.
Its operands must preserve compatible query population, measured field and unit.

The tool-free planner may use `binding: null` when field names are not known.
After schema/query discovery, the executor must use `bind_calculation` with the
requirement ID and exact binding. A binding is assigned once; calculation and
completion are unavailable while it remains deferred. This avoids asking the
planner to invent unseen schema fields. Trusted Python callers retain the old
unbound scalar API, which does not claim population validation.

For calendar-year aggregation, an optional `utc_year_by: [["shippedAt"]]`
explicitly groups ISO dates or timezone-qualified timestamps by UTC year.
Naive timestamps and invalid dates are rejected. Other date transforms and
implicit grouping are unsupported. Aggregation over multiple pages or distinct
query populations is currently unsupported; collect one complete bounded query
or report that the declared aggregate cannot be established.

`calculate_batch` accepts a nonempty list of ordinary `calculate` actions.
Every item is validated against staged evidence; an invalid item or expired
deadline commits none of the batch. This removes one model round per scalar
without extending production deadlines. Numerical/scope validation does not
prove that the model chose the correct interpretation of the user's question.

### Instruction roles and conversational scope

The captured `GENERAL_MANAGER.CHAT.system_prompt` remains an application system
instruction in planning, execution and synthesis. Each phase's later instruction
specifies its tool/action limits. Visible user and assistant dialogue is supplied
as conversation context, allowing explicit user choices to carry forward.
Previous assistant statements are not record evidence. Schemas, tool results and
rejected outputs remain untrusted data and cannot override application or phase
instructions. Factual claims still require selected eligible tool evidence.


Schema tools default to a compact overview. Use its exact `type_manifest` names
and manager-bound `snapshot` with `view="detail"` to load input, enum or output
definitions as needed. Inspect related manager references with their own
overview. Schema requirements declare structured `schema` scope; unspecified
legacy requirements require explicit `view="full"`. `schema_complete` describes
full-schema inspection, not data or task completeness. See the chat API reference
for the full Python helper defaults and migration contract.


## Grounded record choices and follow-up references

A clear follow-up reference to a previously displayed set preserves that set
when current eligible query evidence corroborates its identities and scope.
Previous answer values are not record evidence; current queries supply values.
Complete task coverage does not by itself resolve an ambiguous user choice or
consent.

Analytical clarification topics keep the existing closed language/requirements
shape. The internal `record_selector` branch now requires a selector witness:
`{"evidence_id": "<selected-query-id>", "field": "name"}`. The identity field
must be selected by that query and be `id`, `code`, `name`, or `designation`.
The query must return a complete set of 2–20 distinct nonempty identities
(labels at most 160 characters). Its manager must match the query provenance.
The runtime renders the actual choices and returns the query evidence ID for
grounding. Model-authored names or values are not accepted. Bare generic record
questions are rejected and use at most the existing single fallback. Custom
synthesis-provider test doubles must migrate that branch; analytical questions
and public chat entry-point defaults are unchanged.

For external evaluation extraction, an unadjusted existing shipment-plan series
in pieces has the canonical `shipped_quantity` metric and `existing_plan` source
kind. A supported gross-basis qualifier alone is not a gross/net comparison.
This does not alias gross and net metrics or change exact scoring; contradictions
and explicit comparative metrics retain their existing checks. The response
schema shape remains 1.4, and the revised instruction is bound by the new source
and prompt hashes. Existing results are not rejudged or rescored.
