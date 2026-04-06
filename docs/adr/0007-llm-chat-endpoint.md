# ADR 0007: Automatic LLM chat endpoint with pluggable providers

## Status
Accepted

## Context
GeneralManager auto-generates GraphQL APIs from manager definitions, giving teams
a complete query and mutation layer with minimal boilerplate. A natural next step
is an LLM-powered chat endpoint that lets users interact with their data in
natural language — asking questions like "what projects contain parts with cobalt?"
and receiving answers grounded in the live GraphQL schema.

Key requirements gathered from design discussions:

- **Auto-generated endpoint** analogous to `/graphql/`, activated via settings.
- **Natural language to structured query**: the LLM queries and mutates managers
  on the user's behalf, using the existing GraphQL execution layer.
- **Pluggable LLM providers**: Ollama (local), Anthropic, OpenAI, and Google
  Gemini shipped by default, with a protocol-based interface for custom providers.
- **Permission-aware**: the LLM operates with the requesting user's permissions.
  Mutations require explicit allow-listing; managers can opt out of chat exposure.
- **WebSocket streaming** with SSE/HTTP fallback for environments that block WS.
- **Conversation persistence**: tied to authenticated user or anonymous session,
  with context window management (recent messages in full, older summarised).
- **Rate limiting**: configurable per-user request and token budgets.
- **Scalable tool design**: must work for deployments with 200+ managers without
  overwhelming the LLM's context window.

## Decision

### 1. Subpackage placement

Add `general_manager.chat` as an optional subpackage, following the pattern of
`general_manager.search` and `general_manager.workflow`. The feature is activated
via `GENERAL_MANAGER["CHAT"]` in Django settings.

`general_manager.chat` is a subpackage of the existing `general_manager` Django
app. It does **not** introduce a separate `ChatConfig(AppConfig)` or a second
migration package. Startup hooks are wired from
`general_manager.apps.GeneralmanagerConfig.ready()`, and any chat persistence
models live under the existing `general_manager` app label and migration tree.

### 2. Endpoint transport

- **Primary**: WebSocket via Django Channels (reuses existing Channels
  infrastructure from GraphQL subscriptions).
- **Fallback**: SSE over HTTP and a non-streaming HTTP POST endpoint for
  environments that block WebSocket connections.

### 3. Tool architecture — two-tier discovery

With 200+ managers, loading one tool per manager would consume 30-50k tokens of
context before the user asks a question. Instead, use a small fixed set of
meta-tools that let the LLM discover and query managers dynamically:

| Tool | Purpose |
|------|---------|
| `search_managers(query)` | Text search over GraphQL schema types (names, descriptions, fields) |
| `get_manager_schema(manager)` | Introspect a GraphQL type: fields, filters, nested types, relations |
| `find_path(from, to)` | Wrapper around `PathMap` — returns traversal path between two managers |
| `query(manager, filters, fields, limit, offset)` | Execute a structured query via GraphQL with user permissions |
| `mutate(mutation, input)` | Execute an allow-listed mutation via GraphQL with user permissions |

For small deployments (<30 managers), a `"tool_strategy": "direct"` setting loads
one tool per manager for simpler interactions.

### 4. Structured queries, not raw GraphQL

The LLM passes **structured parameters** (manager name, filter dict, field tree)
to the `query` tool. The framework translates these into valid GraphQL queries
internally. This avoids the fragility of LLMs constructing raw GraphQL strings
with project-specific filter conventions.

**Field tree format**: `fields` is a list of strings for scalar fields and
nested dicts for relations:

```python
# Scalar fields only:
fields=["name", "status"]

# With nested relation fields:
fields=["name", {"billOfMaterials": ["quantity", {"part": ["name", {"material": ["name"]}]}]}]

# Resulting GraphQL selection:
# { name status billOfMaterials { quantity part { name material { name } } } }
```

**Filter format**: filters use double-underscore lookup syntax matching the
GraphQL schema filter conventions:

```python
filters={"billOfMaterials__part__material__name__icontains": "cobalt"}
```

This keeps the LLM's job simple (build a dict and a nested list) while the
framework handles correct GraphQL generation, pagination, and permission
enforcement.

### 5. GraphQL schema and PathMap as complementary authorities

The GraphQL schema and `PathMap` serve complementary roles:

- Chat requires the GeneralManager GraphQL schema to already exist. If chat is
  enabled and `GraphQL.get_schema()` is still `None`, startup validation raises
  a configuration error. Chat does **not** build a private schema. In standard
  deployments this means GraphQL auto-generation must already be enabled.

