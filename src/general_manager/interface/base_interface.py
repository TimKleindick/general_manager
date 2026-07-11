"""Abstract interface layer shared by all GeneralManager implementations."""

from __future__ import annotations
from abc import ABC
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
import inspect
from threading import Condition, RLock
from types import CellType, CodeType, FunctionType, MappingProxyType
from typing import (
    Type,
    TYPE_CHECKING,
    NotRequired,
    TypeVar,
    Iterable,
    ClassVar,
    Callable,
    Literal,
    TypedDict,
    Protocol,
    cast,
)
from weakref import ReferenceType, ref
from django.conf import settings
from django.core.signals import setting_changed
from django.db.models import Model
from django.dispatch import receiver

from general_manager.conf import get_setting
from general_manager.utils.args_to_kwargs import args_to_kwargs
from general_manager.api.property import GraphQLProperty
from general_manager.interface.capabilities.base import Capability, CapabilityName
from general_manager.interface.capabilities.configuration import (
    CapabilityConfigEntry,
    InterfaceCapabilityConfig,
    iter_capability_entries,
)
from general_manager.interface.capabilities.factory import CapabilityOverride
from general_manager.interface.infrastructure.startup_hooks import register_startup_hook
from general_manager.interface.infrastructure.system_checks import register_system_check

if TYPE_CHECKING:
    from general_manager.interface.interfaces.calculation import CalculationInterface
    from general_manager.manager.input import Input
    from general_manager.manager.general_manager import GeneralManager
    from general_manager.bucket.base_bucket import Bucket
    from general_manager.interface.manifests.capability_models import (
        CapabilitySelection,
    )
    from general_manager.interface.utils.models import GeneralManagerBasisModel
    from general_manager.interface.manifests.capability_builder import (
        ManifestCapabilityBuilder,
    )


GeneralManagerType = TypeVar("GeneralManagerType", bound="GeneralManager")
ResultT = TypeVar("ResultT")
type generalManagerClassName = str
type attributes = dict[str, object]
type interfaceBaseClass = Type[InterfaceBase]
type newlyCreatedInterfaceClass = Type[InterfaceBase]
type relatedClass = Type[Model] | None
type newlyCreatedGeneralManagerClass = Type[GeneralManager]

type classPreCreationMethod = Callable[
    [
        generalManagerClassName,
        attributes,
        interfaceBaseClass,
        type["GeneralManagerBasisModel"] | None,
    ],
    tuple[attributes, interfaceBaseClass, relatedClass],
]

type classPostCreationMethod = Callable[
    [newlyCreatedGeneralManagerClass, newlyCreatedInterfaceClass, relatedClass],
    None,
]

_SINGLE_INPUT_VALUE_CACHE_PREFIX = "interface_single_input_value"
_SINGLE_INPUT_VALUE_CACHE_MISS = object()
_OBSERVABILITY_HOOK_MISSING = object()
_RUN_SCOPED_SCALAR_INPUT_TYPES = (str, int, bool)
_FORMATLESS_IDENTIFICATION_VALUE_TYPES = {str, int, float, bool, type(None)}
_STATIC_ATTRIBUTE_MISSING = object()
_INSTANCE_DICT_NAME = "__dict__"
_EMPTY_CLOSURE_CELL = object()
_MANAGER_INPUT_SEED_PLAN_NAME = "_gm_manager_input_seed_plan"
_MANAGER_INPUT_SEED_PLAN_TOKEN = object()
_RESOLVED_INPUT_VALUES_CACHE_NAME = "_resolved_input_values"
_SEEDED_INPUT_VALUES_CACHE_NAME = "_gm_seeded_input_values_cache"
_LAZY_INPUT_VALUES_CACHE_NAME = "_gm_lazy_input_values_cache"


@dataclass(frozen=True, slots=True)
class _FunctionClosureSnapshot:
    cell: CellType
    content: object
    function: _FunctionSnapshot | None


@dataclass(frozen=True, slots=True)
class _FunctionSnapshot:
    function: FunctionType
    code: CodeType
    closure: tuple[_FunctionClosureSnapshot, ...]
    defaults: tuple[object, ...] | None
    kwdefaults: dict[str, object] | None
    kwdefault_items: tuple[tuple[object, object], ...]
    annotations: dict[str, object]
    annotation_items: tuple[tuple[object, object], ...]
    attributes: dict[str, object]
    attribute_items: tuple[tuple[object, object], ...]


@dataclass(frozen=True, slots=True)
class _StaticDescriptorSnapshot:
    descriptor: object
    functions: tuple[_FunctionSnapshot, ...]


type _StaticDispatchSnapshot = tuple[tuple[str, _StaticDescriptorSnapshot], ...]

_INTERFACE_BASE_PROVENANCE: tuple[type[object], _StaticDispatchSnapshot] | None = None
_INPUT_PROVENANCE: tuple[type[object], _StaticDispatchSnapshot] | None = None
_CALCULATION_INTERFACE_PROVENANCE: (
    tuple[type[object], type[object], _StaticDispatchSnapshot, _StaticDispatchSnapshot]
    | None
) = None
_ORM_INTERFACE_PROVENANCE: (
    tuple[type[object], type[object], _StaticDispatchSnapshot, _StaticDispatchSnapshot]
    | None
) = None
_CALCULATION_CAPABILITY_PROVENANCE: (
    tuple[
        type[object],
        _StaticDispatchSnapshot,
        type[object],
        _StaticDispatchSnapshot,
    ]
    | None
) = None
_GENERAL_MANAGER_PROVENANCE: tuple[type[object], _StaticDispatchSnapshot] | None = None
_GENERAL_MANAGER_META_PROVENANCE: (
    tuple[type[object], _StaticDispatchSnapshot] | None
) = None


@dataclass(slots=True)
class _SeededFieldOrigin:
    manager_ref: ReferenceType[object] | None
    formatted_identification: dict[str, object]
    condition: Condition
    lazy: bool = False
    resolving_thread_id: int | None = None


@dataclass(slots=True)
class _SeededInterfaceOrigin:
    interface_ref: ReferenceType[object]
    resolved_values: dict[str, object]
    fields: dict[str, _SeededFieldOrigin]
    transition_condition: Condition
    waiting_fields_by_thread: dict[int, str] = field(default_factory=dict)


_SEEDED_INTERFACE_ORIGINS: dict[int, _SeededInterfaceOrigin] = {}
_SEEDED_INTERFACE_ORIGINS_LOCK = RLock()


def _register_seeded_interface_origin(
    interface: object,
    resolved_values: dict[str, object],
) -> bool:
    """Register constructor-seeded values without hashing interface objects."""
    interface_id = id(interface)
    coordination_lock = RLock()
    fields: dict[str, _SeededFieldOrigin] = {}
    for field_name, manager in resolved_values.items():
        if type(field_name) is not str:
            return False
        manager_state = object.__getattribute__(manager, _INSTANCE_DICT_NAME)
        if type(manager_state) is not dict:
            return False
        formatted_identification = dict.get(
            manager_state,
            "_GeneralManager__id",
            _STATIC_ATTRIBUTE_MISSING,
        )
        if type(formatted_identification) is not dict:
            return False
        try:
            manager_ref = ref(manager)
        except TypeError:
            manager_ref = None
        fields[field_name] = _SeededFieldOrigin(
            manager_ref=manager_ref,
            formatted_identification=formatted_identification,
            condition=Condition(coordination_lock),
        )

    def remove_origin(interface_ref: ReferenceType[object]) -> None:
        with _SEEDED_INTERFACE_ORIGINS_LOCK:
            current = _SEEDED_INTERFACE_ORIGINS.get(interface_id)
            if current is not None and current.interface_ref is interface_ref:
                _SEEDED_INTERFACE_ORIGINS.pop(interface_id, None)

    try:
        interface_ref = ref(interface, remove_origin)
    except TypeError:
        return False
    origin = _SeededInterfaceOrigin(
        interface_ref=interface_ref,
        resolved_values=resolved_values,
        fields=fields,
        transition_condition=Condition(coordination_lock),
    )
    with _SEEDED_INTERFACE_ORIGINS_LOCK:
        _SEEDED_INTERFACE_ORIGINS[interface_id] = origin
    return True


def _seeded_interface_origin(interface: object) -> _SeededInterfaceOrigin | None:
    """Return an exact weakref-matched origin entry, safe against id reuse."""
    with _SEEDED_INTERFACE_ORIGINS_LOCK:
        origin = _SEEDED_INTERFACE_ORIGINS.get(id(interface))
        if origin is None or origin.interface_ref() is not interface:
            return None
        return origin


def _discard_seeded_interface_origin(
    interface: object,
    origin: _SeededInterfaceOrigin,
) -> None:
    """Discard an exact origin without allowing an id-reused replacement."""
    with _SEEDED_INTERFACE_ORIGINS_LOCK:
        current = _SEEDED_INTERFACE_ORIGINS.get(id(interface))
        if current is origin and current.interface_ref() is interface:
            _SEEDED_INTERFACE_ORIGINS.pop(id(interface), None)


def _seeded_interface_origin_by_id(
    interface_id: int,
) -> _SeededInterfaceOrigin | None:
    """Test-support read that never treats an id alone as authoritative."""
    with _SEEDED_INTERFACE_ORIGINS_LOCK:
        origin = _SEEDED_INTERFACE_ORIGINS.get(interface_id)
        if origin is None or origin.interface_ref() is None:
            return None
        return origin


def _seeded_interface_registry_size() -> int:
    with _SEEDED_INTERFACE_ORIGINS_LOCK:
        return len(_SEEDED_INTERFACE_ORIGINS)


def _static_descriptor(cls: type[object], attribute_name: str) -> object:
    """Read one descriptor without invoking dynamic class lookup hooks."""
    try:
        return inspect.getattr_static(cls, attribute_name)
    except AttributeError:
        return _STATIC_ATTRIBUTE_MISSING


def _function_snapshot(
    function: FunctionType,
    seen: set[int],
) -> _FunctionSnapshot:
    seen.add(id(function))
    closure_snapshot: list[_FunctionClosureSnapshot] = []
    for cell in function.__closure__ or ():
        try:
            content = cell.cell_contents
        except ValueError:
            content = _EMPTY_CLOSURE_CELL
        nested = None
        if type(content) is FunctionType and id(content) not in seen:
            nested = _function_snapshot(content, seen)
        closure_snapshot.append(
            _FunctionClosureSnapshot(cell=cell, content=content, function=nested)
        )
    kwdefaults = function.__kwdefaults__
    annotations = function.__annotations__
    attributes = function.__dict__
    return _FunctionSnapshot(
        function=function,
        code=function.__code__,
        closure=tuple(closure_snapshot),
        defaults=function.__defaults__,
        kwdefaults=kwdefaults,
        kwdefault_items=() if kwdefaults is None else tuple(kwdefaults.items()),
        annotations=annotations,
        annotation_items=tuple(annotations.items()),
        attributes=attributes,
        attribute_items=tuple(attributes.items()),
    )


