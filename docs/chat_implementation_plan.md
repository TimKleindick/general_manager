# LLM Chat Endpoint — Implementation Plan

> Companion to [ADR 0007](adr/0007-llm-chat-endpoint.md).

## Overview

This plan breaks the chat endpoint into four phases. Each phase produces a
working, testable increment. Later phases build on earlier ones but can be
scoped independently.

---

## Phase 1: Core loop (MVP)

**Goal**: A working WebSocket chat endpoint with a single LLM provider, basic
tool set, and in-memory conversations. Enough to demo "ask a question, get an
answer from live data".

### 1.1 Package scaffolding

Create `src/general_manager/chat/` with:

```
chat/
├── __init__.py
├── bootstrap.py         # Initialization hooks called from GeneralmanagerConfig.ready()
├── consumer.py          # Channels WebSocket consumer
├── routing.py           # URL routing for WS endpoint
├── tools.py             # Tool definitions and registry
├── schema_index.py      # GraphQL schema introspection + search index
├── system_prompt.py     # Auto-generates system prompt from schema
├── signals.py           # Django signals for extensibility
├── checks.py            # Django system checks
├── audit.py             # Chat-specific audit event/logger contract (Phase 2)
├── providers/
│   ├── __init__.py
│   └── base.py          # BaseLLMProvider protocol
├── models.py            # Conversation/message persistence models (Phase 3)
└── settings.py          # Setting defaults, validation, accessor
```

`general_manager.chat` stays inside the existing `general_manager` Django app.
It does **not** introduce a separate `ChatConfig(AppConfig)` or a second
migration package. `general_manager.apps.GeneralmanagerConfig.ready()` calls
into `chat/bootstrap.py` when chat is enabled, and persistent chat models use
the existing `general_manager` app label and `src/general_manager/migrations/`.

`bootstrap.py`:
- Builds the schema index and PathMap-derived relationship graph.
- Fails fast when chat is enabled but the GeneralManager GraphQL schema has not
  been created yet. Chat does **not** build a private schema; standard setups
  must enable GraphQL schema generation first (typically
  `AUTOCREATE_GRAPHQL=True`).
- Validates `allowed_mutations` and `confirm_mutations` against the schema.
- Registers Django system checks from `checks.py`.

`signals.py` defines Django signals for extensibility:
- `chat_message_received` — before LLM call (for logging, filtering).
- `chat_mutation_executed` — after mutation completes (for notifications).
- `chat_tool_called` — after any tool call completes.
- `chat_error` — on provider or tool failure (for monitoring).

**Acceptance criteria**:
- Package importable, no side effects on import.
- Chat initialization runs from `GeneralmanagerConfig.ready()` when chat is enabled.
- Settings validated at Django `check` time when `enabled = True`.
- Enabling chat while no GraphQL schema exists raises a clear startup/check error
  instead of building a private schema.
- No separate Django app or migration package is introduced for chat.
- Configuring a provider whose SDK is not installed raises a clear error:
  `"To use AnthropicProvider, install: pip install general-manager[chat-anthropic]"`.

### 1.2 LLM provider protocol + first provider

Define `BaseLLMProvider`:

```python
class BaseLLMProvider(Protocol):
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition],
    ) -> AsyncIterator[ChatEvent]: ...
```

`ChatEvent` is a tagged union:
- `TextChunkEvent(content: str)`
- `ToolCallEvent(id: str, name: str, args: dict)`
- `DoneEvent(usage: TokenUsage)`

Implement `OllamaProvider` first (no API key needed, easiest for local dev).

**Provider operational controls** (implemented in the base consumer, not
per-provider):
- **Request timeout**: `asyncio.timeout(provider_config["timeout_seconds"])`
  wraps each `complete()` call. Default: 60s. Cancels if no chunk arrives.
- **Stalled stream detection**: if no chunk is received for
  `provider_config["stream_timeout_seconds"]` (default: 30s) after streaming
  has started, cancel the provider call and send an error event.
- **Client disconnect cleanup**: `consumer.disconnect()` cancels the
  in-flight provider task via `asyncio.Task.cancel()`. Prevents abandoned
  provider calls from consuming resources.
