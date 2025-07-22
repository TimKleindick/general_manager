from __future__ import annotations

from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.expressions import Col
from decimal import Decimal
import pint
from general_manager.measurement.measurement import Measurement, ureg, currency_units


class MeasurementField(models.Field):
    description = "Stores a measurement (value + unit) but exposes a single field API"

    empty_values = (None,)  # nur None zählt als leer

    def __init__(
        self, base_unit: str, null=False, blank=False, editable=True, *args, **kwargs
    ):
        self.base_unit = base_unit
        self.base_dimension = ureg.parse_expression(self.base_unit).dimensionality

        nb = {}
        if null:
            nb["null"] = True
        if blank:
            nb["blank"] = True

        self.editable = editable
        self.value_field = models.DecimalField(
            max_digits=30, decimal_places=10, db_index=True, editable=editable, **nb
        )
        self.unit_field = models.CharField(max_length=30, editable=editable, **nb)

        super().__init__(null=null, blank=blank, editable=editable, *args, **kwargs)

    def contribute_to_class(self, cls, name, private_only=False, **kwargs):
        # Register myself first (so opts.get_field('height') works)
        super().contribute_to_class(cls, name, private_only=private_only, **kwargs)
        self.concrete = False
        self.column = None  # type: ignore # will not be set in db
        self.field = self

        self.value_attr = f"{name}_value"
        self.unit_attr = f"{name}_unit"

        # prevent duplicate attributes
        if hasattr(cls, self.value_attr):
            self.value_field = getattr(cls, self.value_attr).field
        else:
            self.value_field.set_attributes_from_name(self.value_attr)
            self.value_field.contribute_to_class(cls, self.value_attr)

        if hasattr(cls, self.unit_attr):
            self.unit_field = getattr(cls, self.unit_attr).field
        else:
            self.unit_field.set_attributes_from_name(self.unit_attr)
            self.unit_field.contribute_to_class(cls, self.unit_attr)

        # Descriptor override
        setattr(cls, name, self)

    # ---- ORM Delegation ----
    def get_col(self, alias, output_field=None):
        return Col(alias, self.value_field, output_field or self.value_field)  # type: ignore

    def get_lookup(self, lookup_name):
        return self.value_field.get_lookup(lookup_name)

    def get_transform(self, lookup_name) -> models.Transform | None:
        return self.value_field.get_transform(lookup_name)

    def db_type(self, connection) -> None:  # type: ignore
        """
        Returns the database type for this field.
        MeasurementField does not have a direct database representation,
        so it returns None.
        """
        return None

    def run_validators(self, value: Measurement | None) -> None:
        if value is None:
            return
        for v in self.validators:
            v(value)

    def clean(
        self, value: Measurement | None, model_instance: models.Model | None = None
    ) -> Measurement | None:
        self.validate(value, model_instance)
        self.run_validators(value)
        return value

    def to_python(self, value):
        return value

    def get_prep_value(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = Measurement.from_string(value)
        if isinstance(value, Measurement):
            try:
                return Decimal(str(value.quantity.to(self.base_unit).magnitude))
            except pint.errors.DimensionalityError:
                raise ValidationError(
                    {self.name: [f"Inkompatible Einheit zu '{self.base_unit}'."]}
                )
        raise ValidationError(
            {self.name: ["Value must be a Measurement instance or None."]}
        )

    # ------------ Descriptor ------------
    def __get__(  # type: ignore
        self, instance: models.Model | None, owner: None = None
    ) -> MeasurementField | Measurement | None:
        if instance is None:
            return self
        val = getattr(instance, self.value_attr)
        unit = getattr(instance, self.unit_attr)
        if val is None or unit is None:
            return None
        qty_base = Decimal(val) * ureg(self.base_unit)
        try:
            qty_orig = qty_base.to(unit)
        except pint.errors.DimensionalityError:
            qty_orig = qty_base
        return Measurement(qty_orig.magnitude, str(qty_orig.units))

    def __set__(self, instance, value):
        if not self.editable:
            raise ValidationError(f"{self.name} is not editable.")
        if value is None:
            setattr(instance, self.value_attr, None)
            setattr(instance, self.unit_attr, None)
            return
        if isinstance(value, str):
            try:
                value = Measurement.from_string(value)
            except ValueError:
                raise ValidationError(
                    {self.name: ["Value must be a Measurement instance or None."]}
                )
        if not isinstance(value, Measurement):
            raise ValidationError(
                {self.name: ["Value must be a Measurement instance or None."]}
            )

        if str(self.base_unit) in currency_units:
            if not value.is_currency():
                raise ValidationError(
                    {
                        self.name: [
                            f"Unit must be a currency ({', '.join(currency_units)})."
                        ]
                    }
                )
        else:
            if value.is_currency():
                raise ValidationError({self.name: ["Unit cannot be a currency."]})
            if value.quantity.dimensionality != self.base_dimension:
                raise ValidationError(
                    {self.name: [f"Unit must be compatible with '{self.base_unit}'."]}
                )

        try:
            base_mag = value.quantity.to(self.base_unit).magnitude
        except pint.errors.DimensionalityError:
            raise ValidationError(
                {self.name: [f"Unit must be compatible with '{self.base_unit}'."]}
            )

        setattr(instance, self.value_attr, Decimal(str(base_mag)))
        setattr(instance, self.unit_attr, str(value.quantity.units))

    def validate(
        self, value: Measurement | None, model_instance: models.Model | None = None
    ) -> None:
        if value is None:
            if not self.null:
                raise ValidationError(self.error_messages["null"], code="null")
            return
        if value in ("", [], (), {}):
            if not self.blank:
                raise ValidationError(self.error_messages["blank"], code="blank")
            return

        for validator in self.validators:
            validator(value)