def _function_mapping_matches(
    current: dict[str, object] | None,
    expected: dict[str, object] | None,
    expected_items: tuple[tuple[object, object], ...],
) -> bool:
    if current is not expected:
        return False
    if current is None:
        return not expected_items
    if type(current) is not dict or len(current) != len(expected_items):
        return False
    return all(
        current_key is expected_key and current_value is expected_value
        for (current_key, current_value), (expected_key, expected_value) in zip(
            current.items(), expected_items, strict=True
        )
    )


def _function_matches_snapshot(
    function: FunctionType,
    expected: _FunctionSnapshot,
    seen: set[int],
) -> bool:
    function_id = id(function)
    if function_id in seen:
        return True
    seen.add(function_id)
    if (
        function is not expected.function
        or function.__code__ is not expected.code
        or function.__defaults__ is not expected.defaults
        or not _function_mapping_matches(
            function.__kwdefaults__, expected.kwdefaults, expected.kwdefault_items
        )
        or not _function_mapping_matches(
            function.__annotations__,
            expected.annotations,
            expected.annotation_items,
        )
        or not _function_mapping_matches(
            function.__dict__, expected.attributes, expected.attribute_items
        )
    ):
        return False
    closure = function.__closure__
    if closure is None:
        return not expected.closure
    if len(closure) != len(expected.closure):
        return False
    for current_cell, expected_cell in zip(closure, expected.closure, strict=True):
        if current_cell is not expected_cell.cell:
            return False
        try:
            current_content = current_cell.cell_contents
        except ValueError:
            current_content = _EMPTY_CLOSURE_CELL
        if current_content is not expected_cell.content:
            return False
        if expected_cell.function is not None and not _function_matches_snapshot(
            current_content, expected_cell.function, seen
        ):
            return False
    return True


def _descriptor_functions(descriptor: object) -> tuple[FunctionType, ...]:
    if type(descriptor) is FunctionType:
        return (descriptor,)
    if type(descriptor) in {classmethod, staticmethod}:
        function = object.__getattribute__(descriptor, "__func__")
        return (function,) if type(function) is FunctionType else ()
    if type(descriptor) is property:
        return tuple(
            function
            for function in (descriptor.fget, descriptor.fset, descriptor.fdel)
            if type(function) is FunctionType
        )
    return ()


def _capture_static_descriptor(descriptor: object) -> _StaticDescriptorSnapshot:
    return _StaticDescriptorSnapshot(
        descriptor=descriptor,
        functions=tuple(
            _function_snapshot(function, set())
            for function in _descriptor_functions(descriptor)
        ),
    )


def _static_descriptor_matches(
    descriptor: object,
    expected: _StaticDescriptorSnapshot,
) -> bool:
    if descriptor is not expected.descriptor:
        return False
    functions = _descriptor_functions(descriptor)
    return len(functions) == len(expected.functions) and all(
        _function_matches_snapshot(function, function_snapshot, set())
        for function, function_snapshot in zip(
            functions, expected.functions, strict=True
        )
    )


def _capture_static_dispatch(
    cls: type[object],
    names: tuple[str, ...],
) -> _StaticDispatchSnapshot:
    """Capture immutable descriptor identities while a framework class loads."""
    return tuple(
        (name, _capture_static_descriptor(_static_descriptor(cls, name)))
        for name in names
    )


def _matches_static_dispatch(
    cls: type[object],
    snapshot: _StaticDispatchSnapshot,
) -> bool:
    """Compare live descriptors with identities captured at module load."""
    return all(
        _static_descriptor_matches(_static_descriptor(cls, name), expected)
        for name, expected in snapshot
    )


def _register_input_seed_provenance(input_class: type[object]) -> None:
    """Register canonical Input dispatch once, during the Input module import."""
    global _INPUT_PROVENANCE
    if _INPUT_PROVENANCE is None:
        _INPUT_PROVENANCE = (
            input_class,
            _capture_static_dispatch(
                input_class,
                (
                    "__init__",
                    _INSTANCE_DICT_NAME,
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "cast",
                    "normalize",
                    "resolve_possible_values",
                    "validate_bounds",
                    "validate_with_callable",
                ),
            ),
        )


def _register_calculation_interface_seed_provenance(
    interface_class: type[object],
) -> None:
    """Register canonical calculation interface and metaclass dispatch once."""
    global _CALCULATION_INTERFACE_PROVENANCE
    if _CALCULATION_INTERFACE_PROVENANCE is None:
        interface_metaclass = type(interface_class)
        _CALCULATION_INTERFACE_PROVENANCE = (
            interface_class,
            interface_metaclass,
            _capture_static_dispatch(
                interface_class,
                (
                    "__init__",
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "parse_input_fields_to_identification",
                    "_process_input_field",
                    "format_identification",
                    "get_attributes",
                    "get_field_type",
                    "handle_interface",
                ),
            ),
            _capture_static_dispatch(
                interface_metaclass,
                ("__call__", "__getattribute__", "__getattr__", "__setattr__"),
            ),
        )


def _register_orm_interface_seed_provenance(
    interface_class: type[object],
) -> None:
    """Register canonical ORM-interface dispatch once at module startup."""
    global _ORM_INTERFACE_PROVENANCE
    if _ORM_INTERFACE_PROVENANCE is None:
        interface_metaclass = type(interface_class)
        _ORM_INTERFACE_PROVENANCE = (
            interface_class,
            interface_metaclass,
            _capture_static_dispatch(
                interface_class,
                (
                    "__init__",
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "_from_trusted_orm_instance",
                    "normalize_search_date",
                    "parse_input_fields_to_identification",
                    "_process_input_field",
                    "format_identification",
                    "get_data",
                    "require_capability",
                    "handle_interface",
                ),
            ),
            _capture_static_dispatch(
                interface_metaclass,
                ("__call__", "__getattribute__", "__getattr__", "__setattr__"),
            ),
        )


def _calculation_interface_seed_provenance() -> (
    tuple[type[object], type[object], _StaticDispatchSnapshot, _StaticDispatchSnapshot]
    | None
):
    """Return live provenance after calculation-interface module startup."""
    return _CALCULATION_INTERFACE_PROVENANCE


def _register_calculation_capability_seed_provenance(
    lifecycle_class: type[object],
    read_class: type[object],
) -> None:
    """Register calculation capability implementations at module load."""
    global _CALCULATION_CAPABILITY_PROVENANCE
    if _CALCULATION_CAPABILITY_PROVENANCE is None:
        _CALCULATION_CAPABILITY_PROVENANCE = (
            lifecycle_class,
            _capture_static_dispatch(
                lifecycle_class,
                (
                    "__init__",
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "pre_create",
                    "post_create",
                ),
            ),
            read_class,
            _capture_static_dispatch(
                read_class,
                (
                    "__init__",
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "get_data",
                    "get_attributes",
                    "get_field_type",
                ),
            ),
        )


def _register_general_manager_seed_provenance(
    manager_class: type[object],
) -> None:
    """Register canonical GeneralManager dispatch at module load."""
    global _GENERAL_MANAGER_PROVENANCE
    if _GENERAL_MANAGER_PROVENANCE is None:
        _GENERAL_MANAGER_PROVENANCE = (
            manager_class,
            _capture_static_dispatch(
                manager_class,
                (
                    "__new__",
                    "__init__",
                    _INSTANCE_DICT_NAME,
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "identification",
                    "_track_identification_dependency",
                    "_track_identification_dependency_active",
                    "_track_own_identification_dependency_active",
                    "_reload_interface_state",
                    "_invalidate_manager_state",
                    "_ensure_manager_state_valid",
                    "_ensure_manager_not_invalidated",
                ),
            ),
        )


def _register_general_manager_meta_seed_provenance(
    manager_metaclass: type[object],
) -> None:
    """Register canonical GeneralManagerMeta dispatch at module load."""
    global _GENERAL_MANAGER_META_PROVENANCE
    if _GENERAL_MANAGER_META_PROVENANCE is None:
        _GENERAL_MANAGER_META_PROVENANCE = (
            manager_metaclass,
            _capture_static_dispatch(
                manager_metaclass,
                (
                    "__call__",
                    "__getattribute__",
                    "__getattr__",
                    "__setattr__",
                    "__new__",
                ),
            ),
        )


def _dict_has_identity_keys(
    value: object,
    expected_keys: tuple[str, ...],
) -> bool:
    """Compare exact-dict keys without invoking user-defined equality hooks."""
    return (
        type(value) is dict
        and len(value) == len(expected_keys)
        and all(
            type(current_key) is str and current_key is expected_key
            for current_key, expected_key in zip(value, expected_keys, strict=True)
        )
    )


def _mapping_value_by_identity(
    mapping: Mapping[str, object],
    expected_key: str,
    default: object = None,
) -> object:
    """Read an exact-string mapping entry without equality or hashing."""
    for key, value in mapping.items():
        if type(key) is not str:
            return default
        if key is expected_key:
            return value
    return default


def _mro_state_access_is_canonical(
    cls: type[object],
    canonical_owner: type[object],
    protected_names: tuple[str, ...],
) -> bool:
    """Reject state descriptors introduced before a canonical MRO owner."""
    mro = type.__getattribute__(cls, "__mro__")
    if type(mro) is not tuple:
        return False
    for candidate in mro:
        if candidate is canonical_owner:
            return True
        class_state = type.__getattribute__(candidate, "__dict__")
        if type(class_state) is not MappingProxyType:
            return False
        for key in class_state:
            if type(key) is not str:
                return False
            if any(key == protected_name for protected_name in protected_names):
                return False
    return False


def _manager_candidate_present(
    interface: object,
    identification: object,
) -> bool:
    """Cheaply detect whether canonical Input state contains a manager value."""
    input_provenance = _INPUT_PROVENANCE
    if type(identification) is not dict or input_provenance is None:
        return False
    input_class, input_dispatch = input_provenance
    expected_dict_snapshot = next(
        expected for name, expected in input_dispatch if name is _INSTANCE_DICT_NAME
    )
    if not _static_descriptor_matches(
        _static_descriptor(input_class, _INSTANCE_DICT_NAME),
        expected_dict_snapshot,
    ):
        return False
    interface_class = type(interface)
    interface_class_state = type.__getattribute__(interface_class, "__dict__")
    if type(interface_class_state) is not MappingProxyType:
        return False
    input_fields = _mapping_value_by_identity(interface_class_state, "input_fields")
    if type(input_fields) is not dict or len(input_fields) != len(identification):
        return False
    for (field_name, input_field), identification_name in zip(
        input_fields.items(), identification, strict=True
    ):
        if (
            type(field_name) is not str
            or field_name is not identification_name
            or type(input_field) is not input_class
        ):
            return False
        input_state = object.__getattribute__(input_field, "__dict__")
        if type(input_state) is not dict:
            return False
        is_manager = _mapping_value_by_identity(input_state, "is_manager")
        if type(is_manager) is not bool:
            return False
        if is_manager and dict.__getitem__(identification, field_name) is not None:
            return True
    return False


