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
| `permission` | `None` | Callable or dotted path receiving `(user, scope)`. Returning `False` denies the request or socket. |
| `allowed_origins` | `None` | Explicit WebSocket origin list. When empty, Channels' allowed-host origin validator is used. |
| `allowed_mutations` | `[]` | Exact generated GraphQL mutation names the `mutate` tool may execute. |
| `confirm_mutations` | `[]` | Allowed mutation names that require client confirmation. Every name must also be in `allowed_mutations`. |
| `confirm_timeout_seconds` | `30` | Lifetime of a pending mutation confirmation. |
| `max_results` | `200` | Maximum query page size accepted by the chat query tool. |
| `query_timeout_seconds` | `None` | Optional database query timeout in seconds. It is converted to milliseconds for supported database execution. |
| `max_retries_per_message` | `3` | Maximum non-mutation tool-loop retries in one user turn. |
| `tool_strategy` | `"discovery"` | `discovery` exposes the stable discovery tool set; `direct` adds one query tool per exposed manager. |
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
to that SDK.

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
| `mutate` | `mutation`, `input`, `confirmed` | Mutation result, denial, or confirmation requirement |

Only managers with `chat_exposed = True` are accepted. Query fields, nested
selections, filters, limits, and offsets are validated against the indexed
schema before execution. Mutations require an authenticated user and an exact
name in `allowed_mutations`.

## Persistence and cleanup

Chat persistence is installed with the GeneralManager Django migrations:

```bash
python manage.py migrate
```

`ChatConversation` belongs to an authenticated user or anonymous Django
session. `ChatMessage` records ordered conversation and tool items.
`ChatPendingConfirmation` stores confirmation state, including expiry and
resolution metadata, until cleanup removes the record.

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

Planned orchestration is opt-in. Legacy `provider` and `provider_config` keep
their existing behavior; when `planned.enabled` is false, GeneralManager selects
the legacy loop before it creates a planner. A planned read that the planner
classifies as a mutation is sent through that unchanged legacy loop, so planned
executors never receive the `mutate` tool.

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
                "simple_executor": "fast_local",
                "complex_executor": "strong_local",
                "synthesizer": "strong_local",
                "fallback_executor": "strong_local",
            },
            "max_concurrent_tasks": 3,
            "evidence_timeout_seconds": 90,
            "synthesis_timeout_seconds": 30,
        },
    },
}
```

The required role names are `planner`, `simple_executor`, `complex_executor`,
`synthesizer`, and `fallback_executor`. If `provider_profiles` is omitted,
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
