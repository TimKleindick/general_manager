# ADR 0007: GraphQL capability exposure for frontend authorization

## Status
Proposed

## Context
GeneralManager already enforces backend authorization through manager
permissions, GraphQL read filtering, and mutation checks. Frontends still need a
safe way to ask business-oriented questions such as "can this user rename this
project?" or "can this user create a derivative from this object?" without
reading Django groups, permission strings, or role names.

The GraphQL API needs this without weakening backend checks or adding N+1
permission work in list queries. It also needs to avoid confusion with the
existing interface capability system, which is an internal composition mechanism
for interfaces.

## Decision
Add a dedicated GraphQL permission capability layer. Capability fields are
advisory boolean hints for clients; real reads and writes continue to use the
existing backend authorization path.

### Declaration API
Object capabilities are declared on a manager's nested `Permission` class:

```python
class Permission(AdditiveManagerPermission):
    graphql_capabilities = (
        permission_capability(Derivative, "create", name="canCreateDerivative"),
        object_capability("canRename", can_rename_project),
    )
```

The declarations live on `Permission`, not `Interface.configured_capabilities`,
so frontend authorization metadata stays separate from interface composition
capabilities.

Provide three helpers:

- `permission_capability(target, action, *, name=None, payload=None)` delegates
  to the existing manager create/update/delete permission entrypoints.
- `mutation_capability(mutation, *, name=None, payload=None)` delegates to the
  real mutation permission path.
- `object_capability(name, evaluator, *, batch_evaluator=None)` covers
  domain-specific rules that cannot delegate to an existing operation.

When omitted, helper-generated names use lower-camel business names such as
`updateProject` or `renameProject`. Applications may override names whenever a
more domain-specific label is clearer.

### Current-user capabilities
Global capabilities and current-user fields are supplied by an optional provider
configured in Django settings:

```python
GENERAL_MANAGER = {
    "GRAPHQL_GLOBAL_CAPABILITIES_PROVIDER": "my_app.auth.GraphQLCapabilities",
}
```

The provider declares:

- explicit whitelisted fields for `me`
- global capability declarations for `me.capabilities`

If no provider is configured, GraphQL does not expose a synthetic `me` object.
There is no automatic exposure of the Django user model and no separate
`user_capability(...)` helper.

### GraphQL shape
Managers with declared object capabilities get:

```graphql
Project {
  capabilities: ProjectCapabilities!
}
```

Configured global capabilities get:

```graphql
me: Me
Me.capabilities: MeCapabilities!
```

Capability object types are generated and cached by the schema builder beside
the existing generated GraphQL types.

### Evaluation and caching
Capability evaluation runs through an operation-scoped
`CapabilityEvaluationContext`.

- Query and mutation operations get a fresh context per operation.
- Batched HTTP requests do not share capability cache entries between
  operations.
- Subscription events get a fresh context per emitted event to avoid stale
  authorization results.

Object cache keys use manager type, normalized interface identification, user
identity, and capability name. They must not assume a single `id` field because
GeneralManager supports composite and non-ORM identification.

Capability evaluators are deny-on-error. Failures are logged, resolve to
`false`, and are cached for the rest of the operation. Capability fields are
non-null booleans, so clients never need to distinguish `false` from `null`.

### List performance
`object_capability(...)` may provide a `batch_evaluator`. List resolvers inspect
the selected fields and warm capability results for the returned page when
`capabilities` is requested.

If no batch evaluator exists, or if batch evaluation fails, the resolver falls
back to per-object evaluation through the same cached context.

## Alternatives Considered
### Expose raw groups, roles, or Django permissions
Rejected. That would couple frontends to backend storage and policy details.

### Auto-generate capability names from permission rules
Rejected. The frontend contract should use stable domain language, not backend
CRUD or field-level rule names.

### Expose UI flags
Rejected. Names such as `showAdminSidebarButton` encode presentation choices
instead of authorization facts.

### Resolve every row independently in lists
Rejected. Lists need explicit batching and operation-scoped caching to avoid
N+1 permission checks.

## Consequences
- Frontends get a stable, domain-oriented authorization contract.
- Backend authorization remains the source of truth for all real operations.
- Applications must explicitly declare the capability names they expose.
- The GraphQL layer gains provider resolution, generated capability types,
  resolver wiring, operation-scoped caching, and optional list warmup.

## Implementation Plan
1. Add declaration primitives, provider configuration, operation-scoped
   evaluation context, and unit tests.
2. Add the `Permission.graphql_capabilities` opt-in surface and public exports.
3. Generate `me.capabilities` and per-object `capabilities` fields in GraphQL.
4. Add resolver evaluation, selection-aware list warmup, deny-on-error logging,
   and integration tests.
5. Document declaration patterns, provider configuration, performance behavior,
   and the advisory-only contract.
