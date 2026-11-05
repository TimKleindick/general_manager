# Permission System Overview

GeneralManager represents attribute-based access control (ABAC) with permission
classes attached to managers. A policy evaluates a user, manager attributes and
related managers to decide whether an operation is allowed. The entry point
determines when that policy runs.

A manager can define a nested `Permission` class derived from `BasePermission`
or a subclass such as `AdditiveManagerPermission` or `OverrideManagerPermission`.
The permission class decides:

- Whether the user can perform CRUD actions on the manager.
- Which specific attributes are visible or editable.
- Which read constraints and instance checks an endpoint needs to apply.

## Where permissions run

| Entry point | Permission behavior |
| --- | --- |
| Python manager writes: `create()`, `update()`, `delete()` | Check the configured write policy using `creator_id`, unless explicitly bypassed. Omitted actors resolve to anonymous users. |
| Python reads: construction, `get()`, `filter()`, `all()`, iteration and attribute access | Do not infer a request user or automatically enforce read permissions. |
| Generated GraphQL queries | Use `info.context.user` to enforce object and field read permissions. List/search resolvers apply permission filters and any required final instance checks. |
| Generated GraphQL mutations | Derive the actor from the request context and enforce write permissions. Clients cannot supply an actor or permission-bypass flag. |
| Custom endpoints and resolvers | Own authorization for the records and fields they expose. Calling a manager read method alone does not authorize its result. |

For Python writes in a Django view, pass `creator_id=request.user.pk`; do not
trust a client-supplied actor ID. Calling a manager with an ID identifies its
data, not the requesting user. Superusers bypass permission checks, so use
ordinary users when testing allow/deny behavior.

Managers without an explicit policy receive `AdditiveManagerPermission` during
initialization. Its built-in defaults allow public reads and require
authenticated writes. Configure an explicit policy for your application; see
[default permissions](manager_based_permission.md#default-permissions-from-settings).

## Custom read integrations

A custom transport must decide which records the caller can read and which
fields it may expose. Permission prefilters alone may be insufficient when a
rule depends on a concrete instance. Check the instance policy and selected
field rules before reading or serializing protected values. If the integration
has already selected an authorized subset, preserve it with
[`with_instances()`](../../examples/permission_cookbook.md#preserve-an-authorized-bucket-subset).
That method preserves the subset; it does not perform authorization.

Generated GraphQL already owns these read checks. Execute it with the correct
request user instead of adding a second authorization layer around its results.
See [GraphQL security](../graphql/security.md) for its full contract.

## Continue reading

- [Define and test a permission policy](../../howto/add_permission_rule.md)
- [Manager-based permissions](manager_based_permission.md)
- [Object-level delegation](object_level.md)
- [Practical examples](examples.md)
- [GraphQL permission capabilities](../graphql/permission_capabilities.md)
- [Audit logging](../observability/audit_logging.md)
