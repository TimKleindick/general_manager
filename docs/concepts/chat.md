# Chat architecture and request lifecycle

Chat provides a natural-language interface to the GeneralManager GraphQL API.
The application chooses the managers, model providers and access policy. The
server validates plans, executes tools in the requesting user's context and
checks evidence before producing an answer. GeneralManager supplies no browser
chat widget.

Start with [installation](../howto/install_llm_chat.md), then follow
[configuration](../howto/configure_chat.md) and
[client integration](../howto/integrate_chat.md). The
[Chat API reference](../api/chat.md) contains exact settings and Python signatures.

## One read request

Every new HTTP, SSE and WebSocket read follows the same scheduler:

1. The transport identifies the Django user or session, applies the configured
   chat permission callable and rate limit, and loads conversation history.
2. The planner receives the current request, projected history, application
   definitions and a compact summary of exposed managers. It returns a read
   plan or a mutation intent.
3. Python validates the whole plan before application-data access: task IDs,
   requirement shapes, completion criteria and dependencies must be consistent.
4. Ready tasks use one executor role to discover schemas, query records and
   request calculations. Dependencies determine which tasks may start; the
   configured concurrency limit bounds simultaneous tasks.
5. Successful observations become immutable evidence. Only compatible evidence
   linked to the task's requirements may establish completion.
6. Synthesis receives eligible selected evidence from resolved tasks, the user
   request, conversation choices and application definitions. Its response must
   name eligible evidence IDs, or use the closed clarification shape.
7. The transport delivers tool activity, answer text and a terminal event with
   usage and complete or partial task coverage.

The planner has one correction attempt and then one fallback attempt. Synthesis
has its own bounded fallback. These attempts share their stage's deadline.
Task complexity does not route work to different executor models. Explicit
provider profiles assign the `planner`, `executor`, `synthesizer` and `fallback`
roles; all mapped profiles must belong to one trust group.

A valid mutation intent leaves this read flow and enters the existing mutation
continuation. Authentication, mutation allow-listing and configured confirmation
still apply; read task completion cannot authorize a write.

## Responsibilities and trust boundaries

| Layer | Responsibility |
| --- | --- |
| Application | Domain managers, executable GraphQL schema, business definitions, model credentials, access policy, retention schedule and UI |
| Transport | Authentication context, chat admission, history, delivery and confirmation ownership |
| Planner and executor | Propose structured work, select observed schema names and gather missing evidence |
| Python scheduler and validators | Enforce plan shape, dependencies, tools, evidence eligibility, deadlines and completion |
| Native GraphQL | Execute existing resolvers, middleware and manager/field permissions with the current user and request context |
| Synthesizer | Explain the selected evidence and distinguish unanswered work or unresolved choices |

Manager `chat_exposed` defaults to `False`. Opt-in controls discovery, paths and
chat query selection; it does not grant record access. HTTP passes the actual
request, bound to the same user. WebSocket uses `ScopeChatContext` with the
current user and ASGI scope. A Python query without an explicit context remains
anonymous. See [schema and exposure](chat_schema.md).

Model-generated plans, quoted text, tool results and application definitions
have distinct roles. A plan is proposed work; a tool result is an observation;
a user reply can select conversational scope. None is a privileged instruction
or an independent mutation permission. See [definitions and choices](chat_prompting.md).

## Evidence, arithmetic and clarification

A schema observation is bound to manager, view, selected definitions and
snapshot. A query records canonical call arguments and source provenance.
Calculations refer to observed numeric leaves or verified earlier calculations;
Python performs the arithmetic. A tool returning successfully does not mean the
user's question is fully answered. See [evidence and calculations](chat_evidence.md).

A concrete record ambiguity can produce a query-backed list of choices. An
analytical ambiguity can ask for a metric, horizon, unit or other supported
choice. The next user reply is bound to the original question and scope. It
supplies no record facts; the executor must gather current data for the selected
scope. Partial replies and conflicting definitions can leave a question open.

## Deadlines, rounds and lifecycle

The default planning/evidence stage is **90 seconds**, followed by a separate
**30-second synthesis stage**. The evidence stage includes planning. Provider
requests are bounded by the remaining stage time. Normal planned turns count
provider rounds and observed token usage without a fixed successful-work round
cap. Explicit evaluator dispatch caps are a separate mechanism.

Repeated invalid actions stop only when the scheduler proves a validation cycle
against unchanged task/evidence inputs after supplying the feedback. A valid
schema exploration is allowed to continue within the deadline. Corrected
queries, changed evidence and valid work do not form that invalid-action cycle.

At termination, the scheduler cancels outstanding tasks. PostgreSQL statement
timeouts use Django's default connection and the smaller applicable query/stage
budget. Cancellation of synchronous work on other backends is best effort; a
worker thread can finish after the response stops awaiting it. These mechanisms
do not promise a universal database or network wall-clock cutoff.

Plans, task evidence, dependencies and calculation bindings live in memory for
one turn. A disconnect or process restart ends that turn; it is not resumable.
Conversations and rendered clarification metadata persist, but are not a saved
scheduler. `planned.enabled=False` is deprecated and still dispatches planned
reads. Disable the endpoint with `CHAT.enabled=False` or roll back the release
when operational rollback is required.

## Boundaries to account for

- Typed GraphQL object/leaf selections and supported generated pages are
  available. Unions, interfaces, fragments and arbitrary GraphQL roots are not.
- Recursive manager-owned grouped outputs are not exposed by the chat contract;
  native GraphQL grouping and supported `group_by` queries are separate features.
- Pagination completeness describes observed data, not a transactional snapshot
  of changing business records.
- Exact source binding and arithmetic validation do not prove that a model has
  interpreted every business definition correctly. Evaluate the actual model
  and configuration on representative questions.
- Diagnostic request guards, extended eval deadlines and offline scripted
  success do not establish production readiness or model accuracy.

[Operations and troubleshooting](../howto/operate_chat.md) explains how to act
on partial coverage, provider failures and deadline expiry. The continuing
architectural decisions are recorded in [ADR 0008](../adr/0008-llm-chat-endpoint.md),
[ADR 0010](../adr/0010-planned-chat-evidence.md) and
[ADR 0011](../adr/0011-chat-schema-contracts.md).
