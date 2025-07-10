from __future__ import annotations
from typing import Any, cast, TYPE_CHECKING
from factory.declarations import LazyFunction
from factory.faker import Faker
import exrex
from django.db import models
from django.core.validators import RegexValidator
import random
from decimal import Decimal
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurementField import MeasurementField
from datetime import date, datetime, time, timezone

if TYPE_CHECKING:
    from general_manager.factory.autoFactory import AutoFactory


def getFieldValue(
    field: models.Field[Any, Any] | models.ForeignObjectRel,
) -> object:
    """
    Generate a suitable fake or factory value for a given Django model field, for use in tests or data factories.
    
    Returns a value appropriate for the field type, including support for measurement, text, numeric, date/time, boolean, email, URL, IP address, UUID, duration, and character fields (with regex support). For relational fields (OneToOneField and ForeignKey), attempts to use a related model factory or select a random existing instance; raises ValueError if neither is available. Returns None for unsupported field types or with a 10% chance if the field allows null values.
    """
    if field.null:
        if random.choice([True] + 9 * [False]):
            return None

    if isinstance(field, MeasurementField):

        def _measurement():
            value = Decimal(random.randrange(0, 10_000_000)) / Decimal("100")  # two dp
            return Measurement(value, field.base_unit)

        return LazyFunction(_measurement)
    elif isinstance(field, models.TextField):
        return cast(str, Faker("paragraph"))
    elif isinstance(field, models.IntegerField):
        return cast(int, Faker("random_int"))
    elif isinstance(field, models.DecimalField):
        max_digits = field.max_digits
        decimal_places = field.decimal_places
        left_digits = max_digits - decimal_places
        return cast(
            Decimal,
            Faker(
                "pydecimal",
                left_digits=left_digits,
                right_digits=decimal_places,
                positive=True,
            ),
        )
    elif isinstance(field, models.FloatField):
        return cast(float, Faker("pyfloat", positive=True))
    elif isinstance(field, models.DateTimeField):
        return cast(
            datetime,
            Faker(
                "date_time_between",
                start_date="-1y",
                end_date="now",
                tzinfo=timezone.utc,
            ),
        )
    elif isinstance(field, models.DateField):
        return cast(date, Faker("date_between", start_date="-1y", end_date="today"))
    elif isinstance(field, models.BooleanField):
        return cast(bool, Faker("pybool"))
    elif isinstance(field, models.EmailField):
        return cast(str, Faker("email"))
    elif isinstance(field, models.URLField):
        return cast(str, Faker("url"))
    elif isinstance(field, models.GenericIPAddressField):
        return cast(str, Faker("ipv4"))
    elif isinstance(field, models.UUIDField):
        return cast(str, Faker("uuid4"))
    elif isinstance(field, models.DurationField):
        return cast(time, Faker("time_delta"))
    elif isinstance(field, models.CharField):
        max_length = field.max_length or 100
        # Check for RegexValidator
        regex = None
        for validator in field.validators:
            if isinstance(validator, RegexValidator):
                regex = getattr(validator.regex, "pattern", None)
                break
        if regex:
            # Use exrex to generate a string matching the regex
            return LazyFunction(lambda: exrex.getone(regex))
        else:
            return cast(str, Faker("text", max_nb_chars=max_length))
    elif isinstance(field, models.OneToOneField):
        related_model = getRelatedModel(field)
        if hasattr(related_model, "_general_manager_class"):
            related_factory = related_model._general_manager_class.Factory  # type: ignore
            return related_factory()
        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(related_model.objects.all())
            if related_instances:
                return LazyFunction(lambda: random.choice(related_instances))
            else:
                raise ValueError(
                    f"No factory found for {related_model.__name__} and no instances found"
                )
    elif isinstance(field, models.ForeignKey):
        related_model = getRelatedModel(field)
        # Create or get an instance of the related model
        if hasattr(related_model, "_general_manager_class"):
            create_a_new_instance = random.choice([True, True, False])
            if not create_a_new_instance:
                existing_instances = list(related_model.objects.all())
                if existing_instances:
                    # Pick a random existing instance
                    return LazyFunction(lambda: random.choice(existing_instances))

            related_factory = related_model._general_manager_class.Factory  # type: ignore
            return related_factory()

        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(related_model.objects.all())
            if related_instances:
                return LazyFunction(lambda: random.choice(related_instances))
            else:
                raise ValueError(
                    f"No factory found for {related_model.__name__} and no instances found"
                )
    else:
        return None


def getRelatedModel(
    field: models.ForeignObjectRel | models.Field[Any, Any],
) -> type[models.Model]:
    """
    Return the related model class for a given Django relational field.
    
    Raises:
        ValueError: If the field does not have a related model defined.
    """
    related_model = field.related_model
    if related_model is None:
        raise ValueError(f"Field {field.name} does not have a related model defined.")
    if related_model == "self":
        related_model = field.model
    return related_model  # For unsupported field types


def getManyToManyFieldValue(
    field: models.ManyToManyField,
) -> list[models.Model]:
    """
    Generate a list of model instances to associate with a ManyToMany field for testing or factory purposes.
    
    If a related model factory is available, creates new instances as needed. Otherwise, selects from existing instances. Raises a ValueError if neither a factory nor existing instances are available.
    
    Returns:
        list[models.Model]: A list of related model instances for the ManyToMany field.
    """
    related_factory = None
    related_model = getRelatedModel(field)
    related_instances = list(related_model.objects.all())
    if hasattr(related_model, "_general_manager_class"):
        related_factory = related_model._general_manager_class.Factory  # type: ignore

    min_required = 0 if field.blank else 1
    number_of_instances = random.randint(min_required, 10)
    if related_factory and related_instances:
        number_to_create = random.randint(min_required, number_of_instances)
        number_to_pick = number_of_instances - number_to_create
        if number_to_pick > len(related_instances):
            number_to_pick = len(related_instances)
        existing_instances = random.sample(related_instances, number_to_pick)
        new_instances = [related_factory() for _ in range(number_to_create)]
        return existing_instances + new_instances
    elif related_factory:
        number_to_create = number_of_instances
        new_instances = [related_factory() for _ in range(number_to_create)]
        return new_instances
    elif related_instances:
        number_to_create = 0
        number_to_pick = number_of_instances
        if number_to_pick > len(related_instances):
            number_to_pick = len(related_instances)
        existing_instances = random.sample(related_instances, number_to_pick)
        return existing_instances
    else:
        raise ValueError(
            f"No factory found for {related_model.__name__} and no instances found"
        )
