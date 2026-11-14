# Security

Security in the GraphQL layer relies on permission checks and robust error handling.

## Permission enforcement

- List and search queries obtain read filters from the manager's `Permission` class via `get_read_permission_filter()`. Managers without a `Permission` class receive no GraphQL read prefilter and no per-object read gate from this helper.
- Permission classes can return a full `ReadPermissionPlan` from the zero-argument permission instance method `get_read_permission_plan()`. `ReadPermissionPlan` is an internal adapter with `filters`, `requires_instance_check`, `instance_check_reasons`, and `decision`; it is used by generated resolvers, not intended as a stable user import. `decision` is compatibility-defaulted to `"conditional"`: older plan-shaped values that lack it retain legacy behavior. `"allow_all"` and `"deny_all"` are definitive static outcomes; `"conditional"` uses row constraints and/or a final instance gate. If `get_read_permission_plan()` is absent or does not return that adapter, GraphQL falls back to the zero-argument legacy permission instance method `get_permission_filter()` and runs per-object read checks after the prefilter.
- The helper reads `Permission` from the manager class with normal Python attribute lookup and calls it positionally as `Permission(manager_class, info.context.user)`. The later row gate calls the same permission class as `Permission(instance, info.context.user).can_read_instance()`.
- Legacy permission filter entries may contain optional `filter` and `exclude` mappings. Missing keys are treated as empty mappings by resolvers. Malformed entries are not validated by the helper and fail later when applied to the bucket or search backend. If the fallback `get_permission_filter()` method is missing, the resulting `AttributeError` propagates. The legacy fallback plan uses `requires_instance_check=True` and `instance_check_reasons=("no_prefilter_backend",)`.
- For lists, `"deny_all"` returns an empty bucket without a per-row check and `"allow_all"` returns the candidate bucket without a prefilter or per-row check. Conditional plans apply each `filter` then `exclude` constraint group as an OR alternative; a row-level `can_read_instance()` call happens only when the plan requires it. A single aggregate structured authorization log is emitted only for a conditional list/search path that ran that final gate, not for static fast paths.
- Generated detail queries call `Permission(instance, request_user).can_read_instance()` before returning the object, including selections containing only `__typename`. Denied objects and missing ORM or request-backed targets resolve to `null` without a not-found error. Expected request not-found errors from lazy hydration are also normalized; unexpected construction and permission-backend errors still propagate. Historical queries check the instance hydrated in the active historical context. Managers without a permission class retain default-allow behavior.
- Mutations invoke `check_create_permission`, `check_update_permission`, or `check_delete_permission` before executing. A `PermissionError` returns the fixed public message `Permission denied.` rather than its original text.
- Attribute-level restrictions hide protected fields even when the user can access the object.
- Optional GraphQL permission capabilities expose advisory boolean hints for clients. They do not replace read or mutation permission checks.

Use manager write methods inside custom mutations so the application's write
policy, validation, and history participate. Generated GraphQL read resolvers
own the row and field authorization described above. Simply calling
`Manager.all()`, `Manager.filter()`, or reading a manager attribute in a custom
resolver or REST view does not perform those read checks. A custom response
must apply its requesting user's row and field permissions before exposing
values. See the [permission overview](../permission/index.md) for these
boundaries.

Generated create and update mutations reserve `creator_id`, `ignore_permission`,
and `history_comment` for manager execution metadata. Schema generation rejects
editable interface fields with these names, including relation aliases such as
`creator` that normalize to `creator_id`. Rename colliding fields or make them
read-only. At runtime, payloads containing `creator_id` or `ignore_permission`
are rejected with `BAD_USER_INPUT` before upload-token processing. A second
check rejects collisions introduced by relation normalization. The actor always comes from the authenticated context. The
optional `historyComment` argument remains available as audit metadata.

## Capability hints

See [GraphQL permission capabilities](permission_capabilities.md) for the full
concept, declaration helpers, current-user provider configuration, and batching
examples.

Declare object capability hints on an existing manager's nested `Permission`
class. This fragment assumes a registered `Project` with `name` and `status`
fields; retain its `Interface` definition:

