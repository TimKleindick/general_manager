# GraphQL API

::: general_manager.api.graphql.GraphQL

::: general_manager.api.graphql.MeasurementType

::: general_manager.api.graphql.MeasurementScalar

::: general_manager.api.graphql.BigIntScalar

::: general_manager.api.graphql_errors.PublicGraphQLError

Stable imports from `general_manager.api` include `GraphQL`,
`MeasurementType`, `MeasurementScalar`, `PublicGraphQLError`, and the file-upload
contracts documented below. Stable imports from
`general_manager.api.graphql` are limited to the compatibility exports `GraphQL`,
`MeasurementType`, `MeasurementScalar`, and `BigIntScalar`. The implementation keeps lower-level helpers,
constants, pagination types, permission-plan adapters, and exception mappers in
extracted submodules; those submodules are private implementation details and
are not stable import paths. Some internal types may still appear in generated
reference pages or type-checker-visible implementation annotations when they are
part of generated GraphQL plumbing; that visibility does not make them public
import targets.

## File uploads

Use the lazy stable imports from `general_manager.api`; modules under
`general_manager.uploads` are implementation locations, not additional public
import promises.

Configuration and inspection:

- `FileUploadPolicy` configures one manager file field.
- `FileInspection` is the credential-free bounded value passed to a
  `FileContentInspector`.
- `FileUploadConfigurationError` reports invalid policy/settings construction.

GraphQL contracts:

- `UploadToken` is the opaque generated mutation scalar.
- `StoredFile` and `StoredImage` are generated structured output types.
- `StoredFileStatus` contains `AVAILABLE`, `PROCESSING`, and `FAILED`.
- `UploadTransport` contains `DIRECT` and `PROXY`.

Custom storage extension contracts:

- `register_upload_adapter(storage_class, factory)` registers one global
  `UploadAdapterFactory` before upload use.
- `UploadAdapter` is the complete transfer/inspect/download protocol.
- `UploadFinalizationAdapter` is the exact post-commit and replacement cleanup
  protocol.
- `ExactPublicDownloadAdapter` is the optional unsigned immutable public URL
  protocol required for retained uploads in public mode.
- `ProxyUploadSink` adds streaming `save_stage` for proxy adapters.
- `UploadInstructions`, `ObjectVersion`, and `ClaimedObject` are immutable
  boundary values. Instruction credentials and object-version identities have
  redacted reprs; treat a `ClaimedObject` key as sensitive operational data and
  do not log it.

See [the setup and custom-adapter guide](../howto/graphql_file_uploads.md) for
method semantics and contract testing. Persistence models, token/digest helpers,
the registry instance, local capability codecs, and finalization functions are
not public API.

Expected failures derive from `UploadError` and carry stable `code` class
attributes. Public exception classes are:

- `UploadAuthenticationError`, `UploadManagerInvalidError`,
  `UploadFieldInvalidError`, `UploadOperationInvalidError`,
  `UploadTargetUnavailableError`;
- `InvalidUploadFilenameError`, `InvalidUploadSizeError`,
  `InvalidUploadChecksumError`, `UploadQuotaExceededError`,
  `UploadRateLimitExceededError`, `UploadDatabaseMismatchError`;
- `UploadExpiredError`, `UploadTokenInvalidError`, `UploadIncompleteError`,
  `UploadAlreadyConsumedError`, `UploadTransferConflictError`,
  `UploadSupersededError`, `UploadBindingMismatchError`;
- `UploadSizeMismatchError`, `UploadChecksumMismatchError`,
  `InvalidFileTypeError`, `InvalidImageError`;
- `UploadBackendUnsupportedError`, `UploadStorageChangedError`,
  `UploadObjectMissingError`, `UploadFinalizationFailedError`, and
  `UploadStorageError`.

Adapters may raise these framework exceptions. Arbitrary subclasses, messages,
and exception chains are sanitized at GraphQL/HTTP boundaries; do not rely on
custom error codes reaching clients.

