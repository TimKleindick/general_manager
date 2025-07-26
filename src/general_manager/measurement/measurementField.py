from __future__ import annotations

from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.expressions import Col
from decimal import Decimal
import pint
from general_manager.measurement.measurement import Measurement, ureg, currency_units


class MeasurementField(models.Field):
    description = "Stores a measurement (value + unit) but exposes a single field API"

    empty_values = (None, "", [], (), {})

    def __init__(
        self, base_unit: str, *args, null=False, blank=False, editable=True, **kwargs
    ):
        """
        Initialize a MeasurementField to store a numeric value and its unit with unit-aware validation.

        Parameters:
            base_unit (str): The canonical unit for the measurement, used for conversions and validation.
            null (bool, optional): Whether the field allows NULL values. Defaults to False.
            blank (bool, optional): Whether the field allows blank values. Defaults to False.
            editable (bool, optional): Whether the field is editable in forms and admin. Defaults to True.

        The field internally manages a DecimalField for the value and a CharField for the unit, both configured according to the provided options.
        """
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

        super().__init__(*args, null=null, blank=blank, editable=editable, **kwargs)

    def contribute_to_class(self, cls, name, private_only=False, **kwargs):
        # Register myself first (so opts.get_field('height') works)
        """
        Registers the MeasurementField with the model class and attaches internal value and unit fields.

        This method sets up the composite field by creating and adding separate fields for the numeric value and unit to the model class, ensuring they are not duplicated. It also overrides the model attribute with the MeasurementField descriptor itself to manage access and assignment.
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
    def get_col(self, alias, output_field=None):
        """
        Returns a Django ORM column expression for the internal value field, enabling queries on the numeric part of the measurement.
        """
        return Col(alias, self.value_field, output_field or self.value_field)  # type: ignore

    def get_lookup(self, lookup_name):
        """
        Return the lookup class for the specified lookup name, delegating to the internal value field.

        Parameters:
                lookup_name (str): The name of the lookup to retrieve.

        Returns:
                The lookup class corresponding to the given name, as provided by the internal decimal value field.
        """
        return self.value_field.get_lookup(lookup_name)

    def get_transform(self, lookup_name) -> models.Transform | None:
        """
        Delegates retrieval of a transform operation to the internal value field.

        Returns:
            The transform corresponding to the given lookup name, or None if not found.
        """
        return self.value_field.get_transform(lookup_name)

    def db_type(self, connection) -> None:  # type: ignore
        """
        Return None to indicate that MeasurementField does not correspond to a single database column.

        This field manages its data using separate internal fields and does not require a direct database type.
        """
        return None

    def run_validators(self, value: Measurement | None) -> None:
        """
        Runs all validators on the provided Measurement value if it is not None.

        Parameters:
            value (Measurement | None): The measurement to validate, or None to skip validation.
        """
        if value is None:
            return
        for v in self.validators:
            v(value)

    def clean(
        self, value: Measurement | None, model_instance: models.Model | None = None
    ) -> Measurement | None:
        """
        Validates and cleans a Measurement value for use in the model field.

        Runs field-level validation and all configured validators on the provided value, returning it unchanged if valid.

        Parameters:
            value (Measurement | None): The measurement value to validate and clean.
            model_instance (models.Model | None): The model instance this value is associated with, if any.

        Returns:
            Measurement | None: The validated measurement value, or None if the input was None.
        """
        self.validate(value, model_instance)
        self.run_validators(value)
        return value

    def to_python(self, value):
        """
        Returns the input value unchanged.

        This method is required by Django custom fields to convert database values to Python objects, but no conversion is performed for this field.
        """
        return value

    def get_prep_value(self, value):
        """
        Prepare a value for database storage by converting a Measurement to its decimal magnitude in the base unit.

        If the input is a string, it is parsed into a Measurement. If the value cannot be converted to the base unit due to dimensionality mismatch, a ValidationError is raised. Only Measurement instances or None are accepted.

        Returns:
            Decimal: The numeric value of the measurement in the base unit, or None if the input is None.

        Raises:
            ValidationError: If the value is not a Measurement or cannot be converted to the base unit.
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
        Retrieve the measurement value from the model instance, reconstructing it as a `Measurement` object with the stored unit.

        Returns:
            Measurement: The measurement with its original unit if both value and unit are present.
            None: If either the value or unit is missing.
            MeasurementField: If accessed from the class rather than an instance.
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

    def __set__(self, instance, value):
        """
        Assigns a measurement value to the model instance, validating type, unit compatibility, and editability.

        If the value is a string, attempts to parse it as a Measurement. Ensures the unit matches the expected base unit's dimensionality or currency status. Stores the numeric value (converted to the base unit) and the original unit string in the instance. Raises ValidationError if the value is invalid or incompatible.
        """
        if not self.editable:
            raise ValidationError(f"{self.name} is not editable.")
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
        Validates a measurement value against null and blank constraints and applies all field validators.

        Raises:
            ValidationError: If the value is None and the field does not allow nulls, or if the value is blank and the field does not allow blanks, or if any validator fails.
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
