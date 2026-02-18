# MCP Gateway Security Model

The GeneralManager MCP gateway is designed as a constrained, read-only interface.

## Security principles

- No direct database access from AI clients.
- No raw GraphQL passthrough from clients.
- Structured request validation before execution.
- Explicit domain/field allowlists in settings.
- Authenticated principal required for query execution.
- Existing GeneralManager GraphQL permission filters remain the source of truth.

## Enforcement flow

1. Authenticate principal (HTTP `request.user` or MCP auth payload resolver).
2. Validate request against allowlisted domain policy.
3. Compile to fixed GraphQL list template.
4. Execute with authenticated GraphQL context.
5. Return normalized response with provenance.

## Risk controls

- Strict pagination caps (`page_size` default 50, max 200).
- Unsupported operations denied with stable error codes.
- Unknown domain/field requests denied.
- Optional permission audit integration records gateway outcomes.

## Non-goals in v1

- Write operations (`create`, `update`, `delete`).
- Freeform natural-language parsing inside the gateway.
- Cross-project routing from a central gateway.