Custom adapters should raise `UploadObjectMissingError` only when an exact
inspect or cleanup operation can prove that its target is already absent. The
cleanup worker treats that signal as idempotent success. Direct-upload
preflight maps it to the generic client-safe `UPLOAD_INCOMPLETE` error; the
exception message is never exposed to GraphQL clients.

For local replacement cleanup, `gm-upload-old-claims/` is reserved internal
storage. The built-in adapter's exact-delete contract assumes no application or
external process mutates that namespace; GeneralManager's durable lease
serializes framework workers. Storage integrations that cannot provide this
exclusivity should not enable replacement deletion and should instead register
an adapter with a backend-native atomic exact-delete operation.

::: general_manager.uploads.config.FileUploadPolicy

::: general_manager.uploads.config.FileInspection

::: general_manager.uploads.public.register_upload_adapter

::: general_manager.uploads.adapters.UploadAdapter

::: general_manager.uploads.adapters.UploadFinalizationAdapter

::: general_manager.uploads.adapters.ExactPublicDownloadAdapter

::: general_manager.uploads.adapters.ProxyUploadSink

::: general_manager.uploads.graphql_types.UploadToken

::: general_manager.uploads.graphql_types.StoredFile

::: general_manager.uploads.graphql_types.StoredImage

`MeasurementScalar` parses string inputs such as `"12.5 m/s"` into
`Measurement` values and serializes stored measurements with the canonical
`Measurement` string formatting; it does not preserve the caller's original text
exactly. Invalid measurement text propagates the measurement module's validation
errors during input coercion. `BigIntScalar` serializes large integers as strings
so clients do not lose precision beyond GraphQL's built-in `Int` range; boolean
values and non-coercible Python objects are rejected. Float and `Decimal` inputs
are accepted for compatibility; current releases guarantee Python `int(...)`
truncation toward zero for those inputs.

For pagination, treat the generated GraphQL `pageInfo` response field as the
user-facing contract. `totalCount` is counted after permission filters, user
filters, excludes, sorting, and grouping, but before page slicing. Supplying
only one of `page` or `pageSize` defaults the missing value to page 1 or size 10
for slicing. Falsey explicit values such as `page: 0` or `pageSize: 0` follow
the same fallbacks for slicing; `currentPage` is `page || 1`. The reported
`pageSize` is the original argument value, not the effective slicing default, so
it can be `null` when only `page` is supplied and `0` when `pageSize: 0` is
supplied. `totalPages` is `1` when the original `pageSize` is omitted or falsey.
Negative `page` or `pageSize` values raise a GraphQL `BAD_USER_INPUT` error
before slicing. Do not import generated/internal Python pagination classes
directly.

::: general_manager.api.mutation.graph_ql_mutation

`graph_ql_mutation` registers a synchronous function as a Graphene mutation at
import time and returns the original function unchanged. Supported decorator
forms are bare usage, call usage, positional `MutationPermission`, and
`permission=...`; if both permission forms are supplied, the positional class is
used. The generated mutation name is the function name converted by
`snake_to_camel`: the first underscore-delimited segment is kept unchanged and
later segments are title-cased. Duplicate generated names are first-writer-wins
in `GraphQL._mutations`.
Every non-`info` parameter needs a type annotation. Optional annotations make an
argument nullable, defaults become Graphene defaults, list annotations become
GraphQL lists, manager arguments with no declared inputs or only one `id` input
become `ID`, and multi-input manager arguments become cached nested input
objects keyed by manager module and qualified name. Runtime manager
normalization preserves existing instances, leaves `None` unchanged, constructs
mapping inputs with `Manager(**value)`, and constructs non-mapping inputs with
`Manager(value)`. For `list[Manager]` and `List[Manager]` arguments, each list
item follows that same normalization. Return annotations create output fields
plus required `success`; tuple annotations create multiple output fields and
duplicate field names are rejected. Type aliases expose the alias name while
mapping through the target type. Runtime tuple results are assigned to output
fields in annotation order without exact length validation. Resolver execution
normalizes manager arguments before permission checks and before calling the
original function.
Handled GeneralManager domain errors are converted with the normal GraphQL error
mapper; other exceptions propagate.

