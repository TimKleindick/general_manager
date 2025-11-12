# ADR 0001: Capability-Driven Interface Skeletons

- Status: In Progress
- Date: 2025-11-12

## Context

Every GeneralManager interface is being reduced to a declarative shell that lists the capabilities it needs. Capabilities now encapsulate persistence plumbing, lifecycle wiring, validation, history, observability, and mutation logic. The manifest + builder pipeline guarantees every interface receives the correct capability set.

## Capability Strategy

1. **Manifest-first wiring** – `CapabilityManifest` defines required/optional capabilities per interface family. `ManifestCapabilityBuilder` resolves overrides, applies flag-based toggles, instantiates handlers, and binds them to the interface class.
2. **Interfaces as capability registries** – Each interface exposes metadata (`_interface_type`, `input_fields`, capability overrides). Public methods immediately delegate to the appropriate capability handler.
3. **Shared capability families** – Common responsibilities are implemented once:
   - `orm_support`: manager/queryset/db alias & schema descriptors.
   - `orm_mutation`: simple-field writes, history saves, many-to-many syncing.
   - `orm_lifecycle`: `_pre_create/_post_create`, interface/model/factory creation, Meta/rules/soft-delete.
   - `orm_history`, `orm_query`, `orm_validation`, `read_only_management`, `existing_model_resolution`, etc.

## Responsibility Map (Current State)

| Responsibility | Capability | Status | Notes |
| --- | --- | --- | --- |
| Lifecycle hooks (ORM) | `orm_lifecycle` | ✅ done | Capability owns `_pre_create/_post_create`, model/factory construction, and Meta handling. |
| Manager/queryset/schema helpers | `orm_support` | ✅ done | Validation/query/history pull helpers through this capability. |
| Mutation helpers | `orm_mutation` | ✅ done | Create/update/delete now call capability functions. |
| Historical lookups | `orm_history` | ✅ done | Read capability calls into history handler for current/past snapshots. |
| Read-only schema & lifecycle | `read_only_management` | ✅ done | Capability now wraps pre/post-create hooks, schema validation, and sync work. |
| Existing-model resolution + lifecycle | `existing_model_resolution` | ✅ done | Capability now resolves models, ensures history/rules, builds interface/factory, and wires post-create. |
| Calculation lifecycle | `calculation_lifecycle` | ✅ done | Capability extracts inputs, builds interface subclasses, and wires parent classes. |

## Updated Interface Skeletons

These skeletons show the slimmed-down interface classes. Any omitted behavior lives in capabilities referenced through `capability_overrides`.

### `InterfaceBase`

```python
class InterfaceBase(ABC):
    _parent_class: ClassVar[Type["GeneralManager"]]
    _interface_type: ClassVar[str]
    _use_soft_delete: ClassVar[bool]
    input_fields: ClassVar[dict[str, Input]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        identification = self.parse_input_fields_to_identification(*args, **kwargs)
        self.identification = self.format_identification(identification)

    def get_data(self) -> Any:
        handler = self.get_capability_handler("read")
        if handler is None:
            raise NotImplementedError
        return handler.get_data(self)

    @classmethod
    def filter(cls, **kwargs: Any) -> "Bucket":
        handler = cls.get_capability_handler("query")
        if handler is None:
            raise NotImplementedError
        return handler.filter(cls, **kwargs)

    # create / update / delete follow the same delegation pattern
```

### `OrmPersistenceInterface`

```python
class OrmPersistenceInterface(InterfaceBase, Generic[HistoryModelT]):
    _interface_type = "database"
    input_fields = {"id": Input(int)}
    database: ClassVar[str | None] = None
    capability_overrides = {
        "orm_support": OrmPersistenceSupportCapability,
        "orm_lifecycle": OrmLifecycleCapability,
        "read": OrmReadCapability,
        "validation": OrmValidationCapability,
        "history": OrmHistoryCapability,
        "query": OrmQueryCapability,
        "observability": LoggingObservabilityCapability,
    }

    def get_data(self) -> HistoryModelT:
        return super().get_data()

    @classmethod
    def handle_interface(cls) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        lifecycle = cls._lifecycle_capability()
        return (
            lambda name, attrs, interface, base_model_class=GeneralManagerModel: lifecycle.pre_create(
                name=name,
                attrs=attrs,
                interface=interface,
                base_model_class=base_model_class,
            ),
            lambda new_cls, interface_cls, model: lifecycle.post_create(
                new_class=new_cls,
                interface_class=interface_cls,
                model=model,
            ),
        )
```

### `OrmWritableInterface`

```python
class OrmWritableInterface(OrmPersistenceInterface[WritableModelT]):
    capability_overrides = OrmPersistenceInterface.capability_overrides | {
        "orm_mutation": OrmMutationCapability,
        "create": OrmCreateCapability,
        "update": OrmUpdateCapability,
        "delete": OrmDeleteCapability,
    }

    def update(...):
        handler = self.get_capability_handler("update")
        if handler is None:
            raise NotImplementedError
        return handler.update(self, ...)
```

### `ExistingModelInterface`

```python
class ExistingModelInterface(OrmWritableInterface[ExistingModelT]):
    _interface_type = "existing"
    model: ClassVar[type[models.Model] | str | None] = None
    capability_overrides = OrmWritableInterface.capability_overrides | {
        "existing_model_resolution": ExistingModelResolutionCapability,
        # planned: "existing_model_lifecycle"
    }
```

### `ReadOnlyInterface`

```python
class ReadOnlyInterface(OrmPersistenceInterface[GeneralManagerBasisModel]):
    _interface_type = "readonly"
    capability_overrides = OrmPersistenceInterface.capability_overrides | {
        "read_only_management": ReadOnlyManagementCapability,
    }

    @classmethod
    def sync_data(cls) -> None:
        handler = cls.get_capability_handler("read_only_management")
        handler.sync_data(cls, ...)
```

### `CalculationInterface`

```python
class CalculationInterface(InterfaceBase):
    _interface_type = "calculation"
    capability_overrides = {
        "read": CalculationReadCapability,
        "query": CalculationQueryCapability,
        "observability": LoggingObservabilityCapability,
        # planned: "calculation_lifecycle"
    }
```

## Consequences

- **Single source of truth** – The manifest documents which capabilities belong to each interface family and enables tooling/testing to enforce that contract.
- **Composable interfaces** – Adding a new interface is primarily a matter of subclassing `InterfaceBase`, declaring `_interface_type`, and listing the desired capabilities. All heavy behavior lives in reusable capability modules.
- **Incremental migration** – ORM interfaces already rely purely on capabilities for persistence, mutation, and lifecycle logic. Existing-model, read-only, and calculation interfaces retain a few bespoke helpers, and the table above records the remaining work to extract them.