- **GraphQL schema** is the authority for field shapes, filter types, mutation
  definitions, and manager descriptions. The `search_managers` and
  `get_manager_schema` tools introspect the schema.
- **`PathMap`** is the authority for relationship traversal between managers.
  It supports all interface types (Database, Calculation, ReadOnly, Request,
  ExistingModel) and pre-computes paths between every manager pair at startup.
  The `find_path` tool wraps `PathMap` and returns the attribute chain needed
  to navigate between any two managers.

Together they give the LLM complete knowledge: the schema tells it *what* each
manager looks like, and `PathMap` tells it *how* to get from one to another.

### 6. LLM provider interface

A minimal async protocol that all providers implement:

```python
class BaseLLMProvider(Protocol):
    async def complete(
        self, messages: list[Message], tools: list[ToolDefinition]
    ) -> AsyncIterator[ChatEvent]:
        """Stream a response. Yields text chunks or tool-call requests."""
        ...
```

Shipped providers: `OllamaProvider`, `AnthropicProvider`, `OpenAIProvider`,
`GoogleProvider`. Custom providers implement the same protocol.

**Provider operational controls**:

- **Request timeout**: each provider call is wrapped in an
  `asyncio.timeout()` with a configurable `provider_timeout_seconds`
  (default: 60). If the provider does not begin streaming within this
  window, the call is cancelled and the consumer sends an error event to the
  client.
- **Stalled stream detection**: if no chunk (text or tool call) is received
  for `provider_stream_timeout_seconds` (default: 30) after streaming has
  started, the stream is considered stalled. The consumer cancels the
  provider call, sends an error event, and does not retry.
- **Client disconnect cleanup**: when the WebSocket client disconnects
  mid-stream, the consumer cancels the in-flight provider call via
  `asyncio.Task.cancel()` in the consumer's `disconnect()` handler. This
  prevents abandoned provider calls from consuming resources. The same
  applies to SSE (client closes connection) and HTTP (request cancelled).
- **No provider-level retries**: provider adapters must not retry failed HTTP
  requests to the LLM API. Retries are managed exclusively by the tool loop
  (read-only tools only) and the user (for mutations). This avoids hidden
  duplicate calls and unpredictable latency.

**Configuration**:

```python
"provider_config": {
    "model": "llama3",
    "base_url": "http://localhost:11434",
    "timeout_seconds": 60,           # max wait for first chunk
    "stream_timeout_seconds": 30,    # max gap between chunks
},
```

### 7. Transport security

Every transport must enforce authentication and origin validation before
executing any LLM interaction. The implementation relies on Django and Channels
built-in security middleware wherever possible.

**WebSocket**:
- Wrapped in `channels.security.websocket.OriginValidator` (or
  `AllowedHostsOriginValidator` when the deploy uses `ALLOWED_HOSTS`) **plus**
  `channels.auth.AuthMiddlewareStack` for session/user resolution. Both are
  standard Channels components already used by the GraphQL subscription
  endpoint.
- `allowed_origins` in settings maps directly to the `OriginValidator`
  `allowed_origins` argument. When omitted, the framework falls back to
  `AllowedHostsOriginValidator`, which derives allowed origins from Django's
  `ALLOWED_HOSTS` setting.
- Anonymous users may connect and issue read-only queries. Mutation tool calls
  require an authenticated user — the `mutate` tool checks
  `scope["user"].is_authenticated` and returns an error if not.

**SSE (POST → streaming response)**:
- Django's `CsrfViewMiddleware` applies automatically (views are not
  CSRF-exempt).
- Authentication via Django's standard middleware stack:
  `SessionMiddleware` + `AuthenticationMiddleware`. The chat views read
  `request.user` — they do not implement any auth logic themselves.
- Deployments that require token-based auth (e.g., DRF
  `TokenAuthentication`, JWT) add the appropriate authentication middleware
  or backend to their Django `MIDDLEWARE` / `AUTHENTICATION_BACKENDS`
  settings. The chat views are auth-mechanism-agnostic — they only inspect
  `request.user.is_authenticated`.
- Anonymous users may query; mutations require authentication.

**HTTP POST (non-streaming)**:
- Same Django CSRF and middleware-based authentication as SSE.
- Same anonymous query / authenticated mutation split.

**Configuration**:

```python
GENERAL_MANAGER = {
    "CHAT": {
        ...
        # Optional: explicit WS origin allowlist.
        # If omitted, falls back to AllowedHostsOriginValidator
        # (derives from Django ALLOWED_HOSTS).
        "allowed_origins": ["https://app.example.com"],
    },
}
```

### 8. Permission model

