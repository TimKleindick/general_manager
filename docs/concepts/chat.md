# How LLM chat works

GeneralManager chat is a grounded tool loop over the project's generated
GraphQL schema. The LLM does not receive unrestricted database access and does
not construct arbitrary GraphQL documents. It receives a small, stable tool
set that can discover explicitly exposed managers, inspect their schema, find
relations, run bounded reads, and optionally invoke allow-listed mutations.

## Responsibilities

GeneralManager provides:

- provider adapters for Ollama, OpenAI-compatible APIs, Anthropic, and Gemini
- HTTP, SSE, and WebSocket transports
- conversation, message, and confirmation persistence
- manager discovery, schema inspection, path finding, querying, and mutation
  tools
- grounding instructions, bounded retries, rate limits, and public error events
- permission, origin, mutation allow-list, and audit hooks

The consuming project provides:

- the GeneralManager classes and generated GraphQL schema
- explicit `chat_exposed` decisions
- a provider, model, credentials, and network access
- authentication and a chat permission callable
- provider budgets, retention operations, monitoring, and a frontend

GeneralManager does not ship a chat user interface or choose which project data
is appropriate to expose.

## One chat turn

A normal read follows this sequence:

1. The HTTP, SSE, or WebSocket transport identifies the Django user or session.
2. GeneralManager applies the permission callable and cache-backed rate limit.
3. The user message is appended to the active `ChatConversation`.
4. GeneralManager builds a system prompt and adds summarized plus recent
   conversation history.
5. The configured provider receives provider-neutral messages and tool schemas.
6. The model emits text or a structured tool call.
7. GeneralManager validates and executes the tool against the exposed GraphQL
   schema, then returns JSON to the model.
8. The loop continues until the model emits a final answer and token usage.
9. Tool exchanges and the assistant answer are persisted and emitted as
   transport events.

The frontend sees tool activity, but it never executes model-requested tools.
Tool validation and execution stay on the server.

## Schema exposure is explicit

`GeneralManager.chat_exposed` defaults to `False`. Only classes that set it to
`True` appear in the chat schema index. The index contains manager names,
descriptions, scalar fields, relations, and supported filters derived from the
generated GraphQL registry.

This opt-in is an exposure boundary, not a replacement for authorization.
Queries still execute with the current user as GraphQL context, so the
project's normal permissions must enforce row- and operation-level access.

## Discovery tools

The default `tool_strategy` is `discovery`. It keeps the provider prompt small
even when a project has hundreds of managers:

| Tool | Purpose |
| --- | --- |
| `search_managers` | Search exposed managers by name, description, fields, and relations. |
| `get_manager_schema` | Return one manager's fields, filters, and relations. |
| `find_path` | Find a relation path between two exposed managers. |
| `query` | Execute a bounded, validated GraphQL read. |
| `mutate` | Execute an authenticated, allow-listed GraphQL mutation. |

The optional `direct` strategy generates one query tool per exposed manager.
It may help a very small schema, but its tool list grows with the project. The
discovery strategy is the scalable default.

The query tool validates manager exposure, field and filter names, nested field
selections, pagination, result limits, and configured query timeouts before it
executes GraphQL. The model cannot use the tool to select a hidden manager or
an unknown schema field.

## Grounding and recovery

The system prompt instructs the model to discover uncertain schema, answer data
questions from tool results, copy result values exactly, report empty results,
and avoid writes unless the user requested one. A project can append
domain-specific instructions with the `system_prompt` chat setting.

`max_retries_per_message` bounds non-mutation tool calls in one turn.
`recover_missing_tool_calls` can add one corrective prompt when a model answers
a data question without the required tool call or returns an empty answer after
using tools. Recovery never fabricates tool output and does not bypass the tool
retry limit.

Grounding reduces model mistakes; it does not make an unsuitable model safe.
Evaluate the exact model, prompt, schema, and provider configuration before
production use.

## Conversation persistence

Chat uses three Django models:

- `ChatConversation` identifies an authenticated user or anonymous session and
  stores an optional history summary.
