"""Capabilities tailored for calculation interfaces."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock, get_ident
from types import CellType, CodeType, FunctionType, MappingProxyType
from typing import TYPE_CHECKING, ClassVar, cast
from weakref import WeakKeyDictionary

from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.interface.base_interface import (
    _INSTANCE_DICT_NAME,
    _INPUT_PROVENANCE,
    _INTERFACE_BASE_PROVENANCE,
    _LAZY_INPUT_VALUES_CACHE_NAME,
    _MANAGER_INPUT_SEED_PLAN_NAME,
    _RESOLVED_INPUT_VALUES_CACHE_NAME,
    _SEEDED_INPUT_VALUES_CACHE_NAME,
    _SeededFieldOrigin,
    _SeededInterfaceOrigin,
    _STATIC_ATTRIBUTE_MISSING,
    _GENERAL_MANAGER_PROVENANCE,
    _canonical_manager_class_state,
    _canonical_database_nested_interface_state,
    _calculation_interface_seed_provenance,
    _calculation_manager_input_seed_plan,
    _dict_has_identity_keys,
    _discard_seeded_interface_origin,
    _mro_state_access_is_canonical,
    _mapping_value_by_identity,
    _matches_static_dispatch,
    _register_calculation_capability_seed_provenance,
    _seeded_interface_origin,
    _static_descriptor,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import _is_canonical_manager_attribute_descriptor

from ..base import CapabilityName
from ..builtin import BaseCapability
from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.calculation import (
        CalculationInterface,
    )


_CALCULATION_INPUT_ACCESSOR_TOKEN = object()
_CALCULATION_INPUT_ACCESSOR_STATE = (
    "_gm_calculation_input_accessor_token",
    "_gm_calculation_input_accessor_self",
    "_gm_calculation_interface_cls",
    "_gm_calculation_field_name",
)
_EMPTY_CLOSURE_CELL = object()
_MISSING_INTERFACE_STATE = object()
_USE_VIRTUAL_RESOLUTION = object()


@dataclass(frozen=True, slots=True)
class _SeededResolutionClaim:
    origin: _SeededInterfaceOrigin
    field_origin: _SeededFieldOrigin
    resolved_values: dict[str, object]
    input_field: Input[type[object]]
    raw_value: object
    cache_context: tuple[type[object], str] | None
    thread_id: int


def _static_class_value(
    cls: type[object],
    name: str,
) -> object:
    """Read a class mapping entry without descriptor dispatch or key hooks."""
    mro = type.__getattribute__(cls, "__mro__")
    if type(mro) is not tuple:
        return _MISSING_INTERFACE_STATE
    for candidate in mro:
        class_state = type.__getattribute__(candidate, "__dict__")
        if type(class_state) is not MappingProxyType:
            return _MISSING_INTERFACE_STATE
        for key, value in class_state.items():
            if type(key) is not str:
                return _MISSING_INTERFACE_STATE
            if key == name:
                return value
    return _MISSING_INTERFACE_STATE


def _exact_string_dict(value: object) -> bool:
    return type(value) is dict and all(type(key) is str for key in value)


def _exact_string_set(value: object) -> bool:
    return type(value) is set and all(type(item) is str for item in value)


def _seeded_interface_surrounding_state_safe(
    interface_instance: "CalculationInterface",
) -> bool:
    """Validate the live interface shell without consulting marker mirrors."""
    interface_provenance = _INTERFACE_BASE_PROVENANCE
    if interface_provenance is None:
        return False
    interface_class = type(interface_instance)
    if not _mro_state_access_is_canonical(
        interface_class,
        interface_provenance[0],
        (
            _INSTANCE_DICT_NAME,
            "_resolved_input_values",
            _SEEDED_INPUT_VALUES_CACHE_NAME,
            _LAZY_INPUT_VALUES_CACHE_NAME,
            "identification",
        ),
    ):
        return False
    state = object.__getattribute__(interface_instance, _INSTANCE_DICT_NAME)
    if not _exact_string_dict(state):
        return False
    expected_names = (
        "_resolved_input_values",
        _SEEDED_INPUT_VALUES_CACHE_NAME,
        _LAZY_INPUT_VALUES_CACHE_NAME,
        "identification",
    )
    return len(state) in {2, 3, 4} and all(
        any(key == expected for expected in expected_names) for key in state
    )


def _seeded_interface_dispatch_is_canonical(
    interface_instance: "CalculationInterface",
) -> bool:
    """Revalidate every interface dispatch owner captured before seeding."""
    base_provenance = _INTERFACE_BASE_PROVENANCE
    calculation_provenance = _calculation_interface_seed_provenance()
    if base_provenance is None or calculation_provenance is None:
        return False
    canonical_base, base_dispatch = base_provenance
    (
        calculation_interface,
        canonical_metaclass,
        calculation_dispatch,
        metaclass_dispatch,
    ) = calculation_provenance
    interface_class = type(interface_instance)
    if type(interface_class) is not canonical_metaclass:
        return False
    mro = type.__getattribute__(interface_class, "__mro__")
    return (
        type(mro) is tuple
        and any(candidate is calculation_interface for candidate in mro)
        and _matches_static_dispatch(canonical_base, base_dispatch)
        and _matches_static_dispatch(
            calculation_interface,
            calculation_dispatch,
        )
        and _matches_static_dispatch(canonical_metaclass, metaclass_dispatch)
        and _matches_static_dispatch(interface_class, calculation_dispatch)
    )


def _live_seeded_raw_value(
    interface_instance: "CalculationInterface",
    field_name: str,
) -> tuple[bool, object]:
    """Read current canonical identification state without virtual dispatch."""
    if not _seeded_interface_surrounding_state_safe(interface_instance):
        return False, _MISSING_INTERFACE_STATE
    state = object.__getattribute__(interface_instance, _INSTANCE_DICT_NAME)
    identification = _mapping_value_by_identity(
        state,
        "identification",
        _MISSING_INTERFACE_STATE,
    )
    if not _exact_string_dict(identification):
        return False, _MISSING_INTERFACE_STATE
    return True, _mapping_value_by_identity(
        cast(dict[str, object], identification),
        field_name,
        _MISSING_INTERFACE_STATE,
    )


def _replace_live_resolved_cache_if_safe(
    interface_instance: "CalculationInterface",
    resolved_values: dict[str, object],
) -> None:
    """Mirror a reconciled external cache only when direct state access is safe."""
    interface_provenance = _INTERFACE_BASE_PROVENANCE
    if interface_provenance is None:
        return
    interface_class = type(interface_instance)
    if not _mro_state_access_is_canonical(
        interface_class,
        interface_provenance[0],
        (_INSTANCE_DICT_NAME, "_resolved_input_values"),
    ):
        return
    state = object.__getattribute__(interface_instance, _INSTANCE_DICT_NAME)
    if _exact_string_dict(state):
        dict.__setitem__(
            state,
            _RESOLVED_INPUT_VALUES_CACHE_NAME,
            resolved_values,
        )


def _transition_mirror_field_to_lazy_if_safe(
    interface_instance: "CalculationInterface",
    field_name: str,
) -> None:
    """Release mirror references without trusting mirror keys or descriptors."""
    interface_provenance = _INTERFACE_BASE_PROVENANCE
    if interface_provenance is None:
        return
    interface_class = type(interface_instance)
    if not _mro_state_access_is_canonical(
        interface_class,
        interface_provenance[0],
        (
            _INSTANCE_DICT_NAME,
            _SEEDED_INPUT_VALUES_CACHE_NAME,
            _LAZY_INPUT_VALUES_CACHE_NAME,
        ),
    ):
        return
    state = object.__getattribute__(interface_instance, _INSTANCE_DICT_NAME)
    if not _exact_string_dict(state):
        return
    seeded_values = dict.get(
        state,
        _SEEDED_INPUT_VALUES_CACHE_NAME,
        _MISSING_INTERFACE_STATE,
    )
    if type(seeded_values) is dict:
        if _exact_string_dict(seeded_values):
            dict.pop(seeded_values, field_name, None)
        else:
            seeded_values = {}
            dict.__setitem__(
                state,
                _SEEDED_INPUT_VALUES_CACHE_NAME,
                seeded_values,
            )
        if not seeded_values:
            dict.pop(state, _SEEDED_INPUT_VALUES_CACHE_NAME, None)
    lazy_fields = dict.get(
        state,
        _LAZY_INPUT_VALUES_CACHE_NAME,
        _MISSING_INTERFACE_STATE,
    )
    if not _exact_string_set(lazy_fields):
        lazy_fields = set()
        dict.__setitem__(state, _LAZY_INPUT_VALUES_CACHE_NAME, lazy_fields)
    set.add(lazy_fields, field_name)


def _transition_origin_to_virtual_fallback(
    interface_instance: "CalculationInterface",
    origin: _SeededInterfaceOrigin,
) -> None:
    """Release all external seeds before honoring changed virtual dispatch."""
    with origin.transition_condition:
        if _seeded_interface_origin(interface_instance) is not origin:
            return
        origin.resolved_values = {}
        _clear_seed_mirrors_if_safe(interface_instance)
        _discard_seeded_interface_origin(interface_instance, origin)
        if type(origin.fields) is dict:
            for field_origin in dict.values(origin.fields):
                if type(field_origin) is _SeededFieldOrigin:
                    field_origin.condition.notify_all()
        origin.waiting_fields_by_thread = {}


def _clear_seed_mirrors_if_safe(
    interface_instance: "CalculationInterface",
) -> None:
    """Rebuild safe live state without invoking keys or changed dispatch."""
    interface_provenance = _INTERFACE_BASE_PROVENANCE
    if interface_provenance is None:
        return
    interface_class = type(interface_instance)
    if not _mro_state_access_is_canonical(
        interface_class,
        interface_provenance[0],
        (_INSTANCE_DICT_NAME,),
    ):
        return
    state = object.__getattribute__(interface_instance, _INSTANCE_DICT_NAME)
    if type(state) is not dict:
        return
    retained_state: list[tuple[str, object]] = []
    for key, value in dict.items(state):
        if type(key) is not str:
            continue
        if key == _SEEDED_INPUT_VALUES_CACHE_NAME:
            if type(value) is dict:
                dict.clear(value)
        elif key == _LAZY_INPUT_VALUES_CACHE_NAME:
            if type(value) is set:
                set.clear(value)
        elif key == _RESOLVED_INPUT_VALUES_CACHE_NAME:
            if type(value) is dict:
                dict.clear(value)
        else:
            retained_state.append((key, value))
    dict.clear(state)
    for key, value in retained_state:
        dict.__setitem__(state, key, value)


def _seeded_wait_chain_has_cycle(
    origin: _SeededInterfaceOrigin,
    starting_thread_id: int,
) -> bool:
    """Detect a field-owner wait cycle while the origin lock is held."""
    if type(origin.waiting_fields_by_thread) is not dict:
        return True
    current_thread_id = starting_thread_id
    visited_threads: set[int] = set()
    while True:
        if current_thread_id in visited_threads:
            return True
        visited_threads.add(current_thread_id)
        waiting_field = dict.get(
            origin.waiting_fields_by_thread,
            current_thread_id,
            _MISSING_INTERFACE_STATE,
        )
        if type(waiting_field) is not str:
            return False
        field_origin = _mapping_value_by_identity(
            origin.fields,
            waiting_field,
            _MISSING_INTERFACE_STATE,
        )
        if type(field_origin) is not _SeededFieldOrigin:
            return True
        owner_thread_id = field_origin.resolving_thread_id
        if owner_thread_id is None:
            return False
        current_thread_id = owner_thread_id


def _post_seeded_manager_state_is_safe(
    cached_value: object,
    manager_type: type[object],
) -> bool:
    """Allow canonical cache evolution while rejecting unsafe manager state."""
    manager_provenance = _GENERAL_MANAGER_PROVENANCE
    interface_provenance = _INTERFACE_BASE_PROVENANCE
    if manager_provenance is None or interface_provenance is None:
        return False
    if type(cached_value) is not manager_type:
        return False
    if not _mro_state_access_is_canonical(
        manager_type,
        manager_provenance[0],
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
    manager_state = object.__getattribute__(cached_value, _INSTANCE_DICT_NAME)
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
    identification = dict.__getitem__(manager_state, "_GeneralManager__id")
    attribute_cache = dict.__getitem__(manager_state, "_attribute_value_cache")
    dependency_cache = dict.__getitem__(
        manager_state,
        "_identification_dependency_cache",
    )
    if (
        type(identification) is not dict
        or type(attribute_cache) is not dict
        or (dependency_cache is not None and type(dependency_cache) is not tuple)
        or dict.__getitem__(manager_state, "_manager_state_valid") is not True
        or dict.__getitem__(manager_state, "_manager_state_reason") is not None
    ):
        return False

    nested_interface = dict.__getitem__(manager_state, "_interface")
    nested_interface_class = type(nested_interface)
    if _static_class_value(manager_type, "Interface") is not nested_interface_class:
        return False
    if _canonical_database_nested_interface_state(
        manager_type,
        nested_interface,
        identification,
    ):
        return True
    if not _mro_state_access_is_canonical(
        nested_interface_class,
        interface_provenance[0],
        (
            _INSTANCE_DICT_NAME,
            "_resolved_input_values",
            _SEEDED_INPUT_VALUES_CACHE_NAME,
            _LAZY_INPUT_VALUES_CACHE_NAME,
            "identification",
        ),
    ):
        return False
    nested_state = object.__getattribute__(nested_interface, _INSTANCE_DICT_NAME)
    if type(nested_state) is not dict or not 1 <= len(nested_state) <= 4:
        return False
    if any(type(key) is not str for key in nested_state):
        return False
    allowed_nested_names = (
        "identification",
        "_resolved_input_values",
        _SEEDED_INPUT_VALUES_CACHE_NAME,
        _LAZY_INPUT_VALUES_CACHE_NAME,
    )
    if any(
        not any(key == allowed for allowed in allowed_nested_names)
        for key in nested_state
    ):
        return False
    nested_identification = dict.get(
        nested_state,
        "identification",
        _MISSING_INTERFACE_STATE,
    )
    if nested_identification is not identification:
        return False
    nested_resolved = dict.get(
        nested_state,
        "_resolved_input_values",
        _MISSING_INTERFACE_STATE,
    )
    if (
        nested_resolved is not _MISSING_INTERFACE_STATE
        and type(nested_resolved) is not dict
    ):
        return False
    nested_seeded = dict.get(
        nested_state,
        _SEEDED_INPUT_VALUES_CACHE_NAME,
        _MISSING_INTERFACE_STATE,
    )
    nested_lazy = dict.get(
        nested_state,
        _LAZY_INPUT_VALUES_CACHE_NAME,
        _MISSING_INTERFACE_STATE,
    )
    return (
        nested_seeded is _MISSING_INTERFACE_STATE or type(nested_seeded) is dict
    ) and (nested_lazy is _MISSING_INTERFACE_STATE or type(nested_lazy) is set)


def _cached_manager_matches_formatted_identification(
    parent_class: object,
    field_name: str,
    input_field: Input[type[object]],
    cached_value: object,
    formatted_identification: dict[str, object],
) -> bool:
    """Validate one seeded manager without invoking instance/class hooks."""
    input_provenance = _INPUT_PROVENANCE
    if input_provenance is None:
        return False
    input_class, input_dispatch = input_provenance
    if type(input_field) is not input_class or not _matches_static_dispatch(
        input_class,
        input_dispatch,
    ):
        return False
    if any(
        _static_descriptor(input_class, state_name) is not _STATIC_ATTRIBUTE_MISSING
        for state_name in ("is_manager", "type")
    ):
        return False
    input_state = object.__getattribute__(input_field, _INSTANCE_DICT_NAME)
    if type(input_state) is not dict:
        return False
    is_manager = _mapping_value_by_identity(input_state, "is_manager")
    if type(is_manager) is not bool:
        return False
    if is_manager is not True:
        return True
    manager_type = cast(
        type[object],
        _mapping_value_by_identity(input_state, "type"),
    )
    if not _canonical_manager_class_state(manager_type):
        return False
    if type(cached_value) is not manager_type:
        return False
    if not _canonical_manager_class_state(cast(type[object], parent_class)):
        return False
    canonical_parent = cast(type[GeneralManager], parent_class)
    descriptor = inspect.getattr_static(canonical_parent, field_name)
    if not _is_canonical_manager_attribute_descriptor(
        descriptor,
        canonical_parent,
        field_name,
    ):
        return False
    if not _post_seeded_manager_state_is_safe(cached_value, manager_type):
        return False
    manager_state = object.__getattribute__(cached_value, _INSTANCE_DICT_NAME)
    private_identification = dict.__getitem__(
        manager_state,
        "_GeneralManager__id",
    )
    if dict.__getitem__(manager_state, "_manager_state_valid") is not True:
        return False
    return formatted_identification is private_identification


@dataclass(frozen=True, slots=True)
class _ClosureCellProvenance:
    cell: CellType
    content: object
    function: _FunctionProvenance | None


@dataclass(frozen=True, slots=True)
class _FunctionProvenance:
    code: CodeType
    closure: tuple[_ClosureCellProvenance, ...]
    defaults: tuple[object, ...] | None
    kwdefaults: dict[str, object] | None
    kwdefault_items: tuple[tuple[object, object], ...]
    annotations: dict[str, object]
    annotation_items: tuple[tuple[object, object], ...]
    attributes: dict[str, object] | None
    attribute_items: tuple[tuple[object, object], ...]


@dataclass(frozen=True, slots=True)
class _AccessorProvenance:
    interface_cls: type["CalculationInterface"]
    field_name: str
    function: _FunctionProvenance


_CALCULATION_INPUT_ACCESSOR_PROVENANCE: WeakKeyDictionary[
    FunctionType, _AccessorProvenance
] = WeakKeyDictionary()
_CALCULATION_INPUT_ACCESSOR_PROVENANCE_LOCK = RLock()


def _function_snapshot(
    function: FunctionType,
    seen: set[int],
    *,
    include_attributes: bool,
) -> _FunctionProvenance:
    seen.add(id(function))
    closure_snapshot: list[_ClosureCellProvenance] = []
    for cell in function.__closure__ or ():
        try:
            content = cell.cell_contents
        except ValueError:
            content = _EMPTY_CLOSURE_CELL
        nested = None
        if type(content) is FunctionType and id(content) not in seen:
            nested = _function_snapshot(content, seen, include_attributes=True)
        closure_snapshot.append(
            _ClosureCellProvenance(cell=cell, content=content, function=nested)
        )
    kwdefaults = function.__kwdefaults__
    annotations = function.__annotations__
    attributes = function.__dict__ if include_attributes else None
    return _FunctionProvenance(
        code=function.__code__,
        closure=tuple(closure_snapshot),
        defaults=function.__defaults__,
        kwdefaults=kwdefaults,
        kwdefault_items=() if kwdefaults is None else tuple(kwdefaults.items()),
        annotations=annotations,
        annotation_items=tuple(annotations.items()),
        attributes=attributes,
        attribute_items=() if attributes is None else tuple(attributes.items()),
    )


def _register_calculation_input_accessor(
    accessor: FunctionType,
    interface_cls: type["CalculationInterface"],
    field_name: str,
) -> None:
    provenance = _AccessorProvenance(
        interface_cls=interface_cls,
        field_name=field_name,
        function=_function_snapshot(accessor, set(), include_attributes=False),
    )
    with _CALCULATION_INPUT_ACCESSOR_PROVENANCE_LOCK:
        _CALCULATION_INPUT_ACCESSOR_PROVENANCE[accessor] = provenance


def _mapping_matches_snapshot(
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
    expected: _FunctionProvenance,
    seen: set[int],
) -> bool:
    function_id = id(function)
    if function_id in seen:
        return True
    seen.add(function_id)
    if (
        function.__code__ is not expected.code
        or function.__defaults__ is not expected.defaults
        or not _mapping_matches_snapshot(
            function.__kwdefaults__, expected.kwdefaults, expected.kwdefault_items
        )
        or not _mapping_matches_snapshot(
            function.__annotations__,
            expected.annotations,
            expected.annotation_items,
        )
        or (
            expected.attributes is not None
            and not _mapping_matches_snapshot(
                function.__dict__,
                expected.attributes,
                expected.attribute_items,
            )
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


def _is_canonical_calculation_input_accessor(
    accessor: object,
    interface_cls: type["CalculationInterface"],
    field_name: str,
) -> bool:
    """Return whether ``accessor`` is the exact callable created for a field."""
    if type(accessor) is not FunctionType:
        return False
    with _CALCULATION_INPUT_ACCESSOR_PROVENANCE_LOCK:
        provenance = _CALCULATION_INPUT_ACCESSOR_PROVENANCE.get(accessor)
    if provenance is None:
        return False
    state = accessor.__dict__
    function = provenance.function
    return (
        provenance.interface_cls is interface_cls
        and provenance.field_name is field_name
        and _function_matches_snapshot(accessor, function, set())
        and len(state) == len(_CALCULATION_INPUT_ACCESSOR_STATE)
        and all(
            current_key is expected_key
            for current_key, expected_key in zip(
                state, _CALCULATION_INPUT_ACCESSOR_STATE, strict=True
            )
        )
        and state["_gm_calculation_input_accessor_token"]
        is _CALCULATION_INPUT_ACCESSOR_TOKEN
        and state["_gm_calculation_input_accessor_self"] is accessor
        and state["_gm_calculation_interface_cls"] is interface_cls
        and state["_gm_calculation_field_name"] is field_name
    )


def _track_cached_manager(value: object) -> None:
    if isinstance(value, GeneralManager):
        value.__class__._track_identification_dependency(value.identification)


class CalculationReadCapability(BaseCapability):
    """Calculations expose inputs only and never persist data."""

    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "CalculationInterface") -> object:
        """Reject persisted-data access for calculation interface instances.

        Parameters:
            interface_instance: Calculation interface instance whose stored data
                was requested. The value is accepted for capability compatibility
                and is not inspected.

        Raises:
            NotImplementedError: Always raised because calculation managers are
                derived from their inputs and do not have backing storage.
        """
        raise NotImplementedError("Calculations do not store data.")

    def get_attribute_types(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> dict[str, dict[str, object]]:
        """Build metadata for each declared calculation input.

        Parameters:
            interface_cls: Calculation interface class whose collected
                ``input_fields`` mapping is inspected.

        Returns:
            Mapping from input name to metadata rows with the exact keys
            ``"type"``, ``"default"``, ``"is_editable"``, ``"is_required"``,
            and ``"is_derived"``. Calculation inputs always report
            ``default=None``, ``is_editable=False``, and ``is_derived=False``;
            ``is_required`` mirrors ``Input.required``.
        """
        return {
            name: {
                "type": field.type,
                "default": None,
                "is_editable": False,
                "is_required": field.required,
                "is_derived": False,
            }
            for name, field in interface_cls.input_fields.items()
        }

    def get_attributes(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> dict[str, Callable[["CalculationInterface"], object]]:
        """Create lazy input accessors for a calculation interface.

        Parameters:
            interface_cls: Calculation interface class whose ``input_fields``
                mapping defines available input names, types, and dependencies.

        Returns:
            Mapping from input name to a callable. Each callable resolves
            declared dependencies first, casts the raw value from the instance's
            ``identification`` mapping, memoizes the cast result on that
            instance, and returns the typed value as ``object``. Missing
            identification keys are passed to ``Input.cast()`` as ``None``;
            required-input enforcement is handled by separate validation, not
            by this accessor.

        Raises:
            KeyError: Propagated from missing dependency names or field names.
            TypeError: Propagated from input casting callbacks with incompatible
                signatures or values.
            ValueError: Propagated from input normalization/parsing failures.
        """

        def _resolve_input_value(
            interface_instance: "CalculationInterface",
            field_name: str,
        ) -> object:
            origin = _seeded_interface_origin(interface_instance)
            return _resolve_input_value_from_origin(
                interface_instance,
                field_name,
                origin,
            )

        def _resolve_input_value_from_origin(
            interface_instance: "CalculationInterface",
            field_name: str,
            origin: _SeededInterfaceOrigin | None,
        ) -> object:
            if origin is None:
                try:
                    resolved_values = interface_instance._resolved_input_values
                except AttributeError:
                    resolved_values = {}
                    interface_instance._resolved_input_values = resolved_values
                input_field = interface_cls.input_fields[field_name]
                cached_value = dict.get(
                    resolved_values,
                    field_name,
                    _MISSING_INTERFACE_STATE,
                )
                if cached_value is not _MISSING_INTERFACE_STATE:
                    _track_cached_manager(cached_value)
                    return cached_value
                dependency_values = {
                    dependency_name: _resolve_input_value(
                        interface_instance,
                        dependency_name,
                    )
                    for dependency_name in input_field.depends_on
                }
                identification = interface_instance.identification
                raw_value = identification.get(field_name)
                cache_context = (interface_cls._parent_class, field_name)
                value = input_field.cast(
                    raw_value,
                    dependency_values,
                    cache_context=cache_context,
                )
                dict.__setitem__(resolved_values, field_name, value)
                return value

            claim, resolved_value = _claim_seeded_input_resolution(
                interface_instance,
                field_name,
                origin,
            )
            if claim is None:
                if resolved_value is _USE_VIRTUAL_RESOLUTION:
                    return _resolve_input_value_from_origin(
                        interface_instance,
                        field_name,
                        None,
                    )
                _track_cached_manager(resolved_value)
                return resolved_value

            try:
                dependency_values = {
                    dependency_name: _resolve_input_value(
                        interface_instance,
                        dependency_name,
                    )
                    for dependency_name in claim.input_field.depends_on
                }
                value = claim.input_field.cast(
                    claim.raw_value,
                    dependency_values,
                    cache_context=claim.cache_context,
                )
            except BaseException:
                _finish_seeded_input_resolution(
                    interface_instance,
                    field_name,
                    claim,
                    _MISSING_INTERFACE_STATE,
                )
                raise
            if _finish_seeded_input_resolution(
                interface_instance,
                field_name,
                claim,
                value,
            ):
                return value
            return _resolve_input_value_from_origin(
                interface_instance,
                field_name,
                None,
            )

        def _claim_seeded_input_resolution(
            interface_instance: "CalculationInterface",
            field_name: str,
            origin: _SeededInterfaceOrigin,
        ) -> tuple[_SeededResolutionClaim | None, object]:
            while True:
                if (
                    _seeded_interface_origin(interface_instance) is not origin
                    or not _seeded_interface_dispatch_is_canonical(interface_instance)
                    or not _exact_string_dict(origin.fields)
                ):
                    if _seeded_interface_origin(interface_instance) is origin:
                        _transition_origin_to_virtual_fallback(
                            interface_instance,
                            origin,
                        )
                    return None, _USE_VIRTUAL_RESOLUTION
                field_origin_value = _mapping_value_by_identity(
                    origin.fields,
                    field_name,
                    _MISSING_INTERFACE_STATE,
                )
                input_fields = _static_class_value(interface_cls, "input_fields")
                input_field_value = (
                    _mapping_value_by_identity(
                        cast(dict[str, object], input_fields),
                        field_name,
                        _MISSING_INTERFACE_STATE,
                    )
                    if _exact_string_dict(input_fields)
                    else _MISSING_INTERFACE_STATE
                )
                if (
                    type(field_origin_value) is not _SeededFieldOrigin
                    or type(input_field_value) is not Input
                ):
                    _transition_origin_to_virtual_fallback(
                        interface_instance,
                        origin,
                    )
                    return None, _USE_VIRTUAL_RESOLUTION
                field_origin = field_origin_value
                input_field = input_field_value
                with field_origin.condition:
                    if (
                        _seeded_interface_origin(interface_instance) is not origin
                        or not _seeded_interface_dispatch_is_canonical(
                            interface_instance
                        )
                        or type(origin.waiting_fields_by_thread) is not dict
                    ):
                        if _seeded_interface_origin(interface_instance) is origin:
                            _transition_origin_to_virtual_fallback(
                                interface_instance,
                                origin,
                            )
                        return None, _USE_VIRTUAL_RESOLUTION
                    if _exact_string_dict(origin.resolved_values):
                        resolved_values = origin.resolved_values
                    else:
                        resolved_values = {}
                        origin.resolved_values = resolved_values
                        _replace_live_resolved_cache_if_safe(
                            interface_instance,
                            resolved_values,
                        )
                    live_raw_available, live_raw_value = _live_seeded_raw_value(
                        interface_instance,
                        field_name,
                    )
                    cached_value = dict.get(
                        resolved_values,
                        field_name,
                        _MISSING_INTERFACE_STATE,
                    )
                    if cached_value is not _MISSING_INTERFACE_STATE:
                        if field_origin.lazy:
                            return None, cached_value
                        parent_class = _static_class_value(
                            interface_cls,
                            "_parent_class",
                        )
                        if (
                            field_origin.manager_ref is not None
                            and field_origin.manager_ref() is cached_value
                            and live_raw_available
                            and live_raw_value is field_origin.formatted_identification
                            and _cached_manager_matches_formatted_identification(
                                parent_class,
                                field_name,
                                input_field,
                                cached_value,
                                field_origin.formatted_identification,
                            )
                        ):
                            return None, cached_value
                    if field_origin.resolving_thread_id is not None:
                        thread_id = get_ident()
                        if field_origin.resolving_thread_id == thread_id:
                            raise RuntimeError(field_name)
                        dict.__setitem__(
                            origin.waiting_fields_by_thread,
                            thread_id,
                            field_name,
                        )
                        if _seeded_wait_chain_has_cycle(origin, thread_id):
                            dict.pop(
                                origin.waiting_fields_by_thread,
                                thread_id,
                                None,
                            )
                            raise RuntimeError(field_name)
                        try:
                            field_origin.condition.wait()
                        finally:
                            dict.pop(
                                origin.waiting_fields_by_thread,
                                thread_id,
                                None,
                            )
                        continue
                    if cached_value is not _MISSING_INTERFACE_STATE:
                        dict.pop(resolved_values, field_name, None)
                    if not field_origin.lazy:
                        field_origin.manager_ref = None
                        field_origin.lazy = True
                        _transition_mirror_field_to_lazy_if_safe(
                            interface_instance,
                            field_name,
                        )
                    thread_id = get_ident()
                    field_origin.resolving_thread_id = thread_id
                    raw_value = (
                        (
                            None
                            if live_raw_value is _MISSING_INTERFACE_STATE
                            else live_raw_value
                        )
                        if live_raw_available
                        else field_origin.formatted_identification
                    )
                    parent_class = _static_class_value(
                        interface_cls,
                        "_parent_class",
                    )
                    cache_context = (
                        (cast(type[object], parent_class), field_name)
                        if _canonical_manager_class_state(
                            cast(type[object], parent_class)
                        )
                        else None
                    )
                    return (
                        _SeededResolutionClaim(
                            origin=origin,
                            field_origin=field_origin,
                            resolved_values=resolved_values,
                            input_field=input_field,
                            raw_value=raw_value,
                            cache_context=cache_context,
                            thread_id=thread_id,
                        ),
                        _MISSING_INTERFACE_STATE,
                    )

        def _finish_seeded_input_resolution(
            interface_instance: "CalculationInterface",
            field_name: str,
            claim: _SeededResolutionClaim,
            value: object,
        ) -> bool:
            published = False
            with claim.field_origin.condition:
                if claim.field_origin.resolving_thread_id == claim.thread_id:
                    if (
                        value is not _MISSING_INTERFACE_STATE
                        and _seeded_interface_origin(interface_instance) is claim.origin
                        and _seeded_interface_dispatch_is_canonical(interface_instance)
                        and claim.origin.resolved_values is claim.resolved_values
                        and _exact_string_dict(claim.resolved_values)
                    ):
                        dict.__setitem__(
                            claim.resolved_values,
                            field_name,
                            value,
                        )
                        published = True
                    claim.field_origin.resolving_thread_id = None
                    dict.pop(
                        claim.origin.waiting_fields_by_thread,
                        claim.thread_id,
                        None,
                    )
                    claim.field_origin.condition.notify_all()
            return published

        def _make_accessor(
            field_name: str,
        ) -> Callable[["CalculationInterface"], object]:
            def _access(interface_instance: "CalculationInterface") -> object:
                return _resolve_input_value(interface_instance, field_name)

            _access.__dict__.update(
                {
                    "_gm_calculation_input_accessor_token": (
                        _CALCULATION_INPUT_ACCESSOR_TOKEN
                    ),
                    "_gm_calculation_input_accessor_self": _access,
                    "_gm_calculation_interface_cls": interface_cls,
                    "_gm_calculation_field_name": field_name,
                }
            )
            _register_calculation_input_accessor(
                cast(FunctionType, _access), interface_cls, field_name
            )
            return _access

        return {
            name: _make_accessor(name) for name in interface_cls.input_fields.keys()
        }

    def get_field_type(
        self,
        interface_cls: type["CalculationInterface"],
        field_name: str,
    ) -> type[object]:
        """Return the declared Python type for one calculation input.

        Parameters:
            interface_cls: Calculation interface class containing
                ``input_fields``.
            field_name: Name of the input field to look up.

        Returns:
            The Python type declared on the input field.

        Raises:
            KeyError: If ``field_name`` is absent from
                ``interface_cls.input_fields``.
        """
        field = interface_cls.input_fields.get(field_name)
        if field is None:
            raise KeyError(field_name)
        return field.type


class CalculationQueryCapability(BaseCapability):
    """Expose CalculationBucket helpers via the generic query capability."""

    name: ClassVar[CapabilityName] = "query"

    def filter(
        self,
        interface_cls: type["CalculationInterface"],
        **kwargs: object,
    ) -> CalculationBucket[GeneralManager]:
        """Create a filtered calculation bucket for an interface.

        Parameters:
            interface_cls: Calculation interface whose parent manager class is
                enumerated.
            **kwargs: Query filter parameters forwarded to
                ``CalculationBucket.filter``.

        Returns:
            Bucket representing calculation input combinations that match the
            provided filters.

        Raises:
            InvalidCalculationInterfaceError: Propagated if the parent manager
                does not use a calculation interface.
            KeyError: Propagated from missing dependency values.
            TypeError: Propagated from invalid filter values, input domains, or
                callback signatures.
            ValueError: Propagated from input parsing, normalization, or filter
                value failures.
        """
        payload_snapshot: dict[str, object] = {"kwargs": dict(kwargs)}

        def _perform() -> CalculationBucket[GeneralManager]:
            return CalculationBucket(
                interface_cls._parent_class,
                filter_definitions=dict(kwargs),
            )

        return call_with_observability(
            interface_cls,
            operation="calculation.query.filter",
            payload=payload_snapshot,
            func=_perform,
        )

    def exclude(
        self,
        interface_cls: type["CalculationInterface"],
        **kwargs: object,
    ) -> CalculationBucket[GeneralManager]:
        """Create a calculation bucket with matching combinations excluded.

        Parameters:
            interface_cls: Calculation interface whose parent manager class is
                enumerated.
            **kwargs: Exclusion criteria forwarded to
                ``CalculationBucket.exclude``.

        Returns:
            Bucket representing calculation input combinations after matching
            combinations are removed.

        Raises:
            InvalidCalculationInterfaceError: Propagated if the parent manager
                does not use a calculation interface.
            KeyError: Propagated from missing dependency values.
            TypeError: Propagated from invalid exclusion values, input domains,
                or callback signatures.
            ValueError: Propagated from input parsing, normalization, or filter
                value failures.
        """
        payload_snapshot: dict[str, object] = {"kwargs": dict(kwargs)}

        def _perform() -> CalculationBucket[GeneralManager]:
            return CalculationBucket(
                interface_cls._parent_class,
                exclude_definitions=dict(kwargs),
            )

        return call_with_observability(
            interface_cls,
            operation="calculation.query.exclude",
            payload=payload_snapshot,
            func=_perform,
        )

    def all(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> CalculationBucket[GeneralManager]:
        """Create a bucket for all calculation input combinations.

        Parameters:
            interface_cls: Calculation interface whose parent manager class is
                enumerated.

        Returns:
            Bucket representing every possible calculation input combination.

        Raises:
            InvalidCalculationInterfaceError: Propagated if the parent manager
                does not use a calculation interface.
            KeyError: Propagated from missing dependency values.
            TypeError: Propagated from invalid input domain definitions or
                callback signatures.
            ValueError: Propagated from input parsing or normalization failures.
        """
        payload_snapshot: dict[str, object] = {}

        def _perform() -> CalculationBucket[GeneralManager]:
            return CalculationBucket(interface_cls._parent_class)

        return call_with_observability(
            interface_cls,
            operation="calculation.query.all",
            payload=payload_snapshot,
            func=_perform,
        )


class CalculationLifecycleCapability(BaseCapability):
    """Manage calculation interface pre/post creation hooks."""

    name: ClassVar[CapabilityName] = "calculation_lifecycle"

    def pre_create(
        self,
        *,
        name: str,
        attrs: dict[str, object],
        interface: type["CalculationInterface"],
    ) -> tuple[dict[str, object], type["CalculationInterface"], None]:
        """Collect calculation inputs and attach the generated interface class.

        Parameters:
            name: Declared manager class name, used only for observability
                payloads.
            attrs: Mutable manager-class attribute mapping. This method sets
                ``"_interface_type"`` and ``"Interface"``.
            interface: User-declared calculation interface class. Only its own
                ``Input`` class attributes are collected; inherited descriptors
                and pre-existing ``input_fields`` values are not merged.

        Returns:
            Tuple of updated attrs, generated calculation interface subclass,
            and ``None`` for lifecycle hook compatibility.
        """
        payload_snapshot: dict[str, object] = {
            "interface": interface.__name__,
            "name": name,
        }

        def _perform() -> tuple[dict[str, object], type["CalculationInterface"], None]:
            input_fields: dict[str, Input[type[object]]] = {}
            for key, value in vars(interface).items():
                if key.startswith("__"):
                    continue
                if isinstance(value, Input):
                    input_fields[key] = value

            attrs["_interface_type"] = interface._interface_type
            interface_cls = type(
                interface.__name__,
                (interface,),
                {
                    "input_fields": input_fields,
                    _MANAGER_INPUT_SEED_PLAN_NAME: (
                        _calculation_manager_input_seed_plan(input_fields)
                    ),
                },
            )
            attrs["Interface"] = interface_cls
            return attrs, interface_cls, None

        return call_with_observability(
            interface,
            operation="calculation.pre_create",
            payload=payload_snapshot,
            func=_perform,
        )

    def post_create(
        self,
        *,
        new_class: type[GeneralManager],
        interface_class: type["CalculationInterface"],
        model: None = None,
    ) -> None:
        """Attach the concrete manager class to the generated interface.

        Parameters:
            new_class: Concrete manager class just created.
            interface_class: Generated calculation interface class whose
                ``_parent_class`` backlink will be updated.
            model: Reserved for lifecycle compatibility and ignored.
        """
        payload_snapshot: dict[str, object] = {"interface": interface_class.__name__}

        def _perform() -> None:
            interface_class._parent_class = new_class

        call_with_observability(
            interface_class,
            operation="calculation.post_create",
            payload=payload_snapshot,
            func=_perform,
        )


_register_calculation_capability_seed_provenance(
    CalculationLifecycleCapability,
    CalculationReadCapability,
)
