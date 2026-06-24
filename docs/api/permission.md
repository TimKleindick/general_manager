# Permission API

## Core classes

::: general_manager.permission.base_permission.BasePermission

`BasePermission(instance, request_user)` stores the permission context and
resolves `request_user` through `get_user_with_id()`. The request user may be a
Django user, `AnonymousUser`, `SimpleLazyObject`, or a primary-key value. Lazy
users are forced once; existing user objects are returned unchanged; lookup
failures, invalid primary-key values, and unsupported values resolve to
`AnonymousUser`.

`check_create_permission(data, manager, request_user)` and
`check_update_permission(data, old_manager_instance, request_user)` accept
`dict[str, object]` payloads. Each payload key is checked as an attribute; empty
payloads still evaluate the operation-level gate once. Superusers bypass checks
and, when audit logging is enabled, emit bypass audit events. Denials are
logged with manager/action/user context, collected, and raised as one
`PermissionCheckError`. `check_delete_permission()` iterates the manager's
permission attributes and follows the same superuser, audit, denial logging, and
denial aggregation behavior.

`can_read_instance()` returns `True` for superusers and for read plans that do
not require a row-level check. Otherwise it infers candidate readable attributes
from the manager `_attributes` mapping or dictionary permission payload keys and
returns after the first allowed read attribute. If no candidate readable
attribute can be inferred, it raises `NotImplementedError`.

`PermissionCheckError(user, errors)` renders the user as `anonymous` when no
`id` is available, otherwise as `id=<id>`, and includes the collected denial
messages.

`_get_permission_filter(permission)` resolves a permission expression to optional
`filter`/`exclude` mappings with object-valued lookup values. Superusers and
registered permission filters that return `None` both produce empty filter and
exclude mappings. `_get_permission_filter_info(permission)` returns the same
constraint plus a boolean indicating whether the permission was representable as
a prefilter; `None` from the registered filter means `False`. Unknown permission
names raise `PermissionNotFoundError`.

::: general_manager.permission.manager_based_permission.AdditiveManagerPermission

::: general_manager.permission.manager_based_permission.OverrideManagerPermission

::: general_manager.permission.manager_based_permission.ManagerBasedPermission

::: general_manager.permission.mutation_permission.MutationPermission

`MutationPermission` protects custom GraphQL mutations registered with
`graph_ql_mutation`. Subclasses normally declare `__mutate__: ClassVar[list[str]]`
with permission expressions that apply to every payload field. Any expression in
that list may grant the global mutation gate. A subclass may also declare
field-specific `list[str]` class attributes whose names match payload keys; those
expressions add a second gate for that field. For matching fields, both the
global gate and the field-specific gate must pass. Expressions within one list
are alternatives. Payload fields with no field-specific list are governed by the
global gate alone.

`MutationPermission.check(data, request_user)` receives the normalized mutation
argument mapping. Manager-typed GraphQL arguments have already been converted to
manager instances. `request_user` may be a Django user, `AnonymousUser`, lazy
user, or user identifier; non-user values are resolved through the same helper as
manager CRUD permissions, falling back to anonymous access when resolution fails.
Directly constructing `MutationPermission(data, request_user)` expects an already
resolved Django user or `AnonymousUser`.

Omitting `__mutate__` denies by default. Setting `__mutate__ = []` intentionally
allows the global mutation gate, while field-specific lists still apply to their
matching payload keys. `__mutate__` itself is resolved through normal class
inheritance, so a subclass may inherit a base class's global mutation gate.
`__mutate__` must be a `list` containing only strings; tuples, mixed-type lists,
non-list values, and other sequences deny the global gate.

Empty field-specific lists allow that field gate. Only non-dunder attributes
declared directly on the concrete permission class and whose value is a `list`
containing only strings are collected as field-specific permission lists;
inherited field lists, mixed-type lists, tuples, other sequences, constants, and
non-list attributes are ignored. `describe_permissions(attribute)` returns
declared expressions for diagnostics in evaluation order, with global expressions
first and matching field expressions second, without deduplication. Superusers
bypass expression evaluation. When audit logging is enabled, `check()` emits one
mutation audit event per payload key and marks superuser events as bypassed. On
denial, all failed fields are collected and one `PermissionCheckError` is raised.

## Data access helpers

::: general_manager.permission.permission_data_manager.InvalidPermissionDataError

::: general_manager.permission.permission_data_manager.PermissionDataManager

`PermissionDataManager` is the public wrapper used by create, update, delete,
mutation, and delegated permission checks when the permission engine needs one
attribute-access surface for either a payload dictionary or a manager instance.

Dictionary payloads must be `dict` instances or subclasses, not arbitrary
`Mapping` objects. Missing attributes resolve to `None` because lookups use
`dict.get(...)`; `manager=None` is valid when delegated manager resolution is
not needed. Wrapper properties win over same-named payload keys, so a key such
as `"manager"` must be read from `permission_data` directly. For manager
instance payloads, lookups delegate to `getattr(instance, name)` and therefore
follow the manager's normal attribute behavior.

`for_update(base_data, update_data)` requires `base_data` to support
`dict(base_data)`, shallowly overlays `update_data`, and records
`type(base_data)` as the associated manager class. The wrapper exposes only the
merged final state; keep the original manager instance separately when a rule
needs an explicit before/after comparison. Unsupported payload types raise
`InvalidPermissionDataError` with the stable message
`permission_data must be either a dict or an instance of GeneralManager.`