def _calculation_manager_input_seed_plan(
    input_fields: Mapping[str, object],
) -> object:
    """Return the private seed token when canonical manager inputs exist."""
    input_provenance = _INPUT_PROVENANCE
    if input_provenance is None:
        return False
    input_class = input_provenance[0]
    for field_name, input_field in input_fields.items():
        if type(field_name) is not str or type(input_field) is not input_class:
            continue
        input_state = object.__getattribute__(input_field, _INSTANCE_DICT_NAME)
        if type(input_state) is not dict:
            continue
        if _mapping_value_by_identity(input_state, "is_manager") is True:
            return _MANAGER_INPUT_SEED_PLAN_TOKEN
    return False


def _interface_uses_manager_input_seed_plan(interface: object) -> bool:
    """Read the lifecycle-owned plan marker without instance dispatch."""
    interface_state = type.__getattribute__(type(interface), _INSTANCE_DICT_NAME)
    return (
        type(interface_state) is MappingProxyType
        and _mapping_value_by_identity(
            interface_state,
            _MANAGER_INPUT_SEED_PLAN_NAME,
        )
        is _MANAGER_INPUT_SEED_PLAN_TOKEN
    )


def _canonical_manager_class_state(manager_class: type[object]) -> bool:
    """Validate the hook-free manager machinery required by hydration seeding."""
    manager_provenance = _GENERAL_MANAGER_PROVENANCE
    metaclass_provenance = _GENERAL_MANAGER_META_PROVENANCE
    if manager_provenance is None or metaclass_provenance is None:
        return False
    canonical_manager, manager_dispatch = manager_provenance
    canonical_metaclass, metaclass_dispatch = metaclass_provenance
    if type(manager_class) is not canonical_metaclass:
        return False
    return (
        _matches_static_dispatch(canonical_manager, manager_dispatch)
        and _matches_static_dispatch(manager_class, manager_dispatch)
        and _matches_static_dispatch(canonical_metaclass, metaclass_dispatch)
        and _matches_static_dispatch(type(manager_class), metaclass_dispatch)
    )


def _canonical_database_nested_interface_state(
    manager_class: type[object],
    interface: object,
    identification: dict[str, object],
) -> bool:
    """Validate one exact canonical ORM wrapper without invoking hooks."""
    provenance = _ORM_INTERFACE_PROVENANCE
    if provenance is None:
        return False
    (
        canonical_interface,
        canonical_metaclass,
        interface_dispatch,
        metaclass_dispatch,
    ) = provenance
    interface_class = type(interface)
    if type(interface_class) is not canonical_metaclass:
        return False
    mro = type.__getattribute__(interface_class, "__mro__")
    if type(mro) is not tuple or not any(
        candidate is canonical_interface for candidate in mro
    ):
        return False
    if not (
        _matches_static_dispatch(canonical_interface, interface_dispatch)
        and _matches_static_dispatch(interface_class, interface_dispatch)
        and _matches_static_dispatch(canonical_metaclass, metaclass_dispatch)
        and _matches_static_dispatch(type(interface_class), metaclass_dispatch)
    ):
        return False
    if not _mro_state_access_is_canonical(
        interface_class,
        canonical_interface,
        (
            _INSTANCE_DICT_NAME,
            "identification",
            "pk",
            "_search_date",
            "_instance",
        ),
    ):
        return False
    state = object.__getattribute__(interface, _INSTANCE_DICT_NAME)
    expected_keys = ("identification", "pk", "_search_date", "_instance")
    if not _dict_has_identity_keys(state, expected_keys):
        return False
    if dict.__getitem__(state, "identification") is not identification:
        return False
    if not _dict_has_identity_keys(identification, ("id",)):
        return False
    primary_key = dict.__getitem__(identification, "id")
    if dict.__getitem__(state, "pk") is not primary_key:
        return False
    search_date = dict.__getitem__(state, "_search_date")
    if search_date is not None and type(search_date) is not datetime:
        return False
    model_class = _static_descriptor(interface_class, "_model")
    if not isinstance(model_class, type):
        return False
    model_mro = type.__getattribute__(model_class, "__mro__")
    if type(model_mro) is not tuple or not any(
        candidate is Model for candidate in model_mro
    ):
        return False
    instance = dict.__getitem__(state, "_instance")
    return (
        type(instance) is model_class
        and type.__getattribute__(manager_class, "Interface") is interface_class
    )


def _canonical_nested_manager(
    value: object,
    manager_class: type[object],
) -> bool:
    """Return whether a parsed manager wrapper has untouched canonical state."""
    manager_provenance = _GENERAL_MANAGER_PROVENANCE
    interface_provenance = _INTERFACE_BASE_PROVENANCE
    if manager_provenance is None or interface_provenance is None:
        return False
    canonical_manager = manager_provenance[0]
    canonical_interface = interface_provenance[0]
    if type(value) is not manager_class:
        return False
    if not _canonical_manager_class_state(manager_class):
        return False
    if not _mro_state_access_is_canonical(
        manager_class,
        canonical_manager,
        (
            _INSTANCE_DICT_NAME,
            "_interface",
            "_GeneralManager__id",
            "_attribute_value_cache",
            "_identification_dependency_cache",
            "_manager_state_valid",
            "_manager_state_reason",
        ),
    ):
        return False

    manager_state = object.__getattribute__(value, _INSTANCE_DICT_NAME)
    expected_manager_keys = (
        "_interface",
        "_GeneralManager__id",
        "_attribute_value_cache",
        "_identification_dependency_cache",
        "_manager_state_valid",
        "_manager_state_reason",
    )
    if not _dict_has_identity_keys(manager_state, expected_manager_keys):
        return False
    identification = manager_state["_GeneralManager__id"]
    attribute_cache = manager_state["_attribute_value_cache"]
    if (
        type(identification) is not dict
        or type(attribute_cache) is not dict
        or attribute_cache
        or manager_state["_identification_dependency_cache"] is not None
        or manager_state["_manager_state_valid"] is not True
        or manager_state["_manager_state_reason"] is not None
    ):
        return False

    interface = manager_state["_interface"]
    interface_class = type(interface)
    if type.__getattribute__(manager_class, "Interface") is not interface_class:
        return False
    if not _mro_state_access_is_canonical(
        interface_class,
        canonical_interface,
        (_INSTANCE_DICT_NAME, "_resolved_input_values", "identification"),
    ):
        return False
    interface_state = object.__getattribute__(interface, _INSTANCE_DICT_NAME)
    if (
        _dict_has_identity_keys(interface_state, ("identification",))
        and interface_state["identification"] is identification
    ):
        return True
    return _canonical_database_nested_interface_state(
        manager_class,
        interface,
        identification,
    )


def _seed_calculation_resolved_manager_values(
    interface: InterfaceBase,
    identification: dict[str, object],
) -> None:
    """Seed manager-valued calculation inputs after canonical parsing.

    This private fast path is deliberately fail-closed. It writes only when the
    complete interface, manager, capability, field, accessor, descriptor, and
    parsed-wrapper graph still uses the framework's canonical implementations.
    """
    if not _manager_candidate_present(interface, identification):
        return
    try:
        from general_manager.interface.capabilities.calculation.lifecycle import (
            _is_canonical_calculation_input_accessor,
        )
        from general_manager.manager.meta import (
            _is_canonical_manager_attribute_descriptor,
        )

        interface_provenance = _INTERFACE_BASE_PROVENANCE
        input_provenance = _INPUT_PROVENANCE
        calculation_provenance = _CALCULATION_INTERFACE_PROVENANCE
        capability_provenance = _CALCULATION_CAPABILITY_PROVENANCE
        if (
            interface_provenance is None
            or input_provenance is None
            or calculation_provenance is None
            or capability_provenance is None
        ):
            return
        canonical_interface, interface_dispatch = interface_provenance
        input_class, input_dispatch = input_provenance
        (
            calculation_interface,
            canonical_interface_metaclass,
            calculation_dispatch,
            interface_metaclass_dispatch,
        ) = calculation_provenance
        (
            lifecycle_class,
            lifecycle_dispatch,
            read_class,
            read_dispatch,
        ) = capability_provenance
        if (
            not _matches_static_dispatch(canonical_interface, interface_dispatch)
            or not _matches_static_dispatch(input_class, input_dispatch)
            or not _matches_static_dispatch(calculation_interface, calculation_dispatch)
            or not _matches_static_dispatch(
                canonical_interface_metaclass, interface_metaclass_dispatch
            )
            or not _matches_static_dispatch(lifecycle_class, lifecycle_dispatch)
            or not _matches_static_dispatch(read_class, read_dispatch)
        ):
            return

        interface_class = type(interface)
        interface_metaclass = type(interface_class)
        if interface_metaclass is not canonical_interface_metaclass:
            return
        if not _matches_static_dispatch(
            interface_metaclass, interface_metaclass_dispatch
        ):
            return
        interface_mro = type.__getattribute__(interface_class, "__mro__")
        if type(interface_mro) is not tuple or not any(
            candidate is calculation_interface for candidate in interface_mro
        ):
            return
        if type(identification) is not dict:
            return
        if not _mro_state_access_is_canonical(
            interface_class,
            canonical_interface,
            (_INSTANCE_DICT_NAME, "_resolved_input_values", "identification"),
        ):
            return
        interface_state = object.__getattribute__(interface, _INSTANCE_DICT_NAME)
        if type(interface_state) is not dict or interface_state:
            return
        if not _matches_static_dispatch(interface_class, calculation_dispatch):
            return

        interface_class_state = type.__getattribute__(interface_class, "__dict__")
        input_fields = interface_class_state.get("input_fields")
        handlers = interface_class_state.get("_capability_handlers")
        parent_class = interface_class_state.get("_parent_class")
        if (
            type(input_fields) is not dict
            or type(handlers) is not dict
            or any(type(handler_name) is not str for handler_name in handlers)
            or not _canonical_manager_class_state(parent_class)
            or type.__getattribute__(parent_class, "Interface") is not interface_class
        ):
            return

        lifecycle_handler = handlers.get("calculation_lifecycle")
        read_handler = handlers.get("read")
        if (
            type(lifecycle_handler) is not lifecycle_class
            or type(read_handler) is not read_class
            or vars(lifecycle_handler)
            or vars(read_handler)
        ):
            return
        if not (
            _matches_static_dispatch(type(lifecycle_handler), lifecycle_dispatch)
            and _matches_static_dispatch(type(read_handler), read_dispatch)
        ):
            return

        parent_state = type.__getattribute__(parent_class, "__dict__")
        parent_attributes = parent_state.get("_attributes")
        input_names = tuple(input_fields)
        if (
            type(parent_attributes) is not dict
            or len(identification) != len(input_names)
            or len(parent_attributes) != len(input_names)
            or any(type(name) is not str for name in identification)
            or any(type(name) is not str for name in parent_attributes)
            or any(
                current is not expected
                for current, expected in zip(identification, input_names, strict=True)
            )
            or any(
                current is not expected
                for current, expected in zip(
                    parent_attributes, input_names, strict=True
                )
            )
        ):
            return

        expected_input_state = (
            "type",
            "possible_values",
            "required",
            "min_value",
            "max_value",
            "validator",
            "normalizer",
            "is_manager",
            "depends_on",
        )
        resolved_manager_values: dict[str, object] = {}
        for field_name, input_field in input_fields.items():
            if type(field_name) is not str or type(input_field) is not input_class:
                return
            input_state = object.__getattribute__(input_field, "__dict__")
            if (
                not _dict_has_identity_keys(input_state, expected_input_state)
                or type(input_state["depends_on"]) is not list
                or type(input_state["is_manager"]) is not bool
            ):
                return

            accessor = parent_attributes.get(field_name)
            descriptor = inspect.getattr_static(parent_class, field_name)
            if not _is_canonical_calculation_input_accessor(
                accessor,
                cast("type[CalculationInterface]", interface_class),
                field_name,
            ) or not _is_canonical_manager_attribute_descriptor(
                descriptor, parent_class, field_name
            ):
                return

            if input_state["is_manager"] is not True:
                continue
            manager_type = input_state["type"]
            if not _canonical_manager_class_state(manager_type):
                return
            value = identification.get(field_name)
            if value is None:
                continue
            if not _canonical_nested_manager(value, manager_type):
                return
            resolved_manager_values[field_name] = value

        if resolved_manager_values:
            dict.__setitem__(
                interface_state,
                _RESOLVED_INPUT_VALUES_CACHE_NAME,
                resolved_manager_values,
            )
            dict.__setitem__(
                interface_state,
                _SEEDED_INPUT_VALUES_CACHE_NAME,
                dict(resolved_manager_values),
            )
            dict.__setitem__(
                interface_state,
                _LAZY_INPUT_VALUES_CACHE_NAME,
                set(),
            )
            if not _register_seeded_interface_origin(
                interface,
                resolved_manager_values,
            ):
                dict.pop(interface_state, _RESOLVED_INPUT_VALUES_CACHE_NAME, None)
                dict.pop(interface_state, _SEEDED_INPUT_VALUES_CACHE_NAME, None)
                dict.pop(interface_state, _LAZY_INPUT_VALUES_CACHE_NAME, None)
    except (AttributeError, KeyError, TypeError):
        return