::: general_manager.api.property.graph_ql_property

::: general_manager.api.property.GraphQLPropertyReturnAnnotationError

::: general_manager.api.property.GraphQLPropertyTimeoutConfigurationError

::: general_manager.api.property.GraphQLPropertyWarmUpConfigurationError

::: general_manager.api.registry.GraphQLRegistry

`GraphQLRegistry` is the public snapshot shape returned by
`GraphQL.get_registry_snapshot()`. Registry dictionaries in a snapshot are
shallow copies, so adding or removing keys on the snapshot does not mutate the
live `GraphQL` registries. The generated Graphene classes, fields, and resolver
objects stored inside those dictionaries are shared with the live registry.
Use `GraphQL.reset_registry()` in tests before rebuilding a schema; it clears
generated query, mutation, subscription, search, capability, and manager
registries. Generated registry entries are Graphene classes, fields, and
callables, so treat them as opaque objects unless you are writing integration
tests around schema assembly.

`GraphQL.create_graphql_interface(MyManager)` registers the manager's Graphene
object type, list/detail queries, relation filters, pagination wrapper, and
subscription fields. It returns without side effects when the manager has no
`Interface`, and it adds a `capabilities` field only when the manager exposes
GraphQL permission capabilities. `GraphQL.create_graphql_mutation(MyManager)`
also returns without side effects when the manager has no `Interface`; otherwise
it registers create/update/delete mutations only for operations supported by the
manager interface through an overridden base method or an advertised capability.
Mutation factory results of `None` are skipped.

Filter helper inputs are mapping-shaped lookup objects or JSON object strings;
malformed JSON and decoded non-object JSON normalize to empty filters. Search
filter helpers additionally accept the list-of-filter-object form used by search
UIs, where each item contains `field` plus optional `op`, `value`, or `values`.
When `values` is present and `op` is omitted, search filter parsing uses the
`__in` lookup, and `values` takes precedence over `value`. JSON strings may also
decode to that list form. The lookup key is `field` when `op` is blank and
`field__op` otherwise; operator names are not validated during parsing.
Malformed list entries are ignored.

Permission constraints used by generated resolvers are ordered alternatives.
Each entry may contain a `filter` mapping, an `exclude` mapping, both, or
neither. Search merges each alternative's `filter` mapping over the user filter
mapping before the backend query; matching `exclude` mappings remain paired
with that alternative for the per-instance authorization pass. Empty
constraints such as `{}`, `{"filter": {}}`, and `{"exclude": {}}` therefore
represent one unrestricted alternative relative to the user filters. If the
read permission plan still requires instance checks, that unrestricted
alternative must still pass `can_read_instance()`.

GraphQL relation filters are flattened before backend/query execution. Direct
relations are attribute metadata entries with `relation_kind="direct"` and
flatten to the relation's `filter_lookup` prefix. Collection relations use
`relation_kind="collection"` and expose nested `any` and `none` inputs: `any`
becomes positive filters, `none` becomes excludes, and nested excludes under
`none` invert back into positive filters. Generated relation filter input types
are cached only in the caller-owned registry under a name containing the manager
class and remaining relation depth; rebuild them with a fresh registry when
metadata or depth changes. Equality-style `id`, `id__exact`, and
list/tuple-shaped `id__in` filters are cast with the manager interface's `id`
input field when one is available; other iterable shapes are returned unchanged.
Subscription identifiers and signal payloads are object-valued manager
identification mappings and are copied before channel dispatch.

The optional global `me` field is added only when
`GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER` points to a provider class. Provider
`graphql_fields` entries may be Graphene fields or Python types; provider
methods named `resolve_<field>` are used when present, otherwise the field is
read from the current request user. Provider `graphql_capabilities` entries that
are not `GraphQLPermissionCapability` instances are ignored.

GeneralManager-generated mutations and mutations created with
`@graph_ql_mutation` convert exceptions through the shared safe error mapper.
This boundary does not intercept arbitrary third-party GraphQL resolvers that
bypass GeneralManager's generated and decorator mutation paths.