## Registry and reusable checks

::: general_manager.permission.permission_checks.register_permission

::: general_manager.permission.permission_checks.permission_functions

`register_permission(name, *, permission_filter=None)` returns a decorator for
custom permission checks. Applying the decorator registers the function in the
global `permission_functions` registry and returns the original function
unchanged. Duplicate names raise `ValueError` when the decorator is applied.

Permission methods receive `(instance, user, config)` and return `True` to allow
access or `False` to deny access. `config` is the list of colon-separated values
after the permission name, so `"belongsToCustomer:customer"` passes
`["customer"]`. The low-level evaluator normalizes permission method results
with `bool(...)`, but custom methods should return real `bool` values.

`name` is stored exactly as the registry key. Permission expression parsing
splits on `&` and `:` without escaping, so use colon-free names for permissions
that must be referenced from permission strings. Empty config segments are
preserved: `"rule:"` passes `[""]`, and `"rule::x"` passes `["", "x"]`.

Permission filters receive `(user, config)` and return one of these shapes:

- `None` when the rule cannot be represented as a queryset prefilter.
- `{"filter": {"field": value}}` for Django-style filter kwargs.
- `{"exclude": {"field": value}}` for Django-style exclude kwargs.
- Both `filter` and `exclude` keys when a rule needs both constraints.

Registry entries always contain a callable `permission_filter`. When a
permission is registered without one, GeneralManager stores a default callable
that returns `None`.

For Django queryset authorization, GeneralManager applies returned constraints as
`queryset.filter(**filter_kwargs).exclude(**exclude_kwargs)`. Search backends use
the `filter` side as a backend prefilter and rely on the final instance gate for
`exclude` checks. Custom permission methods and filters are called without
exception wrapping; exceptions from those callables propagate to the caller.

`permission_functions` is a normal process-local mutable dictionary. Direct
mutation changes later permission checks in the current process. Tests may
snapshot and restore it, but application code should prefer
`register_permission()` so duplicate-name protection stays active.

Built-in registry names:

| Name | Config | Instance check | Query filter |
| --- | --- | --- | --- |
| `public` | none | Allows every user, including anonymous and inactive users. | None |
| `matches` | `<field>:<value>` | Allows when `str(getattr(instance, field)) == value`. | `{"filter": {field: value}}` |
| `isAdmin` | none | Allows Django staff users, including superusers. | None |
| `isSelf` | none | Allows when `instance.creator == user`. | `{"filter": {"creator_id": user.id}}` |
| `isAuthenticated` | none | Allows authenticated users. | None |
| `isActive` | none | Allows active users. | None |
| `hasPermission` | `<app_label.codename>` | Delegates to `user.has_perm(...)`. | None |
| `inGroup` | `<group name>` | Allows users in the named Django group. | None |
| `relatedUserField` | `<field>` | Allows when `getattr(instance, field) == user`. | `{"filter": {f"{field}_id": user.id}}` |
| `manyToManyContainsUser` | `<field>` | Allows when the related manager contains the user. | `{"filter": {f"{field}__id": user.id}}` |

## GraphQL permission capabilities

::: general_manager.permission.graphql_capabilities.object_capability

::: general_manager.permission.graphql_capabilities.permission_capability

::: general_manager.permission.graphql_capabilities.mutation_capability

::: general_manager.permission.graphql_capabilities.CapabilityEvaluationContext

::: general_manager.permission.graphql_capabilities.GraphQLPermissionCapability

## Audit logging

::: general_manager.permission.audit.AuditLogger

::: general_manager.permission.audit.FileAuditLogger

::: general_manager.permission.audit.DatabaseAuditLogger

::: general_manager.permission.audit.configure_audit_logger

::: general_manager.permission.audit.configure_audit_logger_from_settings

::: general_manager.permission.audit.get_audit_logger

::: general_manager.permission.audit.audit_logging_enabled

::: general_manager.permission.audit.emit_permission_audit_event

::: general_manager.permission.audit.PermissionAuditEvent

## Utility functions

::: general_manager.permission.utils.validate_permission_string

`validate_permission_string(permission, data, request_user)` is the low-level
AND evaluator used by permission classes and mutation checks. It splits
`permission` on `&`, evaluates fragments left-to-right, and stops at the first
fragment whose registered permission method returns `False`. A later unknown
permission name is therefore raised only when every earlier fragment grants
access.

Each reached fragment is split on `:`. The first part selects a key from
`permission_functions`; the remaining parts are passed unchanged as the
permission method's `config` list. There is no escaping: `rule:` passes `[""]`,
`rule::x` passes `["", "x"]`, `rule&&other` attempts to resolve an empty
permission name between the two ampersands, and an entirely empty string also
attempts to resolve the empty permission name. Unknown reached names raise
`PermissionNotFoundError`, and exceptions from custom permission methods
propagate unchanged. Permission method results are normalized through `bool(...)`
before the AND expression continues.

::: general_manager.permission.utils.PermissionNotFoundError

`PermissionNotFoundError.permission` stores the full unresolved fragment,
including any colon-separated config, and the exception message remains
`Permission <fragment> not found.`