class AttributeTypedDict(TypedDict):
    """Describe metadata captured for each interface attribute."""

    type: type
    graphql_scalar: NotRequired[str]
    relation_kind: NotRequired[str]
    filter_lookup: NotRequired[str]
    orm_field_kind: NotRequired[Literal["file", "image"]]
    file_clearable: NotRequired[bool]
    default: object
    is_required: bool
    is_editable: bool
    is_derived: bool


class UnexpectedInputArgumentsError(TypeError):
    """Raised when parseInputFields receives keyword arguments not defined by the interface."""

    def __init__(self, extra_args: Iterable[str]) -> None:
        """
        Initialize the exception with a message listing unexpected input argument names.

        Parameters:
            extra_args (Iterable[str]): Names of the unexpected keyword arguments to include in the error message.
        """
        extras = ", ".join(extra_args)
        super().__init__(f"Unexpected arguments: {extras}.")


class MissingInputArgumentsError(TypeError):
    """Raised when required interface inputs are not supplied."""

    def __init__(self, missing_args: Iterable[str]) -> None:
        """
        Initialize the exception for missing required input arguments.

        Parameters:
            missing_args (Iterable[str]): Names of required input arguments that were not provided; these will be joined into the exception message.
        """
        missing = ", ".join(missing_args)
        super().__init__(f"Missing required arguments: {missing}.")


class CircularInputDependencyError(ValueError):
    """Raised when input fields declare circular dependencies."""

    def __init__(self, unresolved: Iterable[str]) -> None:
        """
        Initialize the CircularInputDependencyError with the names of inputs involved in the cycle.

        Parameters:
            unresolved (Iterable[str]): Iterable of input names that form the detected circular dependency.
        """
        names = ", ".join(unresolved)
        super().__init__(f"Circular dependency detected among inputs: {names}.")


class InvalidInputTypeError(TypeError):
    """Raised when an input value does not match its declared type."""

    def __init__(self, name: str, provided: type, expected: type) -> None:
        """
        Initialize the InvalidInputTypeError with a message describing a type mismatch for a named input.

        Parameters:
            name (str): The name of the input field with the invalid type.
            provided (type): The actual type that was provided.
            expected (type): The type that was expected.
        """
        super().__init__(f"Invalid type for {name}: {provided}, expected: {expected}.")


class InvalidPossibleValuesTypeError(TypeError):
    """Raised when an input's possible_values configuration is not callable or iterable."""

    def __init__(self, name: str) -> None:
        """
        Exception raised when an input's `possible_values` configuration is neither callable nor iterable.

        Parameters:
            name (str): The input field name whose `possible_values` is invalid; included in the exception message.
        """
        super().__init__(f"Invalid type for possible_values of input {name}.")


class InvalidInputValueError(ValueError):
    """Raised when a provided input value is not within the allowed set."""

    def __init__(self, name: str, value: object, allowed: Iterable[object]) -> None:
        """
        Initialize the exception with a message describing an invalid input value for a specific field.

        Parameters:
            name (str): The name of the input field that received the invalid value.
            value (object): The value that was provided and deemed invalid.
            allowed (Iterable[object]): An iterable of permitted values for the field; used to include allowed options in the exception message.
        """
        super().__init__(
            f"Invalid value for {name}: {value}, allowed: {list(allowed)}."
        )


