# fields.py
from __future__ import annotations
from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal
from general_manager.measurement.measurement import Measurement, ureg, currency_units
import pint
from typing import Any


class MeasurementField(models.Field):  # type: ignore
    description = (
        "A field that stores a measurement value, both in base unit and original unit"
    )

    def __init__(
        self,
        base_unit: str,
        null: bool = False,
        blank: bool = False,
        editable: bool = True,
        *args: list[Any],
        **kwargs: dict[str, Any],
    ):
        self.base_unit = base_unit  # E.g., 'meter' for length units
        # Determine the dimensionality of the base unit
        self.base_dimension = ureg.parse_expression(self.base_unit).dimensionality
        # Internal fields
        null_blank_kwargs = {}
        if null is True:
            null_blank_kwargs["null"] = True
        if blank is True:
            null_blank_kwargs["blank"] = True
        self.editable = editable
        self.value_field: models.DecimalField[Decimal] = models.DecimalField(
            max_digits=30,
            decimal_places=10,
            db_index=True,
            **null_blank_kwargs,
            editable=editable,
        )
        self.unit_field: models.CharField[str] = models.CharField(
            max_length=30, **null_blank_kwargs, editable=editable
        )
        super().__init__(null=null, blank=blank, *args, **kwargs)

    def contribute_to_class(
        self, cls: type, name: str, private_only: bool = False, **kwargs: dict[str, Any]
    ) -> None:
        self.name = name
        self.value_attr = f"{name}_value"
        self.unit_attr = f"{name}_unit"
        self.value_field.attname = self.value_attr
        self.unit_field.attname = self.unit_attr
        self.value_field.name = self.value_attr
        self.unit_field.name = self.unit_attr
        self.value_field.column = self.value_attr
        self.unit_field.column = self.unit_attr

        self.value_field.model = cls
        self.unit_field.model = cls

        self.value_field.contribute_to_class(cls, self.value_attr)
        self.unit_field.contribute_to_class(cls, self.unit_attr)

        setattr(cls, self.name, self)

    def __get__(self, instance: Any, owner: Any) -> Any:
        if instance is None:
            return self
        value = getattr(instance, self.value_attr)
        unit = getattr(instance, self.unit_attr)
        if value is None or unit is None:
            return None
        # Create a Measurement object with the value in the original unit
        quantity_in_base_unit = Decimal(value) * ureg(self.base_unit)
        # Convert back to the original unit
        try:
            quantity_in_original_unit: pint.Quantity = quantity_in_base_unit.to(unit)  # type: ignore
        except pint.errors.DimensionalityError:
            # If the unit is not compatible, return the value in base unit
            quantity_in_original_unit = quantity_in_base_unit
        return Measurement(
            quantity_in_original_unit.magnitude, str(quantity_in_original_unit.units)
        )

    def __set__(self, instance: Any, value: Any) -> None:
        if self.editable is False:
            raise ValidationError(f"{self.name} is not editable.")
        if value is None:
            setattr(instance, self.value_attr, None)
            setattr(instance, self.unit_attr, None)
            return
        elif isinstance(value, str):
            try:
                value = Measurement.from_string(value)
            except ValueError:
                raise ValidationError(
                    {self.name: ["Value must be a Measurement instance or None."]}
                )
        if isinstance(value, Measurement):
            if str(self.base_unit) in currency_units:
                # Base unit is a currency
                if not value.is_currency():
                    raise ValidationError(
                        {
                            self.name: [
                                f"The unit must be a currency ({', '.join(currency_units)})."
                            ]
                        }
                    )
            else:
                # Physical unit
                if value.is_currency():
                    raise ValidationError(
                        {self.name: ["The unit cannot be a currency."]}
                    )
                elif value.quantity.dimensionality != self.base_dimension:
                    raise ValidationError(
                        {
                            self.name: [
                                f"The unit must be compatible with '{self.base_unit}'."
                            ]
                        }
                    )
            # Store the value in the base unit
            try:
                value_in_base_unit: Any = value.quantity.to(self.base_unit).magnitude  # type: ignore
            except pint.errors.DimensionalityError:
                raise ValidationError(
                    {
                        self.name: [
                            f"The unit must be compatible with '{self.base_unit}'."
                        ]
                    }
                )
            setattr(instance, self.value_attr, Decimal(str(value_in_base_unit)))
            # Store the original unit
            setattr(instance, self.unit_attr, str(value.quantity.units))
        else:
            raise ValidationError(
                {self.name: ["Value must be a Measurement instance or None."]}
            )

    def get_prep_value(self, value: Any) -> Any:
        # Not needed since we use internal fields
        pass

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["base_unit"] = self.base_unit
        return name, path, args, kwargs
