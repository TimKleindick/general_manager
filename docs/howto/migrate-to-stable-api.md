# Migrate to the stable API

This guide covers the pre-1.0 public-contract changes in the stable API. Make
the changes in a feature branch, deploy code that understands the new contract,
and then run the applicable data/index cutovers below.

## Replace ordering arguments

GraphQL lists, groups, and selected-index searches now take typed `orderBy`
terms. Replace the former global ordering flags:

```graphql
# Before
projectList(sortBy: "name", sortDesc: true) { results { id } }

# After
projectList(orderBy: [{field: name}, {field: date, direction: DESC}]) {
  items { id name date }
}
```

`direction` defaults to `ASC`. Generated field enums provide autocomplete and
use GraphQL camelCase spelling; relation paths use the generated eligible
relation fields. A selected search index validates every requested field before
calling its backend. Python buckets use signed positional fields instead:
`bucket.sort("name", "-date")`. The remote API retains its documented
`ordering` wire alias.

Concrete `CalculationBucket` and `DatabaseBucket` constructors no longer
accept ordering metadata (`sort_key`, `reverse`, `sort_keys`, or
`sort_reverse`). Construct the bucket with its source and filters, then call
`.sort(*signed_fields)`. New bucket pickles retain that signed ordering; rebuild
pre-stable ordered constructor calls and serialized tuples through this API.

## Use sibling group fields

`groupBy` no longer belongs on entity-list fields. Query the generated sibling
`<manager>Groups` field, whose result has `keys`, paginated `members`, `count`,
and eligible numeric `sums`:

```graphql
projectGroups(groupBy: ["status"], orderBy: [{field: status}]) {
  groups { keys { status } members { items { owner { id } } } count sums { amount } }
}
```

Groups may order only by selected keys. Permissions apply to group keys and
sums before values are disclosed, including singular relations under members.

## Check pagination inputs and metadata

Explicit `page` and `pageSize` are positive integers. Supplying either value
defaults the other to page 1 or size 10. `pageInfo` reports effective values;
an empty known result has `totalPages: 0`, while an out-of-range positive page
has an empty `items`/`groups` list. Request-backed pages distinguish fetched
rows from an upstream total, which can be unknown.

## Move search and remote controls to trusted boundaries

Public search no longer exposes backend `raw` documents. Remote execution
controls are trusted server inputs: use the supported request metadata and the
provider-supplied confirmation flow for mutations instead of body controls.
The provider cannot approve its own request. Optional search/workflow backends
remain capability stubs until configured; do not infer support from their
importable names.

## Roll forward cache entries

Cache keys and prefetch manifests use v2 namespaces. Old values are misses and
are regenerated; no whole-cache flush is required. Dependency caching requires
Django's configured default backend, while timeout caching may use a compatible
custom backend. `cached` rejects async functions, async callable objects, and
runtime awaitable results.

## Update Measurement values and field defaults

`Measurement` is immutable. Parse text with `Measurement.from_string("1 m")`
and compare with another `Measurement`, rather than comparing to a string.
Compatible relational comparisons use the same canonical magnitude/bin as
equality and hashing; Decimal formatting retains supplied coefficients. Normal
`MeasurementField` scalar defaults now serialize through Django migrations,
alongside callable defaults and field options.

## Use public facades and explicit result shapes

Replace removed utility deep imports with the documented package facades. Group
results are explicit group objects, not entity buckets. Runtime collation and
null placement can vary by backend; request pages cannot promise global
ordering beyond the supplied page. Upload adapters require PUT support and
exact object/value validation. Factory `build()` relation side effects and Rule
source-inspection constraints remain documented limits.

## Cut over durable handlers and chat summaries

Persistent handler registrations need durable registration IDs. Deploy the new
IDs before enabling the new registry; existing deliveries are not replayed
automatically. The chat migration adds a nullable summary watermark. Existing
summaries retain their text but have unknown coverage and are regenerated lazily
when their older-message boundary is needed.

## Rebuild Meilisearch indexes

Meilisearch primary keys now use a uniform SHA-256 encoding that is
collision-resistant but not reversible. Rebuild each affected index, switch
readers to the rebuilt index, then remove the old index; do not retain duplicate
old documents. The rebuild preserves every original ID, including empty strings,
in stored metadata for round trips.

## Update factories, seeders, and logging callers

Use the documented relation override spellings, including raw foreign-key
attnames. Seeder counts report actual created objects and respect an aliased
transaction scope. Audit logger `flush()` drains accepted records without
closing the logger; `close()` is terminal. Thread-local dependency tracking is
limited to its request/calculation context and does not provide distributed
tracker semantics.
