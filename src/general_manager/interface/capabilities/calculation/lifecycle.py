"""Capabilities tailored for calculation interfaces."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, ClassVar

from general_manager.bucket.calculation_bucket import CalculationBucket
from general_manager.manager.input import Input

from ..base import CapabilityName
from ..builtin import BaseCapability
from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.interfaces.calculation import (
        CalculationInterface,
    )


class CalculationReadCapability(BaseCapability):
    """Calculations expose inputs only and never persist data."""

    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "CalculationInterface") -> Any:
        """
        Indicates that calculation interfaces do not persist instance data and this operation is unsupported.

        Raises:
            NotImplementedError: Always raised with the message "Calculations do not store data."
        """
        raise NotImplementedError("Calculations do not store data.")

    def get_attribute_types(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> dict[str, dict[str, Any]]:
        """
        Builds a mapping of input field metadata for a calculation interface.

        Parameters:
            interface_cls (type): CalculationInterface subclass whose `input_fields` mapping will be inspected.

        Returns:
            dict[str, dict[str, Any]]: A dictionary where each key is an input field name and each value is a metadata dictionary with:
                - "type": the field's declared Python type,
                - "default": None,
                - "is_editable": False,
                - "is_required": True,
                - "is_derived": False
        """
        return {
            name: {
                "type": field.type,
                "default": None,
                "is_editable": False,
                "is_required": True,
                "is_derived": False,
            }
            for name, field in interface_cls.input_fields.items()
        }

    def get_attributes(
        self,
        interface_cls: type["CalculationInterface"],
    ) -> dict[str, Any]:
        """
        Provide attribute accessors for each input field of the calculation interface.

        Parameters:
            interface_cls (type[CalculationInterface]): Calculation interface class whose `input_fields` mapping defines available input names and types.

        Returns:
            dict[str, Callable[[Any], Any]]: Mapping from each input field name to a callable. Each callable takes an interface instance (`self`) and returns the value from `self.identification` for that field cast using the field's `cast` method.
        """
        return {
            name: lambda self, name=name: interface_cls.input_fields[name].cast(
                self.identification.get(name)
            )
            for name in interface_cls.input_fields.keys()
        }

    def get_field_type(
        self,
        interface_cls: type["CalculationInterface"],
        field_name: str,
    ) -> type:
        """
        Retrieve the declared Python type for a named input field on the calculation interface.

        Parameters:
            interface_cls (type[CalculationInterface]): Calculation interface class containing `input_fields`.
            field_name (str): Name of the input field to look up.

        Returns:
            type: The Python type declared for the input field.

        Raises:
            KeyError: If `field_name` is not present in `interface_cls.input_fields`.
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
        **kwargs: Any,
    ) -> CalculationBucket:
        """
        Create a filtered CalculationBucket for the given calculation interface using the provided query criteria.

        Parameters:
            interface_cls (type[CalculationInterface]): Calculation interface whose underlying parent class will be queried.
            **kwargs: Query filter parameters forwarded to CalculationBucket.filter.

        Returns:
            CalculationBucket: A bucket representing the filtered set of calculations.
        """
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> CalculationBucket:
            """
            Execute the filter operation against the CalculationBucket for the interface's parent class.

            Returns:
                CalculationBucket: A bucket containing calculations matching the filter criteria provided to the enclosing method.
            """
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
        **kwargs: Any,
    ) -> CalculationBucket:
        """
        Execute an exclusion query against the calculation interface's bucket and record the operation for observability.

        Parameters:
            interface_cls (type[CalculationInterface]): The calculation interface class whose parent model is used to construct the CalculationBucket.
            **kwargs: Filter criteria forwarded to the bucket's `exclude` method.

        Returns:
            CalculationBucket: A bucket representing the query with the specified exclusions applied.
        """
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> CalculationBucket:
            """
            Create a CalculationBucket for the interface's parent class and apply exclusion filters from the surrounding scope.

            Returns:
                CalculationBucket: Bucket with items excluded according to the provided keyword arguments.
            """
            return CalculationBucket(interface_cls._parent_class).exclude(**kwargs)

        return call_with_observability(
            interface_cls,
            operation="calculation.query.exclude",
            payload=payload_snapshot,
            func=_perform,
        )

    def all(self, interface_cls: type["CalculationInterface"]) -> CalculationBucket:
        """
        Retrieve all calculation instances for the specified calculation interface.

        Parameters:
            interface_cls (type[CalculationInterface]): The calculation interface class whose calculations should be returned.

        Returns:
            CalculationBucket: A bucket containing all calculation instances for the given interface.
        """
        payload_snapshot: dict[str, Any] = {}

        def _perform() -> CalculationBucket:
            """
            Get a CalculationBucket containing all calculation instances for the interface's parent class.

            Returns:
                CalculationBucket: a bucket representing all calculations for the interface's parent class.
            """
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
        attrs: dict[str, Any],
        interface: type["CalculationInterface"],
    ) -> tuple[dict[str, Any], type["CalculationInterface"], None]:
        """
        Builds and attaches a specialized CalculationInterface subclass and updates class attributes for creation.

        Parameters:
            name (str): The declared name for the new interface class.
            attrs (dict[str, Any]): The attribute mapping that will be updated with interface metadata; this function sets "_interface_type" and "Interface".
            interface (type[CalculationInterface]): The base calculation interface class used to collect Input-declared fields.

        Returns:
            tuple[dict[str, Any], type[CalculationInterface], None]: A tuple containing the possibly modified `attrs` dict, the newly created interface subclass with an `input_fields` mapping, and `None`.
        """
        payload_snapshot = {
            "interface": interface.__name__,
            "name": name,
        }

        def _perform() -> tuple[dict[str, Any], type["CalculationInterface"], None]:
            """
            Collect input fields from the given interface, create a new interface subclass containing those inputs, attach it to attrs, and return the updated metadata.

            The function scans attributes on `interface` for instances of `Input` and builds an `input_fields` mapping. It sets `attrs["_interface_type"]` from `interface._interface_type`, creates a new subclass named after `interface` with an `input_fields` attribute, assigns that subclass to `attrs["Interface"]`, and returns the updated values.

            Returns:
                tuple:
                    - attrs (dict[str, Any]): The input `attrs` dictionary updated with `_interface_type` and `Interface`.
                    - interface_cls (type[CalculationInterface]): Newly created subclass of the provided `interface` containing the `input_fields` mapping.
                    - None: Placeholder to match expected return signature.
            """
            input_fields: dict[str, Input[Any]] = {}
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
        new_class: type,
        interface_class: type["CalculationInterface"],
        model: None = None,
    ) -> None:
        """
        Attach the created class as the parent implementation for a calculation interface.

        Sets the interface_class's _parent_class attribute to new_class and records the operation for observability.

        Parameters:
            new_class (type): The concrete class just created for the interface.
            interface_class (type[CalculationInterface]): The interface class whose parent link will be updated.
            model (None): Reserved for compatibility with lifecycle hooks; not used.
        """
        payload_snapshot = {"interface": interface_class.__name__}

        def _perform() -> None:
            """
            Attach the newly created class as the interface's parent.

            This mutates the provided interface_class by assigning its `_parent_class` attribute to `new_class`.
            """
            interface_class._parent_class = new_class  # type: ignore[attr-defined]

        return call_with_observability(
            interface_class,
            operation="calculation.post_create",
            payload=payload_snapshot,
            func=_perform,
        )