Existing `GraphQLError` instances are trusted and returned unchanged, preserving
object identity, message, and the full existing `extensions` mapping.
`PublicGraphQLError` is the stable application contract for an intentional
public failure with a safe message and stable application `code`; import it from
`general_manager.api`.

Django `ValidationError` remains public validation output. Unstructured errors
preserve Django's rendered validation message with code `BAD_USER_INPUT`. When a
`ValidationError` has `message_dict`, the mutation error uses the generic message
`Validation failed.` and includes structured details in
`extensions.fieldErrors` and `extensions.nonFieldErrors`. Generated mutations
use schema-aware input field names, so a Python/Django field such as
`project_phase_type` is reported as `projectPhaseType`, and relation raw-id
implementation keys such as `customer_id` are reported as their exposed GraphQL
input field, such as `customer`. Decorator-created mutations map structured
validation field keys with `snake_to_camel`.

`PermissionError` returns the fixed message `Permission denied.` with code
`PERMISSION_DENIED`, unless application code deliberately raises an explicit
public error. Every other ordinary exception crossing these boundaries,
including `ValueError`, returns exactly `An internal server error occurred.`
with code `INTERNAL_SERVER_ERROR` and an opaque `errorId`. Server logs retain the
original exception details and traceback with the matching `error_id`; failures
while rendering the original exception are also kept out of the client response.

Migrate client-facing `ValueError` uses to `PublicGraphQLError`, or to Django
`ValidationError` for validation (use structured validation errors for field
details). Public messages must never contain secrets or other internal details.

::: general_manager.api.graphql_view.GeneralManagerGraphQLView

::: general_manager.api.graphql_warmup.GraphQLWarmUpManagerClass

::: general_manager.api.graphql_warmup.GraphQLWarmUpSummary

::: general_manager.api.graphql_warmup.warmable_graphql_properties

::: general_manager.api.graphql_warmup.warm_up_graphql_properties

::: general_manager.api.graphql_warmup.warm_up_graphql_recipe

::: general_manager.api.graphql_warmup.refresh_due_graphql_warmup_recipes

::: general_manager.api.graphql_warmup.enqueue_graphql_warmup

::: general_manager.api.graphql_warmup.enqueue_graphql_recipe_warmup

::: general_manager.api.graphql_warmup.graphql_warmup_enabled

::: general_manager.api.graphql_warmup_registry.GraphQLWarmUpRecipe

::: general_manager.api.graphql_warmup_registry.GraphQLWarmUpRecipeLock

::: general_manager.api.graphql_warmup_registry.GraphQLWarmUpRecipeLockTimeoutError

::: general_manager.api.graphql_warmup_registry.register_graphql_warmup_recipe

::: general_manager.api.graphql_warmup_registry.get_graphql_warmup_recipe

::: general_manager.api.graphql_warmup_registry.get_graphql_warmup_recipes

::: general_manager.api.graphql_warmup_registry.graphql_warmup_recipe_keys

::: general_manager.api.graphql_warmup_registry.due_timeout_graphql_warmup_recipe_keys

::: general_manager.api.graphql_warmup_registry.delete_graphql_warmup_recipe

::: general_manager.api.graphql_warmup_registry.acquire_graphql_warmup_recipe_lock

::: general_manager.api.graphql_warmup_registry.release_graphql_warmup_recipe_lock

::: general_manager.api.graphql_warmup_tasks.configure_graphql_warmup_beat_schedule_from_settings

::: general_manager.api.graphql_warmup_tasks.warm_up_graphql_properties_task

::: general_manager.api.graphql_warmup_tasks.warm_up_graphql_recipes_task

::: general_manager.api.graphql_warmup_tasks.refresh_due_graphql_warmup_recipes_task

::: general_manager.api.graphql_warmup_tasks.dispatch_graphql_warmup

::: general_manager.api.graphql_warmup_tasks.dispatch_graphql_recipe_warmup

::: general_manager.api.graphql.SubscriptionEvent