- `ChatMessage` stores user, assistant, and tool exchange items in order.
- `ChatPendingConfirmation` stores durable mutation confirmation state.

An authenticated user reuses their most recently updated conversation. An
anonymous actor is keyed by the Django session and therefore needs session
middleware and a saved session key.

When history exceeds `summarize_after`, older messages can be summarized by the
same provider. The generated summary plus the latest `max_recent_messages`
become subsequent context. `ttl_hours` controls what the `chat_cleanup`
management command considers stale; cleanup is not scheduled automatically.

## Transports

All transports use the same provider, prompt, tools, persistence, permissions,
and rate limits. Choose based on client behavior:

| Transport | Best for | Streaming | Confirmed mutations |
| --- | --- | --- | --- |
| HTTP `POST /chat/` | Simple request/response clients | No | No pause-and-resume support |
| SSE `POST /chat/stream/` | HTTP clients that consume a response stream | Yes | Resolve with `POST /chat/confirm/` |
| WebSocket `/chat/` | Interactive browser or app clients | Yes | Resolve on the socket |

The SSE endpoint uses a POST body, so the browser's GET-only `EventSource` API
is not sufficient by itself. Use streaming `fetch` or an SSE client that
supports POST.

Every transport shares the same core event vocabulary and may emit
`tool_call`, `tool_result`, `text_chunk`, `done`, and `error` as the turn
requires. Confirmation-capable transports can also emit `confirm_mutation`.

## Authentication, permission, and origins

Django authentication identifies the actor. The optional chat `permission`
callable is the endpoint-level authorization gate and receives `(user, scope)`.
It should return `False` to deny access. If no callable is configured, chat does
not add an endpoint-level denial.

HTTP and SSE views are POST-only and CSRF-protected. The WebSocket application
uses Channels' authentication middleware and validates origins. With
`allowed_origins`, GeneralManager uses that explicit list; otherwise it uses
Django's allowed-host validation.

These layers complement manager and GraphQL permissions. They do not replace
them.

## Mutations and confirmation

The model sees the `mutate` tool, but execution is denied unless the exact
GraphQL mutation name appears in `allowed_mutations`. Anonymous actors cannot
mutate.

Names in `confirm_mutations` must also appear in `allowed_mutations`. For those
operations, GeneralManager stores a pending confirmation and emits the proposed
mutation and input without executing them. The client approves or rejects the
same confirmation ID before `confirm_timeout_seconds` expires. Claiming and
resolving confirmation state is transactional so repeated requests cannot
execute the same pending operation twice.

Keep both lists empty for a read-only assistant.

## Limits, retention, and observability

Chat rate limiting uses the Django cache and can enforce request, total-token,
input-token, and output-token budgets per user, session, or client address. A
local-memory cache only coordinates one process; multi-process deployments need
a shared cache.

`max_results` caps query rows, `query_timeout_seconds` applies a database query
timeout where supported, and provider-specific timeout settings bound model
requests. Public failures use stable error events instead of returning internal
exception details.

Optional WebSocket audit logging can emit sanitized message and tool activity
to a project-supplied callable. HTTP, SSE, and WebSocket paths also emit Django
signals for messages, tool calls, mutations, and errors where applicable.
Redaction is key-name based and tool results can be truncated. Treat auditing
as an observability hook, not a guarantee that arbitrary free-form user or
model text contains no sensitive data.

## Choosing and validating a model

Use a model with reliable structured tool calling. Model size alone does not
predict success: manager discovery, argument construction, multi-hop traversal,
and exact answer grounding all matter.

GeneralManager ships deterministic tests and live eval datasets. Run the
[installed chat eval suite](../howto/run_chat_evals.md) with the same provider
and model used by the application. Treat hard product-contract failures as
behavior defects and strategy deviations as evidence that the model skipped a
preferred discovery path.

Continue with the [installation tutorial](../howto/install_llm_chat.md) for a
working configuration or the [Chat API reference](../api/chat.md) for exact
settings and event shapes. Prompt and eval contributors should also read
[Chat prompt and eval iteration](chat_prompting.md).
