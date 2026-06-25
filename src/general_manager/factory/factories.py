"""Helpers for generating realistic factory values for Django models."""

from __future__ import annotations

from collections.abc import Callable
import string
from typing import Protocol, cast

from factory.declarations import LazyFunction
from factory.faker import Faker
import exrex
from django.apps import apps
from django.core.validators import RegexValidator
from django.db import models
from datetime import timezone
from decimal import Decimal
from random import SystemRandom
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.manager.general_manager import GeneralManager


_RNG = SystemRandom()
_NO_DEFAULT = object()
type DjangoField = models.Field[object, object]
type RelatedFactory = Callable[[], object]

_LazyFunctionConstructor = cast(
    Callable[[Callable[[], object]], LazyFunction],
    LazyFunction,
)
_FakerConstructor = cast(
    Callable[..., Faker],
    Faker,
)


class GeneralManagerFactoryOwner(Protocol):
    """Protocol for generated manager classes that expose an AutoFactory."""

    Factory: RelatedFactory


class GeneralManagerBackedModel(Protocol):
    """Protocol for Django model classes wired to a GeneralManager class."""

    _general_manager_class: GeneralManagerFactoryOwner


def _lazy_function(callback: Callable[[], object]) -> LazyFunction:
    """Create a typed LazyFunction declaration from an untyped factory_boy API."""
    return _LazyFunctionConstructor(callback)


def _faker(provider: str, **kwargs: object) -> Faker:
    """Create a typed Faker declaration from factory_boy's untyped API."""
    return _FakerConstructor(provider, **kwargs)


class MissingFactoryOrInstancesError(ValueError):
    """
    Raised when a related model offers neither a factory nor existing instances.

    Public callers should handle this by exception type; the message is
    diagnostic and not a stable parsing contract.
    """

    def __init__(self, related_model: type[models.Model]) -> None:
        """
        Exception raised when a related model has neither a registered factory nor any existing instances.

        Parameters:
            related_model (type[models.Model]): The Django model class that lacks both a factory and existing instances.
        """
        super().__init__(
            f"No factory found for {related_model.__name__} and no instances found."
        )


class MissingRelatedModelError(ValueError):
    """
    Raised when a relational field lacks a related model definition.

    Public callers should handle this by exception type; the message is
    diagnostic and not a stable parsing contract.
    """

    def __init__(self, field_name: str) -> None:
        """
        Initialize the exception for a field that does not declare a related model.

        Parameters:
            field_name (str): The name of the field missing a related model; included in the exception message.
        """
        super().__init__(f"Field {field_name} does not have a related model defined.")


class InvalidRelatedModelTypeError(TypeError):
    """
    Raised when a relational field references an incompatible model type.

    This also covers scalar/non-relational fields passed to relation helpers.
    Public callers should handle this by exception type; the message is
    diagnostic and not a stable parsing contract.
    """

    def __init__(self, field_name: str, related: object) -> None:
        """
        Initialize the exception indicating a relational field references a non-model type.

        Parameters:
            field_name (str): Name of the relational field that declared an invalid related model.
            related (object): The value provided as the related model; its repr is included in the exception message.
        """
        super().__init__(
            f"Related model for {field_name} must be a Django model class, got {related!r}."
        )


class UnableToResolveManagerInstanceError(ValueError):
    """
    Raised when a GeneralManager instance cannot be converted back into its model.

    Public callers should handle this by exception type; the message is
    diagnostic and not a stable parsing contract.
    """

    def __init__(self, manager: object) -> None:
        """
        Initialize with the offending factory output.

        Parameters:
            manager: The manager or factory output that could not be resolved.
        """
        super().__init__(f"Unable to resolve model instance from manager {manager!r}.")