- The LLM executes queries and mutations using the requesting user's permission
  context — the same `AdditiveManagerPermission` / `OverrideManagerPermission`
  system that governs direct GraphQL access.
- The chat endpoint itself is gated separately by
  `GENERAL_MANAGER["CHAT"]["permission"]`, an optional dotted-path callable
  `(user, context) -> bool` evaluated before any WebSocket connect or HTTP/SSE
  request is processed. `context` is the Channels scope or Django request.
- Mutations are **deny-by-default**: only mutations listed in
  `allowed_mutations` in settings can be invoked via chat.
- **Mutations require an authenticated user.** Anonymous sessions may issue
  read-only queries but never mutations, regardless of the allow-list.
- `allowed_mutations` is configured as a list of GraphQL mutation field names on
  the schema mutation root. This supports both auto-generated CRUD mutations and
  custom project-defined mutations through one consistent contract.
- Managers set `chat_exposed = False` to opt out entirely.
- `chat_exposed = False` removes a manager from all chat discovery surfaces:
  schema indexing, system prompt generation, relationship graph generation, and
  every tool.
- `excluded_managers` is not part of the contract. `chat_exposed = False`
  replaces it as the manager-level opt-out.

### 9. Mutation safety

**No auto-retry**: mutations are **never automatically retried** by the tool
loop. If a mutation tool call fails (network error, timeout, permission denied,
validation error), the error is returned to the LLM as a tool result. The LLM
may explain the failure to the user, but it must not re-issue the same mutation
without an explicit new user message requesting it.

This prevents duplicate side effects from:
- Provider-level retries (the provider adapter must not retry mutation requests).
- Tool loop recovery (the retry cap in query guardrails applies only to
  read-only tool calls; mutation failures do **not** consume the read-only
  retry budget, since they are a different category of failure).
- WebSocket reconnects (a reconnecting client starts a new assistant turn;
  the previous in-flight mutation is considered failed).

If a deployment needs idempotent mutations, the application should implement
idempotency keys at the GraphQL mutation level — this is outside the scope of
the chat framework.

**User confirmation for destructive mutations**: mutations listed in
`confirm_mutations` require an explicit user confirmation turn before
execution. When the LLM calls a mutation that is in the confirm list, the
framework intercepts the tool call and:

1. Sends a `{"type": "confirm_mutation", "name": "...", "args": {...}}`
   event to the client instead of executing immediately.
2. Waits for a `{"type": "confirm", "confirmed": true}` or
   `{"type": "confirm", "confirmed": false}` response from the client,
   with a configurable timeout (`confirm_timeout_seconds`, default: 30).
3. If confirmed, executes the mutation. If rejected or timed out, returns a
   cancellation result to the LLM and sends an error event to the client
   (on timeout).

Mutations **not** in `confirm_mutations` execute immediately (subject to the
allow-list and permission checks). This separates the "which mutations are
allowed" decision (`allowed_mutations`) from the "which mutations are
dangerous" decision (`confirm_mutations`).

**Transport-specific behaviour**:

- **WebSocket**: confirmation works as described above — the server sends a
  `confirm_mutation` event and waits for a `confirm` response.
- **SSE**: confirmation works the same way. The SSE stream sends a
  `confirm_mutation` event and pauses. The client sends a separate POST to a
  `/chat/confirm/` endpoint with the `confirmation_id` and `confirmed` flag.
  The SSE stream resumes after receiving the response or timing out.
- **HTTP POST (non-streaming)**: confirmed mutations are **rejected**. The
  response returns an error: `"Mutations requiring confirmation are not
  supported on the non-streaming HTTP transport. Use WebSocket or SSE."` This
  is because one-shot HTTP has no mechanism for a mid-request confirmation
  round-trip.

```python
"allowed_mutations": ["createOrder", "deleteProject", "archiveProject"],
"confirm_mutations": ["deleteProject"],  # subset of allowed_mutations
"confirm_timeout_seconds": 30,           # cancel if user doesn't respond
```

If `confirm_mutations` is not configured, no mutations require confirmation
(backward-compatible default). The system check validates that every entry in
`confirm_mutations` also appears in `allowed_mutations`.

### 10. Conversation management

- Conversations are stored server-side, tied to the authenticated user or
  anonymous session.
- WebSocket connections must establish a server-side session key for anonymous
  users before the first chat message is processed so persistence semantics match
  SSE/HTTP transports.
- Anonymous conversations are ephemeral and never merge with authenticated ones.
  Logging in starts a fresh conversation.
- Shared conversation state must survive multi-worker deployments. A follow-up
  SSE request or confirmation POST may be served by a different Django/ASGI
  worker process than the original stream, so conversation and pending
  confirmation state cannot live only in process memory.
