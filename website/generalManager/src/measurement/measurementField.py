# fields.py

from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal
from generalManager.src.measurement.units import Measurement, ureg, currency_units
import pint


class MeasurementField(models.Field):
    description = "A field that stores a measurement value, both in base unit and original unit"

    def __init__(self, base_unit, *args, **kwargs):
        null = kwargs.get('null', False)
        self.base_unit = base_unit  # E.g., 'meter' for length units
        # Determine the dimensionality of the base unit
        self.base_dimension = ureg.parse_expression(
            self.base_unit).dimensionality
        # Internal fields
        self.value_field = models.DecimalField(
            max_digits=30, decimal_places=10, db_index=True, null=null)
        self.unit_field = models.CharField(max_length=30, null=null)
        super().__init__(*args, **kwargs)

    def contribute_to_class(self, cls, name, **kwargs):
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

        cls._meta.add_field(self.value_field)
        cls._meta.add_field(self.unit_field)
        setattr(cls, self.name, self)

    def __get__(self, instance, owner):
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
            quantity_in_original_unit = quantity_in_base_unit.to(unit)
        except pint.errors.DimensionalityError:
            # If the unit is not compatible, return the value in base unit
            quantity_in_original_unit = quantity_in_base_unit
        return Measurement(quantity_in_original_unit.magnitude, str(quantity_in_original_unit.units))

    def __set__(self, instance, value):
        if value is None:
            setattr(instance, self.value_attr, None)
            setattr(instance, self.unit_attr, None)
        elif isinstance(value, Measurement):
            if str(self.base_unit) in currency_units:
                # Base unit is a currency
                if not value.is_currency():
                    raise ValidationError(f"The unit must be a currency ({
                                          ', '.join(currency_units)}).")
            else:
                # Physical unit
                if value.is_currency():
                    raise ValidationError("The unit cannot be a currency.")
                elif value.quantity.dimensionality != self.base_dimension:
                    raise ValidationError(
                        f"The unit must be compatible with '{self.base_unit}'.")
            # Store the value in the base unit
            try:
                value_in_base_unit = value.quantity.to(
                    self.base_unit).magnitude
            except pint.errors.DimensionalityError:
                raise ValidationError(
                    f"The unit must be compatible with '{self.base_unit}'.")
            setattr(instance, self.value_attr,
                    Decimal(str(value_in_base_unit)))
            # Store the original unit
            setattr(instance, self.unit_attr, str(value.quantity.units))
        else:
            raise ValueError("Value must be a Measurement instance or None.")

    def get_prep_value(self, value):
        # Not needed since we use internal fields
        pass

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['base_unit'] = self.base_unit
        return name, path, args, kwargs
