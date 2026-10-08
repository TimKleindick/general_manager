# Chat API

GeneralManager chat exposes selected managers to a tool-capable LLM through
HTTP, server-sent events (SSE), or WebSocket. Enable it through
`GENERAL_MANAGER["CHAT"]`; GeneralManager installs the routes during Django
application startup.

See [Add LLM chat to a GeneralManager project](../howto/install_llm_chat.md)
for a complete setup and [How LLM chat works](../concepts/chat.md) for the
runtime model.

## Settings

The minimal configuration is:

```python
GENERAL_MANAGER = {
    "CHAT": {
        "enabled": True,
        "provider": "general_manager.chat.providers.OllamaProvider",
        "provider_config": {"model": "qwen3.5:9b"},
    }
}
```

### Top-level chat settings

| Setting | Default | Behavior |
| --- | --- | --- |
| `enabled` | `False` | Register chat HTTP, SSE, and eligible WebSocket routes at startup. |
| `url` | `"/chat/"` | Base path used for every chat transport. Leading and trailing slashes are normalized for Django routing. |
| `provider` | `"general_manager.chat.providers.OllamaProvider"` | Dotted provider class path. The class is constructed without arguments for each HTTP/SSE request or WebSocket connection. |
| `provider_config` | `{}` | Mapping read by the selected provider. Supported keys depend on the adapter. |
| `provider_profiles` | `{}` | Optional named provider-profile mappings used by planned roles. Each profile supplies `provider`, `provider_config`, and `trust_group`. |
| `planned` | `{}` | Planned read-orchestration settings. `enabled` defaults to `True`; see [Planned read orchestration](#planned-read-orchestration). |
| `permission` | `None` | Callable or dotted path receiving `(user, scope)`. Returning `False` denies the request or socket. |
| `allowed_origins` | `None` | Explicit WebSocket origin list. When empty, Channels' allowed-host origin validator is used. |
| `allowed_mutations` | `[]` | Exact generated GraphQL mutation names the `mutate` tool may execute. |
| `confirm_mutations` | `[]` | Allowed mutation names that require client confirmation. Every name must also be in `allowed_mutations`. |
| `confirm_timeout_seconds` | `30` | Lifetime of a pending mutation confirmation. |
| `max_results` | `200` | Maximum query page size accepted by the chat query tool. |
| `query_timeout_seconds` | `None` | Optional database query timeout in seconds. It is converted to milliseconds for supported database execution. |
| `max_retries_per_message` | `3` | Maximum non-mutation tool-loop retries in one user turn. |
| `max_mutations_per_message` | `8` | Maximum mutation tool executions in one user turn, including a mutation resumed after confirmation. Set `0` to disallow writes for a turn. |
| `max_total_rounds_per_message` | `None` | Optional positive cap for legacy provider rounds in a turn, including summaries and recovery. When omitted, the cap is `max_retries_per_message + max_mutations_per_message + 2`. Normal planned reads count rounds without a round cap and retain their stage deadlines. |
| `tool_strategy` | `"discovery"` | `discovery` exposes the stable discovery tool set for the retained mutation workflow; `direct` replaces that set with one query tool per exposed manager. This setting does not select read orchestration. |
| `recover_missing_tool_calls` | `False` | Add bounded recovery prompts when a model answers without required tools or returns no answer after tools. |
| `system_prompt` | `""` | Project-specific instructions appended to the built-in system prompt. |
| `max_recent_messages` | `20` | Recent persisted messages retained verbatim when conversation context is built. |
| `summarize_after` | `10` | Message count after which older history may be summarized by the provider. |
| `ttl_hours` | `24` | Retention threshold used by `python manage.py chat_cleanup`. Cleanup is not scheduled automatically. |

### Rate limits

The `rate_limit` mapping is merged with these defaults:

```python
{
    "requests": 60,
    "window_seconds": 3600,
    "tokens": None,
    "input_tokens": None,
    "output_tokens": None,
}
```

Positive integer limits are enforced through the Django cache. The scope is
the authenticated user ID, then the anonymous session key, then the client IP.
`None`, zero, and negative values do not create a budget for that counter.
Provider usage events supply token counts.

Once a token budget is exhausted, GeneralManager records the completed
provider usage and rejects the next tool, confirmation write, summary, or
provider round. A `rate_limited` event includes `retry_after_seconds`.

### Audit settings

The `audit` mapping is merged with:

```python
{
    "enabled": False,
    "level": "off",
    "logger": None,
    "max_result_size": 4096,
    "redact_fields": ["password", "secret", "token", "key", "credential"],
}
```

For WebSocket chat, `logger` is a callable or dotted callable path receiving
one sanitized event mapping. `level="messages"` emits user and assistant
messages; `level="all"` also emits tool activity. `max_result_size` truncates
serialized tool results. Redaction recursively replaces values whose key
contains a configured term. All transports separately expose Django signals
for chat activity and errors.

## Provider adapters

Provider credentials can be passed in `provider_config`. When an SDK supports
its own environment variables, omitting `api_key` delegates credential lookup
to that SDK. Built-in providers retain zero-argument construction for legacy
chat. They also accept an explicit mapping through `from_config(config)` and
expose the copied, read-only mapping through `provider_config`; planned profile
construction uses these two hooks without changing legacy settings.

An explicitly named `provider_profiles` entry is always constructed with its
own `provider_config`, including `{}`. It never inherits credentials, model, or
endpoint values from the legacy `provider_config`. Only an omitted
`provider_profiles` mapping creates the implicit legacy profile.

Provider history keeps tool call IDs, names, arguments, and results as
structured data. The built-in adapters translate that history to each SDK's
native tool-call and tool-result format; custom providers receive it through
the `Message` tool fields.

Two timeout keys apply to every adapter at the GeneralManager provider loop:

- `timeout_seconds` defaults to `60` and limits the wait for the first provider
  event. Ollama, OpenAI, and Anthropic also pass it to their SDK client.
- `stream_timeout_seconds` defaults to `30` and limits the wait between later
  streamed events.

| Provider path | Extra | Provider configuration |
| --- | --- | --- |
| `general_manager.chat.providers.OllamaProvider` | `chat-ollama` | `model` (default `gemma4:e4b`), `base_url` (default `http://127.0.0.1:11434`), `timeout_seconds` (default `60`) |
| `general_manager.chat.providers.OpenAIProvider` | `chat-openai` | `model` (default `gpt-4.1-mini`), `api_key`, `base_url`, `timeout_seconds` (default `60`) |
| `general_manager.chat.providers.AnthropicProvider` | `chat-anthropic` | `model` (default `claude-3-5-haiku-latest`), `api_key`, `max_tokens` (default `1024`), `timeout_seconds` (default `60`) |
| `general_manager.chat.providers.GeminiProvider` | `chat-google` | `model` (default `gemini-2.5-flash`), `api_key`, `timeout_seconds` (first event), `stream_timeout_seconds` (between events) |

`GoogleProvider` is an alias of `GeminiProvider`. The OpenAI provider accepts a
`base_url` for OpenAI-compatible services.

The configured model must implement structured tool or function calling.

## Routes and request contracts

With the default `url="/chat/"`, startup registers three Django routes and one
WebSocket route. HTTP views are POST-only and CSRF-protected.

### Non-streaming HTTP

```http
POST /chat/
Content-Type: application/json
X-CSRFToken: <token>

{"text": "Which projects use aluminum parts?"}
```

Successful and tool-level error responses use HTTP 200 with ordered events:

```json
{
  "events": [
    {"type": "text_chunk", "content": "Mercury uses ..."},
    {"type": "done", "usage": {"input_tokens": 100, "output_tokens": 20}}
  ],
  "answer": "Mercury uses ..."
}
```

`answer` concatenates all `text_chunk.content` values. Permission denial uses
HTTP 403. This endpoint cannot pause and resume a mutation listed in
`confirm_mutations`; it emits `confirmation_required_transport` instead.

### SSE

```http
POST /chat/stream/
Content-Type: application/json
Accept: text/event-stream
X-CSRFToken: <token>

{"text": "Which projects use aluminum parts?"}
```

Each event is encoded as `data: <JSON>\n\n` and the response content type is
`text/event-stream`. Because the endpoint is POST-based, use streaming `fetch`
or an SSE client that supports POST rather than the browser's GET-only
`EventSource` constructor.

The server creates an anonymous session before SSE headers are sent, so the
response includes a usable Django session cookie even before its first event.
Chat stores a deterministic hash only when a signed-cookie value exceeds the
conversation key column limit; the cookie itself is unchanged.

Resolve a pending SSE confirmation through:

```http
POST /chat/confirm/
Content-Type: application/json
X-CSRFToken: <token>

{"confirmation_id": "call-1", "confirmed": true}
```

The confirmation endpoint returns the same `{events, answer}` envelope as the
non-streaming endpoint and resumes the provider after the tool result.

### WebSocket

Connect to `/chat/` using `ws` or `wss`. The application is wrapped in
Channels' `AuthMiddlewareStack`, so Django session authentication is available
as `scope["user"]`.

Send a user message:

```json
{"type": "message", "text": "List all materials"}
```

Resolve a mutation confirmation:

```json
{
  "type": "confirm",
  "confirmation_id": "call-1",
  "confirmed": true
}
```

Only one turn and one pending confirmation may be active on a socket. Unknown
event types produce `bad_event`; an overlapping message produces
`turn_in_progress` or `confirmation_pending`.

## Persistent context

Long legacy HTTP, SSE, and WebSocket turns share one bounded-context path. It
retains recent messages and summarizes the older prefix using the selected
provider's whole-request timeout. A summary records the exact last message it
covers; a missing or outdated marker regenerates the summary rather than
reusing unknown coverage. Tool windows retain each selected assistant call
group and only that group's linked results. Historical rows without a stored
call identity are labeled assistant context, never sent as native tool-result
messages.

## Server event contract

Events arrive in order. Clients should ignore unknown fields so compatible
metadata can be added later.

### `tool_call`

The provider requested a server-side tool:

```json
{"type": "tool_call", "id": "call-1", "name": "query", "args": {}}
```

### `tool_result`

The server validated and executed a tool:

```json
{
  "type": "tool_result",
  "id": "call-1",
  "name": "query",
  "result": {"data": [], "total_count": 0, "has_more": false}
}
```

### `text_chunk`

One assistant text fragment:

```json
{"type": "text_chunk", "content": "No matching records were found."}
```

### `done`

The turn is complete:

```json
{
  "type": "done",
  "usage": {"input_tokens": 100, "output_tokens": 20}
}
```

Usage values depend on the provider SDK and may be zero when the provider does
not report them.

### `confirm_mutation`

An allow-listed mutation is waiting for explicit approval:

```json
{
  "type": "confirm_mutation",
  "id": "call-1",
  "mutation": "createPart",
  "input": {"name": "Bolt"}
}
```

### `error`

A public, non-sensitive failure:

```json
{
  "type": "error",
  "message": "Chat rate limit exceeded. Try again later.",
  "code": "rate_limited",
  "retry_after_seconds": 3600
}
```

The optional fields depend on `code`. Unexpected internal exceptions are
reported as the generic `chat_error` event and are emitted through the chat
error signal for server-side observability.

| Code | Transport | Meaning and client action |
| --- | --- | --- |
| `bad_message` | HTTP, SSE, WebSocket | `text` is absent, blank, or not a string. Correct the payload before retrying. |
| `bad_event` | Confirmation HTTP, WebSocket | The event shape, confirmation payload, or confirmation ID is invalid. Do not retry unchanged. |
| `confirmation_pending` | WebSocket | A new message arrived before the current mutation confirmation was resolved. Resolve or reject it first. |
| `confirmation_unavailable` | WebSocket | Durable confirmation state was already claimed, resolved, or expired. Refresh the conversation state. |
| `turn_in_progress` | WebSocket | Another turn is still streaming on this socket. Wait for its terminal event. |
| `rate_limited` | HTTP, SSE, WebSocket | The actor exceeded a configured budget. Retry after `retry_after_seconds`. |
| `tool_retry_limit` | HTTP, SSE, WebSocket | The model exceeded `max_retries_per_message`; the turn is terminal. |
| `mutation_limit` | HTTP, SSE, WebSocket | The turn exhausted `max_mutations_per_message`; no further mutation is executed. |
| `turn_limit` | HTTP, SSE, WebSocket | The turn exhausted `max_total_rounds_per_message`; no further provider round is started. |
| `mutation_batch_unsupported` | HTTP, SSE, WebSocket | A completion requested `mutate` with another tool call. Request one mutation in a completion by itself. |
| `confirmation_required_transport` | HTTP | A confirmed mutation needs SSE or WebSocket. Retry the workflow on a confirmation-capable transport. |
| `chat_error` | HTTP, SSE, WebSocket | An unexpected server or provider failure. Show the generic message and correlate server-side logs or signals. |

Permission denial returns HTTP 403 for HTTP/SSE and closes a WebSocket with
code `4403`. A WebSocket startup failure closes with `1011`. CSRF rejection and
non-POST HTTP methods use Django's standard HTTP 403 and 405 responses before a
chat event is produced.

## Chat tools

The discovery strategy exposes:

| Tool | Important inputs | Result |
| --- | --- | --- |
| `search_managers` | `query` | Matching exposed manager summaries |
| `get_manager_schema` | `manager` | Fields, filters, descriptions, and relations |
| `find_path` | `from_manager`, `to_manager` | Exposed relation path or no path |
| `query` | `manager`, `filters`, `fields`, `limit`, `offset` | Bounded GraphQL data page |
| `mutate` | `mutation`, `input` | Mutation result, denial, or confirmation requirement |

Only managers with `chat_exposed = True` are accepted. Query fields, nested
selections, filters, limits, and offsets are validated against the indexed
schema before execution. Mutations require an authenticated user and an exact
name in `allowed_mutations`.

Read-only tool calls may be batched in one provider completion. A completion
that includes `mutate` and any other tool call is rejected before any tool runs;
request each mutation by itself so the client-confirmation protocol can retain
one pending write safely.

## Persistence and cleanup

Chat persistence is installed with the GeneralManager Django migrations:

```bash
python manage.py migrate
```

`ChatConversation` belongs to an authenticated user or anonymous Django
session. `ChatMessage` records ordered conversation and tool items.
`ChatPendingConfirmation` stores confirmation state, including expiry and
resolution metadata, until cleanup removes the record. A successful HTTP or
WebSocket confirmation atomically claims the pending record scoped to the
current authenticated actor or anonymous session before the server executes the
mutation. This prevents durable approval replay across processes when
persistence is enabled. When persistence is unavailable, a WebSocket pending
confirmation remains owned by that socket session and can be consumed once; it
does not provide cross-process replay protection.

Prune stale conversations and resolved or expired confirmations using:

```bash
python manage.py chat_cleanup
```

The command reads `ttl_hours`. Schedule it with the deployment's normal task
runner; GeneralManager does not schedule it automatically.

## Signals

`general_manager.chat.signals` exposes four Django signals for application
observability:

| Signal | Emitted for |
| --- | --- |
| `chat_message_received` | An accepted user message with user and conversation context |
| `chat_tool_called` | A completed server-side tool call with arguments and result |
| `chat_mutation_executed` | Mutation tool outcomes, including immediate execution and confirmation resolution (execution, rejection, or timeout); inspect `result.status` |
| `chat_error` | A transport or provider failure with server-side context |

Receivers should avoid raising exceptions; GeneralManager dispatches these
signals with Django's robust signal delivery.

::: general_manager.chat.signals.chat_message_received

::: general_manager.chat.signals.chat_tool_called

::: general_manager.chat.signals.chat_mutation_executed

::: general_manager.chat.signals.chat_error

## Provider protocol

A custom provider class is instantiated without arguments and must implement
the asynchronous provider protocol. It receives provider-neutral messages and
tool definitions and yields text, tool-call, and terminal events:

```python
from collections.abc import AsyncIterator

from general_manager.chat.providers.base import (
    ChatEvent,
    DoneEvent,
    Message,
    TokenUsage,
    ToolDefinition,
)


class MyProvider:
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]:
        # Adapt the provider SDK's streaming response here.
        yield DoneEvent(usage=TokenUsage())
```

Yield `TextChunkEvent` for assistant text and `ToolCallEvent` with a stable ID,
tool name, and decoded argument mapping for tool requests. Finish every normal
completion with `DoneEvent`. A provider may define `check_configuration()` for
startup validation and `required_extra` for an installation hint.

::: general_manager.chat.providers.base.BaseLLMProvider

::: general_manager.chat.providers.base.Message

::: general_manager.chat.providers.base.ToolDefinition

::: general_manager.chat.providers.base.TextChunkEvent

::: general_manager.chat.providers.base.ToolCallEvent

::: general_manager.chat.providers.base.DoneEvent

::: general_manager.chat.providers.openai.OpenAIProvider

::: general_manager.chat.providers.anthropic.AnthropicProvider

::: general_manager.chat.providers.google.GeminiProvider

::: general_manager.chat.providers.google.GoogleProvider

::: general_manager.chat.providers.ollama.OllamaProvider

::: general_manager.chat.providers.openai.OpenAIDependencyImportError

::: general_manager.chat.providers.anthropic.AnthropicDependencyImportError

::: general_manager.chat.providers.google.GoogleDependencyImportError

::: general_manager.chat.providers.ollama.OllamaDependencyImportError

::: general_manager.chat.providers.ollama.OllamaBaseUrlError

## Settings, errors, and audit helpers

::: general_manager.chat.settings.ChatConfigurationError

::: general_manager.chat.settings.get_chat_settings

::: general_manager.chat.settings.validate_chat_settings

::: general_manager.chat.errors.PublicChatError

::: general_manager.chat.errors.public_chat_error

::: general_manager.chat.errors.planned_public_error

`planned_public_error(reason)` accepts only the stable planned reasons listed
below. It returns a `PublicChatError`; invalid or unknown reasons use the
generic `chat_error` code and message. `public_chat_error(exc)` preserves an
exception's valid `public_reason`, maps a plain `TimeoutError` to
`deadline_exceeded`, and maps other unexpected exceptions to `chat_error`.

| Code | Public message |
| --- | --- |
| `invalid_plan` | `I could not prepare a safe plan for that request.` |
| `manager_unresolved` | `I could not resolve the required application data.` |
| `dependency_blocked` | `A required part of the request could not be completed.` |
| `budget_exhausted` | `The request reached its execution limit.` |
| `deadline_exceeded` | `The request reached its time limit.` |
| `provider_failed` | `The provider could not complete the request.` |
| `synthesis_failed` | `I could not produce a grounded answer from the available data.` |

::: general_manager.chat.audit.planned_audit_lineage_id

::: general_manager.chat.audit.emit_planned_audit_event

`emit_planned_audit_event(event_type, payload, *, sink=None)` accepts only the
allowlisted planned event categories. It hashes planner-controlled identifiers
and canonical call identities, validates category-specific fields, and drops
raw plans, manager names, profiles, trust groups, credentials, results, and
exceptions before forwarding to the generic audit sink.

## Django system checks

When chat is enabled, `python manage.py check` can report:

| ID | Meaning |
| --- | --- |
| `general_manager.chat.E001` | The generated GraphQL schema is not initialized. |
| `general_manager.chat.E002` | Chat settings, permissions, or mutation allow-lists are invalid. |
| `general_manager.chat.E003` | The selected provider's optional dependency is missing. |
| `general_manager.chat.E004` | The provider import failed for another reason. |

## Installed evaluation CLI

```text
python -m general_manager.chat.evals [OPTIONS]
```

The module command configures Django, optionally registers a built-in schema
fixture, loads packaged YAML datasets, constructs one or more providers, runs
the selected cases synchronously, prints a report, and returns no Python value.
`python -m general_manager.chat.evals --help` can run without Django settings;
every evaluation run requires either `--settings MODULE` or a nonempty
`DJANGO_SETTINGS_MODULE`.

| Option | Value and behavior |
| --- | --- |
| `--settings` | Import path for the Django settings module. Overrides an existing `DJANGO_SETTINGS_MODULE` for this process. |
| `--provider` | Provider class import path. When omitted, GeneralManager imports the provider configured in `GENERAL_MANAGER["CHAT"]`. |
| `--model` | Model name merged into the selected provider configuration before provider construction. |
| `--dataset` | One legacy-compatible packaged dataset name. When omitted, the runner selects all legacy-compatible datasets; `planned_orchestration` is excluded because it requires deterministic role-pinned providers. |
| `--fixture` | `toy` or `large`; registers the matching built-in eval schema before the run. |
| `--tier` | Integer tier filter. |
| `--tag` | Required tag filter; repeat the option to pass multiple tags. |
| `--compare` | Comma-separated provider class import paths. Runs each provider and prints a comparison report instead of the single-provider report. |
| `--verbose`, `-v` | Includes detailed failure information in a single-provider report. |
| `--trace-jsonl` | File path that receives per-case JSONL traces. |

The packaged dataset names are `basic_queries`, `demo_readiness`, `edge_cases`,
`follow_ups`, `large_schema`, `multi_hop`, and `planned_orchestration`. The
installed CLI's legacy suite runs the first six; selecting
`planned_orchestration` explicitly is rejected. That packaged dataset is
instead exercised by the deterministic planned tests described in the task
guide.

### Eval exit status

- Status 0: argument help was displayed, or every selected result passed.
- Status 1: at least one selected result failed.
- Status 2: argument parsing failed, including a run without Django settings.
- Other nonzero termination: Django setup, provider import or construction,
  fixture registration, dataset loading, trace writing, or evaluation raised an
  exception.

`--settings` takes precedence over `DJANGO_SETTINGS_MODULE`; `--provider` takes
precedence over the configured provider; and `--compare` takes precedence over
single-provider selection.

Application automation should prefer the module CLI rather than import eval
runner internals. See the [task guide](../howto/run_chat_evals.md) and
[command cookbook](../examples/chat_eval_cli.md).

## Planned read orchestration

Planner and synthesis share the clarification-topic distinction: `criterion`
asks for the missing standard of an open evaluative judgment; `metric` asks for
the missing measurement of an already defined analysis. They ask only for
choices still unresolved by the user or an applicable definition. Once the
standard and measurement are supplied, they do not ask for them again. The
existing structured question renderer and closed topic vocabulary are unchanged.

All new HTTP, SSE, and WebSocket messages use Planned orchestration. The planner
classifies reads and writes before dispatch. Reads have three processing stages:
planner, executor, and synthesizer. A shared `fallback` profile handles recovery
under the existing trust and deadline checks; task complexity does not select a
provider. A validated mutation plan enters the retained mutation workflow, which
keeps its permissions, confirmation, ownership, duplicate, and retry checks. The
write provider is constructed only when that workflow needs it.

`planned.enabled=False` is deprecated: it emits `DeprecationWarning` and still
uses Planned. Invalid settings fail validation rather than selecting an older
read loop. The global `CHAT.enabled` switch still controls whether chat is exposed.

```python
GENERAL_MANAGER = {
    "CHAT": {
        "provider": "myproject.providers.LegacyProvider",
        "provider_config": {},
        "provider_profiles": {
            "fast_local": {
                "provider": "myproject.providers.LocalProvider",
                "provider_config": {"model": "small"},
                "trust_group": "local",
            },
            "strong_local": {
                "provider": "myproject.providers.StrongProvider",
                "provider_config": {"model": "large"},
                "trust_group": "local",
            },
        },
        "planned": {
            "enabled": True,
            "catalog": "myproject.chat.get_manager_catalog",
            "roles": {
                "planner": "strong_local",
                "executor": "strong_local",
                "synthesizer": "strong_local",
                "fallback": "strong_local",
            },
            "max_concurrent_tasks": 3,
            "evidence_timeout_seconds": 90,
            "synthesis_timeout_seconds": 30,
        },
    },
}
```

The required role names are `planner`, `executor`, `synthesizer`, and `fallback`.
Old role maps must be migrated explicitly: select the desired executor profile
and rename the recovery role to `fallback`. The removed
`planned.routing.select_executor_role` API has no replacement; every task uses
`executor`, subject to recovery. `routing_features` remains validated descriptive
plan metadata and does not select a provider. If `provider_profiles` is omitted,
planned mode creates the implicit `default` profile from the legacy provider and
configuration, assigns every role to it, and uses trust group `default`. Every
profile used by a normal turn must share one `trust_group`; client HTTP, SSE,
and WebSocket payloads cannot choose a profile or trust group. `planned.catalog`
may be a mapping, callable, or dotted callable path. A catalog entry has the
exact chat-exposed manager name as its key and `domain`, `aliases`, `use_when`,
and `distinguish_from` fields:

```python
{
    "PartManager": {
        "domain": "manufacturing",
        "aliases": ["part", "component", "item"],
        "use_when": "The question concerns designed or purchased components.",
        "distinguish_from": ["MaterialManager"],
    },
}
```

Catalog metadata only ranks candidates; it never changes schema visibility,
permissions, field access, or query authorization.

Planned mode keeps the normal transport vocabulary. Actual tool events add
`task_id`; final synthesis produces `text_chunk`; exactly one `done` reports
complete or partial coverage; and an `error` is terminal only when no grounded
answer is available. Stable planned error codes are `invalid_plan`,
`manager_unresolved`, `dependency_blocked`, `budget_exhausted`,
`deadline_exceeded`, `provider_failed`, and `synthesis_failed`. Their messages
are stable and do not include profiles, trust groups, catalog data, plans, or
exceptions. Other exceptions remain the generic `chat_error` mapping.

Planned audit events use the existing `audit` setting and are allowlisted before
the generic audit sink. They can record deterministic opaque hashes of plan/task
lineage, role (never profile), trust-group validation outcome, match-source
categories, a SHA-256 canonical call hash, duplicate/progress state, round
budgets, latency, reported token usage/cost, evidence-kind counts, coverage,
and terminal reason. Raw tool results, manager names, plans, credentials, and
exceptions are excluded; the existing configured field redaction and result-size
limits still apply to the generic audit layer.

Planned synthesis receives the current request, eligible resolved evidence and a
turn-local snapshot of `GENERAL_MANAGER["CHAT"]["system_prompt"]`, when that
setting is a nonempty string. Only this configured text is added; generated
schema prompts and conversation history are not copied. This preserves existing
application definitions across planning, follow-ups and synthesis fallback.
Schema field descriptions remain inside their original evidence payloads.
Definitions are reference data, not instructions or proof of record values.
Their manager and field scope must be respected; missing or conflicting meanings
must not be invented. Evidence eligibility, tool permissions and provider limits
are unchanged.

### Planned settings and catalog

The settings helpers normalize the nested `planned` mapping into immutable
profile and role data. `get_planned_chat_settings()` always enables Planned
reads. All four required roles must resolve to configured profiles,
all mapped profiles must share one `trust_group`, and each configured provider
must support the explicit configuration construction used by
`build_profile_provider()`.

::: general_manager.chat.planned.config.REQUIRED_ROLES

::: general_manager.chat.planned.config.ProviderProfile

::: general_manager.chat.planned.config.PlannedChatSettings

::: general_manager.chat.planned.config.get_planned_chat_settings

::: general_manager.chat.planned.config.profile_for_role

::: general_manager.chat.planned.config.build_profile_provider

::: general_manager.chat.planned.config.validate_profile_provider

`load_manager_catalog(source, schema_index)` accepts `None`, a mapping, a
callable, or a dotted callable path. It returns an immutable catalog whose
entries contain `domain`, normalized `aliases`, `use_when`, and
`distinguish_from`; a catalog entry for a manager absent from `schema_index`
raises `ChatConfigurationError`. Catalog metadata ranks candidates but does not
change schema exposure or authorization.

::: general_manager.chat.planned.catalog.ManagerCatalogEntry

::: general_manager.chat.planned.catalog.ManagerCatalog

::: general_manager.chat.planned.catalog.normalize_match_text

::: general_manager.chat.planned.catalog.load_manager_catalog

### Planned task and validation types

Plans are JSON-compatible mappings. `validate_plan(payload)` returns a frozen
`ValidatedPlan` for one to six read roots, or a mutation plan with no tasks;
invalid shapes raise `PlanValidationError` before application data access.
`validate_dynamic_children(parent, payload, existing_tasks)` validates at most
two non-recursive children and enforces globally unique task IDs and compatible
dependencies.

::: general_manager.chat.planned.models.TaskStatus

::: general_manager.chat.planned.models.TerminalReason

::: general_manager.chat.planned.models.RequirementKind

::: general_manager.chat.planned.models.RoutingFeature

::: general_manager.chat.planned.models.PlanIntent

::: general_manager.chat.planned.models.CALCULATION_OPERATIONS

::: general_manager.chat.planned.models.EvidenceRequirement

::: general_manager.chat.planned.models.PlannedTask

::: general_manager.chat.planned.models.ValidatedPlan

::: general_manager.chat.planned.validation.MAX_ROOT_TASKS

::: general_manager.chat.planned.validation.MAX_CHILDREN_PER_ROOT

::: general_manager.chat.planned.validation.MAX_ROOT_DEPENDENCY_DEPTH

::: general_manager.chat.planned.validation.PlanValidationError

::: general_manager.chat.planned.validation.validate_plan

::: general_manager.chat.planned.validation.validate_dynamic_children

### Planned resolution, evidence, and calculations

`ManagerResolver` ranks up to five managers that are already present in the
schema index. `resolve(query, anchors=())` returns deterministic
`ManagerCandidate` records and may use anchors to prefer managers connected by
an exposed relation path.

::: general_manager.chat.planned.resolver.AUDIT_MATCH_SOURCES

::: general_manager.chat.planned.resolver.ManagerCandidate

::: general_manager.chat.planned.resolver.ManagerResolver

`EvidenceRecord` snapshots JSON payloads and read-only provenance. An
`EvidenceStore` owns turn-local records and links them to compatible
`EvidenceRequirement` objects. `canonical_call_identity()` produces stable
canonical JSON for a tool name and its arguments. `calculate_evidence()` allows
only the operations in `CALCULATION_OPERATIONS` and returns the derived value as
another immutable evidence record. Add that returned record to an
`EvidenceStore` explicitly when it should participate in later requirement
resolution; the calculation helper does not persist it itself.

::: general_manager.chat.planned.evidence.EvidenceError

::: general_manager.chat.planned.evidence.InvalidEvidenceError

::: general_manager.chat.planned.evidence.DuplicateEvidenceError

::: general_manager.chat.planned.evidence.EvidenceNotFoundError

::: general_manager.chat.planned.evidence.IncompatibleEvidenceError

::: general_manager.chat.planned.evidence.EvidenceLinkError

::: general_manager.chat.planned.evidence.EvidenceKind

::: general_manager.chat.planned.evidence.EvidenceRecord

::: general_manager.chat.planned.evidence.EvidenceStore

::: general_manager.chat.planned.evidence.canonical_call_identity

::: general_manager.chat.planned.calculations.CalculationError

::: general_manager.chat.planned.calculations.CalculationOperand

::: general_manager.chat.planned.calculations.calculate

::: general_manager.chat.planned.calculations.calculate_evidence

### Planned orchestration helpers

The lower-level planned modules are transport-neutral and are useful when an
application owns a custom chat transport or deterministic test harness. Normal
applications should configure `CHAT["planned"]` and use the existing transport
routes. `plan_request()` performs at most one correction and one fallback
attempt; `complete_provider_round()` accepts exactly one terminal `DoneEvent`
and either text or ordered tool calls with unique IDs; `synthesize_answer()`
references only eligible resolved evidence. `ProviderRoundResult.tool_calls`
contains every accepted call in order. Its original three positional arguments
and `tool_call` accessor remain supported; `tool_call` is the first call or
`None` for a text response.

::: general_manager.chat.planned.budget.RoundBudget

Normal planned-turn factories use `RoundBudget(root_ids, enforce_limits=False)`.
Counts remain integers; unbounded limits and remaining values are `None`.
Direct `RoundBudget(root_ids)` construction preserves the historical admission
limits for existing callers. Explicit numeric `global_limit` and `subtree_limit`
values remain enforceable in either mode.

::: general_manager.chat.planned.budget.RoundBudgetExhausted

::: general_manager.chat.planned.budget.BudgetExhaustedError

::: general_manager.chat.planned.planner.InvalidPlanError

::: general_manager.chat.planned.planner.PlanningResult

::: general_manager.chat.planned.planner.plan_request

::: general_manager.chat.planned.provider_calls.InvalidProviderRoundError

::: general_manager.chat.planned.provider_calls.ProviderRoundResult

::: general_manager.chat.planned.provider_calls.complete_provider_round


::: general_manager.chat.planned.synthesis.SynthesisFailedError

::: general_manager.chat.planned.synthesis.SynthesisResult

::: general_manager.chat.planned.synthesis.synthesize_answer

::: general_manager.chat.planned.events.PLANNED_PUBLIC_MESSAGES

::: general_manager.chat.planned.events.planned_done_event

::: general_manager.chat.planned.events.planned_error_event

::: general_manager.chat.planned.events.planned_tool_call_event

::: general_manager.chat.planned.events.planned_tool_result_event

::: general_manager.chat.planned.scheduler.SchedulerCallbacks

::: general_manager.chat.planned.scheduler.PlannedCoverage

::: general_manager.chat.planned.scheduler.PlannedExecutionResult

::: general_manager.chat.planned.scheduler.PreparedPlannedTurn

::: general_manager.chat.planned.scheduler.prepare_planned_turn

::: general_manager.chat.planned.scheduler.iter_planned_read_events


### GraphQL-native read contract (version 2)

At the root, `query.fields` selects row fields directly, for example
`["code"]`. The adapter adds the root `items` and `pageInfo` wrapper. Nested
collection selections still include their advertised `items`/`pageInfo` paths.

Read tools now use the exact executable GraphQL names. `get_manager_schema`
returns `contract_version: 2`, valid `roots`, scalar `fields`, directed
`relations` (including wrapper paths), root arguments, and named `types` with
nullability, defaults and input shapes. Domain manager names remain stable
identifiers for discovery and provenance. The runtime schema supplies all
executable names; no Python spelling aliases are accepted.

For example, use `isActive`, `densityGCm3`, `shippedAt_Gte` or an explicitly
customized field name exactly as returned by schema discovery. A former
`is_active` field request must be migrated, unless that is the actual GraphQL
name in a schema configured without automatic camel casing. Filtering likewise
uses the actual input object, including its declared relation depth. Relation
filters for managers without `chat_exposed = True` are omitted from discovery
and rejected before execution, including nested arguments and effective input
defaults. This chat boundary does not change public GraphQL permissions.

```json
{
  "manager": "Project",
  "filters": {"customer": {"code": "C01"}},
  "fields": [
    "code",
    {
      "field": "materialsList",
      "arguments": {"pageSize": 10},
      "fields": [{"items": ["code", "name"]}, {"pageInfo": ["totalCount"]}]
    }
  ]
}
```

The example requires those fields to be exposed by the application's schema.
Use `root` when a manager has several advertised roots. Optional `arguments`
contains exact root arguments, such as `orderBy`, `page`, or `includeInactive`
**only when advertised**. `filters` is a convenience input for the actual
filter argument (including its explicit custom name) and cannot also be
provided in `arguments`. `limit` and
`offset` retain their tool pagination semantics; when specifying a native page
greater than one, its page size must be explicit or resolvable from the schema
or configured cap. Native argument defaults are preserved before caps and
coverage calculations, at both root and nested pages. Nested selections support scalar
names, `{fieldName: [fields]}`, and `{field, arguments, fields}`. Output keys
retain their GraphQL names.

The response retains `data`, `total_count` and `has_more`, and adds `complete`.
`has_more` refers to the root page; `complete` describes whether this response
contains the full root result and complete selected nested pages. Missing nested
page totals cannot establish completeness. Partial responses remain usable for
bounded questions; exhaustive questions need all relevant pages. Configured
result caps also clamp supported nested pages.

`find_path` returns directed executable selection paths, including `items` for
page wrappers. It does not invent reverse edges. Inspect another manager when a
schema detail marks its output as a `reference`. Discovery and both transports'
planner payloads remain compact; expanded schema types are loaded on demand.
The existing 200,000-character provider guard remains unchanged.

Version 2 currently supports exposed manager roots returning generated pages,
object/leaf selections and typed arguments. Unions, interfaces, fragments,
arbitrary roots and Python aliases are unsupported. The read compiler uses
GraphQL ASTs and typed variables and executes existing resolvers with the same
request context and deadlines. Schema validation errors are actionable executor
feedback, not evidence. Other backend errors retain their generic public error.
REST serialization and mutation inputs/confirmation rules are unchanged. Existing
Python callers of the read tools must migrate their requested names explicitly;
there is no silent legacy conversion.

Structured clarification responses contain only `clarification.language` (`de`,
`en`, `fr`) and a nonempty unique `clarification.requirements` list drawn from
`criterion`, `metric`, `horizon`, `population`, `customer_identity`,
`record_selector`, and `unit`. The runtime renders fixed questions; this branch
accepts no free answer text, result claims, entity names, numbers, or evidence IDs.
The existing read plan must still resolve schema/identity evidence. Data answers
continue to require nonempty unique eligible `evidence_ids`; mixed answers and
questions use that grounded branch. The normal fallback, delivery, and persistence
paths apply to both response shapes.

### Deferred calculation binding validation

A deferred `bind_calculation` action is validated against concrete, same-task
source evidence before the binding becomes immutable. Each declared source must
already be linked to its requirement. Root query populations must be complete;
value paths must select numeric scalars and grouping/unit paths must be compatible.
A relation's `items` array is not a scalar, even when it contains one row. Query
the related manager as the root population instead. Derived bindings require
recomputed, compatible predecessor calculations. Failed admission leaves the
requirement unbound so the executor can correct its query or binding.

Rejected root query bindings now report each linked query evidence ID and its
reproduced source error, alongside the attempted value, grouping and unit paths.
Observed scalar paths come only from mappings in that source's actual root rows;
collections are never flattened and no field is inferred from schemas or prose.
The listed non-null scalar paths are present in every observed row. They describe
observed structure, not numeric suitability or a suggested binding. Heterogeneous
paths are counted separately; foreign and unlinked evidence is excluded.
Observations are bounded to 64 scalar paths, depth 16 and 4096 visited nodes per
row, with an explicit observation truncation marker. The existing 1024-character
feedback limit and `truncated` flag still apply. Diagnostics neither select a
source nor repair a binding, and the original admission validator is unchanged.
Unsupported diagnostic contexts retain their previous error message.

Repeated validation failures terminate only when the same normalized action and
feedback recur against unchanged structured task, dependency and evidence inputs,
after that feedback was supplied in a completed provider round. Corrected actions,
changed evidence and successful operations permit continued work. Diagnosed action
cycles carry `reason_origin=scheduler_validation_cycle` and a `validation_cycle`
proof containing the first, feedback-delivery and repeated pass numbers plus hashes
of action, feedback and evidence state. Audit records contain no raw input payloads.
Transport exceptions retain their separate origin and are not validation cycles.

### Schema transport projection

Planned requests can encode repeated schema definitions through the versioned
`gm.schema-data/1` format. This changes only the provider transport view. The
planner and executor select eligible schema positions from runtime evidence and
persisted schema-tool origins before serialization. Ordinary user text, query
rows, errors, native tool exchanges and application instructions are not searched
for JSON or schema-shaped values.

The `REFERENCE_DATA` user block retains its usual top-level fields and adds
`schema_transport`, containing the format explanation, a request-local object
table and an occurrence map. References are interpreted only at those declared
schema positions. They point backward within the table. Literal marker-shaped
objects are escaped. Every occurrence keeps its task, evidence or historical
origin binding even when several occurrences share an identical value. There is
no shared cache across requests or users. Equality of a value does not create an
evidence link or permission.

Historical schema text is eligible only when conversion of a persisted
`get_manager_schema` row supplies its structured result and origin. Its rendered
text must match that result exactly. Other historical text remains unchanged.
Original roles, indices, JSON formatting and text remain available to replay and
adjudication. The evaluator records original logical messages separately from
transport hashes and occurrence bindings, and profiles identify the transport
contract version.

Before dispatch, expansion is checked against the original reference and the
independent occurrence bindings. Invalid references, foreign bindings, unused
objects, expansion beyond bounded depth, work or size, and mismatched hashes are
rejected. Small requests retain their original encoding when projection would
not reduce the serialized size. The existing 200,000-character Responses request
guard still measures the final body, including protocol text, table and escaping.
It does not truncate data or retry an oversized request.

Lossless offline replay and smaller payloads do not establish model comprehension
or improved answers. A new live evaluation needs separately reviewed controls
bound to this source revision and transport version; previous controls and
historical scores are not relabeled.

### Completed task context for synthesis

The synthesizer also receives the objectives, requirement descriptions and
completion criteria of resolved root tasks. These are the actual runtime tasks,
including valid changes made during execution. Each requirement lists only
selected evidence records linked to it by `EvidenceStore.for_requirement()`;
evidence names are not used to infer links. Synthesis eligibility filtering still
applies. Query restrictions and completeness remain in the original call identity
and payload rather than a second interpretation.

This versioned internal context describes planner intent and runtime work. A
planner assumption is not user consent, and completed coverage does not establish
an unambiguous user scope. Visible user messages and the existing application
context retain their roles. No authoritative open-decision state is invented, and
no new clarification admission gate is introduced. Both supported response
branches and the bounded fallback retain their existing behavior.

HTTP, SSE and WebSocket answers do not automatically append this context, task
lists, completion criteria or internal evidence IDs. The model may use relevant
substantive details in its answer. This is a context improvement, not a proven
fix for any particular model response; live effectiveness remains unmeasured.


### Snapshot-bound schema inspection (protocol 3)

The public `get_manager_schema` tool, including Python calls through
`execute_chat_tool`, defaults to `view="overview"`. It returns exact executable
root argument signatures and defaults, output field signatures, relations and
`type_manifest`. Inspection protocol 3 includes only direct signature references
and visible relation manager references in the overview manifest. Transitive input
graphs are discovered from each subsequent detail manifest. Responses carry
`inspection_version=3`, and snapshot preimages bind projection version 3; old
version-2 snapshot tokens require fresh observation. The manifest reports input/object/enum/scalar definitions,
related manager references, or unsupported types. It does not recursively expand
fields or enum values. The full Python inspection APIs `manager_schema(manager)`,
`get_manager_schema_summary(manager)` and `tools.get_manager_schema(manager)`
retain full output by default for compatibility. For the last helper, use an
explicit `view` keyword to request a tagged selective view.

```python
overview = execute_chat_tool("get_manager_schema", {"manager": "Part"}, context)
# Choose these names from this observed type_manifest; never guess generated names.
needed = [name for name, info in overview["type_manifest"].items()
          if info["kind"] in {"input", "enum"}]
details = execute_chat_tool("get_manager_schema", {
    "manager": "Part", "view": "detail", "types": needed,
    "snapshot": overview["snapshot"],
}, context)
```

Detail requires a nonempty distinct list of exact reachable names and that
manager's snapshot. It returns each selected direct definition exactly as in the
full exposure-filtered GraphQL contract, without recursive expansion. It also returns
the selected definitions and their immediately referenced types in `type_manifest`.
Load further referenced definitions by these newly observed names at the same
manager snapshot. A manager reference is a boundary:
inspect its target manager's own overview and snapshot before requesting its
fields. Unions/interfaces/fragments remain unsupported. Full inspection is
available only by explicit `view="full"`; invalid or stale selectors return an
atomic error without partial definitions or automatic full fallback. Hidden
managers return no schema. Default, enum and exposure changes invalidate detail
tokens. Schema inspection is fresh on every tool call and never served from the
turn cache.

Planned schema requirements may declare `schema={"manager": "Part", "view":
"detail", "types": ["observedType"], "snapshot": "current"}`. `current` binds to
the latest successful schema capture across all tasks in the current turn for this manager; a literal
digest binds exactly that snapshot and must still be current. Overview/full
bindings use `types=[]`. Detail fragments become completion proof only when all
required types are covered at one current snapshot, and the completion selection
must include that full coverage. A full view can satisfy narrower scopes;
detail cannot satisfy overview. Snapshot mismatch or hidden-manager feedback
invalidates old proof until fresh observation. Requirements with no `schema`
binding retain full-inspection semantics. Prose descriptions never select scope.

`schema_complete` means a full contract was returned; `query.complete` describes
data population coverage. Selective projection intentionally does not round-trip
to the full schema. The v24 reference codec remains the separate lossless codec.
Measure complete discovery → details → query exchanges, including cumulative
request characters, when comparing model input size.

The lossless reference codec also applies to synthesis. It selects only schema
payloads from eligible resolved evidence and schema history with verified stored
tool origins. Each occurrence retains its own evidence, task, call and provenance
binding. The original reference, query results, history text and completed task
context reconstruct exactly; schema descriptions remain data without instruction
authority. Synthesis keeps its `RESOLVED_REFERENCE_DATA=` prefix. The internal
codec helper still defaults to `REFERENCE_DATA=` for planner/executor callers;
tool defaults and the production synthesis timeout of 30 seconds are unchanged.

Rejected planned `complete` actions retain strict evidence selection and requirement
coverage. `unsatisfied_requirements` feedback names uncovered requirement IDs and,
for a bound grouped reduction, the source query ID and missing or duplicate groups.
The groups come from the verified selected query population and declared binding;
nested relations and business descriptions do not define a different population.
Invalid or forged selected evidence receives no coverage claim. Diagnostic text
uses the existing 1024-character bound and explicitly marks truncation. Gather or
select the missing evidence before completing; repeated identical rejected actions
after delivered feedback retain the scheduler's validation-cycle stop.