```python
from general_manager.permission import AdditiveManagerPermission, object_capability


def can_rename(project, user):
    return project.status == "draft" and user.is_authenticated


class Project(GeneralManager):
    class Permission(AdditiveManagerPermission):
        graphql_capabilities = (
            object_capability("canRename", can_rename),
        )
```

Managers with declarations expose a generated non-null capability object:

```graphql
query {
  projectList {
    items {
      name
      capabilities {
        canRename
      }
    }
  }
}
```

Use `batch_evaluator=` on `object_capability(...)` for list pages that would otherwise repeat expensive policy checks. Batch warmup runs only when `items { capabilities { ... } }` is selected, and any evaluator exception is logged and resolved as `false`.

Projects can also configure current-user capability hints:

```python
GENERAL_MANAGER = {
    "GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER": "my_app.auth.GraphQLCapabilities",
}


class GraphQLCapabilities:
    graphql_fields = {"username": str}
    graphql_capabilities = (
        object_capability("canOpenAdmin", lambda instance, user: user.is_staff),
    )

    def resolve_username(self, user, info):
        return user.username
```

For `object_capability`, the evaluator is called as `(instance, user)`, where
`instance` is the provider or managed object and `user` is the requesting or
resolved user.

When no provider is configured, the schema does not expose `me`.

## Authentication

Set `AUTHENTICATION_BACKENDS` and middleware according to your project. The
GraphQL view expects `info.context.user` to be populated. For generated
mutations and mutations created with `@graph_ql_mutation`, a denied mutation
permission produces a top-level GraphQL `errors` entry with the fixed message
`Permission denied.` and code `PERMISSION_DENIED`; GraphQL execution sets the
mutation field in `data` to `null`. It does not return a payload error field.

## File capabilities and upload tokens

GraphQL file uploads use three different credentials with deliberately different
scope:

- the authenticated GraphQL session creates a field/operation/target-bound
  intent and later runs the complete manager permission check;
- proxy transfer uses a distinct short-lived `GMUpload` authorization value (or
  an S3 signature), never the consumption token in a URL;
- private display/download uses a short-lived signed capability because an
  `<img>` request cannot reliably add application authorization headers.

Treat every token, returned header, and signed URL as a bearer secret. Require
HTTPS, keep token and URL TTLs short, redact query strings/authorization headers,
and never place them in analytics, exception locals, audit payloads, or browser
console logs. GeneralManager logs only bounded intent/adapter/manager/field/state
metadata and does not expose staging or filesystem paths.

Local download capabilities revalidate the current manager/object/field binding
and exact retained bytes on each `GET` or `HEAD`, so replacement invalidates an
old local URL. S3 presigned URLs cannot be revoked and retain access until their
TTL expires. Public mode is explicit and must be backed by an adapter that proves
a genuinely public URL.

Filename suffix and declared `Content-Type` are not content proof. `ImageField`
decodes the image under finite dimension/pixel limits; strict general file
formats require a bounded content inspector. Keep staging private, require
SHA-256, and retain exact immutable versions until reconciliation completes.

`DELETE_REPLACED_FILES` is off by default. Enabling it can still leave old
objects when exact ownership/deletion is unsupported, and shared keys cannot
always be detected. For the built-in filesystem adapter,
`gm-upload-old-claims/` is framework-exclusive: only GeneralManager workers
under the durable cleanup lease may mutate it. POSIX lacks portable atomic
compare-and-unlink, so do not enable local replacement deletion where operators,
sidecars, or application code can write that reserved namespace.
Cross-database upload sagas, resumable uploads, S3 multipart,
and built-in malware scanning are outside v1. See
[GraphQL file uploads and downloads](file_uploads.md) for the full threat and
consistency model.

## Error propagation

GeneralManager's generated mutations and mutations created with
`@graph_ql_mutation` pass exceptions through a shared safe error mapper. This is
a guarantee at those GeneralManager mutation boundaries, not a claim that every
arbitrary third-party GraphQL resolver is intercepted when it bypasses them.

Explicit `GraphQLError` instances are trusted and returned unchanged, preserving
their object identity, message, and full extensions mapping.
`PublicGraphQLError`, imported from `general_manager.api`, is the stable
application contract for deliberately public resolver failures: applications
provide both a safe message and a stable `code`.