def get_field_value(
    field: DjangoField | models.ForeignObjectRel,
) -> object:
    """
    Generate a realistic sample value appropriate for the given Django model field or relation.

    This returns a value suitable for assignment to the field: common scalar and
    text fields produce Faker-generated declarations; `CharField` respects
    `max_length` and `RegexValidator`; `MeasurementField` returns a
    `LazyFunction` that produces a `Measurement` in the field's base unit; and
    `OneToOneField`/`ForeignKey` values return model instances or LazyFunction
    wrappers that either create instances via a GeneralManager factory or select
    existing related instances. Unsupported scalar or custom field types return
    `None`. Many-to-many fields are not generated here; passing one returns
    `None`, and callers should use `get_many_to_many_field_value()` for
    many-to-many assignment values.

    Nullable fields have a 10% chance to return `None`. Nullable relation fields
    whose declared default is `None` return `None` immediately. Nullable
    foreign-key or one-to-one fields with no factory and no existing rows return
    `None`; non-nullable relation fields in the same situation raise
    `MissingFactoryOrInstancesError`. `blank` does not change foreign-key or
    one-to-one generation behavior.

    Parameters:
        field: A Django scalar field, foreign-key field, one-to-one field, or
            relation descriptor to generate a value for. Many-to-many fields are
            accepted by the broad type shape but intentionally return `None`
            here; pass them to `get_many_to_many_field_value()` instead.

    Returns:
        A value suitable for assignment to the field (scalar, string, Measurement-producing LazyFunction, model instance, LazyFunction that yields a related instance, or `None`).

    Raises:
        MissingFactoryOrInstancesError: When a related field's model has neither a registered factory nor any existing instances.
        MissingRelatedModelError: When a relational field does not declare a related model.
        InvalidRelatedModelTypeError: When a relational field's related value is not a Django model class.
    """
    if (
        getattr(field, "null", False)
        and getattr(field, "default", _NO_DEFAULT) is None
        and getattr(field, "is_relation", False)
    ):
        return None

    if field.null:
        if _RNG.choice([True] + 9 * [False]):
            return None

    if isinstance(field, MeasurementField):

        def _measurement() -> Measurement:
            """
            Create a Measurement using the field's base unit and a randomly chosen value.

            Returns:
                measurement (Measurement): A Measurement whose value is a Decimal with two decimal places between 0.00 and 100000.00 and whose unit is the enclosing field's `base_unit`.
            """
            value = Decimal(_RNG.randrange(0, 10_000_000)) / Decimal("100")  # two dp
            return Measurement(value, field.base_unit)

        return _lazy_function(_measurement)
    if (
        getattr(field, "choices", None)
        and not getattr(field, "many_to_one", False)
        and not getattr(field, "many_to_many", False)
    ):
        # Use any declared choices directly to keep generated values valid.
        flat_choices = [
            choice[0] if isinstance(choice, (list, tuple)) and choice else choice
            for choice in list(getattr(field, "flatchoices", ()))
        ]
        if flat_choices:
            return _lazy_function(lambda: _RNG.choice(flat_choices))
        # Fall through to default behaviour when no usable choices were discovered.
    if isinstance(field, models.TextField):
        return _faker("paragraph")
    elif isinstance(field, models.IntegerField):
        return _faker("random_int")
    elif isinstance(field, models.DecimalField):
        max_digits = field.max_digits
        decimal_places = field.decimal_places
        left_digits = max_digits - decimal_places
        return _faker(
            "pydecimal",
            left_digits=left_digits,
            right_digits=decimal_places,
            positive=True,
        )
    elif isinstance(field, models.FloatField):
        return _faker("pyfloat", positive=True)
    elif isinstance(field, models.DateTimeField):
        return _faker(
            "date_time_between",
            start_date="-1y",
            end_date="now",
            tzinfo=timezone.utc,
        )
    elif isinstance(field, models.DateField):
        return _faker("date_between", start_date="-1y", end_date="today")
    elif isinstance(field, models.BooleanField):
        return _faker("pybool")
    elif isinstance(field, models.EmailField):
        return _faker("email")
    elif isinstance(field, models.URLField):
        return _faker("url")
    elif isinstance(field, models.GenericIPAddressField):
        return _faker("ipv4")
    elif isinstance(field, models.UUIDField):
        return _faker("uuid4")
    elif isinstance(field, models.DurationField):
        return _faker("time_delta")
    elif isinstance(field, models.CharField):
        if field.max_length == 0:
            return ""
        max_length = field.max_length or 100
        # Check for RegexValidator
        regex = None
        for validator in field.validators:
            if isinstance(validator, RegexValidator):
                regex = getattr(validator.regex, "pattern", None)
                break
        if regex:
            # Use exrex to generate a string matching the regex
            return _lazy_function(lambda: exrex.getone(regex))
        else:
            if max_length < 5:
                alphabet = string.ascii_letters + string.digits
                return _lazy_function(
                    lambda: "".join(_RNG.choice(alphabet) for _ in range(max_length))
                )
            return _faker("text", max_nb_chars=max_length)
    elif isinstance(field, models.OneToOneField):
        related_model = get_related_model(field)
        if hasattr(related_model, "_general_manager_class"):
            related_factory = _get_related_factory(related_model)
            return _ensure_model_instance(related_factory())
        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(related_model.objects.all())
            if related_instances:
                return _lazy_function(lambda: _RNG.choice(related_instances))
            if field.null:
                return None
            raise MissingFactoryOrInstancesError(related_model)
    elif isinstance(field, models.ForeignKey):
        related_model = get_related_model(field)
        # Create or get an instance of the related model
        if hasattr(related_model, "_general_manager_class"):
            create_a_new_instance = _RNG.choice([True, True, False])
            if not create_a_new_instance:
                existing_instances = list(related_model.objects.all())
                if existing_instances:
                    # Pick a random existing instance
                    return _lazy_function(lambda: _RNG.choice(existing_instances))

            related_factory = _get_related_factory(related_model)
            return _ensure_model_instance(related_factory())

        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(related_model.objects.all())
            if related_instances:
                return _lazy_function(lambda: _RNG.choice(related_instances))
            if field.null:
                return None
            raise MissingFactoryOrInstancesError(related_model)
    else:
        return None


