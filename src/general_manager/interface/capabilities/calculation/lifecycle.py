"""Capabilities tailored for calculation interfaces."""

from __future__ import annotations

from collections.abc import Callable
from types import FunctionType
from typing import TYPE_CHECKING, ClassVar

from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input

from ..base import CapabilityName
from ..builtin import BaseCapability
from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.calculation import (
        CalculationInterface,
    )


_CALCULATION_INPUT_ACCESSOR_TOKEN = object()
_CALCULATION_INPUT_ACCESSOR_STATE = frozenset(
    {
        "_gm_calculation_input_accessor_token",
        "_gm_calculation_interface_cls",
        "_gm_calculation_field_name",
    }
)


def _is_canonical_calculation_input_accessor(
    accessor: object,
    interface_cls: type["CalculationInterface"],
    field_name: str,
) -> bool:
    """Return whether ``accessor`` is the exact callable created for a field."""
    if type(accessor) is not FunctionType:
        return False
    state = accessor.__dict__
    return (
        state.keys() == _CALCULATION_INPUT_ACCESSOR_STATE
        and state["_gm_calculation_input_accessor_token"]
        is _CALCULATION_INPUT_ACCESSOR_TOKEN
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
            try:
                resolved_values = interface_instance._resolved_input_values
            except AttributeError:
                resolved_values = {}
                interface_instance._resolved_input_values = resolved_values
            if field_name in resolved_values:
                cached_value = resolved_values[field_name]
                _track_cached_manager(cached_value)
                return cached_value

            input_field = interface_cls.input_fields[field_name]
            dependency_values = {
                dependency_name: _resolve_input_value(
                    interface_instance,
                    dependency_name,
                )
                for dependency_name in input_field.depends_on
            }
            value = input_field.cast(
                interface_instance.identification.get(field_name),
                dependency_values,
                cache_context=(interface_cls._parent_class, field_name),
            )
            resolved_values[field_name] = value
            return value

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
                    "_gm_calculation_interface_cls": interface_cls,
                    "_gm_calculation_field_name": field_name,
                }
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
            return CalculationBucket(interface_cls._parent_class).filter(**kwargs)

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
            return CalculationBucket(interface_cls._parent_class).exclude(**kwargs)

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
            return CalculationBucket(interface_cls._parent_class).all()

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
                {"input_fields": input_fields},
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
