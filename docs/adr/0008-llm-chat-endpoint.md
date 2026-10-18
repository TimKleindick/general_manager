# ADR 0008: Optional chat over the native GeneralManager API

## Status

Accepted; amended by the subsequent planned-read and schema decisions. The
original endpoint/provider decision remains in force. Earlier operational
examples in this record described the initial tool loop and are replaced by the
current guides. Read execution and schema inspection are now governed by
[ADR 0010](0010-planned-chat-evidence.md) and
[ADR 0011](0011-chat-schema-contracts.md). The guides and API reference describe
current configuration; this record explains why these boundaries exist.

## Context

Applications already declare managers, relations, permissions and an executable
GraphQL API. Natural-language access should reuse those contracts while letting
the application choose a model, hosting boundary and user interface. Schemas can
contain hundreds of managers, so placing one fully expanded tool per manager in
every model request is an unsuitable general default.

## Decision

Keep chat optional under `general_manager.chat`, activated by
`GENERAL_MANAGER["CHAT"]["enabled"]`. It belongs to the existing Django app and
migration package; it does not register a second chat app. Initialize chat after
the application's GraphQL schema and reject missing schemas through system checks.

Supply interchangeable provider adapters and a provider-neutral event protocol.
Provider SDKs remain optional extras. HTTP, SSE and WebSocket clients share the
read scheduler and event vocabulary; transports retain their own delivery and
mutation continuation. Persist conversations, messages and pending confirmations
in Django so confirmation ownership is not process-local.

Managers explicitly opt in with `chat_exposed=True`. Discover exposed managers
and relation paths using a small tool set, then compile structured read arguments
against native GraphQL. Execute with the actual user/request context and existing
middleware. Chat exposure narrows the available surface; native permission owners
continue to authorize reads and writes.

Writes require authentication and an exact GraphQL mutation allow-list. The
`confirm_mutations` subset additionally requires a durable user confirmation.
SSE and WebSocket support that continuation. Plain HTTP returns a transport error
when confirmation is required. Keep mutation lists empty for a read-only assistant.

The application owns provider credentials, domain definitions, chat admission,
origins, retention scheduling, monitoring and the frontend. Endpoint activation
does not choose an appropriate data-sharing or access policy for it.

## Alternatives considered

- **Raw model-generated GraphQL:** rejected as the public tool protocol because
  field spelling, argument types and project filter conventions need native
  validation and typed compilation.
- **Path metadata as the sole schema authority:** rejected because relation paths
  do not define executable field shapes, enum values, defaults or root arguments.
- **One query tool per manager as the standard read architecture:** rejected for
  large schemas. The retained `tool_strategy` setting concerns the mutation
  workflow's tools; it does not select a different read scheduler.
- **Client-owned history and process-local confirmations:** rejected because
  conversation and approval ownership must survive ordinary transport handling
  and concurrent processes.

## Consequences

Applications gain a backend API rather than a ready-made chat UI. They must
install a provider extra, initialize native GraphQL and test their permission
policy. Discovery can expose schema metadata to the configured provider, so
manager exposure must be intentional. A successful transport does not prove
answer quality; deterministic contract tests and separately configured model
comparisons have different purposes.

See [installation](../howto/install_llm_chat.md),
[client integration](../howto/integrate_chat.md) and the
[Chat API](../api/chat.md) for supported configuration and event shapes.
