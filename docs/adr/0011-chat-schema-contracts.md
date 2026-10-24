# ADR 0011: Snapshot-bound selective schema discovery

## Status

Accepted. Extends [ADR 0008](0008-llm-chat-endpoint.md) and
[ADR 0010](0010-planned-chat-evidence.md).

## Context

Full nested GraphQL filter/type graphs can dominate model input before useful
queries run. Omitting fields heuristically, reflecting ORM internals or returning
partial selectors would create a second, unreliable schema. Relations and
exposure changes also make an unbound detail token unsafe to reuse.

## Decision

Compile one immutable contract from the executable, exposure-filtered GraphQL
schema through the shared GeneralManager interface abstraction. Public schema
tools default to a compact overview of executable signatures and immediate type
references. Fetch exact definitions by observed names, manager and snapshot.
Related managers are bounded references and require their own discovery.

Bind planned schema requirements to view, type set and current snapshot. Retain
explicit full inspection for old unspecified requirements and compatibility
Python helpers. Reject invalid selectors and stale tokens atomically; do not
introduce automatic full fallback, cache bypass or invented union support.

Rebuild the executable schema when definitions, exposure or bindings change.
Generation identity participates in snapshots. Reuse compiled metadata but never
request data or authorization decisions. Keep native GraphQL permission checks
on every execution in the current user/request context.

Keep selective projection separate from lossless provider transport. The latter
may share repeated values only at explicitly bound locations, reconstruct the
entire original input exactly and preserve independent occurrence provenance.
A smaller encoding grants no evidence or permission authority.

## Alternatives considered

- **One transitive full-schema prompt:** rejected as the default because callers
  can request only the definitions their task actually needs.
- **ORM-specific discovery:** rejected because supported interfaces and executable
  GraphQL names, defaults and filters must share one authority.
- **Silently accept valid parts of a selector:** rejected because completion could
  then certify definitions that were never returned.
- **Expand relation targets under the source snapshot:** rejected because each
  manager's exposure and captured contract must remain explicit.
- **Treat compression as removal of redundant evidence:** rejected because equal
  payloads can belong to different tasks, snapshots and source occurrences.

## Consequences

Custom integrations must follow observed manifests and intentionally choose helper
defaults. Tokens become invalid after a rebuild. A selective exchange is checked
for exact selected definitions and complete query sequences; it does not claim a
full-schema roundtrip. Lossless codec checks remain separate. Publicly unsupported
types stay unavailable until modeled rather than inferred from returned data.

See [schema discovery](../concepts/chat_schema.md) and
[reference-data transport](../concepts/chat_reference_data.md).
