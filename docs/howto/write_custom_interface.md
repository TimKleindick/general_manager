# Write a Custom Interface

A custom interface is a backend implementation: it must supply identification,
attribute metadata, collection behavior, and the operations its managers expose.
Subclassing `InterfaceBase` and fetching a dictionary is not sufficient.

First check whether an existing interface owns the behavior you need:

- Use [RequestInterface](request_interface.md) for an HTTP-style service.
- Use [ExistingModelInterface](../concepts/interfaces/existing_model_interface.md) for a Django model you already have.
- Use [CalculationInterface](../concepts/interfaces/computed_data_interfaces.md) for derived values.
- Use [ExcelInterface](excel_interface.md) for a workbook.

Continue here when your source needs a different backend contract. This guide
maps the responsibilities to implement; the linked capability guides provide
the extension mechanics.

## Step 1: Subclass InterfaceBase

Choose `InterfaceBase` for a non-ORM source or `OrmInterfaceBase` when you need
the existing Django ORM behavior. Define the inputs that identify one record
and decide which public attributes the manager will expose.

Follow [Create a custom interface type](create_custom_interface_type.md) to
declare and bind capabilities. Reuse the existing capability that owns a
responsibility where possible. A new transport, for example, does not necessarily
need a new filtering, permission, or lifecycle implementation.

## Step 2: Implement manager operations

Trace the manager API into the interface before implementing an operation:

| Manager use | Interface responsibility |
| --- | --- |
| Construct one manager | Validate identification inputs and initialize its data access |
| Read an attribute | Supply attribute metadata and the corresponding getter |
| `Manager.all()` | Return a bucket through `Interface.filter()` with no filters |
| `Manager.filter(...)` / `exclude(...)` | Return a compatible bucket with the source's filtering semantics |
| `create(...)` | Persist through the supported mutation handler and return identification for the new manager |
| `update(...)` / `delete(...)` | Implement the mutation contract expected by the manager and its lifecycle |

An `all()` method yielding dictionaries does not satisfy the collection
contract: `GeneralManager.all()` calls `Interface.filter()`. A bucket must yield
the expected manager values and preserve its source context. Use the
[Interface API](../api/interface.md) and [bucket contracts](../api/core.md)
for signatures and result shapes, then follow
[Create a custom capability](create_custom_capability.md) to implement a missing
handler.

Declare only supported operations. In particular, do not imply transactional
writes, global sorting, or historical reads when the source cannot provide them.

## Step 3: Handle dependency tracking

Read tracking records what a calculation used; invalidation also needs a way
to observe relevant changes. Reuse the source's existing query-key and
invalidation machinery when available. Calling `DependencyTracker.track()`
alone does not detect a remote system changing.

For a new backend, define how its read dependencies correspond to mutation or
external-change events. If changes cannot be observed reliably, choose an
explicit freshness policy such as timeout caching instead of promising automatic
dependency invalidation. See [caching and dependency tracking](../concepts/caching.md)
for cache scopes, tracker boundaries, and transaction behavior.

## Step 4: Wire up permissions

Attach a manager permission policy. Normal manager writes evaluate it before
delegating to the interface. Plain Python reads still need explicit checks at
the application boundary.

Generated GraphQL applies its read policy, but schema integration requires
working attribute metadata, manager registration, and the appropriate interface
capabilities. `get_attribute_types()` alone is not a complete integration.
Verify which operations the generated schema actually exposes. The
[permission guide](../concepts/permission/index.md) explains the boundaries;
the [GraphQL reference](../api/graphql.md) defines the generated contract.

## Step 5: Document limitations

Explain the source's supported filters, ordering and pagination, write
atomicity, history, caching freshness, and error behavior. State what happens
after a remote timeout when a write may already have succeeded. These are
decisions application developers need before choosing the interface.

Keep source-specific setup and limitations beside the interface's usage guide.
For a worked integration using a supplied backend, see the
[request-interface recipe](../examples/request_interface_end_to_end.md).