Django `ValidationError` remains a public validation path. A structured error
with `message_dict` returns `Validation failed.` with code `BAD_USER_INPUT`, plus
`fieldErrors` and `nonFieldErrors`. An unstructured `ValidationError` retains
Django's rendered validation message and returns code `BAD_USER_INPUT`.
`PermissionError` always returns the fixed message `Permission denied.` and code
`PERMISSION_DENIED`; applications that deliberately need a different public
permission message must raise an explicit public error instead.

Every other ordinary exception crossing these mutation boundaries, including
`ValueError`, returns exactly `An internal server error occurred.` with code
`INTERNAL_SERVER_ERROR` and an opaque `errorId`. Server logs retain the original
exception details and traceback under the matching `error_id` so operators can
correlate a client report without exposing internals. If rendering the original
exception itself fails, the client response remains sanitized.

Migrate client-facing uses of plain `ValueError` to `PublicGraphQLError`, or to
Django `ValidationError` for validation failures. Treat every deliberately
public message, including validation text and explicit `GraphQLError` messages,
as untrusted output design: it must never contain secrets, credentials, private
paths, or other internal details.

## Hardening tips

- Enable query depth or complexity limits in your GraphQL server to avoid expensive queries.
- Combine permissions with `filter` arguments so users cannot guess identifiers of objects they do not own.
- Log denied permissions with the manager name and user ID to monitor suspicious behaviour.
- Avoid exposing `ignore_permission=True` paths in public APIs; reserve them for internal management commands.

### Query operands and search projections

Filtering, exclusions, ordering and grouping can expose values without selecting
their output fields. Ordinary lists evaluate these operations over readable
contributors. A denied scalar or singular relation removes its root row from
that operation's domain before count and pagination. Denial is availability:
`exclude` and null comparisons cannot recover an unreadable value. An
output-only denied scalar retains its existing `null` behavior.

Relation paths require permission on the relation, related row and leaf field;
relation-ID aliases also require the canonical relation permission. Plural
`any` predicates use the same readable child witness for their mapping. Hidden
children are absent from `none` and grouped exclusions too. Grouped exclusions
retain Django's independent-witness semantics: different readable children can
satisfy different lookups in the excluded mapping. Multiple readable matching
joins retain the backend's existing duplicate-row behavior.

Only used query paths are classified. Proven ordinary native fields keep SQL
filter/count/order/page execution, even when an unused field has extra rights.
Exact permission plans can constrain native related rows in SQL. Unknown or
custom executable policies retain runtime field and row checks on the same
instances used for Python predicates. Denied Python property getters and
annotation providers are not invoked. Callback, getter, validation, configured
work-limit and incomplete-source errors remain errors. The builtin `matches`
rule compares `str(value)` and retains runtime checks because typed SQL equality
is not equivalent.

Runtime field/path visits with additional or unknown restrictions consume the
query-permission budget, unlimited by default and optionally bounded. A field
proven covered by its current row gate does not consume it. Standard delegated
policies inspect the concrete resolved permission through the normal field
check; custom and restrictive delegated policies remain budgeted. Permission
plans and passive structural metadata live within one operation. User/object
grants and Permission instances are not shared across requests or actors.

Grouping keys use authorized roots and key values before groups are built.
Each metric has its own readable member subset: denying a member's `cost`
does not remove that member's readable `hours`. An aggregate without non-null
contributors returns the existing empty value (`null`). Group sorting uses the
same authorized metric subset before group pagination.

Calculation lists first restrict the **requested constructor domain** using
proven native scalar inputs or manager-identity input constraints. They freeze
that exact domain before intersecting permission filters and applying row
checks. Permission and client constraints cannot replace one another, including
constraints on the same input. Contributor authorization removes unreadable
values before computed-property or related-value predicates, sorting and
pagination. Custom getters and nonidentity relation fields are not safe
constructor constraints; exclusions remain atomic conjunctions. This deliberately
changes the former full-domain enumeration contract: calculations outside the
requested native constructor scope are no longer constructed or authorized.

Search authorizes every configured serialized searchable, filterable, and sortable
key, including keys unused by request filters or ordering. DevSearch scores all
serialized values. At schema construction, manager/index definitions are
classified: fields with no extra read restrictions can use the fast route;
additional field rules or unknown/custom executable rules require the thorough
route. Inherited rules and field-rule OR alternatives that include all manager
row-rule alternatives are not extra restrictions. Definitions are assumed immutable until schema rebuild after
restart. Classification stores no user or object permission results.

