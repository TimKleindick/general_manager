# GraphQL Subscriptions

`general_manager.api.graphql.GraphQL` adds subscription fields for every registered manager so clients can react to live changes. This page covers the basic lifecycle and the advanced dependency tracking optimisations that keep the streams efficient.

## Overview

For each manager class (e.g. `Project`), GraphQL exposes a subscription field named `on<ManagerClass>Change` (e.g. `onProjectChange`). The resolver:

1. Validates the identification arguments (e.g. `id`, other interface inputs).
2. Instantiates the manager and emits an initial `snapshot` event.
3. Adds the subscription to one or more channel groups.
4. Listens for change notifications and forwards them as `SubscriptionEvent` payloads.

```graphql
subscription ($id: ID!) {
  onProjectChange(id: $id) {
    action
    item {
      id
      name
    }
  }
}
```

Each event has two fields:

- `action`: describes what triggered the update (`snapshot`, `update`, `delete`, custom signals).
- `item`: a fresh manager instance (or `null` if the underlying data can no longer be fetched).

### Signals and channels

- Subscriptions require Django Channels. If `get_channel_layer()` returns `None`, the resolver raises a descriptive GraphQL error explaining that `CHANNEL_LAYERS` must be configured.
- Managers are automatically decorated with `@dataChange` and emit `pre_data_change` and `post_data_change` signals. GraphQL listens to `post_data_change` and forwards the event to the relevant channel group (`gm_subscriptions.<Manager>.<digest>`).

### Identification helpers

The subscription arguments mirror the interface inputs. For nested managers, the schema accepts IDs (e.g. `employeeId`) so the server can reconstruct the full identification dictionary. This results in subscriptions that are consistent with query and mutation signatures.

## Dependency tracking

When a client subscribes to `on<Manager>Change`, the resolver primes only the GraphQL properties that appear in the `item { … }` selection. The priming step records two sets of dependencies:

- Inputs defined on the interface (standard behaviour).
- Additional managers accessed inside the requested GraphQL properties.

The subscription then joins the channel groups for all collected dependencies. Any dependent manager that emits `post_data_change` triggers a new event for the subscriber. The optimisation keeps subscriptions responsive without executing unrelated properties for every client.

### Query permutations

The field selection inspection understands:

- Inline fragments on the subscription payload.
- Named fragments reused across subscriptions.
- Aliases applied to GraphQL properties.
- Subscriptions that omit the `item` field entirely (only the `action` is streamed).

No additional configuration is necessary. Continue to annotate computed fields with `@graphQlProperty`; the dependency tracker automatically inspects what each subscriber actually needs.

## Error handling

- Missing channel layer configuration produces a GraphQL error instructing the operator to configure `CHANNEL_LAYERS`.
- If instantiating the manager or a dependency fails during an update, the subscription sends an event with `item = null` and the incoming `action`. Clients can use this to show a placeholder while retrying the fetch.

## Testing tips

1. Extend `GeneralManagerTransactionTestCase` to register test managers and clean up dynamic models.
2. Build the schema with `GraphQL._subscription_class` and call `graphene.Schema.subscribe`.
3. Trigger changes in `asyncio.to_thread` to avoid mixing sync/async database operations.
4. Attach a small log list to the manager to assert which GraphQL properties were evaluated during the subscription lifecycle.
