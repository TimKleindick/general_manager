"""Custom Django model field storing values as unit-aware measurements."""

from __future__ import annotations

from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.expressions import Col
from decimal import Decimal, InvalidOperation
import pint
from general_manager.measurement.measurement import (
    Measurement,
    _unit_uses_offset,
    convert_magnitude,
    currency_units,
    ureg,
)
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import Lookup, Transform
from typing import TYPE_CHECKING, TypeAlias, cast

MeasurementValidationValue: TypeAlias = (
    Measurement | None | str | list[object] | tuple[object, ...] | dict[object, object]
)

if TYPE_CHECKING:
    MeasurementFieldBase = models.Field[Measurement | None, Decimal | None]
    BackingField = models.Field[object, object]
    BackingDecimalField = models.DecimalField[Decimal | None]
else:
    MeasurementFieldBase = models.Field
    BackingField = models.Field
    BackingDecimalField = models.DecimalField


class MeasurementFieldNotEditableError(ValidationError):
    """Raised when attempting to modify a non-editable MeasurementField."""

    def __init__(self, field_name: str) -> None:
        """
        Initialize the exception indicating an attempt to assign to a non-editable measurement field.

        Parameters:
            field_name (str): Name of the field that was attempted to be modified; used to compose the error message.
        """
        super().__init__(f"{field_name} is not editable.")


class InvalidMeasurementFieldBaseUnitError(ValueError):
    """Raised when a measurement field uses an offset unit as its base unit."""

    def __init__(self, base_unit: str) -> None:
        super().__init__(
            f"MeasurementField base_unit '{base_unit}' must be multiplicative. "
            "Use a unit like 'K' for absolute temperatures instead of an offset unit."
        )