- **No provider-level retries**: provider adapters must not retry failed HTTP
  requests. All retry logic is in the tool loop (read-only only).

**Acceptance criteria**:
- Provider can stream a text response with tool calls.
- Provider call is cancelled when request timeout is exceeded.
- Provider call is cancelled when stream stalls beyond the gap timeout.
- Provider call is cancelled on client disconnect (no resource leak).
- Unit tests with mocked HTTP (no real Ollama needed).

### 1.3 Tool system (discovery tier)

Implement the five meta-tools:

| Tool | Backed by |
|------|-----------|
| `search_managers` | `schema_index.py` — text search over GraphQL types |
| `get_manager_schema` | GraphQL schema introspection |
| `find_path` | `PathMap` wrapper |
| `query` | GraphQL executor with structured params → GraphQL translation |
| `mutate` | GraphQL executor, checks `allowed_mutations` |

The `query` tool:
- Accepts `manager`, `filters` (dict), `fields` (field tree), `limit`, `offset`.
- `fields` is a list of strings for scalar fields and nested dicts for
  relations, e.g.:
  ```python
  fields=["name", {"billOfMaterials": ["quantity", {"part": ["name"]}]}]
  # → GraphQL: { name billOfMaterials { quantity part { name } } }
  ```
- `filters` use double-underscore lookup syntax matching the GraphQL schema:
  ```python
  filters={"billOfMaterials__part__material__name__icontains": "cobalt"}
  ```
- Translates to a valid GraphQL query string internally.
- Executes via the existing GraphQL schema with the user's permission context.
- Returns `{"data": [...], "total_count": N, "has_more": bool}`.

All discovery surfaces use the same exposed-manager filter:
- `chat_exposed = False` removes a manager from `search_managers`,
  `get_manager_schema`, `find_path`, the generated system prompt, and all tool
  execution.
- `find_path` only returns paths within the chat-exposed manager graph.
- `excluded_managers` is not part of the contract. Manager-level opt-out is
  `chat_exposed = False`.

The `mutate` tool:
- Accepts `mutation` (GraphQL mutation field name) and `input` (dict).
- Resolves allowed mutations against the GraphQL schema mutation root fields.
- Supports both auto-generated CRUD mutations and custom project-defined mutation
  fields using the same allow-list format.
- If the mutation is in `confirm_mutations`, the tool sends a
  `confirm_mutation` event to the client and waits for user confirmation
  before executing. Rejected mutations return a cancellation result to the
  LLM.

**Acceptance criteria**:
- `search_managers("part material")` returns relevant managers.
- `get_manager_schema("Part")` returns fields, filters, relations.
- `find_path("Material", "Project")` returns path via `PathMap`.
- `query("Part", {"material__name__icontains": "cobalt"}, ["name"])` returns
  filtered results with count.
- `mutate` rejects mutations not in `allowed_mutations`.
- Hidden managers never appear in discovery results, the relationship graph, or
  the system prompt.
- Integration tests against example project managers.

### 1.4 System prompt builder

Auto-generate from two sources:
- **GraphQL schema**: manager names + docstrings (one line each), available tool
  descriptions.
- **PathMap**: compact relationship graph (adjacency list) derived from
  `PathMap.mapping`, which covers all interface types.
- Append developer-provided `system_prompt` from settings.

**Acceptance criteria**:
- System prompt stays under 4k tokens for the example project.
- Relationship graph includes paths across all interface types (Database,
  Calculation, ReadOnly, etc.).
- Managers with `chat_exposed = False` are excluded from the generated prompt
  and relationship graph.

### 1.5 WebSocket consumer + conversation identity bootstrap

`ChatConsumer` (Django Channels `AsyncJsonWebsocketConsumer`):

1. Authenticate user on connect (reuse existing Channels auth).
2. Run the chat endpoint permission check before accepting or processing
   messages.
3. Resolve conversation identity on connect:
   - Reuse `channels.sessions.SessionMiddleware`/existing ASGI session support.
   - If the user is anonymous and the scope has no session key yet, create one
     and save the session before accepting chat messages.
   - Store `user` plus `session_key` in consumer state so WS, SSE, and HTTP
     flows can all use the same conversation lookup contract.