::: general_manager.api.graphql_subscription_consumer.GraphQLSubscriptionContext

::: general_manager.api.graphql_subscription_consumer.GraphQLSubscriptionConsumer

::: general_manager.api.remote_invalidation_client.RemoteInvalidationClient

::: general_manager.api.remote_invalidation.remote_invalidation_group_name

::: general_manager.api.remote_invalidation.emit_remote_invalidation

::: general_manager.api.remote_invalidation.RemoteInvalidationConsumer

::: general_manager.api.remote_invalidation.ensure_remote_invalidation_route

::: general_manager.api.remote_invalidation.clear_remote_invalidation_routes

::: general_manager.api.remote_api.RemoteAPIConfig

::: general_manager.api.remote_api.add_remote_api_urls

::: general_manager.api.remote_api.clear_remote_api_urls

`RemoteAPIConfig` is the normalized server-side REST exposure for managers that
define `RemoteAPI.enabled = True`. `base_path` defaults to `/gm`, is normalized
with one leading slash and no trailing slash, rejects root or empty paths,
rejects empty `//` segments, and requires lowercase slug path segments.
`resource_name` is required; surrounding slashes are stripped before validation,
and the remaining value must be a lowercase slug. At least one of
`allow_filter`, `allow_detail`, `allow_create`, `allow_update`, or `allow_delete`
must be true, and
`websocket_invalidation=True` requires one of the mutation operations. Duplicate
`(base_path, resource_name)` exposures raise `RemoteAPIConfigurationError`.
When the manager interface declares an `id` input typed as `int`, URL item
identifiers are coerced with `int(identifier)`. The coercion check is exactly
`identifier_type is int`; compatible subclasses or other numeric types leave URL
identifiers as strings.

`add_remote_api_urls(manager_classes)` imports `settings.ROOT_URLCONF`, builds
the enabled RemoteAPI registry, and appends generated URL patterns in query,
item, then create order. Repeated calls skip already marked generated routes for
the same route key. It returns without changes when `ROOT_URLCONF` is unset.
`clear_remote_api_urls()` removes only URL patterns marked as generated
RemoteAPI routes and also returns without changes when `ROOT_URLCONF` is unset.

RemoteAPI views accept an optional `X-General-Manager-Protocol-Version` header;
when present it must match the configured protocol version. Request bodies must
be empty or JSON objects. `POST <base>/<resource>/query` accepts optional
`filters`, `excludes`, `ordering`, `page`, and `page_size`. It starts with
`manager_cls.all()`, applies `bucket.filter(**filters)` only when `filters` is
truthy, applies `bucket.exclude(**excludes)` only when `excludes` is truthy,
then applies ordering, computes `total_count`, and finally slices only for
positive integer `page` and `page_size`; invalid pagination values are ignored.
`ordering` may be one field name or an iterable of field names; entries prefixed
with `-` sort descending and are applied in reverse order so multi-key ordering
is stable with bucket chaining. `GET`, `PATCH`, and
`DELETE <base>/<resource>/<identifier>` are controlled by the item operation
flags; disabled operations and unsupported methods return HTTP 405 without
constructing a manager. `PATCH` and `POST <base>/<resource>` require object
payloads and call the manager's normal `update()` or `create()` methods, so
permission, validation, and interface errors still apply.

Success envelopes include `items`, `metadata.protocol_version`,
`metadata.request_id`, response header `X-Request-ID`, optional metadata extras
such as query controls, and `total_count` only when supplied by the endpoint.
Error responses are sanitized envelopes with `error`, `error_code`, `metadata`,
`X-Request-ID`, and optional `details`. `ObjectDoesNotExist` maps to
`404/not_found`, `PermissionError` to `403/permission_denied`, `ValidationError`
to `400/validation_error`, `RuntimeError` to `500/internal_error`, and caught
`AttributeError`, `LookupError`, `RemoteAPIConfigurationError`, `TypeError`,
`ValueError`, and `RemoteAPIRequestError` subclasses map to
`400/invalid_request`.
