# ADR 0005: External search backends with unified search configuration

## Status
Accepted

## Context
GeneralManager needs search that is:
- Typo tolerant and relevant for user-facing queries.
- Independent of any single database backend.
- Compatible with non-database interfaces.
- Able to search across multiple managers and multiple indexes.
- Exposed via GraphQL with mixed-type results.

## Decision
Introduce a backend-agnostic search integration based on:
- Manager-level search configuration (IndexConfig + FieldConfig).
- External search backends (Meilisearch first, then Typesense and OpenSearch/Elasticsearch).
- Optional in-project DevSearch backend for local development.
- A single global GraphQL search query with an `index` parameter.
- Indexing and update hooks decoupled from database operations.

Key rules:
- Use a type-scoped document ID format for all managers: `{TypeLabel}:{identification-json}` (e.g., `Project:{"id":1}`) so IDs are stable and unique across manager types. All indexing and lookup logic that uses manager.identification, document keys, or ID construction must follow this combined format.
- Index only configured fields (or `to_document` when provided).
- No runtime overrides for per-index filters or boosts.
- Respect manager permissions via `get_read_permission_filter()` in the GraphQL resolver.

## Alternatives considered
1) Database-native search only (FTS / trigram)
   - Rejected: does not cover non-DB interfaces and ties search to the DB backend.
2) One GraphQL query per index
   - Rejected: schema churn as indexes evolve; higher maintenance.
3) Runtime overrides of boosts and filters
   - Rejected: harder to reason about relevance, caching, and permissions.

## Consequences
- Requires an indexing pipeline and an external service in production.
- Enables consistent multi-manager search across databases and interfaces.
- Keeps the public GraphQL schema stable as indexes evolve.
- Forces relevance tuning to live in code/config (predictable behavior).

## Follow-up work
- Implement backend protocol and Meilisearch adapter.
- Add Typesense and OpenSearch/Elasticsearch adapters.
- Implement GraphQL `search(index=...)` resolver and union type.
- Add permission-aware search tests.
- Document setup steps for each backend.
