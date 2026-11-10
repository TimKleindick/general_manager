# Interfaces Overview

Interfaces define how managers store or compute data. They encapsulate persistence logic, conversion between Django models and managers, and the inputs required to instantiate a manager.

GeneralManager ships with these interface flavours:

- [Database interfaces](db_based_interface.md) persist records to relational databases.
- [Existing model interfaces](existing_model_interface.md) reuse legacy Django models without duplicating their base tables; enabling history may add history tables.
- [Read-only interfaces](db_based_interface.md#read-only-data) synchronise static datasets from JSON.
- [Calculation interfaces](computed_data_interfaces.md) compute values on the fly from inputs and related managers.
- [Request interfaces](request_interface.md) connect managers to remote HTTP-style services through declarative request operations and filter mappings.
- [Excel-backed interfaces](excel_interface.md) expose typed worksheet rows through a shared cache mirror while keeping Excel authoritative.
- [RemoteAPI websocket invalidation](remote_api.md) keeps remote clients aligned with committed server-side changes.

All interfaces inherit from `general_manager.interface.base_interface.InterfaceBase`, which provides shared behaviour such as identification, validation, and integration with the dependency tracker.

Understanding the capabilities of each interface helps you pick the right tool for each domain object.

## Choose by source and ownership

| Situation | Start with |
| --- | --- |
| You are defining new database tables | `DatabaseInterface` |
| Your Django model already exists | `ExistingModelInterface` |
| You own a fixed reference dataset | A read-only database interface |
| Values are derived from explicit inputs | `CalculationInterface` |
| A service owns the records | `RequestInterface`, or the remote manager integration for a GeneralManager service |
| A workbook owns the rows | `ExcelInterface` |

Declaring an interface does not make every operation available. For example,
a calculation has inputs and computed values rather than persistent rows to
update. A request interface can expose only the operations configured for its
remote service. Use the relevant guide to check supported reads, writes,
filtering, history, and permissions.

The implementation details below are useful when extending interfaces. Normal
application code can use the public classes exported by
`general_manager.interface` without importing manifests or capability internals.

## Module layout

The interface layer is now organised by responsibility:

- `general_manager.interface.interfaces` contains the concrete interface classes (also re-exported via `general_manager.interface` for convenience).
- `general_manager.interface.bundles` defines reusable capability sets such as `ORM_WRITABLE_CAPABILITIES` and `READ_ONLY_CAPABILITIES`.
- `general_manager.interface.capabilities` holds the capability implementations (grouped into subpackages like `orm/`, `read_only/`, and `calculation/`) plus utilities under `capabilities/orm_utils`.
- `general_manager.interface.manifests` owns the manifest + builder pipeline that wires capabilities onto interfaces.
- `general_manager.interface.utils` hosts shared plumbing such as the ORM base models, protocol definitions, and error helpers, while `general_manager.interface.infrastructure` implements the startup-hook and system-check registries.

## Capability-first interfaces

Interfaces now operate in a capability-first mode. Each class declares the capabilities it needs, and the manifest/builder pipeline wires those capabilities at runtime. Read the dedicated guide in [capability-first interfaces](capability-first.md) and see the how-to articles on creating [custom interface types](../../howto/create_custom_interface_type.md) and [custom capabilities](../../howto/create_custom_capability.md) for step-by-step instructions.