4. On `{"type": "message", "text": "..."}`:
   - Build messages list (system prompt + conversation history + new message).
   - Call provider's `complete()`.
   - Stream `tool_call`, `tool_result`, `text_chunk`, `done` events to client.
   - Execute tool calls in the loop (call tool → feed result back to LLM →
     continue until LLM produces final text).
   - For mutations in `confirm_mutations`: send a `confirm_mutation` event
     to the client and await a `confirm` response with a configurable
     timeout (`confirm_timeout_seconds`, default: 30s). If the user rejects
     or the timeout expires, return a cancellation result to the LLM.
5. Concurrent message guard: if a second `message` arrives while a turn is
   in progress, reject it with an error event. One active turn per
   conversation at a time.
6. In-memory conversation history (dict keyed by channel name) for now.

**Acceptance criteria**:
- End-to-end test: connect via WS, send question, receive streamed answer with
  tool calls visible.
- Anonymous WS connection gets a stable session key before the first chat
  message is processed.
- Endpoint-level permission denial happens before any provider or tool work.
- Permission denied on query → LLM receives error, explains to user.
- Works with example project.

### 1.6 Settings integration + URL wiring

- `GENERAL_MANAGER["CHAT"]` setting with defaults and validation.
- `GENERAL_MANAGER["CHAT"]["permission"]` is an optional dotted-path callable
  `(user, context) -> bool`, evaluated before any chat request is processed.
  `context` is the Channels scope for WebSocket and the Django request for
  HTTP/SSE. `None` means allow access.
- Auto-register WS route at configured URL when `enabled = True`.
- Django system check: verify the provider is importable, the permission hook is
  importable when configured, and the GraphQL schema already exists.

**Acceptance criteria**:
- Endpoint activates/deactivates based on `enabled` flag.
- Meaningful error if the GraphQL schema is unavailable or the provider is
  misconfigured.

### 1.7 Transport security

Implement security controls for the WebSocket transport in Phase 1 using
Django and Channels built-in middleware. SSE/HTTP transports inherit Django's
built-in CSRF and session middleware when added in Phase 4.

