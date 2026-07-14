# Security

Security in the GraphQL layer relies on permission checks and robust error handling.

## Permission enforcement

- Every query obtains read filters from the manager's `Permission` class via `get_read_permission_filter()`. Managers without a `Permission` class receive no GraphQL read prefilter and no per-object read gate from this helper.
- Permission classes can return a full `ReadPermissionPlan` from the zero-argument permission instance method `get_read_permission_plan()`. `ReadPermissionPlan` is an internal adapter with `filters`, `requires_instance_check`, and `instance_check_reasons`; it is used by generated resolvers, not intended as a stable user import. If `get_read_permission_plan()` is absent or does not return that adapter, GraphQL falls back to the zero-argument legacy permission instance method `get_permission_filter()` and runs per-object read checks after the prefilter.
- The helper reads `Permission` from the manager class with normal Python attribute lookup and calls it positionally as `Permission(manager_class, info.context.user)`. The later row gate calls the same permission class as `Permission(instance, info.context.user).can_read_instance()`.
- Legacy permission filter entries may contain optional `filter` and `exclude` mappings. Missing keys are treated as empty mappings by resolvers. Malformed entries are not validated by the helper and fail later when applied to the bucket or search backend. If the fallback `get_permission_filter()` method is missing, the resulting `AttributeError` propagates. The legacy fallback plan uses `requires_instance_check=True` and `instance_check_reasons=("no_prefilter_backend",)`.
- Mutations invoke `check_create_permission`, `check_update_permission`, or `check_delete_permission` before executing. A `PermissionError` returns the fixed public message `Permission denied.` rather than its original text.
- Attribute-level restrictions hide protected fields even when the user can access the object.
- Optional GraphQL permission capabilities expose advisory boolean hints for clients. They do not replace read or mutation permission checks.

Always execute GraphQL resolvers through managers; do not reach directly for Django models, or you will bypass permission rules.

## Capability hints

See [GraphQL permission capabilities](permission_capabilities.md) for the full
concept, declaration helpers, current-user provider configuration, and batching
examples.

Declare object capability hints on the manager's nested `Permission` class:

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