def get_related_model(
    field: (
        models.ForeignObjectRel
        | DjangoField
        | models.ManyToManyField[models.Model, models.Model]
    ),
) -> type[models.Model]:
    """
    Resolve and return the Django model class referenced by a relational field.

    If the field's declared related model is the string "self", this resolves it to the field's model before validation.

    Parameters:
        field: Relational field or relation descriptor to inspect. Passing a
            non-relational scalar field is unsupported.

    Returns:
        type[models.Model]: The related Django model class.

    Raises:
        MissingRelatedModelError: If the field does not declare a related model.
        InvalidRelatedModelTypeError: If the resolved related model is not a
            Django model class, including non-relational scalar fields whose
            `related_model` value is `None` or absent.
    """
    related_model: object = getattr(field, "related_model", None)
    if related_model is None:
        raise MissingRelatedModelError(field.name)
    if isinstance(related_model, str):
        related_model = _resolve_related_model_string(field, related_model)
    if not isinstance(related_model, type) or not issubclass(
        related_model, models.Model
    ):
        raise InvalidRelatedModelTypeError(field.name, related_model)
    return related_model


def _resolve_related_model_string(
    field: (
        models.ForeignObjectRel
        | DjangoField
        | models.ManyToManyField[models.Model, models.Model]
    ),
    related_model: str,
) -> object:
    if related_model == "self":
        return field.model
    app_label: str | None
    if "." in related_model:
        app_label, model_name = related_model.split(".", 1)
    else:
        model = getattr(field, "model", None)
        opts = getattr(model, "_meta", None)
        app_label_value = getattr(opts, "app_label", None)
        app_label = app_label_value if isinstance(app_label_value, str) else None
        model_name = related_model
    if not app_label:
        return related_model
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return related_model