**WebSocket origin validation** (Channels built-in):
- Wrap the chat consumer in `channels.security.websocket.OriginValidator`
  with the configured `allowed_origins` list, or fall back to
  `channels.security.websocket.AllowedHostsOriginValidator` (derives from
  Django's `ALLOWED_HOSTS`) when `allowed_origins` is not set.
- In `DEBUG=True` mode, `AllowedHostsOriginValidator` accepts `localhost`
  origins by default, matching standard Django dev behaviour.

**Authentication** (Channels built-in):
- Wrap the consumer in `channels.auth.AuthMiddlewareStack` for session and
  user resolution (same pattern used by the existing GraphQL subscription
  endpoint).

**Endpoint authorization**:
- The chat endpoint itself is gated by `GENERAL_MANAGER["CHAT"]["permission"]`,
  an optional dotted-path callable `(user, context) -> bool`.
- The callable runs before the consumer accepts a WebSocket connection and
  before SSE/HTTP requests begin provider execution.
- This is the chat-level access gate. Manager permissions still apply inside
  `query` and `mutate`.

**Mutation authentication gate**:
- The `mutate` tool checks `scope["user"].is_authenticated` before executing.
  Anonymous users receive a clear error: mutations require authentication.
- This check lives in the tool itself (not the consumer) so it applies
  uniformly across all transports.

**Mutation no-retry policy**:
- Mutations are **never automatically retried** by the tool loop. A failed
  mutation returns the error to the LLM as a tool result. The LLM may explain
  the failure but must not re-issue the mutation without a new user message.
- The provider adapter layer must not retry POST requests for mutation tool
  calls.
- The `max_retries_per_message` counter applies only to read-only tool calls.
  Mutation failures do **not** consume this counter — they are a separate
  category and are never re-attempted.

**Acceptance criteria**:
- WS connection from unlisted origin is rejected at handshake.
- `AllowedHostsOriginValidator` fallback works when `allowed_origins` is not
  configured.
- Anonymous user calling `mutate` receives an authentication-required error.
- Failed mutation is not re-issued by the tool loop.

### 1.8 Evaluation framework

Build the eval harness alongside the MVP so eval cases serve as executable
specifications during development.

#### Package structure

```
chat/
└── evals/
    ├── __init__.py
    ├── runner.py              # Runs eval suite, collects and reports scores
    ├── datasets/
    │   ├── basic_queries.yaml     # Simple single-manager questions
    │   ├── multi_hop.yaml         # Cross-manager relationship queries
    │   ├── mutations.yaml         # Mutation requests (allowed + denied)
    │   ├── follow_ups.yaml        # Multi-turn conversations
    │   └── edge_cases.yaml        # Permission denied, empty results, ambiguous
    └── judges/
        ├── tool_sequence.py       # Asserts correct tool call sequence + args
        ├── result_accuracy.py     # Asserts correct data in query results
        └── answer_quality.py      # Keyword/fact checking in natural language response
```

#### Eval case format

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
    answer_excludes: ["Mercury"]
```

#### Scoring

| Dimension | Scoring | Pass threshold |
|-----------|---------|---------------|
| Tool selection | Binary: correct sequence and arguments | 100% |
| Query correctness | Set comparison: precision + recall on result set | 100% |
| Answer quality | Fraction of expected facts present in response | >= 80% |

Tool selection and query correctness must be 100% — failures are bugs. Answer
quality has tolerance because phrasing varies across providers and runs.

#### Runner CLI

```bash
# Run full eval suite against a provider
python -m general_manager.chat.evals --provider ollama --model llama3

# Run specific dataset
python -m general_manager.chat.evals --dataset multi_hop

# Compare providers side-by-side
python -m general_manager.chat.evals --compare ollama,anthropic,openai,google
```

Output: summary table with pass rates per dimension per provider, plus detailed
failure logs for debugging.

#### Eval datasets to ship with Phase 1

| Dataset | Cases | Tests |
|---------|-------|-------|
| `basic_queries` | 5-10 | Single manager, simple filters, field selection |
| `multi_hop` | 5-10 | Cross-manager joins via `find_path`, 2-4 hops |
| `follow_ups` | 3-5 | Multi-turn conversations with context references |
| `edge_cases` | 3-5 | Empty results, permission denied, ambiguous queries |

Additional datasets (`mutations`, provider-specific edge cases) added in later
phases.

**Acceptance criteria**:
- Eval runner executes all datasets and produces a pass/fail summary.
- At least `basic_queries` and `multi_hop` pass at 100% tool selection and
  query correctness with the Ollama provider.
- `--compare` mode produces a side-by-side table across multiple providers.
- Eval cases use seeded test data (factories) for deterministic expectations.

---

## Phase 2: Guardrails, rate limiting, and production hardening

**Goal**: Production-safe controls before expanding the attack surface with
additional providers and transports. This phase is a prerequisite for Phase 4.

### 2.1 Rate limiting

Implement per-user rate limiter:
- Track requests and tokens per user per rolling time window.
- Check before calling provider; return error event if exceeded.
- Use Django cache backend for counters (works across processes).

**Acceptance criteria**:
- Rate limit enforced on WS transport (extended to SSE/HTTP in Phase 4).
- Clear error message to user when limit hit.
- Anonymous users rate-limited by session/IP.

### 2.2 Query guardrails

- `max_results` enforced in `query` tool — add `LIMIT` and return `total_count`.
- `query_timeout_seconds` — set database statement timeout per query.
- `max_retries_per_message` — cap **read-only** tool call loop iterations,
  surface error after limit. Mutations are never auto-retried (see Phase 1.7).

**Acceptance criteria**:
- Query returning 50k rows gets capped, LLM sees count and `has_more`.
- Slow query times out, LLM receives timeout error.
- Infinite tool-call loop is impossible.

### 2.3 Audit logging

- Log chat interactions: user, timestamp, message, tool calls.
- Implement a chat-specific audit pipeline in `chat/audit.py`.
- Configurable log level (off, messages only, messages + tool calls).

**Audit contract**:

- Chat audit does **not** reuse `PermissionAuditEvent` or
  `GENERAL_MANAGER["AUDIT_LOGGER"]`. The existing permission audit pipeline is
  specific to permission evaluations.
- `GENERAL_MANAGER["CHAT"]["audit"]` configures chat audit behaviour, redaction,
  truncation, and the chat audit sink.
- Projects that want a single sink for permission and chat events can add an
  adapter, but that bridge is separate from the core chat contract.

**Data safety rules**:

- **Tool result truncation**: query results logged in audit records are
  truncated to `audit.max_result_size` (default: 4 KB). The full result is
  available in the conversation history (Phase 3) but not duplicated into
  the audit log.
- **Mutation input redaction**: mutation `input` dicts are logged, but fields
  whose names match a configurable pattern (default:
  `["password", "secret", "token", "key", "credential"]`) are replaced with
  `"[REDACTED]"`. Developers can extend this list via
  `audit.redact_fields`.
- **No raw LLM prompts in audit**: the full system prompt and assembled
  message list are not written to the audit log. Only the user's message
  text, tool call names/args (redacted), and truncated results are persisted.
- **Retention**: audit records are subject to the same `ttl_hours` as
  conversation records. A management command (`chat_cleanup`) purges expired
  audit and conversation records together.

**Configuration**:

```python
"audit": {
    "enabled": True,
    "level": "tool_calls",           # "off" | "messages" | "tool_calls"
    "logger": "path.to.chat_audit_logger",  # optional dotted-path sink
    "max_result_size": 4096,         # bytes, truncate tool results beyond this
    "redact_fields": ["password", "secret", "token", "key", "credential"],
},
```

**Acceptance criteria**:
- Audit records are created for each chat interaction at the configured level.
- Tool results exceeding `max_result_size` are truncated in audit records.
- Mutation inputs with sensitive field names are redacted.
- `chat_cleanup` purges records older than `ttl_hours`.

### 2.4 Mutation allow-list validation

Note: basic validation of `allowed_mutations` and `confirm_mutations` against
the GraphQL schema happens in Phase 1 (`bootstrap.py` at startup). This phase
hardens that validation:

- Move validation from startup-time to a Django system check so it also runs
  via `manage.py check --deploy`.
- Add detailed error messages identifying which mutation names are invalid and
  suggesting close matches (typo detection).
- Verify that `confirm_mutations ⊆ allowed_mutations`.

**Acceptance criteria**:
- Auto-generated CRUD mutation can be allowed by name and invoked successfully.
- Custom GraphQL mutation can be allowed by name and invoked successfully.
- Unknown configured mutation name raises a system check error with a
  suggested correction.
- `confirm_mutations` entry not in `allowed_mutations` raises a system check
  error.

---

## Phase 3: Persistence and conversation management

**Goal**: Durable conversations with context window management.

### 3.1 Conversation and message models

Create `chat/models.py` with plain Django models:

- `ChatConversation`: user (nullable FK), session_key (for anonymous),
  created_at, updated_at.
- `ChatMessage`: conversation FK, role (user/assistant/system/tool),
  content, tool_name, tool_args, tool_result, created_at.
- `ChatPendingConfirmation`: conversation FK, confirmation_id, mutation name,
  payload, expires_at, resolved_at. Used for cross-request confirmation flows.

These models live under the existing `general_manager` Django app and use the
existing migration package (`src/general_manager/migrations/`). Use
`settings.AUTH_USER_MODEL` for the user relation.

**Acceptance criteria**:
- Migrations generated and applied cleanly.
- Conversation tied to user when authenticated, session when anonymous.
- Pending confirmations survive a follow-up request handled by a different
  server process.
- `chat_cleanup` management command exists at
  `src/general_manager/management/commands/chat_cleanup.py`.

### 3.2 Context window management

Implement conversation-to-messages builder:
- Keep last `max_recent_messages` in full.
- When message count exceeds `summarize_after`, summarise older messages
  using the LLM (single summarisation call, cached).
- Always preserve tool results from the most recent exchange.

**Acceptance criteria**:
- Long conversation (50+ messages) stays within context budget.
- Follow-up questions work correctly after summarisation.
- Summary is stored to avoid re-summarising on every request.

### 3.3 Anonymous-to-authenticated boundary

- Logging in starts a fresh authenticated conversation.
- Anonymous conversations are not merged or migrated.

**Acceptance criteria**:
- User with an anonymous conversation who logs in gets a new, empty
  authenticated conversation.
- The previous anonymous conversation remains accessible by session key
  until TTL expiry but is not linked to the user.

### 3.4 Retention cleanup command

- Add `src/general_manager/management/commands/chat_cleanup.py`.
- Purge expired conversations, messages, pending confirmations, summaries, and
  chat audit records based on `ttl_hours`.

**Acceptance criteria**:
- `python manage.py chat_cleanup` removes expired chat records only.
- The command is idempotent and safe to run on a schedule.

---

## Phase 4: Additional providers and transport fallbacks

**Goal**: All four providers working, plus SSE/HTTP fallback. Requires Phase 2
(guardrails) so new attack surface is covered from the start.

### 4.1 Remaining LLM providers

Implement using the same `BaseLLMProvider` protocol:

- `AnthropicProvider` — Claude API with tool use and streaming.
- `OpenAIProvider` — GPT API with function calling and streaming.
- `GoogleProvider` — Gemini API with tool use and streaming.

Each provider:
- Translates the internal `Message`/`ToolDefinition` format to the
  provider-specific SDK format.
- Handles streaming responses and maps back to `ChatEvent`.
- Reports token usage in `DoneEvent`.
- Must not retry mutation tool call requests at the HTTP level.

**Acceptance criteria**:
- Each provider passes the same integration test suite (parameterised).
- Token usage reported accurately for rate limiting.

### 4.2 SSE transport

Add an SSE endpoint (Django view, no Channels dependency):
- Same message flow as WS but over server-sent events.
- Client sends POST with message, receives SSE stream of events.
- Conversation ID in request/response for follow-ups.
- Standard Django CSRF protection applies (views are not CSRF-exempt).
- Authentication via Django's standard middleware stack. The view reads
  `request.user` — it does not implement any auth logic. Deployments
  requiring token-based auth add the appropriate middleware or backend to
  their Django settings.
- Session is validated at request start. If the session expires mid-stream,
  the current stream continues (already authenticated), but the next request
  will require re-authentication.
- Client disconnect cancels the in-flight provider task (same cleanup as WS).
- Concurrent messages: if a second message arrives while the first is still
  being processed, the second is rejected with an error event. One active
  turn per conversation at a time.
- SSE depends on Phase 3 persisted conversation and pending-confirmation state:
  the follow-up POST or confirmation request may be handled by a different
  Django/ASGI worker process than the original stream, so Phase 1 in-memory
  channel state is not sufficient.
- **Mutation confirmation**: SSE streams send a `confirm_mutation` event and
  pause. The client sends a separate POST to `/chat/confirm/` with the
  `confirmation_id` and `confirmed` flag. The stream resumes after the
  response or after `confirm_timeout_seconds`.

### 4.3 Non-streaming HTTP endpoint

Simple POST → JSON response for one-shot questions:
- Buffers the full response, returns as single JSON payload.
- Useful for API integrations and environments hostile to streaming.
- Same Django CSRF and middleware-based authentication as SSE.
- **Confirmed mutations are rejected** on this transport. If the LLM calls a
  mutation in `confirm_mutations`, the tool returns an error and the response
  explains that confirmed mutations require WebSocket or SSE. Non-confirmed
  mutations execute normally.

**Acceptance criteria (SSE)**:
- SSE stream delivers the same event types as WebSocket.
- SSE mutation confirmation round-trip works via the `/chat/confirm/` endpoint.
- SSE client disconnect cancels the in-flight provider task.
- CSRF protection rejects requests without a valid CSRF token.
- Rate limiting applies to SSE requests.
- Session validated at request start; mid-stream session expiry does not
  interrupt the current stream.

**Acceptance criteria (HTTP POST)**:
- HTTP POST returns a complete JSON response with tool calls and answer.
- Mutations in `confirm_mutations` are rejected with a clear error message.
- CSRF protection and rate limiting apply.

**Acceptance criteria (cross-transport)**:
- All transports execute the same tool sequence and return the same structured
  result set for a given question against deterministic test data. Natural
  language phrasing may vary — answer quality is validated by the eval
  framework, not by string comparison.
- SSE and HTTP do not require WebSocket transport setup, even though Channels
  remains a core GeneralManager dependency.

### 4.4 Direct tool strategy

For deployments with <30 managers, implement `"tool_strategy": "direct"`:
- Generate one tool per exposed manager (with fields and filters as parameters).
- Skip the search/schema introspection step.
- Same query execution path underneath.
- `tool_strategy` is evaluated at startup and cached. Changing the strategy
  requires a restart.

**Acceptance criteria**:
- `"direct"` strategy generates one query tool per exposed manager.
- `"direct"` strategy passes the same eval datasets as `"discovery"`.
- Managers with `chat_exposed = False` are excluded from tool generation.

---

## Phase summary

| Phase | Delivers | Depends on |
|-------|----------|------------|
| 1     | Working chat over WS with Ollama, in-memory conversations, transport security, eval framework | — |
| 2     | Rate limits, query guards, audit, mutation validation | Phase 1 |
| 3     | Persistent conversations, context management | Phase 1 |
| 4     | All 4 providers, SSE + HTTP fallback, direct tool strategy | Phase 1 + Phase 2 + Phase 3 |

Phases 2 and 3 are independent of each other. Phase 4 requires both:
- Phase 2, because guardrails must be in place before expanding the attack surface.
- Phase 3, because SSE follow-ups and confirmation round-trips require shared
  durable state rather than Phase 1's in-memory conversation storage.

### Production readiness gate

Phase 1 alone is **not production-safe**. It uses in-memory conversation
state, which is lost on process restart and inconsistent across workers in
multi-process deployments.

**Minimum for production enablement**: Phases 1 + 2 + 3.

- Phase 2 provides rate limiting, query guardrails, and audit logging.
- Phase 3 provides durable conversation persistence across processes and
  restarts.

The Django system check emits an **error** (not a warning) when `DEBUG=False`,
chat is enabled, and the conversation model tables do not exist. This blocks
`manage.py check --deploy` and prevents `manage.py runserver` from starting,
making it impossible to accidentally deploy with lossy in-memory conversation
state in production. During development (`DEBUG=True`), in-memory
conversations work without any check failure.

---

## Testing strategy

### Unit and integration tests

- **Unit tests**: each tool, provider (mocked), system prompt builder,
  schema index, rate limiter, context manager.
- **Integration tests**: end-to-end WS/SSE/HTTP flows against example project
  managers. Parameterised across providers using a mock LLM that returns
  scripted tool calls.
- **Permission tests**: verify chat respects manager permissions, mutation
  allow-list, and `chat_exposed = False`.
- **Load/stress tests**: verify rate limiting, query timeouts, result capping
  under concurrent connections.

### LLM evals

Evals complement deterministic tests by validating the non-deterministic LLM
behaviour end-to-end. They run against a real (or local) LLM provider with
seeded test data and assert on tool selection, query correctness, and answer
quality. See Phase 1.8 for the full eval framework specification.

Eval datasets grow with each phase:

| Phase | Datasets added |
|-------|---------------|
| 1     | `basic_queries`, `multi_hop`, `follow_ups`, `edge_cases` |
| 2     | `mutations`, `rate_limiting`, `guardrails` |
| 3     | `long_conversations` (context window management after summarisation) |
| 4     | Provider-specific datasets (verify all providers pass the same cases) |

## Files touched in existing code

Minimal changes to existing codebase:

- `GeneralManager` base class: add `chat_exposed: ClassVar[bool] = True`.
- `src/general_manager/apps.py` / `src/general_manager/bootstrap.py`: initialize
  chat from the existing app config when enabled; no separate `ChatConfig`.
- `settings.py` / app config: register `GENERAL_MANAGER["CHAT"]` defaults and
  validate the chat permission hook.
- `src/general_manager/migrations/`: add chat persistence models in the
  existing migration package.
- `src/general_manager/management/commands/chat_cleanup.py`: retention command.
- URL/routing config: conditionally include chat routes.
- `pyproject.toml`: add provider SDK extras only. Channels remains a core
  dependency of GeneralManager:
  ```toml
  [project.optional-dependencies]
  chat-ollama = ["ollama"]
  chat-anthropic = ["anthropic"]
  chat-openai = ["openai"]
  chat-google = ["google-generativeai"]
  ```
