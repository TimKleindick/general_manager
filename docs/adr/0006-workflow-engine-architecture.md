# ADR 0006: Workflow engine architecture with pluggable backends

## Status
Accepted

## Context
GeneralManager already has extension patterns for pluggable runtime components:

- Search backend registry (`SEARCH_BACKEND`) with settings-driven resolution.
- Audit logger registry (`AUDIT_LOGGER`) with protocol-style contracts.
- Startup hooks and system checks registered by capabilities.

Teams now need workflow orchestration for event-driven automation, including:

- Triggering workflows from package-level events.
- Executing reusable actions from workflow steps.
- Supporting both local execution and external orchestrators (for example n8n).

Introducing workflows should reuse existing package conventions instead of adding
a one-off subsystem with different configuration and extension behavior.

## Decision
Add a dedicated `general_manager.workflow` subsystem with three layers:

1. **Event ingress**
   - `WorkflowEvent` model and `EventRegistry` protocol.
   - Default `InMemoryEventRegistry` with deduplication by `event_id`.
   - Registry receives domain events and routes them to handlers.

2. **Engine abstraction**
   - `WorkflowEngine` protocol with `start`, `resume`, `cancel`, and `status`.
   - `WorkflowDefinition` and `WorkflowExecution` shared dataclasses.
   - Settings-driven engine registry using `GENERAL_MANAGER["WORKFLOW_ENGINE"]`
     (or top-level `WORKFLOW_ENGINE`) and import-path/callable/mapping resolution,
     consistent with search and audit configuration.

3. **Action execution**
   - `Action` protocol and `ActionRegistry` for named side-effect operations.
   - Workflows invoke actions through the registry to centralize contracts,
     policy checks, retries, and observability in one place.

Backends:

- **LocalWorkflowEngine**: default in-memory backend for development and tests.
- **N8nWorkflowEngine**: adapter stub for future remote orchestration support.

## Alternatives considered
1) Hard-couple workflow orchestration into interface capabilities
   - Rejected: workflows are cross-cutting and not specific to interface CRUD
     concerns; coupling would blur boundaries and complicate adoption.

2) Build only an n8n integration without local backend
   - Rejected: raises adoption barrier for tests/local development and creates
     lock-in before core contracts stabilize.

3) Reuse Django signals directly without an event registry contract
   - Rejected: signals are useful transport, but they do not provide clear event
     typing, deduplication, or backend-agnostic routing guarantees.

## Consequences
- Establishes stable contracts before implementing full orchestration semantics.
- Preserves package consistency by reusing existing pluggable configuration style.
- Enables incremental adoption: local first, external orchestrators later.
- Keeps room for EventSourcing/CQRS work on the roadmap without forcing it now.

## Follow-up work
- Wire workflow engine configuration during app startup.
- Add persistence-backed local engine (Django models) for resumability/audit.
- Define event schema validation conventions and replay tooling.
- Implement n8n adapter API calls and callback mapping.
- Add concept/how-to docs for event triggers, actions, and backend setup.