class InvalidInputConstraintError(ValueError):
    """Raised when a provided input value violates a declared constraint."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"Invalid value for {name}: {detail}.")


_VALIDATE_POSSIBLE_VALUES_CACHE: bool | None = None


@receiver(setting_changed)
def _clear_possible_values_validation_cache(
    *,
    setting: str,
    **_kwargs: object,
) -> None:
    if setting in {
        "DEBUG",
        "GENERAL_MANAGER",
        "GENERAL_MANAGER_VALIDATE_INPUT_VALUES",
        "VALIDATE_INPUT_VALUES",
    }:
        global _VALIDATE_POSSIBLE_VALUES_CACHE
        _VALIDATE_POSSIBLE_VALUES_CACHE = None


def _should_validate_possible_values() -> bool:
    """Return whether ``possible_values`` membership should be enforced."""
    global _VALIDATE_POSSIBLE_VALUES_CACHE
    if _VALIDATE_POSSIBLE_VALUES_CACHE is not None:
        return _VALIDATE_POSSIBLE_VALUES_CACHE

    value = get_setting("VALIDATE_INPUT_VALUES")
    if value is None:
        result = bool(settings.DEBUG)
        _VALIDATE_POSSIBLE_VALUES_CACHE = result
        return result
    if isinstance(value, bool):
        _VALIDATE_POSSIBLE_VALUES_CACHE = value
        return value
    if isinstance(value, str):
        result = value.strip().lower() in {"true", "1", "yes", "on"}
        _VALIDATE_POSSIBLE_VALUES_CACHE = result
        return result
    if isinstance(value, int):
        result = value != 0
        _VALIDATE_POSSIBLE_VALUES_CACHE = result
        return result
    result = bool(settings.DEBUG)
    _VALIDATE_POSSIBLE_VALUES_CACHE = result
    return result


@dataclass(frozen=True, slots=True)
class _InputParsingPlan:
    names: tuple[str, ...]
    name_set: frozenset[str]
    field_by_name: Mapping[str, "Input[type[object]]"]
    alias_to_name: Mapping[str, str]
    required_names: frozenset[str]
    optional_names: frozenset[str]
    dependency_items: tuple[tuple[str, tuple[str, ...]], ...]
    has_dependencies: bool
    field_state: tuple[tuple[str, int, bool, tuple[str, ...]], ...]


class _TrustedEnumerationEvidence(Protocol):
    """Authorize one pre-enumerated value and track its membership dependency."""

    def authorizes(
        self,
        input_field: "Input[type[object]]",
        value: object,
        identification: Mapping[str, object],
    ) -> bool: ...

    def track_membership_dependency(self) -> None: ...


@dataclass(slots=True)
class _TrustedEnumerationLease:
    """Shared revocation state for contexts that inherit a trusted scope."""

    active: bool = True


@dataclass(frozen=True, slots=True)
class _TrustedEnumerationScope:
    """Private evidence available while validating one exact interface class."""

    interface_class: type[object]
    evidence_by_name: Mapping[str, _TrustedEnumerationEvidence]
    lease: _TrustedEnumerationLease


_TRUSTED_ENUMERATION_SCOPE: ContextVar[_TrustedEnumerationScope | None] = ContextVar(
    "general_manager_trusted_enumeration_scope",
    default=None,
)


@contextmanager
def _trusted_enumeration_scope(
    interface_class: type[object],
    evidence_by_name: Mapping[str, _TrustedEnumerationEvidence],
) -> Iterator[None]:
    """Temporarily install trusted enumeration evidence for an interface class."""
    lease = _TrustedEnumerationLease()
    token = _TRUSTED_ENUMERATION_SCOPE.set(
        _TrustedEnumerationScope(interface_class, evidence_by_name, lease)
    )
    try:
        yield
    finally:
        lease.active = False
        _TRUSTED_ENUMERATION_SCOPE.reset(token)


def _trusted_possible_values_membership_authorized(
    interface: object,
    name: str,
    input_field: "Input[type[object]]",
    value: object,
    identification: Mapping[str, object],
) -> bool:
    """Return whether exact scoped evidence authorizes skipping membership."""
    scope = _TRUSTED_ENUMERATION_SCOPE.get()
    if (
        scope is None
        or not scope.lease.active
        or scope.interface_class is not type(interface)
    ):
        return False
    evidence = scope.evidence_by_name.get(name)
    if evidence is None or not evidence.authorizes(
        input_field,
        value,
        identification,
    ):
        return False
    evidence.track_membership_dependency()
    return True


class InterfaceBase(ABC):
    """Common base API for interfaces backing GeneralManager classes."""

    _parent_class: ClassVar[Type["GeneralManager"]]
    _interface_type: ClassVar[str]
    input_fields: ClassVar[dict[str, "Input[type[object]]"]]
    _input_parsing_plan: ClassVar[_InputParsingPlan | None] = None
    _input_dependency_order: ClassVar[tuple[str, ...] | None] = None
    lifecycle_capability_name: ClassVar[CapabilityName | None] = None
    _capabilities: ClassVar[frozenset[CapabilityName]] = frozenset()
    _capability_selection: ClassVar["CapabilitySelection | None"] = None
    _capability_handlers: ClassVar[dict[CapabilityName, "Capability"]] = {}
    capability_overrides: ClassVar[dict[CapabilityName, CapabilityOverride]] = {}
    configured_capabilities: ClassVar[tuple[CapabilityConfigEntry, ...]] = tuple()
    _configured_capabilities_applied: ClassVar[bool] = False
    _automatic_capability_builder: ClassVar["ManifestCapabilityBuilder | None"] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        """
        Initialize capability-related class state for newly created subclasses.

        This method resets per-subclass capability registries and configuration to a clean default, merges configured capability overrides into the class's capability_overrides mapping, and clears the flag that marks configured capabilities as applied. Keyword arguments are forwarded to the superclass implementation.
        """
        super().__init_subclass__(**kwargs)
        cls._input_parsing_plan = None
        cls._input_dependency_order = None
        cls._capabilities = frozenset()
        cls._capability_selection = None
        cls._capability_handlers = {}
        cls.capability_overrides = dict(getattr(cls, "capability_overrides", {}))
        cls.configured_capabilities = tuple(
            getattr(cls, "configured_capabilities", tuple()),
        )
        configured_overrides = cls._build_configured_capability_overrides()
        for name, override in configured_overrides.items():
            cls.capability_overrides.setdefault(name, override)
        cls._configured_capabilities_applied = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        """
        Initialize the interface using the provided identification inputs.

        Positional arguments are mapped to the interface's declared input fields by position; keyword arguments are matched by name. Inputs are validated and normalized according to the interface's input field definitions and the resulting normalized identification is stored on the instance as `self.identification`.

        Parameters:
            *args: Positional identification values corresponding to the interface's input field order.
            **kwargs: Named identification values matching the interface's input field names.
        """
        identification = self.parse_input_fields_to_identification(*args, **kwargs)
        if _interface_uses_manager_input_seed_plan(self):
            _seed_calculation_resolved_manager_values(self, identification)
        if len(identification) == 1:
            value = next(iter(identification.values()))
            if value.__class__ in _FORMATLESS_IDENTIFICATION_VALUE_TYPES:
                self.identification = identification
                return
        self.identification = self.format_identification(identification)

    @classmethod
    def set_capability_selection(cls, selection: "CapabilitySelection") -> None:
        """
        Attach a resolved capability selection to the interface and update its active capability names.

        Parameters:
            selection (CapabilitySelection): The resolved capability selection whose `all` set will become the interface's active capability names.
        """
        cls._capability_selection = selection
        cls._capabilities = selection.all

    @classmethod
    def get_capabilities(cls) -> frozenset[CapabilityName]:
        """
        Get the capability names attached to this interface class.

        Returns:
            frozenset[CapabilityName]: A frozenset of capability names registered on the interface class.
        """
        cls._ensure_capabilities_initialized()
        return cls._capabilities

    @classmethod
    def get_capability_handler(cls, name: CapabilityName) -> "Capability | None":
        """
        Retrieve the capability instance associated with the given capability name.

        Parameters:
            name (CapabilityName): The capability identifier to look up.

        Returns:
            Capability | None: The capability handler registered for `name`, or `None` if no handler is bound.
        """
        if (
            cls._capability_selection is not None
            and cls._configured_capabilities_applied
        ):
            return cls._capability_handlers.get(name)
        cls._ensure_capabilities_initialized()
        return cls._capability_handlers.get(name)

    @classmethod
    def iter_capability_configs(cls) -> Iterable[InterfaceCapabilityConfig]:
        """
        Iterate configured capability entries declared on the interface.

        Returns:
            Iterable[InterfaceCapabilityConfig]: An iterable of capability configuration entries registered on the interface.
        """
        return iter_capability_entries(cls.configured_capabilities)

    @classmethod
    def require_capability(
        cls,
        name: CapabilityName,
        *,
        expected_type: type["Capability"] | None = None,
    ) -> "Capability":
        """
        Retrieve the configured capability handler for the interface by name.

        Parameters:
            name (CapabilityName): The capability identifier to look up.
            expected_type (type[Capability] | None): If provided, require the returned handler to be an instance of this type.

        Returns:
            Capability: The capability handler instance corresponding to `name`.

        Raises:
            NotImplementedError: If the interface has no capability configured under `name`.
            TypeError: If `expected_type` is provided and the handler is not an instance of that type.
        """
        handler = cls.get_capability_handler(name)
        if handler is None:
            raise NotImplementedError(
                f"{cls.__name__} does not have the '{name}' capability configured."
            )
        if expected_type is not None and not isinstance(handler, expected_type):
            message = (
                f"Capability '{name}' on {cls.__name__} must be an instance of "
                f"{expected_type.__name__}."
            )
            raise TypeError(message)
        return handler

    def _require_capability(
        self,
        name: CapabilityName,
        *,
        expected_type: type["Capability"] | None = None,
    ) -> "Capability":
        """
        Retrieve the capability handler with the given name from this interface's class, enforcing an expected handler type if provided.

        Parameters:
                name (CapabilityName): The capability name to retrieve.
                expected_type (type[Capability] | None): If provided, the returned handler must be an instance of this type.

        Returns:
                Capability: The capability handler associated with `name`.

        Raises:
                NotImplementedError: If the named capability is not available.
                TypeError: If the found capability is not an instance of `expected_type`.
        """
        return self.__class__.require_capability(
            name,
            expected_type=expected_type,
        )

    @classmethod
    def capability_selection(cls) -> "CapabilitySelection | None":
        """
        Return the resolved capability selection associated with this interface.

        @returns
            `CapabilitySelection` if a selection has been set, `None` otherwise.
        """
        cls._ensure_capabilities_initialized()
        return cls._capability_selection

    @classmethod
    def _lifecycle_capability(cls) -> "Capability | None":
        """
        Retrieve the lifecycle capability handler attached to the interface, if one is configured.

        Returns:
            Capability | None: The `Capability` instance identified by the class's `lifecycle_capability_name`, or `None` if no lifecycle capability is configured.
        """
        name = getattr(cls, "lifecycle_capability_name", None)
        if not name:
            return None
        return cls.get_capability_handler(name)

    @classmethod
    def _ensure_capabilities_initialized(cls) -> None:
        """
        Ensure the interface's capability registry is initialized and configured for this class.

        If no capability selection has been attached, construct or reuse an automatic ManifestCapabilityBuilder to build the class's capabilities. Afterward, instantiate and bind any configured capability overrides so capability handlers, startup hooks, and system checks are registered.
        """
        if cls._capability_selection is None:
            from general_manager.interface.manifests import ManifestCapabilityBuilder

            builder = cls._automatic_capability_builder
            if builder is None:
                builder = ManifestCapabilityBuilder()
                cls._automatic_capability_builder = builder
            builder.build(cls)
        cls._apply_configured_capabilities()

    @classmethod
    def _apply_configured_capabilities(cls) -> None:
        """
        Apply and bind the interface's configured capability handlers exactly once.

        Instantiates each entry returned by iter_capability_configs and binds the resulting capability handlers to the class. This method is idempotent: if configured capabilities have already been applied it does nothing. It also sets the internal flag that marks the configured capabilities as applied.
        """
        if cls._configured_capabilities_applied:
            return
        configs = tuple(cls.iter_capability_configs())
        if not configs:
            cls._configured_capabilities_applied = True
            return
        for config in configs:
            handler = cls._instantiate_configured_capability(config)
            cls._bind_capability_handler(handler)
        cls._configured_capabilities_applied = True

    @classmethod
    def _build_configured_capability_overrides(
        cls,
    ) -> dict[CapabilityName, CapabilityOverride]:
        """
        Builds a mapping of configured capability names to capability handlers or factory callables.

        Instantiates an entry for each configured capability: if the configured entry supplies options, the value is a factory callable (created via _make_capability_factory) that will produce the capability when invoked; otherwise the value is the capability handler class itself. Configured handlers that do not expose a `name` attribute are skipped.

        Returns:
            overrides (dict[CapabilityName, CapabilityOverride]): Mapping from capability name to either a capability handler class or a factory callable that produces a capability instance.
        """
        overrides: dict[CapabilityName, CapabilityOverride] = {}
        for config in iter_capability_entries(cls.configured_capabilities):
            handler_cls = config.handler
            name = getattr(handler_cls, "name", None)
            if name is None:
                continue
            if config.options:
                overrides[name] = cls._make_capability_factory(
                    handler_cls,
                    dict(config.options),
                )
            else:
                overrides[name] = handler_cls
        return overrides

    @staticmethod
    def _make_capability_factory(
        handler_cls: type[Capability],
        options: dict[str, object],
    ) -> CapabilityOverride:
        """
        Create a factory callable that produces instances of a capability handler using the given options.

        Parameters:
            handler_cls (type[Capability]): The Capability subclass to instantiate.
            options: Keyword arguments to supply to the handler constructor.

        Returns:
            CapabilityOverride: A zero-argument callable that, when invoked, returns a new instance of `handler_cls` constructed with `options`.
        """

        def _factory(
            handler_cls: type[Capability] = handler_cls,
            options: dict[str, object] = options,
        ) -> Capability:
            return handler_cls(**dict(options))

        return _factory

    @classmethod
    def _instantiate_configured_capability(
        cls, config: InterfaceCapabilityConfig
    ) -> Capability:
        """
        Instantiate a configured capability and verify it implements the Capability protocol.

        Parameters:
            config (InterfaceCapabilityConfig): Configuration entry whose `instantiate()` method produces a capability handler.

        Returns:
            Capability: The instantiated capability handler.

        Raises:
            TypeError: If the instantiated handler does not implement the expected Capability protocol (missing a `setup` method).
        """
        handler = config.instantiate()
        if not hasattr(handler, "setup"):
            message = (
                "Configured capability "
                f"{handler!r} does not implement the Capability protocol."
            )
            raise TypeError(message)
        return handler

    @classmethod
    def _bind_capability_handler(cls, handler: Capability) -> None:
        """
        Bind a capability handler to the interface class, replacing any existing handler with the same name.

        Binds the provided capability to the interface by tearing down a previously bound handler with the same name (if any), setting up the new handler, adding its name to the class capability set, and registering any startup hooks and system checks exposed by the handler.

        Parameters:
            handler (Capability): Capability instance to bind. Must expose a `name` attribute.

        Raises:
            AttributeError: If `handler` does not have a `name` attribute.
        """
        name = getattr(handler, "name", None)
        if name is None:
            message = (
                f"Capability instance {handler!r} does not expose a name attribute."
            )
            raise AttributeError(message)
        existing = cls._capability_handlers.get(name)
        if existing is handler:
            return
        if existing is not None:
            existing.teardown(cls)
        handler.setup(cls)
        cls._capabilities = frozenset({*cls._capabilities, name})
        cls._register_startup_hooks(handler)
        cls._register_system_checks(handler)

    @classmethod
    def _register_startup_hooks(cls, handler: Capability) -> None:
        """
        Register startup hooks exposed by a capability handler on the interface class.

        If the handler provides a callable `get_startup_hooks(cls)` that returns hooks, that value is used; otherwise a `startup_hooks` attribute is used if present. If the handler provides a callable `get_startup_hook_dependency_resolver(cls)` or a `startup_hook_dependency_resolver` attribute, that resolver is supplied when registering each hook. Non-callable hook entries and empty or None hook collections are ignored.

        Parameters:
            cls: The interface class to receive the startup hooks.
            handler: A capability handler that may expose startup hooks and an optional dependency resolver.
        """
        hooks: Iterable[Callable[[], None]] | None = None
        dependency_resolver = None
        get_resolver = getattr(handler, "get_startup_hook_dependency_resolver", None)
        if callable(get_resolver):
            dependency_resolver = get_resolver(cls)
        elif hasattr(handler, "startup_hook_dependency_resolver"):
            dependency_resolver = handler.startup_hook_dependency_resolver

        get_hooks = getattr(handler, "get_startup_hooks", None)
        if callable(get_hooks):
            hooks = get_hooks(cls)
        elif hasattr(handler, "startup_hooks"):
            hooks = handler.startup_hooks
        if not hooks:
            return
        for hook in hooks:
            if callable(hook):
                register_startup_hook(
                    cls,
                    hook,
                    dependency_resolver=dependency_resolver,
                )

    @classmethod
    def _register_system_checks(cls, handler: Capability) -> None:
        """
        Register any system check callables exposed by a capability handler for the given interface class.

        The handler may expose checks via a callable `get_system_checks(cls)` or a `system_checks` iterable attribute.
        Each callable found is registered with register_system_check for the provided interface class; non-callable entries are ignored.

        Parameters:
            cls (type): The interface class to associate the system checks with.
            handler (Capability): Capability instance that may provide system checks.
        """
        hooks = None
        get_checks = getattr(handler, "get_system_checks", None)
        if callable(get_checks):
            hooks = get_checks(cls)
        elif hasattr(handler, "system_checks"):
            hooks = handler.system_checks
        if not hooks:
            return
        for hook in hooks:
            if callable(hook):
                register_system_check(cls, hook)

    @classmethod
    def _get_input_parsing_plan(cls) -> _InputParsingPlan:
        plan = cls._input_parsing_plan
        if plan is not None:
            if cls._input_parsing_plan_is_fresh(plan):
                return plan
            cls._input_dependency_order = None

        names = tuple(cls.input_fields.keys())
        field_items = tuple(cls.input_fields.items())
        name_set = frozenset(names)
        field_by_name = MappingProxyType(dict(field_items))
        alias_to_name = MappingProxyType({f"{name}_id": name for name in names})
        required_names = frozenset(
            name for name, input_field in field_items if input_field.required
        )
        optional_names = name_set - required_names
        dependency_items = tuple(
            (name, tuple(input_field.depends_on)) for name, input_field in field_items
        )
        field_state = tuple(
            (name, id(input_field), input_field.required, tuple(input_field.depends_on))
            for name, input_field in field_items
        )

        plan = _InputParsingPlan(
            names=names,
            name_set=name_set,
            field_by_name=field_by_name,
            alias_to_name=alias_to_name,
            required_names=required_names,
            optional_names=optional_names,
            dependency_items=dependency_items,
            has_dependencies=any(depends_on for _name, depends_on in dependency_items),
            field_state=field_state,
        )
        cls._input_parsing_plan = plan
        return plan

    @classmethod
    def _input_parsing_plan_is_fresh(cls, plan: _InputParsingPlan) -> bool:
        if len(plan.field_state) == 1:
            if len(cls.input_fields) != 1:
                return False
            name, input_id, required, depends_on = plan.field_state[0]
            input_field = cls.input_fields.get(name)
            if input_field is None:
                return False
            return (
                id(input_field) == input_id
                and input_field.required == required
                and tuple(input_field.depends_on) == depends_on
            )
        if len(cls.input_fields) != len(plan.field_state):
            return False
        if tuple(cls.input_fields) != plan.names:
            return False
        for name, input_id, required, depends_on in plan.field_state:
            input_field = cls.input_fields.get(name)
            if input_field is None:
                return False
            if id(input_field) != input_id:
                return False
            if input_field.required != required:
                return False
            if tuple(input_field.depends_on) != depends_on:
                return False
        return True

    @classmethod
    def _get_input_dependency_order(
        cls,
        plan: _InputParsingPlan,
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        ordered_names = cls._input_dependency_order
        if ordered_names is not None:
            return ordered_names, frozenset()

        ordered: list[str] = []
        processed: set[str] = set()
        while len(processed) < len(plan.names):
            progress_made = False
            for name, depends_on in plan.dependency_items:
                if name in processed:
                    continue
                if all(dependency in processed for dependency in depends_on):
                    ordered.append(name)
                    processed.add(name)
                    progress_made = True
            if not progress_made:
                break

        ordered_names = tuple(ordered)
        unresolved = frozenset(plan.name_set - processed)
        if not unresolved:
            cls._input_dependency_order = ordered_names
        return ordered_names, unresolved

    @classmethod
    def _single_input_run_cache_key(
        cls,
        name: str,
        input_field: "Input[type[object]]",
        value: object,
    ) -> tuple[object, ...] | None:
        from general_manager.manager.input import Input as ManagerInput

        if type(input_field) is not ManagerInput:
            return None
        if input_field.type not in _RUN_SCOPED_SCALAR_INPUT_TYPES:
            return None
        if type(value) is not input_field.type:
            return None
        if input_field.is_manager:
            return None
        if (
            input_field.possible_values is not None
            or input_field.min_value is not None
            or input_field.max_value is not None
            or input_field.validator is not None
            or input_field.normalizer is not None
        ):
            return None
        try:
            hash(value)
        except TypeError:
            return None
        return (_SINGLE_INPUT_VALUE_CACHE_PREFIX, cls, name, id(input_field), value)

    def parse_input_fields_to_identification(
        self,
        *args: object,
        **kwargs: object,
    ) -> dict[str, object]:
        """
        Convert positional and keyword inputs into a validated identification mapping for the interface's input fields.

        Positional values are assigned in `input_fields` declaration order before
        keyword validation. A keyword named `<field>_id` is accepted when
        `<field>` is a declared input; if both are supplied, the `_id` alias
        overwrites the canonical value. Positional overflow,
        duplicate positional-plus-keyword values, and unknown aliases surface as
        `UnexpectedInputArgumentsError` through the argument-normalization path.

        Parameters:
            *args: Positional arguments matched, in order, to the interface's defined input fields.
            **kwargs: Keyword arguments supplying input values by name.

        Returns:
            dict[str, object]: Mapping of input field names to their validated values.

        Raises:
            UnexpectedInputArgumentsError: If extra keyword arguments are provided that do not match any input field (after allowing keys suffixed with "_id").
            MissingInputArgumentsError: If one or more required input fields are not provided.
            CircularInputDependencyError: If input fields declare dependencies that form a cycle and cannot be resolved.
            InvalidInputTypeError: If a provided value does not match the declared type for an input.
            InvalidInputConstraintError: If bounds validation or the configured
                validator rejects a value.
            InvalidPossibleValuesTypeError: If an input's `possible_values` configuration is neither callable nor iterable.
            InvalidInputValueError: If a provided value is not in the allowed set defined by an input's `possible_values`.
        """
        resolved_identification: dict[str, object] = {}
        plan = type(self)._get_input_parsing_plan()
        if (
            len(plan.names) == 1
            and plan.required_names == plan.name_set
            and plan.dependency_items[0][1] == ()
        ):
            name = plan.names[0]
            raw_value: object = None
            has_single_value = False
            if len(args) == 1 and not kwargs:
                raw_value = args[0]
                has_single_value = True
            elif not args and len(kwargs) == 1 and name in kwargs:
                raw_value = kwargs[name]
                has_single_value = True
            if has_single_value:
                input_field = plan.field_by_name[name]
                cache_key = type(self)._single_input_run_cache_key(
                    name,
                    input_field,
                    raw_value,
                )
                context = None
                if cache_key is not None:
                    from general_manager.cache.run_context import (
                        current_calculation_run_context,
                    )

                    context = current_calculation_run_context()
                    if context is not None:
                        cached_value = context.get(
                            cache_key,
                            _SINGLE_INPUT_VALUE_CACHE_MISS,
                        )
                        if cached_value is not _SINGLE_INPUT_VALUE_CACHE_MISS:
                            return {name: cached_value}
                cache_context = type(self)._input_possible_values_cache_context(name)
                value = input_field.cast(
                    raw_value,
                    resolved_identification,
                    cache_context=cache_context,
                )
                self._process_input_field(
                    name,
                    input_field,
                    value,
                    resolved_identification,
                    cache_context=cache_context,
                )
                if context is not None and cache_key is not None:
                    context.set(cache_key, value)
                return {name: value}

        if not args:
            kwarg_names = kwargs.keys()
            if kwarg_names <= plan.name_set and plan.required_names <= kwarg_names:
                if plan.has_dependencies:
                    ordered_names, unresolved = type(self)._get_input_dependency_order(
                        plan
                    )
                else:
                    ordered_names = plan.names
                    unresolved = frozenset()
                for name in ordered_names:
                    input_field = plan.field_by_name[name]
                    cache_context = type(self)._input_possible_values_cache_context(
                        name
                    )
                    value = input_field.cast(
                        kwargs.get(name),
                        resolved_identification,
                        cache_context=cache_context,
                    )
                    self._process_input_field(
                        name,
                        input_field,
                        value,
                        resolved_identification,
                        cache_context=cache_context,
                    )
                    resolved_identification[name] = value
                if unresolved:
                    raise CircularInputDependencyError(unresolved)
                return {name: resolved_identification[name] for name in plan.names}

        kwargs = args_to_kwargs(args, plan.names, kwargs)

        extra_args = set(kwargs) - plan.name_set
        if extra_args:
            handled: set[str] = set()
            for extra_arg in list(extra_args):
                alias = plan.alias_to_name.get(extra_arg)
                if alias is not None:
                    kwargs[alias] = kwargs.pop(extra_arg)
                    handled.add(extra_arg)
            remaining = (extra_args - handled) | (set(kwargs) - plan.name_set)
            if remaining:
                raise UnexpectedInputArgumentsError(remaining)

        for name in plan.optional_names:
            if name not in kwargs:
                kwargs[name] = None

        missing_args = plan.required_names - set(kwargs)
        if missing_args:
            raise MissingInputArgumentsError(missing_args)

        ordered_names, unresolved = type(self)._get_input_dependency_order(plan)
        for name in ordered_names:
            input_field = plan.field_by_name[name]
            cache_context = type(self)._input_possible_values_cache_context(name)
            value = input_field.cast(
                kwargs.get(name),
                resolved_identification,
                cache_context=cache_context,
            )
            self._process_input_field(
                name,
                input_field,
                value,
                resolved_identification,
                cache_context=cache_context,
            )
            resolved_identification[name] = value
        if unresolved:
            raise CircularInputDependencyError(unresolved)
        identification = {name: resolved_identification[name] for name in plan.names}
        return identification

    @classmethod
    def _input_possible_values_cache_context(
        cls,
        input_name: str,
    ) -> tuple[type[object], str] | None:
        parent_class = getattr(cls, "_parent_class", None)
        if parent_class is None:
            return None
        return (parent_class, input_name)

    @staticmethod
    def format_identification(identification: dict[str, object]) -> dict[str, object]:
        """
        Normalise identification data by replacing manager instances with their IDs.

        Parameters:
            identification: Raw identification mapping possibly containing manager instances.

        Returns:
            dict[str, object]: Identification mapping with nested managers replaced by their identifications.
        """
        from general_manager.manager.general_manager import GeneralManager

        for key, value in identification.items():
            if isinstance(value, GeneralManager):
                identification[key] = value.identification
            elif isinstance(value, (list, tuple)):
                normalized_values: list[object] = []
                for v in value:
                    if isinstance(v, GeneralManager):
                        normalized_values.append(v.identification)
                    elif isinstance(v, dict):
                        normalized_values.append(InterfaceBase.format_identification(v))
                    else:
                        normalized_values.append(v)
                identification[key] = normalized_values
            elif isinstance(value, dict):
                identification[key] = InterfaceBase.format_identification(value)
        return identification

    def _process_input(
        self, name: str, value: object, identification: dict[str, object]
    ) -> None:
        """
        Validate a single input value against its declared Input definition.

        Checks that the provided value matches the declared Python type and
        declared constraints. ``possible_values`` membership is enforced only
        when the ``VALIDATE_INPUT_VALUES`` setting is truthy; when that setting
        is unset, enforcement follows ``settings.DEBUG``. Possible values may
        be an iterable or a callable that receives dependent input values.

        Parameters:
            name: The input field name being validated.
            value: The value to validate.
            identification: Partially resolved identification mapping used to supply dependent input values when evaluating `possible_values`.

        Raises:
            InvalidInputTypeError: If `value` is not an instance of the input's declared `type`.
            InvalidInputConstraintError: If bounds validation or the configured
                validator rejects `value`.
            InvalidPossibleValuesTypeError: If `possible_values` is neither callable nor iterable.
            InvalidInputValueError: If possible-value validation is enabled and `value` is not contained in the evaluated `possible_values`.
        """
        input_field = self.input_fields[name]
        cache_context = type(self)._input_possible_values_cache_context(name)
        self._process_input_field(
            name,
            input_field,
            value,
            identification,
            cache_context=cache_context,
        )

    def _process_input_field(
        self,
        name: str,
        input_field: "Input[type[object]]",
        value: object,
        identification: dict[str, object],
        *,
        cache_context: tuple[type[object], str] | None,
    ) -> None:
        """Validate a value against an already-resolved input field."""
        if value is None:
            if input_field.required:
                raise InvalidInputTypeError(name, type(value), input_field.type)
            return
        if not isinstance(value, input_field.type):
            raise InvalidInputTypeError(name, type(value), input_field.type)
        if (
            input_field.min_value is not None or input_field.max_value is not None
        ) and not input_field.validate_bounds(value):
            raise InvalidInputConstraintError(
                name,
                f"{value} is outside the allowed range"
                f" [{input_field.min_value}, {input_field.max_value}]",
            )
        if input_field.validator is not None and not input_field.validate_with_callable(
            value,
            identification,
        ):
            raise InvalidInputConstraintError(
                name,
                f"{value} did not satisfy the configured validator",
            )

        if input_field.possible_values is None:
            return
        if not _should_validate_possible_values():
            return
        if _trusted_possible_values_membership_authorized(
            self,
            name,
            input_field,
            value,
            identification,
        ):
            return

        allowed_values = input_field.resolve_possible_values(
            identification,
            cache_context=cache_context,
        )
        if allowed_values is None:
            return
        contains = getattr(allowed_values, "contains", None)
        if callable(contains):
            if not contains(value):
                raise InvalidInputValueError(name, value, [])
            return
        if not isinstance(allowed_values, Iterable):
            raise InvalidPossibleValuesTypeError(name)

        if value not in allowed_values:
            raise InvalidInputValueError(name, value, allowed_values)

    @classmethod
    def create(cls, *args: object, **kwargs: object) -> dict[str, object]:
        """
        Create a new managed record in the underlying data store using the interface's inputs.

        This base method is intentionally typed to the capability-level mutation
        result, currently a dictionary such as `{"id": pk}`. Capability handlers
        used with `InterfaceBase.create()` should return that mapping shape;
        higher manager layers may convert it to a manager instance.

        Parameters:
            *args: Positional input values corresponding to the interface's defined input fields.
            **kwargs: Input values provided by name; unexpected extra keywords will be rejected.

        Returns:
            The capability-level create result mapping.
        """
        observer = cls.get_capability_handler("observability")

        def _invoke() -> dict[str, object]:
            """
            Invoke the configured "create" capability handler for this interface and return its result.

            Returns:
                dict[str, object]: The payload returned by the create handler.

            Raises:
                NotImplementedError: If no create capability is available or the handler does not implement `create`.
            """
            handler = cls.require_capability("create")
            if hasattr(handler, "create"):
                create_handler = cast(Callable[..., dict[str, object]], handler.create)
                return create_handler(cls, *args, **kwargs)
            raise NotImplementedError(f"{cls.__name__} does not support create.")

        return cls._execute_with_observability(
            target=cls,
            operation="create",
            payload={"args": args, "kwargs": kwargs},
            func=_invoke,
            observer=observer,
        )

    def update(self, *args: object, **kwargs: object) -> object:
        """
        Update the underlying managed record.

        Positional and keyword arguments are forwarded unchanged to the
        configured update capability together with this interface instance. This
        base method does not combine payload values with `identification` and
        does not reject unexpected keywords itself; validation belongs to the
        update capability.

        Returns:
            The updated record or a manager-specific result.

        Raises:
            NotImplementedError: If this interface does not provide an update capability.
        """
        observer = self.get_capability_handler("observability")

        def _invoke() -> object:
            """
            Invoke the update capability handler to perform an update operation.

            Returns:
                The result returned by the capability's `update` handler.

            Raises:
                NotImplementedError: If the interface does not provide an `update` capability.
            """
            handler = self._require_capability("update")
            if hasattr(handler, "update"):
                update_handler = cast(Callable[..., object], handler.update)
                return update_handler(self, *args, **kwargs)
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support update."
            )

        return self._execute_with_observability(
            target=self,
            operation="update",
            payload={"args": args, "kwargs": kwargs},
            func=_invoke,
            observer=observer,
        )

    def delete(self, *args: object, **kwargs: object) -> object:
        """
        Delete the underlying record managed by this interface.

        Delegates the deletion to the interface's configured delete capability and executes the operation with observability hooks.
        Positional and keyword arguments are forwarded unchanged together with
        this interface instance. This base method does not combine payload values
        with `identification` and does not reject unexpected keywords itself;
        validation belongs to the delete capability.

        Returns:
            The result of the delete operation as returned by the delete capability.

        Raises:
            NotImplementedError: If the interface does not provide a delete capability.
        """
        observer = self.get_capability_handler("observability")

        def _invoke() -> object:
            """
            Invoke the bound delete capability to remove the managed record.

            Returns:
                The result returned by the capability's `delete` handler.

            Raises:
                NotImplementedError: If the interface has no `delete` handler implemented.
            """
            handler = self._require_capability("delete")
            if hasattr(handler, "delete"):
                delete_handler = cast(Callable[..., object], handler.delete)
                return delete_handler(self, *args, **kwargs)
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support delete."
            )

        return self._execute_with_observability(
            target=self,
            operation="delete",
            payload={"args": args, "kwargs": kwargs},
            func=_invoke,
            observer=observer,
        )

    def get_data(self) -> object:
        """
        Get the materialized data for this manager.

        Returns:
            The materialized data for this manager (implementation-defined).

        Raises:
            NotImplementedError: if reading is not supported for this manager.
        """
        observer = self.get_capability_handler("observability")

        def _invoke() -> object:
            """
            Invoke the configured read capability to retrieve this manager's materialized data.

            Returns:
                The materialized data returned by the read capability.

            Raises:
                NotImplementedError: If this interface does not support read (no `get_data` on the read capability).
            """
            handler = self._require_capability("read")
            if hasattr(handler, "get_data"):
                read_handler = cast(Callable[..., object], handler.get_data)
                return read_handler(self)
            raise NotImplementedError(
                f"{self.__class__.__name__} does not support read."
            )

        return self._execute_with_observability(
            target=self,
            operation="read",
            payload={"identification": getattr(self, "identification", None)},
            func=_invoke,
            observer=observer,
        )

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        """
        Retrieve metadata describing each attribute exposed by the manager.

        This method delegates entirely to the read capability. It does not build
        fallback metadata from `input_fields`.

        Returns:
            dict[str, AttributeTypedDict]: Mapping from attribute name to its metadata (keys include `type`, `default`, `is_required`, `is_editable`, and `is_derived`).

        Raises:
            NotImplementedError: If the manager does not provide a read capability implementing `get_attribute_types`.
        """
        handler = cls.get_capability_handler("read")
        if handler is not None and hasattr(handler, "get_attribute_types"):
            get_attribute_types = cast(
                Callable[..., dict[str, AttributeTypedDict]],
                handler.get_attribute_types,
            )
            return get_attribute_types(cls)
        raise NotImplementedError(
            f"{cls.__name__} must provide a read capability implementing get_attribute_types."
        )

    @classmethod
    def get_attributes(cls) -> dict[str, object]:
        """
        Retrieve attribute values exposed by the interface.

        This method delegates entirely to the read capability. It does not build
        fallback values from `input_fields`.

        Returns:
            dict[str, object]: Mapping of attribute names to their current values.

        Raises:
            NotImplementedError: If the interface does not provide a read capability implementing `get_attributes`.
        """
        handler = cls.get_capability_handler("read")
        if handler is not None and hasattr(handler, "get_attributes"):
            get_attributes = cast(
                Callable[..., dict[str, object]],
                handler.get_attributes,
            )
            return get_attributes(cls)
        raise NotImplementedError(
            f"{cls.__name__} must provide a read capability implementing get_attributes."
        )

    @classmethod
    def get_graph_ql_properties(cls) -> dict[str, GraphQLProperty]:
        """
        Collect GraphQLProperty descriptors declared on the interface's parent manager class.

        Returns:
            dict[str, GraphQLProperty]: Mapping from attribute name to the corresponding GraphQLProperty instance found on the parent manager class. Returns an empty dict if no parent class is set or none of its attributes are GraphQLProperty instances.
        """
        if not hasattr(cls, "_parent_class"):
            return {}
        return {
            name: prop
            for name, prop in vars(cls._parent_class).items()
            if isinstance(prop, GraphQLProperty)
        }

    @classmethod
    def filter(cls, **kwargs: object) -> "Bucket[GeneralManager]":
        """
        Filter records through the query capability and return its Bucket of matches.

        Parameters:
            **kwargs: Lookup expressions mapping field lookups (e.g., "name__icontains") to values.

        Returns:
            Bucket[GeneralManager]: Bucket returned by the query capability, containing records that match the lookup expressions.

        Raises:
            NotImplementedError: If the interface's query capability does not implement filtering.
        """
        handler = cls.require_capability("query")
        if hasattr(handler, "filter"):
            filter_handler = cast(
                Callable[..., "Bucket[GeneralManager]"],
                handler.filter,
            )
            return filter_handler(cls, **kwargs)
        raise NotImplementedError

    @classmethod
    def exclude(cls, **kwargs: object) -> "Bucket[GeneralManager]":
        """
        Exclude records through the query capability and return its Bucket of matches.

        Parameters:
            **kwargs: Lookup expressions accepted by the query capability (e.g., field=value, field__lookup=value).

        Returns:
            Bucket[GeneralManager]: Bucket returned by the query capability, containing records that do not match the provided lookup expressions.

        Raises:
            NotImplementedError: If the interface's query capability does not implement an `exclude` operation.
        """
        handler = cls.require_capability("query")
        if hasattr(handler, "exclude"):
            exclude_handler = cast(
                Callable[..., "Bucket[GeneralManager]"],
                handler.exclude,
            )
            return exclude_handler(cls, **kwargs)
        raise NotImplementedError

    @classmethod
    def all(cls) -> "Bucket[GeneralManager]":
        """
        Retrieve all records through the query capability and return its Bucket.

        Returns:
            Bucket[GeneralManager]: Bucket returned by the query capability, containing every record accessible via this interface.

        Raises:
            NotImplementedError: If the configured query capability does not implement `all`.
        """
        handler = cls.require_capability("query")
        if hasattr(handler, "all"):
            all_handler = cast(Callable[..., "Bucket[GeneralManager]"], handler.all)
            return all_handler(cls)
        raise NotImplementedError

    @staticmethod
    def _execute_with_observability(
        *,
        target: object,
        operation: str,
        payload: dict[str, object],
        func: Callable[[], ResultT],
        observer: "Capability | None",
    ) -> ResultT:
        """
        Execute a callable while invoking optional observer lifecycle hooks before, after, and on error.

        Parameters:
            target (object): The subject of the operation (passed to observer hooks).
            operation (str): A short name of the operation (passed to observer hooks).
            payload: Contextual data about the operation (passed to observer hooks).
            func: The callable to execute.
            observer (Capability | None): Optional capability providing `before_operation`, `after_operation`, and/or `on_error` hooks.

        Returns:
            ResultT: The value returned by `func`.

        Notes:
            If `observer.before_operation(...)` raises, `func` is not called. If
            `func` raises, the exception is propagated after calling
            `observer.on_error(...)` if available; an `on_error` exception
            replaces the original. If `observer.after_operation(...)` raises
            after a successful `func`, that exception is propagated instead of
            the result.
        """
        if observer is not None:
            before_operation = getattr(
                observer,
                "before_operation",
                _OBSERVABILITY_HOOK_MISSING,
            )
        else:
            before_operation = _OBSERVABILITY_HOOK_MISSING
        if before_operation is not _OBSERVABILITY_HOOK_MISSING:
            cast(Callable[..., None], before_operation)(
                operation=operation,
                target=target,
                payload=payload,
            )
        try:
            result = func()
        except Exception as error:
            if observer is not None:
                on_error = getattr(
                    observer,
                    "on_error",
                    _OBSERVABILITY_HOOK_MISSING,
                )
            else:
                on_error = _OBSERVABILITY_HOOK_MISSING
            if on_error is not _OBSERVABILITY_HOOK_MISSING:
                cast(Callable[..., None], on_error)(
                    operation=operation,
                    target=target,
                    payload=payload,
                    error=error,
                )
            raise
        if observer is not None:
            after_operation = getattr(
                observer,
                "after_operation",
                _OBSERVABILITY_HOOK_MISSING,
            )
        else:
            after_operation = _OBSERVABILITY_HOOK_MISSING
        if after_operation is not _OBSERVABILITY_HOOK_MISSING:
            cast(Callable[..., None], after_operation)(
                operation=operation,
                target=target,
                payload=payload,
                result=result,
            )
        return result

    @staticmethod
    def _invoke_lifecycle_callable(
        lifecycle_callable: Callable[..., ResultT],
        **kwargs: object,
    ) -> ResultT:
        """
        Invoke a lifecycle callable using only the keyword arguments that match its signature.

        Parameters:
            lifecycle_callable: The callable to invoke.
            **kwargs: Candidate keyword arguments; only those with names present in the callable's parameter list will be passed.

        Returns:
            ResultT: The value returned by calling `lifecycle_callable` with the filtered arguments.
        """
        signature = inspect.signature(lifecycle_callable)
        allowed = {
            name: kwargs[name] for name in signature.parameters.keys() if name in kwargs
        }
        return lifecycle_callable(**allowed)

    @staticmethod
    def _default_base_model_class() -> type["GeneralManagerBasisModel"]:
        """
        Return the default base model class used by GeneralManager implementations.

        Returns:
            GeneralManagerBasisModel: The concrete model class used as the default base for managers.
        """
        from general_manager.interface.utils.models import GeneralManagerBasisModel

        return GeneralManagerBasisModel

    @classmethod
    def handle_interface(
        cls,
    ) -> tuple[
        classPreCreationMethod,
        classPostCreationMethod,
    ]:
        """
        Provide pre- and post-creation hooks for GeneralManager class construction derived from the interface's lifecycle capability.

        Returns:
            tuple[classPreCreationMethod, classPostCreationMethod]:
                - pre-create callable accepting (name, attrs, interface, base_model_class=None) and returning (attrs, interface_class, related_class).
                - post-create callable accepting (new_class, interface_class, model) and returning None.

        Raises:
            NotImplementedError: If no lifecycle capability is declared, or if
                the configured lifecycle capability does not provide callable
                `pre_create` and `post_create` hooks.
            Exception: Exceptions from lifecycle hooks propagate unchanged.
                Malformed hook return values are not validated here and fail in
                the caller that consumes the lifecycle tuple.
        """
        lifecycle = cls._lifecycle_capability()
        if lifecycle is not None:
            pre = getattr(lifecycle, "pre_create", None)
            post = getattr(lifecycle, "post_create", None)
            if callable(pre) and callable(post):
                pre_create = cast(
                    Callable[..., tuple[attributes, interfaceBaseClass, relatedClass]],
                    pre,
                )
                post_create = cast(Callable[..., None], post)

                def pre_wrapper(
                    name: generalManagerClassName,
                    attrs: attributes,
                    interface: interfaceBaseClass,
                    base_model_class: type["GeneralManagerBasisModel"] | None = None,
                ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
                    """
                    Wraps and invoke the lifecycle pre-creation hook for a GeneralManager class.

                    Calls the configured pre-create lifecycle callable with the provided name, attrs, interface, and a base_model_class (uses the interface's default base model class when None) and returns the possibly-modified creation trio.

                    Parameters:
                        name (str): Proposed class name for the GeneralManager.
                        attrs: Attribute dictionary for the class being created.
                        interface (Type[InterfaceBase]): Interface base class passed to the lifecycle hook.
                        base_model_class (type[GeneralManagerBasisModel] | None): Base model class to supply to the lifecycle hook; if None, the interface's default is used.

                    Returns:
                        tuple[attributes, Type[InterfaceBase], Type[Model] | None]: A tuple of (attributes, interface class, related model class) as returned or transformed by the lifecycle pre-create callable.
                    """
                    if base_model_class is None:
                        base_model_class = cls._default_base_model_class()
                    return cls._invoke_lifecycle_callable(
                        pre_create,
                        name=name,
                        attrs=attrs,
                        interface=interface,
                        base_model_class=base_model_class,
                    )

                def post_wrapper(
                    new_class: newlyCreatedGeneralManagerClass,
                    interface_class: newlyCreatedInterfaceClass,
                    model: relatedClass,
                ) -> None:
                    """
                    Invoke the post-creation lifecycle callable for a newly created GeneralManager class.

                    Parameters:
                        new_class (Type[GeneralManager]): The newly created GeneralManager subclass.
                        interface_class (Type[InterfaceBase]): The interface class used to create the manager.
                        model (Type[Model] | None): The related Django model class, or None if not applicable.
                    """
                    cls._invoke_lifecycle_callable(
                        post_create,
                        new_class=new_class,
                        interface_class=interface_class,
                        model=model,
                    )

                return pre_wrapper, post_wrapper

        raise NotImplementedError(
            f"{cls.__name__} must override handle_interface or declare a lifecycle capability."
        )

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Resolve the declared Python type for the named input field.

        If a read capability implements `get_field_type()`, that capability owns
        the result. Otherwise this method falls back only to declared
        `input_fields`.

        Parameters:
            field_name (str): Name of the input field to look up.

        Returns:
            type: The Python type declared for the specified field.

        Raises:
            KeyError: If no input field with the given name is defined.
        """
        handler = cls.get_capability_handler("read")
        if handler is not None and hasattr(handler, "get_field_type"):
            get_field_type = cast(Callable[..., type], handler.get_field_type)
            return get_field_type(cls, field_name)
        field = cls.input_fields.get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field.type


_INTERFACE_BASE_PROVENANCE = (
    InterfaceBase,
    _capture_static_dispatch(
        InterfaceBase,
        (
            "__init__",
            _INSTANCE_DICT_NAME,
            "__getattribute__",
            "__getattr__",
            "__setattr__",
            "parse_input_fields_to_identification",
            "_process_input_field",
            "format_identification",
            "get_attributes",
            "get_field_type",
            "handle_interface",
        ),
    ),
)
