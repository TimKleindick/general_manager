# Interfaces Overview

Interfaces define how managers store or compute data. They encapsulate persistence logic, conversion between Django models and managers, and the inputs required to instantiate a manager.

GeneralManager ships with four main interface flavours:

- [Database interfaces](db_based_interface.md) persist records to relational databases.
- [Existing model interfaces](existing_model_interface.md) wrap legacy Django models without generating new tables.
- [Read-only interfaces](db_based_interface.md#read-only-data) synchronise static datasets from JSON.
- [Calculation interfaces](computed_data_interfaces.md) compute values on the fly from inputs and related managers.

All interfaces inherit from `general_manager.interface.base_interface.InterfaceBase`, which provides shared behaviour such as identification, validation, and integration with the dependency tracker.

Understanding the capabilities of each interface helps you pick the right tool for each domain object.

## Module layout

The interface layer is now organised by responsibility:

- `general_manager.interface.interfaces` contains the concrete interface classes (also re-exported via `general_manager.interface` for convenience).
- `general_manager.interface.bundles` defines reusable capability sets such as `ORM_WRITABLE_CAPABILITIES` and `READ_ONLY_CAPABILITIES`.
- `general_manager.interface.capabilities` holds the capability implementations (grouped into subpackages like `orm/`, `read_only/`, and `calculation/`) plus utilities under `capabilities/orm_utils`.
- `general_manager.interface.manifests` owns the manifest + builder pipeline that wires capabilities onto interfaces.
- `general_manager.interface.utils` hosts shared plumbing such as the ORM base models, protocol definitions, and error helpers, while `general_manager.interface.infrastructure` implements the startup-hook and system-check registries.

## Capability-first interfaces

Interfaces now operate in a capability-first mode. Each class declares the capabilities it needs, and the manifest/builder pipeline wires those capabilities at runtime. Read the dedicated guide in [capability-first interfaces](capability-first.md) and see the how-to articles on creating [custom interface types](../../howto/create_custom_interface_type.md) and [custom capabilities](../../howto/create_custom_capability.md) for step-by-step instructions.
