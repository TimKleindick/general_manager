"""Custom Django model field storing values as unit-aware measurements."""

from __future__ import annotations

from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.expressions import Col
from decimal import Decimal
import pint
from general_manager.measurement.measurement import Measurement, ureg, currency_units
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import Lookup, Transform
from typing import Any, cast


class MeasurementFieldNotEditableError(ValidationError):
    """Raised when attempting to modify a non-editable MeasurementField."""

    def __init__(self, field_name: str) -> None:
        """
        Initialize the exception indicating an attempt to assign to a non-editable measurement field.

        Parameters:
            field_name (str): Name of the field that was attempted to be modified; used to compose the error message.
        """
        super().__init__(f"{field_name} is not editable.")


class MeasurementField(models.Field):
    description = "Stores a measurement (value + unit) but exposes a single field API"

    empty_values: tuple[object, ...] = (None, "", [], (), {})

    def __init__(
        self,
        base_unit: str,
        *args: Any,
        null: bool = False,
        blank: bool = False,
        editable: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Configure a measurement field backed by separate value and unit columns.

        Parameters:
            base_unit (str): Canonical unit used to normalise stored measurements.
            *args: Positional arguments forwarded to the base Field implementation.
            null (bool): If True, the measurement may be stored as NULL in the database.
            blank (bool): If True, forms may accept an empty value for this field.
            editable (bool): If False, assignments through the model API are rejected.
            **kwargs: Additional keyword arguments forwarded to the base Field implementation.
        """
        self.base_unit = base_unit
        self.base_dimension = ureg.parse_expression(self.base_unit).dimensionality

        self.editable = editable
        self.value_field: models.Field[Any, Any]
        self.unit_field: models.Field[Any, Any]
        if null:
            self.value_field = models.DecimalField(
                max_digits=30,
                decimal_places=10,
                db_index=True,
                editable=editable,
                null=True,
                blank=blank,
            )
            self.unit_field = models.CharField(
                max_length=30,
                editable=editable,
                null=True,
                blank=blank,
            )
        else:
            self.value_field = models.DecimalField(
                max_digits=30,
                decimal_places=10,
                db_index=True,
                editable=editable,
                null=False,
                blank=blank,
            )
            self.unit_field = models.CharField(
                max_length=30,
                editable=editable,
                null=False,
                blank=blank,
            )

        options: dict[str, Any] = {
            **kwargs,
            "null": null,
            "blank": blank,
            "editable": editable,
        }
        super().__init__(*args, **options)

    def contribute_to_class(
        self,
        cls: type[models.Model],
        name: str,
        private_only: bool = False,
        **kwargs: object,
    ) -> None:
        """
        Attach the measurement field and its backing value and unit fields to the model and install the descriptor.

        Parameters:
            cls: Model class receiving the field.
            name: Attribute name to use on the model for this field.
            private_only: Whether the field should be treated as private.
            kwargs: Additional options forwarded to the base implementation.
        """
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
    def get_col(
        self,
        alias: str,
        output_field: models.Field[object, object] | None = None,
    ) -> Col:
        """
        Produce a column expression referencing the underlying value field.

        Parameters:
            alias (str): Table alias used within the query.
            output_field (models.Field | None): Optional output field override.

        Returns:
            Col: ORM expression targeting the numeric component.
        """
        return Col(alias, self.value_field, output_field or self.value_field)  # type: ignore

    def get_lookup(self, lookup_name: str) -> type[Lookup]:
        """
        Retrieve a lookup class from the underlying decimal field.

        Parameters:
            lookup_name (str): Name of the lookup to resolve.

        Returns:
            type[models.Lookup]: Lookup class implementing the requested comparison.
        """
        return cast(type[Lookup], self.value_field.get_lookup(lookup_name))

    def get_transform(
        self,
        lookup_name: str,
    ) -> type[Transform] | None:
        """
        Return a transform callable provided by the underlying decimal field.

        Parameters:
            lookup_name (str): Name of the transform to resolve.

        Returns:
            models.Transform | None: Transform class when available; otherwise None.
        """
        transform = self.value_field.get_transform(lookup_name)
        return cast(type[Transform] | None, transform)

    def db_type(self, connection: BaseDatabaseWrapper) -> None:  # type: ignore[override]
        """
        Signal to Django that the field does not map to a single column.

        Parameters:
            connection (BaseDatabaseWrapper): Database connection used for schema generation.

        Returns:
            None
        """
        return None

    def run_validators(self, value: Measurement | None) -> None:
        """
        Execute all configured validators when a measurement is provided.

        Parameters:
            value (Measurement | None): Measurement instance that should satisfy field validators.

        Returns:
            None
        """
        if value is None:
            return
        for v in self.validators:
            v(value)

    def clean(
        self, value: Measurement | None, model_instance: models.Model | None = None
    ) -> Measurement | None:
        """
        Validate a measurement value before it is saved to the model.

        Parameters:
            value (Measurement | None): Measurement provided by forms or assignment.
            model_instance (models.Model | None): Instance associated with the field, when available.

        Returns:
            Measurement | None: The validated measurement value, or None if the input was None.

        Raises:
            ValidationError: If validation fails due to null/blank constraints or validator errors.
        """
        self.validate(value, model_instance)
        self.run_validators(value)
        return value

    def to_python(self, value: Measurement | str | None) -> Measurement | str | None:
        """
        Convert database values back into Python objects.

        Parameters:
            value (Any): Value retrieved from the database.

        Returns:
            Any: Original value without modification.
        """
        return value

    def get_prep_value(self, value: Measurement | str | None) -> Decimal | None:
        """
        Serialise a measurement for storage by converting it to the base unit magnitude.

        Parameters:
            value (Measurement | str | None): Value provided by the model or form.

        Returns:
            Decimal | None: Decimal magnitude in the base unit, or None when no value is supplied.

        Raises:
            ValidationError: If the value cannot be interpreted as a compatible measurement.
        """
        if value is None:
            return None
        if isinstance(value, str):
            value = Measurement.from_string(value)
        if isinstance(value, Measurement):
            try:
                return Decimal(str(value.quantity.to(self.base_unit).magnitude))
            except pint.errors.DimensionalityError as e:
                raise ValidationError(
                    {self.name: [f"Unit must be compatible with '{self.base_unit}'."]}
                ) from e
        raise ValidationError(
            {self.name: ["Value must be a Measurement instance or None."]}
        )

    # ------------ Descriptor ------------
    def __get__(  # type: ignore
        self, instance: models.Model | None, owner: None = None
    ) -> MeasurementField | Measurement | None:
        """
        Resolve the field value on an instance, reconstructing the measurement when possible.

        Parameters:
            instance (models.Model | None): Model instance owning the field, or None when accessed on the class.
            owner (type[models.Model] | None): Model class owning the descriptor.

        Returns:
            MeasurementField | Measurement | None: Descriptor when accessed on the class, reconstructed measurement for instances, or None when incomplete.
        """
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

    def __set__(
        self,
        instance: models.Model,
        value: Measurement | str | None,
    ) -> None:
        """
        Set a measurement on a model instance after validating editability, type, and unit compatibility.

        Parameters:
            instance (models.Model): Model instance receiving the value.
            value (Measurement | str | None): A Measurement, a string parseable to a Measurement, or None to clear the field.

        Raises:
            MeasurementFieldNotEditableError: If the field is not editable.
            ValidationError: If the value is not a Measurement (or valid parseable string), if currency unit rules are violated, or if the unit is incompatible with the field's base unit.
        """
        if not self.editable:
            raise MeasurementFieldNotEditableError(self.name)
        if value is None:
            setattr(instance, self.value_attr, None)
            setattr(instance, self.unit_attr, None)
            return
        if isinstance(value, str):
            try:
                value = Measurement.from_string(value)
            except ValueError as e:
                raise ValidationError(
                    {self.name: ["Value must be a Measurement instance or None."]}
                ) from e
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

        try:
            base_mag = value.quantity.to(self.base_unit).magnitude
        except pint.errors.DimensionalityError as e:
            raise ValidationError(
                {self.name: [f"Unit must be compatible with '{self.base_unit}'."]}
            ) from e

        setattr(instance, self.value_attr, Decimal(str(base_mag)))
        setattr(instance, self.unit_attr, str(value.quantity.units))

    def validate(
        self, value: Measurement | None, model_instance: models.Model | None = None
    ) -> None:
        """
        Enforce null/blank constraints and run validators on the provided value.

        Parameters:
            value (Measurement | None): Measurement value under validation.
            model_instance (models.Model | None): Instance owning the field; unused but provided for API compatibility.

        Returns:
            None

        Raises:
            ValidationError: If the value violates constraint or validator requirements.
        """
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
