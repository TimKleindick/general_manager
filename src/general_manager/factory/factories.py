from __future__ import annotations
from typing import Any, cast
from factory.declarations import LazyFunction
from factory.faker import Faker
import exrex  # type: ignore
from django.db import models
from django.core.validators import RegexValidator
import random
from decimal import Decimal
from general_manager.measurement.measurement import Measurement
from general_manager.measurement.measurementField import MeasurementField
from datetime import date, datetime, time, timezone


def getFieldValue(field: models.Field[Any, Any] | models.ForeignObjectRel) -> object:
    """
    Generates an appropriate value for a given Django model field for use in testing or data factories.
    
    If the field allows null values, there is a 10% chance of returning None. Handles a wide range of Django field types, including measurement, text, numeric, date/time, boolean, relational, and specialized fields, returning a suitable fake or factory-generated value for each. For relational fields (OneToOneField and ForeignKey), attempts to use a factory if available or selects a random existing instance; raises ValueError if neither is possible. Returns None for unsupported field types.
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
    elif isinstance(field, models.OneToOneField):
        if hasattr(field.related_model, "_general_manager_class"):
            related_factory = field.related_model._general_manager_class.Factory
            return related_factory()
        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(field.related_model.objects.all())
            if related_instances:
                return LazyFunction(lambda: random.choice(related_instances))
            else:
                raise ValueError(
                    f"No factory found for {field.related_model.__name__} and no instances found"
                )
    elif isinstance(field, models.ForeignKey):
        # Create or get an instance of the related model
        if hasattr(field.related_model, "_general_manager_class"):
            create_a_new_instance = random.choice([True, True, False])
            if not create_a_new_instance:
                existing_instances = list(field.related_model.objects.all())
                if existing_instances:
                    # Pick a random existing instance
                    return LazyFunction(lambda: random.choice(existing_instances))

            related_factory = field.related_model._general_manager_class.Factory
            return related_factory()

        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(field.related_model.objects.all())
            if related_instances:
                return LazyFunction(lambda: random.choice(related_instances))
            else:
                raise ValueError(
                    f"No factory found for {field.related_model.__name__} and no instances found"
                )
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
            return LazyFunction(lambda: exrex.getone(regex))  # type: ignore
        else:
            return cast(str, Faker("text", max_nb_chars=max_length))
    else:
        return None  # For unsupported field types


def getManyToManyFieldValue(
    field: models.ManyToManyField,
) -> list[models.Model]:
    """
    Returns a list of instances for a ManyToMany field.
    """
    related_factory = None
    related_instances = list(field.related_model.objects.all())
    if hasattr(field.related_model, "_general_manager_class"):
        related_factory = field.related_model._general_manager_class.Factory

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
            f"No factory found for {field.related_model.__name__} and no instances found"
        )