class MeasurementField(MeasurementFieldBase):
    description = "Stores a measurement (value + unit) but exposes a single field API"

    empty_values: tuple[object, ...] = (None, "", [], (), {})

    def __init__(
        self,
        base_unit: str,
        *args: object,
        null: bool = False,
        blank: bool = False,
        editable: bool = True,
        unique: bool = False,
        **kwargs: object,
    ) -> None:
        """
        Create a MeasurementField configured with a canonical base unit and paired backing columns.

        Initializes the field's canonical base unit and derived dimensionality, records the editable flag, constructs a Decimal-backed value column (`<name>_value`) and Char-backed unit column (`<name>_unit`), and forwards remaining arguments to the base Field constructor. Stored magnitudes are converted to `base_unit` and rounded to the backing `DecimalField(max_digits=30, decimal_places=10)` precision; the unit column stores the Measurement's public unit spelling, such as `gram` for `g` and `count` for discrete item counts.

        Parameters:
            base_unit (str): Multiplicative Pint unit used to normalize and store measurements. Currency units must be one of `currency_units`; each configured currency is treated as its own Pint dimension. Use `count` for discrete item quantities.
            *args (object): Positional arguments forwarded to the base Field implementation.
            null (bool): If True, the backing columns may be NULL in the database.
            blank (bool): If True, forms may accept an empty value for this field.
            editable (bool): If False, assignments through the model API will be rejected.
            unique (bool): If True, the backing value column is created with a unique constraint. Equivalent values in different units share the same stored base magnitude and therefore conflict.
            **kwargs (object): Additional keyword arguments forwarded to the base Field implementation.

        Raises:
            InvalidMeasurementFieldBaseUnitError: If `base_unit` is an offset unit such as `degC`.
            pint.errors.PintError: If Pint cannot parse or dimensionally evaluate `base_unit`.
        """
        if _unit_uses_offset(base_unit):
            raise InvalidMeasurementFieldBaseUnitError(base_unit)
        self.base_unit = base_unit
        self.base_dimension = ureg.parse_expression(self.base_unit).dimensionality

        self.editable = editable
        self.value_field: BackingField
        self.unit_field: BackingField
        if null:
            self.value_field = cast(
                BackingField,
                models.DecimalField(
                    max_digits=30,
                    decimal_places=10,
                    db_index=True,
                    unique=unique,
                    editable=editable,
                    null=True,
                    blank=blank,
                ),
            )
            self.unit_field = cast(
                BackingField,
                models.CharField(
                    max_length=100,
                    editable=editable,
                    null=True,
                    blank=blank,
                ),
            )
        else:
            self.value_field = cast(
                BackingField,
                models.DecimalField(
                    max_digits=30,
                    decimal_places=10,
                    db_index=True,
                    unique=unique,
                    editable=editable,
                    null=False,
                    blank=blank,
                ),
            )
            self.unit_field = cast(
                BackingField,
                models.CharField(
                    max_length=100,
                    editable=editable,
                    null=False,
                    blank=blank,
                ),
            )

        options: dict[str, object] = {
            **kwargs,
            "null": null,
            "blank": blank,
            "editable": editable,
            "unique": unique,
        }
        super().__init__(*args, **options)

    def _normalize_base_magnitude(self, magnitude: Decimal | float | int) -> Decimal:
        """Round converted magnitudes to the backing DecimalField precision.

        The backing value column uses ten decimal places. If Decimal cannot
        quantize a very large value, the unquantized Decimal is returned so
        Django's normal field validation can report any precision overflow.
        """
        decimal_magnitude = Decimal(str(magnitude))
        decimal_places = cast(BackingDecimalField, self.value_field).decimal_places
        quantizer = Decimal("1").scaleb(-decimal_places)
        try:
            return decimal_magnitude.quantize(quantizer)
        except InvalidOperation:
            return decimal_magnitude

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

        self._remap_constraints_to_value_field(cls)

        # Descriptor override
        setattr(cls, name, self)

    def _remap_constraints_to_value_field(
        self,
        cls: type[models.Model],
    ) -> None:
        """
        Remap model uniqueness constraints to reference this field's backing value column.

        Updates the given model class's metadata so any uniqueness constraints that
        refer to the logical MeasurementField name are rewritten to use the concrete
        backing value column (self.value_attr). This ensures migrations and schema
        operations target a real database column instead of the non-concrete logical
        field. The remap intentionally ignores the stored unit column, so
        equivalent values in compatible units, such as 1 kilogram and 1000 grams,
        are considered the same value for uniqueness.

        Parameters:
            cls (type[models.Model]): The model class whose _meta.constraints and
                _meta.unique_together will be modified in-place.
        """

        def swap_field_name(field_name: str) -> str:
            """
            Map a field name to the backing value field name when it matches this measurement field's logical name.

            Parameters:
                field_name (str): The field name to potentially remap.

            Returns:
                str: `self.value_attr` when `field_name` equals this field's logical name (`self.name`); otherwise the original `field_name`.
            """
            return self.value_attr if field_name == self.name else field_name

        def rebuild_unique_constraint(
            constraint: models.UniqueConstraint,
            fields: tuple[str, ...],
            include_names: tuple[str, ...],
        ) -> models.UniqueConstraint:
            """
            Create a copy of a UniqueConstraint with remapped fields/include.
            """

            opclasses = getattr(constraint, "opclasses", ())
            include_attr = getattr(constraint, "include", None)
            expressions = tuple(getattr(constraint, "expressions", ()))
            rebuilt_constraint = models.UniqueConstraint(
                *expressions,
                fields=fields,
                name=constraint.name,
                condition=constraint.condition,
                deferrable=getattr(constraint, "deferrable", None),
                include=include_names if include_attr is not None else None,
                opclasses=opclasses,
                nulls_distinct=getattr(constraint, "nulls_distinct", None),
                violation_error_message=getattr(
                    constraint,
                    "violation_error_message",
                    None,
                ),
            )
            if hasattr(rebuilt_constraint, "violation_error_code"):
                rebuilt_constraint.violation_error_code = getattr(
                    constraint,
                    "violation_error_code",
                    None,
                )
            return rebuilt_constraint

        remapped_constraints: list[models.BaseConstraint] = []
        for constraint in cls._meta.constraints:
            if isinstance(constraint, models.UniqueConstraint):
                fields = tuple(swap_field_name(f) for f in constraint.fields)
                include_names = tuple(
                    swap_field_name(f) for f in getattr(constraint, "include", ())
                )
                include_original = getattr(constraint, "include", ())
                if fields != constraint.fields or include_names != include_original:
                    constraint = rebuild_unique_constraint(
                        constraint,
                        fields,
                        include_names,
                    )
            remapped_constraints.append(constraint)

        cls._meta.constraints = remapped_constraints
        cls._meta.unique_together = tuple(
            tuple(swap_field_name(field) for field in unique_set)
            for unique_set in cls._meta.unique_together
        )

    # ---- ORM Delegation ----
    def get_col(
        self,
        alias: str,
        output_field: models.Field[object, object] | None = None,
    ) -> Col:
        """
        Return a Col expression that references this field's backing value column for ORM queries.

        Parameters:
            alias (str): Table alias to use for the column reference.
            output_field (models.Field | None): Optional field to use as the expression's output type; defaults to the backing value field.

        Returns:
            Col: Column expression targeting the numeric backing value field.
        """
        return Col(alias, self.value_field, output_field or self.value_field)  # type: ignore

    def get_lookup(self, lookup_name: str) -> type[Lookup[object]]:
        """
        Retrieve a lookup class from the underlying decimal field.

        Filtering uses the backing value column. Lookup right-hand-side values
        that pass through this field may be Measurement instances or measurement
        strings and are prepared with `get_prep_value()`. Bare numeric values
        are interpreted by Django's decimal lookup machinery as already being in
        the stored base unit when the delegated DecimalField lookup handles the
        RHS directly; bare numbers are not accepted by direct `get_prep_value()`
        calls and are not valid descriptor-assignment values.

        Parameters:
            lookup_name (str): Name of the lookup to resolve.

        Returns:
            type[models.Lookup]: Lookup class implementing the requested comparison.
        """
        return cast("type[Lookup[object]]", self.value_field.get_lookup(lookup_name))

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

    def deconstruct(self) -> tuple[str, str, list[object], dict[str, object]]:
        """
        Return serialization details so migrations can reconstruct the field.

        Ensures the required `base_unit` argument is preserved alongside the
        standard Django field options emitted by the base implementation.
        """
        name, path, args, kwargs = super().deconstruct()
        kwargs["base_unit"] = self.base_unit
        return name, path, list(args), kwargs

    def db_type(self, connection: BaseDatabaseWrapper) -> None:  # type: ignore[override]
        """
        Signal to Django that the field does not map to a single column.

        Parameters:
            connection (BaseDatabaseWrapper): Database connection used for schema generation.

        Returns:
            None
        """
        return None

    def run_validators(self, value: MeasurementValidationValue) -> None:
        """
        Execute all configured validators when a measurement is provided.

        Validators receive the Measurement object supplied to `clean()`. This
        hook does not convert that object to `base_unit`; assignment and
        preparation normalize the persisted backing value separately.
        Empty values skip validators, matching Django's field-validation
        convention. Non-empty values must be Measurement instances; direct calls
        with non-empty strings, non-empty lists, non-empty dictionaries, or other
        objects raise ValidationError with "Value must be a Measurement instance
        or None.". `run_validators()` itself does not check `blank`; direct
        calls with `""`, `[]`, `()`, or `{}` skip validators regardless of the
        field's `blank` setting. `clean()` calls `validate()` first when blank
        enforcement is needed.

        Parameters:
            value (MeasurementValidationValue): Measurement instance that should satisfy field validators, or an empty value.

        Returns:
            None
        """
        if value in self.empty_values:
            return
        if not isinstance(value, Measurement):
            raise ValidationError(
                {self.name: ["Value must be a Measurement instance or None."]}
            )
        super().run_validators(value)

    def clean(
        self,
        value: MeasurementValidationValue,
        model_instance: models.Model | None = None,
    ) -> MeasurementValidationValue:
        """
        Validate a measurement value before it is saved to the model.

        This hook expects an already-normalized Python value. It does not parse
        strings; string parsing happens during descriptor assignment and value
        preparation. Empty literals are valid `clean()` inputs for Django's
        blank-validation path and are returned unchanged when `blank=True`; for
        example, `clean("")`, `clean([])`, `clean(())`, and `clean({})` return
        the same object/value when blank values are allowed.
        `clean(None)` raises Django's standard null ValidationError when
        `null=False` and returns None when `null=True`.
        Direct calls such as `clean("100 g")` do not parse strings and raise
        ValidationError; assign strings through the descriptor or prepare them
        with `get_prep_value()`. For Measurement values, `clean()` runs
        validators exactly once with the same Measurement object and then returns
        that object.

        Parameters:
            value (MeasurementValidationValue): Measurement provided by forms or assignment, or an empty literal handled by blank validation.
            model_instance (models.Model | None): Instance associated with the field, when available.

        Returns:
            MeasurementValidationValue: The original validated value.

        Raises:
            ValidationError: If validation fails due to null/blank constraints,
                non-empty non-Measurement values, or validator errors. Non-empty
                non-Measurement values use "Value must be a Measurement instance
                or None.".
        """
        self.validate(value, model_instance)
        self.run_validators(value)
        return value

    def to_python(
        self, value: MeasurementValidationValue
    ) -> MeasurementValidationValue:
        """
        Return the value Django already supplied for this virtual field.

        Measurement reconstruction happens in the descriptor from the paired
        `<name>_value` and `<name>_unit` columns. Django does not pass those two
        columns through this single-field hook, so this method preserves
        `Measurement`, string, list, tuple, dict, and None values unchanged.
        This is intentionally a validation passthrough, not a string-coercion
        hook; `to_python("1 meter")` returns `"1 meter"` rather than parsing it.
        Django's `clean()` path in this class does not call `to_python()` for
        blank literals; it handles them directly in `validate()`. Direct calls
        with unsupported runtime objects outside `MeasurementValidationValue`
        are outside the typed public contract and are returned unchanged only by
        Python's dynamic dispatch.

        Parameters:
            value (MeasurementValidationValue): Value provided by Django.

        Returns:
            MeasurementValidationValue: The original value without modification.
        """
        return value

    def get_prep_value(self, value: Measurement | str | None) -> Decimal | None:
        """
        Serialise a measurement for storage by converting it to the base unit magnitude.

        Strings use `Measurement.from_string`, so accepted text follows that
        parser's grammar: a Python `Decimal` numeric token, optionally followed
        by a Pint unit expression. Signed decimals and exponent notation are
        accepted by `Decimal`; thousands separators are not. Leading and
        trailing whitespace is ignored by Python's whitespace split, but
        whitespace-only strings are invalid. Numeric strings without a unit
        create dimensionless measurements, so they are compatible only with
        dimensionless `base_unit` values.

        Parameters:
            value (Measurement | str | None): Value provided by the model or form.

        Returns:
            Decimal | None: Decimal magnitude in the base unit quantized to the
            backing field's ten decimal places, or None when no value is
            supplied. Float-origin magnitudes follow the `Measurement` class's
            Decimal conversion.

        Raises:
            ValidationError: If the value cannot be parsed as a measurement, is
                not a Measurement/string/None, or uses a unit incompatible with
                `base_unit`. Invalid strings and wrong types use "Value must be
                a Measurement instance or None."; incompatible units use "Unit
                must be compatible with '<base_unit>'.".
            pint.errors.PintError: Only non-ValueError Pint exceptions that
                escape `Measurement.from_string()` are allowed to propagate;
                parser ValueError and dimensionality failures are wrapped in
                ValidationError. Empty strings, whitespace-only strings, invalid
                unit syntax, and unknown unit names that surface as ValueError
                use the generic invalid-value ValidationError. GeneralManager
                does not make exact non-wrapped Pint subclasses part of this
                field's stable API.
        """
        if value is None:
            return None
        if isinstance(value, str):
            try:
                value = Measurement.from_string(value)
            except ValueError as e:
                raise ValidationError(
                    {self.name: ["Value must be a Measurement instance or None."]}
                ) from e
        if isinstance(value, Measurement):
            try:
                converted = convert_magnitude(
                    value.magnitude, value.unit, self.base_unit
                )
                return self._normalize_base_magnitude(converted)
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

        The descriptor reads `<name>_value` as a base-unit magnitude and
        `<name>_unit` as the Measurement public spelling for the unit originally assigned. It converts the
        base magnitude back to that stored unit for display. If stored data has
        drifted and the unit is no longer parseable or dimensionally compatible with the base
        unit, reconstruction falls back to a Measurement in `base_unit` rather
        than raising during attribute access. The fallback affects only the
        returned Measurement and does not mutate the backing columns. During
        fallback the stored `<name>_value` is treated as already being in
        `base_unit`. If the stored value column itself cannot be converted with
        `Decimal(str(value))`, that Decimal conversion error propagates; only
        stored unit failures use the fallback.

        Parameters:
            instance (models.Model | None): Model instance owning the field, or None when accessed on the class.
            owner (type[models.Model] | None): Model class owning the descriptor.

        Returns:
            MeasurementField | Measurement | None: Descriptor when accessed on the class, reconstructed measurement for instances, or None when either backing column is None.
        """
        if instance is None:
            return self
        val = getattr(instance, self.value_attr)
        unit = getattr(instance, self.unit_attr)
        if val is None or unit is None:
            return None
        try:
            magnitude = convert_magnitude(Decimal(str(val)), self.base_unit, unit)
        except pint.errors.PintError:
            magnitude = Decimal(str(val))
            unit = self.base_unit
        return Measurement(magnitude, str(unit))

    def __set__(
        self,
        instance: models.Model,
        value: Measurement | str | None,
    ) -> None:
        """
        Set a measurement on a model instance after validating editability, type, and unit compatibility.

        `None` clears both backing columns at assignment time, even when
        `null=False`; Django validation rejects that cleared value later if the
        field is not nullable. Saving without model validation relies on the
        database schema for the non-null backing columns. Non-empty strings are parsed with
        `Measurement.from_string`; an empty string is a parse error rather than a
        clear operation. For currency base units, assigned values must use a
        currency from the exported fixed `currency_units` set (`EUR`, `USD`, `GBP`, `JPY`, `CHF`,
        `AUD`, or `CAD`) and must match the specific base currency's Pint
        dimension. Currency aliases such as `dollar` are not part of this public
        field contract unless they are explicitly added to `currency_units`. The
        field does not accept an exchange-rate argument, so cross-currency
        assignments such as EUR into a USD field are rejected with
        ValidationError as incompatible.
        For non-currency base units, currency measurements are rejected.

        Parameters:
            instance (models.Model): Model instance receiving the value.
            value (Measurement | str | None): A Measurement, a string parseable to a Measurement, or None to clear the field.

        Raises:
            MeasurementFieldNotEditableError: If the field was declared with
                `editable=False`.
            ValidationError: If the value is not a Measurement, valid parseable
                string, or None; if currency unit rules are violated; or if the
                unit is incompatible with the field's base unit. Invalid strings
                and wrong types use "Value must be a Measurement instance or
                None."; incompatible units use "Unit must be compatible with
                '<base_unit>'."; non-currency values assigned to currency fields
                use "Unit must be a currency (AUD, CAD, CHF, EUR, GBP, JPY,
                USD)."; currency values assigned to non-currency fields use
                "Unit cannot be a currency."; wrong currencies use "Unit must
                be compatible with '<base_unit>'.".
            pint.errors.PintError: Only non-ValueError Pint exceptions that
                escape `Measurement.from_string()` are allowed to propagate;
                parser ValueError and dimensionality failures are wrapped in
                ValidationError. Empty strings, whitespace-only strings, invalid
                unit syntax, and unknown unit names that surface as ValueError
                use the generic invalid-value ValidationError. GeneralManager
                does not make exact non-wrapped Pint subclasses part of this
                field's stable API.
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
                            f"Unit must be a currency ({', '.join(sorted(currency_units))})."
                        ]
                    }
                )
        else:
            if value.is_currency():
                raise ValidationError({self.name: ["Unit cannot be a currency."]})

        try:
            base_mag = convert_magnitude(value.magnitude, value.unit, self.base_unit)
        except pint.errors.DimensionalityError as e:
            raise ValidationError(
                {self.name: [f"Unit must be compatible with '{self.base_unit}'."]}
            ) from e

        setattr(instance, self.value_attr, self._normalize_base_magnitude(base_mag))
        setattr(instance, self.unit_attr, str(value.unit))

    def validate(
        self,
        value: MeasurementValidationValue,
        model_instance: models.Model | None = None,
    ) -> None:
        """
        Enforce null/blank constraints and run validators on the provided value.

        This method is the Django validation hook for already-normalized Python
        values. Assignment and `get_prep_value` handle string parsing and unit
        compatibility; this method handles null, blank, and non-empty runtime
        type checks. Custom validators run
        through `run_validators()`, including when `clean()` calls it after this
        method.
        Empty literals (`""`, `[]`, `()`, `{}`) are accepted only when
        `blank=True` and otherwise raise Django's standard blank ValidationError.
        Non-empty strings, lists, dictionaries, tuples, and other non-Measurement
        values raise ValidationError with "Value must be a Measurement instance
        or None.".
        Descriptor assignment and `get_prep_value()` do not use this blank path
        for empty strings; they parse `""` and raise ValidationError.

        Parameters:
            value (MeasurementValidationValue): Measurement value or empty literal under validation.
            model_instance (models.Model | None): Instance owning the field; unused but provided for API compatibility.

        Returns:
            None

        Raises:
            ValidationError: If the value violates null/blank constraints or is
                a non-empty non-Measurement value. Null and blank failures use
                Django's standard `null` and `blank` error codes.
        """
        if value is None:
            if not self.null:
                raise ValidationError(self.error_messages["null"], code="null")
            return
        if value in ("", [], (), {}):
            if not self.blank:
                raise ValidationError(self.error_messages["blank"], code="blank")
            return
        if not isinstance(value, Measurement):
            raise ValidationError(
                {self.name: ["Value must be a Measurement instance or None."]}
            )