Search's projection contract and direct `authorize_query_fields()` calls remain
strict: a denied required operand fails the query, including denial on a
nonmatching or off-page candidate. Fast search hits have one exception: a
related-row denial (`QueryRelationPermissionError`) removes that hit before
counting and pagination; other operand denials still fail the query.
Search shares its field/path counter across
projection checks and hit batches within one resolver call without sharing
permission decisions. The budget does not bound preceding row authorization
or database materialization.

Only a native `DatabaseBucket` with standard `id` identification uses fast
hit-batch source hydration. Row authorization remains enforced. Related fast
hits use native permission traversal; a denied referenced object removes the
entire owner projection before count, sort, and pagination. Thorough checks still
fail when any row-authorized candidate denies required operand access.
Calculation and remote sources retain full-source checks, even for static fast
classifications. Incomplete remote sources fail explicitly, and calculation
sources require an enumerable `possible_values` domain. Statically denied
managers skip source and backend inspection.

Custom `SearchConfig.to_document` projections require complete trusted
`field_dependencies` for every configured key. The same contract supports native
properties; actual native serialized fields always remain authorized alongside
declared dependencies. Missing property provenance and invalid paths fail before
backend search. Declarations describe sources, not equality, so custom document
filters cannot become native permission pushdown. See
[projection dependencies](../search.md#projection-dependencies) for the contract.

Source authorization on the thorough route optionally caps candidates across
selected manager types, counting denied rows before native permission checks.
This budget is separate from backend scanning and field/path decisions and does
not bound preceding SQL bucket materialization. Fast hit batches remain subject
to the backend candidate budget and related field/path authorization budget.

Hits must match current authorized native source identities. Every hit requires
stored `SearchHit.data` with exactly the configured search/filter/sort keys. Missing
stored data is an error; extra or missing keys skip the hit pending reindexing so
removed restricted fields cannot influence search. Value freshness belongs to
the separate indexing/invalidation pipeline; the search resolver does not
reserialize documents. That pipeline must remove obsolete content from former
relation targets: permission checks against current sources cannot identify
those old values. Indexing a calculation outside its source domain never
grants access. Both `exact` and `bounded` total modes raise
`SEARCH_SCAN_LIMIT_EXCEEDED` on exhausted scan budgets; neither releases a partial
prefix, and successful responses always have `totalIsExact: true`.


### Configuring query workload budgets

All three query workload budgets default to **unlimited** (`None`). They limit
computation, not permissions: every required row, field and relation check still
runs. Unlimited processing can increase query duration and memory use; configure
finite budgets where bounded resource use is required.

| `GENERAL_MANAGER` setting | Work counted |
| --- | --- |
| `GRAPHQL_QUERY_PERMISSION_LIMIT` | Additional/unknown contributor field and relation-path visits for ordinary lists, including calculation/request lists; all operand visits for strict search/direct-helper checks |
| `GRAPHQL_SEARCH_SOURCE_AUTHORIZATION_LIMIT` | Thorough-search source candidates, including denied rows |
| `GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT` | Backend search hits scanned across selected types |

Each accepts a positive integer or `None`. An environment variable with the
`GENERAL_MANAGER_` prefix accepts a positive integer, `none`, or `unlimited`.
Django settings take precedence over the environment, including explicit `None`.
For example, `GENERAL_MANAGER_GRAPHQL_QUERY_PERMISSION_LIMIT=10000` bounds field
work; `GENERAL_MANAGER_GRAPHQL_SEARCH_TOTAL_SCAN_LIMIT=unlimited` disables the
scan budget. Existing explicitly configured finite values remain effective.
Invalid values (including zero, negative numbers and booleans) are rejected.

Exceeding an explicitly configured budget still fails the whole operation; it
never returns a partially authorized result. On runtime routes, pagination does
not replace the remaining candidate checks required for correct global counts,
ordering and pages. Safe native predicates and exact permission plans can
reduce work earlier. Page-size, transport/subscription, backend-specific index
limits and permission rules are unchanged. Search continues fetching finite
batches until the backend is exhausted, even when its total scan is unlimited.
