# Architecture Decision Records

This directory collects the Architecture Decision Records (ADRs) that document key technical choices in GeneralManager.

Read these records to understand the context, alternatives, and tradeoffs behind
a decision. They preserve the design at the time it was written; a proposed
configuration or historical skeleton is not necessarily today's usage contract.
For implementation and setup, use the current concept and API guides below.

In particular, ADR 0003 remains deferred: its settings block is a proposal,
not a supported configuration surface.

- [ADR 0001: Capability-Driven Interface Skeletons](0001-interface-skeletons.md)
- [ADR 0002: Declarative Capability Configuration](0002-capability-configuration.md)
- [ADR 0003: Configurable Interface Capabilities via Django Settings](0003-capability-overrides-via-settings.md)
- [ADR 0004: Capability-Driven Startup Hooks](0004-startup-hooks.md)
- [ADR 0005: External search backends with unified search configuration](0005-search-backend.md)
- [ADR 0006: Workflow engine architecture with pluggable backends](0006-workflow-engine-architecture.md)
- [ADR 0007: GraphQL capability exposure for frontend authorization](0007-graphql-capability-exposure.md)
- [ADR 0008: Optional chat over the native GeneralManager API](0008-llm-chat-endpoint.md)
- [ADR 0009: Filter calculation inputs before row authorization](0009-calculation-filter-authorization.md)
- [ADR 0010: Planned chat reads with validated evidence](0010-planned-chat-evidence.md)
- [ADR 0011: Snapshot-bound selective schema discovery](0011-chat-schema-contracts.md)

## Current usage guides

| Decision area | Current documentation |
| --- | --- |
| Interface composition (0001–0003) | [Capability-first interfaces](../concepts/interfaces/capability-first.md) and [Interface API](../api/interface.md) |
| Startup hooks (0004) | [Startup maintenance](../howto/startup_maintenance.md) and [ordered hook recipe](../examples/startup_hooks.md) |
| Search and workflows (0005–0006) | [Search](../concepts/search.md) and [workflows](../concepts/workflow.md) |
| Frontend authorization hints (0007) | [GraphQL permission capabilities](../concepts/graphql/permission_capabilities.md) |
| Calculation filtering (0009) | [Filters and pagination](../concepts/graphql/filters_pagination.md) |
| Chat decisions (0008, 0010–0011) | [Chat architecture](../concepts/chat.md), [evidence](../concepts/chat_evidence.md), and [schema discovery](../concepts/chat_schema.md) |