- Context window management uses two thresholds:
  - `summarize_after` (default: 10): when the conversation exceeds this many
    messages, all messages older than the most recent `max_recent_messages`
    are summarised into a single summary message.
  - `max_recent_messages` (default: 20): the number of most recent messages
    always kept in full (never summarised).
  - Tool results from the most recent exchange are always preserved in full
    (required for follow-up questions like "in which projects are they?").
  - Example: at 25 messages with `summarize_after=10, max_recent_messages=20`,
    messages 1-5 are summarised, messages 6-25 are kept in full.
- Configurable TTL for conversation cleanup.

### 11. Query guardrails

- **Result limiting**: default cap on rows returned per query (e.g., 200).
  Tool results include `total_count` and `has_more` so the LLM can paginate or
  suggest filter refinement.
- **Query timeout**: database-level timeout per query (e.g., 5 seconds).
- **Retry cap**: max 3 tool call retries per user message. After that, the error
  is surfaced to the user.
- No hard depth limit on query joins — complex multi-hop queries are valid as
  long as they complete within the timeout.

### 12. Rate limiting

Per-user budgets configured in settings:

- `max_requests_per_hour`: caps the number of chat messages.
- `max_tokens_per_hour`: caps LLM token consumption.

### 13. WebSocket protocol

```
Client -> Server:
  {"type": "message", "text": "List parts containing cobalt"}
  {"type": "confirm", "confirmation_id": "abc123", "confirmed": true}

Server -> Client (streamed):
  {"type": "tool_call",        "name": "search_managers", "args": {...}}
  {"type": "tool_result",      "name": "search_managers", "data": [...]}
  {"type": "tool_call",        "name": "query", "args": {...}}
  {"type": "tool_result",      "name": "query", "data": [...]}
  {"type": "confirm_mutation",  "confirmation_id": "abc123",
                                "name": "deleteProject", "args": {...}}
  {"type": "text_chunk",       "content": "I found 3 parts..."}
  {"type": "text_chunk",       "content": " containing cobalt:\n1. ..."}
  {"type": "error",            "message": "Rate limit exceeded", "code": "..."}
  {"type": "done"}
```

Tool calls and results are streamed to the client for transparency.
`confirm_mutation` pauses execution until the client responds with a `confirm`
message.

### 14. System prompt

- **Auto-generated base**: derived from GraphQL schema — manager names,
  docstrings, field descriptions, and a compact relationship graph.
- **Developer additions**: optional `system_prompt` string in settings, appended
  to the auto-generated base (tone, domain context, language, behavioural rules).

### 15. Configuration

```python
GENERAL_MANAGER = {
    "CHAT": {
        "enabled": True,
        "url": "/chat/",
        "provider": "general_manager.chat.providers.OllamaProvider",
        "provider_config": {"model": "llama3", "base_url": "http://localhost:11434"},
        "permission": "path.to.chat_access",  # optional dotted-path callable
        "allowed_mutations": [
            "createOrder",
            "deleteProject",
            "archiveProject",
            "updateProjectStatus",
        ],
        "confirm_mutations": ["deleteProject"],  # require user confirmation
        "confirm_timeout_seconds": 30,
        "allowed_origins": ["https://app.example.com"],
        "system_prompt": "You are a helpful assistant for our logistics platform.",
        "tool_strategy": "discovery",   # "discovery" (default) or "direct"
        "rate_limit": {
            "max_requests_per_hour": 60,
            "max_tokens_per_hour": 100_000,
        },
        "query_limits": {
            "max_results": 200,
            "query_timeout_seconds": 5,
            "max_retries_per_message": 3,
        },
        "conversation": {
            "max_recent_messages": 20,
            "summarize_after": 10,
            "ttl_hours": 24,
        },
        "audit": {
            "enabled": True,
            "level": "tool_calls",       # "off" | "messages" | "tool_calls"
            "logger": "path.to.chat_audit_logger",
            "max_result_size": 4096,     # truncate tool results in audit log
            "redact_fields": [           # field names redacted in mutation inputs
                "password", "secret", "token", "key", "credential",
            ],
        },
    },
}
```

Manager-level opt-out:

```python
class SecretConfig(GeneralManager):
    chat_exposed = False
```

### 16. Evaluation framework

LLM behaviour is non-deterministic, so unit tests alone cannot verify that the
chat endpoint produces correct answers. A dedicated eval framework validates the
full chain: question → tool selection → query construction → data retrieval →
natural language answer.

Three eval dimensions, each with its own judge:

| Dimension | What it checks | Scoring | Pass threshold |
|-----------|---------------|---------|---------------|
| **Tool selection** | Correct tools called in a valid sequence with correct arguments | Binary per case | 100% |
| **Query correctness** | Final query returns the right data against a seeded database | Set comparison (precision + recall) | 100% |
| **Answer quality** | Expected facts from query results appear in the response | Fraction of expected facts present | >= 80% |

Eval cases are defined as YAML datasets with seeded fixtures, a conversation
(one or more turns), and deterministic expectations:

```yaml
- name: "cobalt_projects"
  description: "Multi-hop: material → part → BOM → project"
  setup:
    fixtures: "cobalt_test_data"
  conversation:
    - user: "What projects contain parts with cobalt?"
  expectations:
    tool_calls:
      - name: "search_managers"
      - name: "query"
        args_contain:
          manager: "Project"
    results_contain: ["Apollo", "Gemini"]
    results_exclude: ["Mercury"]
    answer_contains: ["Apollo", "Gemini"]
```

The eval runner supports provider comparison (`--compare ollama,anthropic,openai`)
and outputs a summary table with pass rates per dimension per provider.

Evals are built alongside Phase 1 (not after) so they serve as executable
specifications during development and enable provider comparison from day one.

## Alternatives considered

1. **One tool per manager**: simpler LLM interaction but does not scale beyond
   ~50 managers. Available as `"tool_strategy": "direct"` for small deployments.

2. **LLM constructs raw GraphQL strings**: leverages LLM's GraphQL knowledge but
   is fragile with project-specific filter conventions and Graphene quirks.
   Rejected in favour of structured parameters translated server-side.

3. **PathMap as sole schema source**: `PathMap` covers all interface types and
   excels at relationship traversal, but does not expose field shapes, filter
   types, or mutation definitions. Using it alone would require duplicating
   information already available in the GraphQL schema. Chosen as the
   relationship authority alongside GraphQL schema for everything else.

4. **Stateless conversations (client sends full history)**: pushes complexity to
   the client, requires sending large tool results back and forth, and makes
   context management harder. Rejected in favour of server-side storage.

5. **Hard query depth limits**: would block legitimate multi-hop queries. Result
   size caps and execution timeouts are better guardrails.

### 17. App configuration

The chat subpackage does **not** add a separate Django app config. Instead,
`general_manager.apps.GeneralmanagerConfig.ready()` calls into chat bootstrap
logic when `GENERAL_MANAGER["CHAT"]["enabled"]` is true:

- Builds the schema index and PathMap-derived relationship graph.
- Raises a startup/system-check error if the GraphQL schema has not already
  been created.
- Validates `allowed_mutations` and `confirm_mutations` against the GraphQL
  schema.
- Registers Django system checks (settings validation, production warnings).

Persistent chat models stay in the existing `general_manager` app and use the
existing migration package.

### 18. Signals

The chat subpackage emits Django signals for extensibility, following the
pattern of `django.db.models.signals`:

| Signal | Sent when | Kwargs |
|--------|-----------|--------|
| `chat_message_received` | Before LLM call | `user`, `message`, `conversation_id` |
| `chat_mutation_executed` | After mutation completes | `user`, `mutation`, `input`, `result` |
| `chat_tool_called` | After any tool call completes | `user`, `tool_name`, `args`, `result` |
| `chat_error` | On provider or tool failure | `user`, `error`, `context` |

Deployers can connect receivers for custom logging, notifications, monitoring,
or policy enforcement without subclassing any chat component.

## Consequences

- **Dependency model**: `channels` remains a core dependency of GeneralManager.
  Chat does not introduce a separate `chat = ["channels"]` extra. LLM provider
  SDKs remain optional dependencies, installed via provider-specific extras:
  ```toml
  [project.optional-dependencies]
  chat-ollama = ["ollama"]
  chat-anthropic = ["anthropic"]
  chat-openai = ["openai"]
  chat-google = ["google-generativeai"]
  ```
  Configuring a provider whose SDK is not installed raises a clear import-time
  error: `"To use AnthropicProvider, install: pip install general-manager[chat-anthropic]"`.
  The provider interface is open for custom implementations.
- **Database tables**: conversation, message, and pending-confirmation storage
  adds new Django models under the existing `general_manager` app.
- **Existing code impact**: minimal. A `chat_exposed` class attribute is added to
  the `GeneralManager` base class (defaults to `True`). No changes to existing
  interfaces, permissions, or GraphQL schema generation.
- **Mutation configuration contract**: mutation exposure is controlled by exact
  GraphQL mutation field names, so generated and custom mutations are configured
  uniformly and can be validated against the schema at startup.