def get_many_to_many_field_value(
    field: models.ManyToManyField[models.Model, models.Model],
) -> list[models.Model]:
    """
    Generate a list of related model instances suitable for assigning to a ManyToManyField.

    The function selects a random number of related objects (at least one when
    the field is not blank, up to 10). It will use the related model's factory
    to create new instances when available, prefer a mix of created and existing
    instances if both are present, or return existing instances when no factory
    is available. `blank=True` allows an empty result; `blank=False` requires at
    least one related instance.

    Parameters:
        field (models.ManyToManyField): The ManyToMany field to generate values for.

    Returns:
        list[models.Model]: A list of related model instances to assign to the field.

    Raises:
        MissingFactoryOrInstancesError: If the related model provides neither a factory nor any existing instances.
        MissingRelatedModelError: If the field does not declare a related model.
        InvalidRelatedModelTypeError: If the resolved related model is not a
            Django model class.
        UnableToResolveManagerInstanceError: If a related GeneralManager factory
            returns a manager instance that cannot be resolved to its Django
            model row.
    """
    related_factory: RelatedFactory | None = None
    related_model = get_related_model(field)
    related_instances: list[models.Model] = list(related_model.objects.all())
    if hasattr(related_model, "_general_manager_class"):
        related_factory = _get_related_factory(related_model)

    min_required = 0 if field.blank else 1
    number_of_instances = _RNG.randint(min_required, 10)
    if related_factory and related_instances:
        number_to_create = _RNG.randint(min_required, number_of_instances)
        number_to_pick = number_of_instances - number_to_create
        if number_to_pick > len(related_instances):
            number_to_pick = len(related_instances)
        existing_instances = _RNG.sample(related_instances, number_to_pick)
        new_factory_values = [related_factory() for _ in range(number_to_create)]
        return existing_instances + [
            _ensure_model_instance(instance) for instance in new_factory_values
        ]
    elif related_factory:
        number_to_create = number_of_instances
        new_instances = [
            _ensure_model_instance(related_factory()) for _ in range(number_to_create)
        ]
        return new_instances
    elif related_instances:
        number_to_create = 0
        number_to_pick = number_of_instances
        if number_to_pick > len(related_instances):
            number_to_pick = len(related_instances)
        existing_instances = _RNG.sample(related_instances, number_to_pick)
        return existing_instances
    else:
        raise MissingFactoryOrInstancesError(related_model)


def _get_related_factory(related_model: type[models.Model]) -> RelatedFactory:
    """Return the factory configured on a GeneralManager-backed related model."""
    backed_model = cast(GeneralManagerBackedModel, related_model)
    return backed_model._general_manager_class.Factory


def _ensure_model_instance(value: object) -> models.Model:
    """
    Normalize a factory output into a Django model instance.

    Attempts to convert GeneralManager objects produced by factories into their underlying Django model
    instances. If `value` is already a Django model instance, it is returned unchanged.

    Parameters:
        value: A factory output, either a GeneralManager or a Django model instance.

    Returns:
        models.Model: The resolved Django model instance.

    Raises:
        UnableToResolveManagerInstanceError: If `value` is a GeneralManager that cannot be resolved to a model instance.
    """
    if isinstance(value, GeneralManager):
        interface = getattr(value, "_interface", None)
        instance = getattr(interface, "_instance", None) if interface else None
        if instance is not None:
            return cast(models.Model, instance)
        manager_cls = value.__class__
        interface_cls = getattr(manager_cls, "Interface", None)
        if interface_cls is None:
            raise UnableToResolveManagerInstanceError(value)
        model_cls = getattr(interface_cls, "_model", None)
        if model_cls is not None:
            model_type = cast(type[models.Model], model_cls)
            return model_type.objects.get(**value.identification)
        raise UnableToResolveManagerInstanceError(value)
    if isinstance(value, models.Model):
        return value
    raise UnableToResolveManagerInstanceError(value)
