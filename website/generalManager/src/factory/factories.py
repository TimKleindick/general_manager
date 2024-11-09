from __future__ import annotations
from typing import TYPE_CHECKING, Type, Callable, Union, Any
import factory
import exrex
from django.db import models
from django.core.validators import RegexValidator
from factory.django import DjangoModelFactory
from django.utils import timezone
import random
from decimal import Decimal
from generalManager.src.measurement.measurement import Measurement
from generalManager.src.measurement.measurementField import MeasurementField

if TYPE_CHECKING:
    from generalManager.src.interface.databaseInterface import DBBasedInterface


class AutoFactory(DjangoModelFactory):
    """
    A factory class that automatically generates values for model fields,
    including handling of unique fields and constraints.
    """

    interface: Type[DBBasedInterface]
    _adjustmentMethod: (
        Callable[..., Union[dict[str, Any], list[dict[str, Any]]]] | None
    ) = None

    @classmethod
    def _generate(cls, create: bool, attrs: dict) -> models.Model | list[models.Model]:
        cls._original_params = attrs
        model: models.Model = cls._meta.model
        field_name_list, to_ignore_list = cls.interface._handleCustomFields(model)

        fields = [
            field
            for field in model._meta.get_fields()
            if field.name not in to_ignore_list
        ]
        special_fields = [getattr(model, field_name) for field_name in field_name_list]
        declared_fields = set(cls._meta.pre_declarations) | set(
            cls._meta.post_declarations
        )

        for field in [*fields, *special_fields]:
            if field.name in [*attrs, *declared_fields]:
                continue  # Skip fields that are already set
            if isinstance(field, models.AutoField) or field.auto_created:
                continue  # Skip auto fields
            attrs[field.name] = get_field_value(field)

        obj = super()._generate(create, attrs)
        if isinstance(obj, list):
            for item in obj:
                cls._handleManyToManyFieldsAfterCreation(item, attrs)
        else:
            cls._handleManyToManyFieldsAfterCreation(obj, attrs)
        return obj

    @classmethod
    def _handleManyToManyFieldsAfterCreation(
        cls, obj: models.Model, attrs: dict
    ) -> None:
        for field in obj._meta.many_to_many:
            if field.name in attrs:
                m2m_values = attrs[field.name]
            else:
                m2m_values = get_m2m_field_value(field)
            if m2m_values:
                getattr(obj, field.name).set(m2m_values)

    @classmethod
    def _adjust_kwargs(cls, **kwargs) -> dict:
        # Remove ManyToMany fields from kwargs before object creation
        model: Type[models.Model] = cls._meta.model
        m2m_fields = {field.name for field in model._meta.many_to_many}
        for field_name in m2m_fields:
            kwargs.pop(field_name, None)
        return kwargs

    @classmethod
    def _create(
        cls, model_class: Type[models.Model], *args, **kwargs
    ) -> models.Model | list[models.Model]:
        kwargs = cls._adjust_kwargs(**kwargs)
        if cls._adjustmentMethod is not None:
            return cls.__createWithGenerateFunc(create=True, attrs=kwargs)
        return cls._modelCreation(model_class, **kwargs)

    @classmethod
    def _build(
        cls, model_class: Type[models.Model], *args, **kwargs
    ) -> models.Model | list[models.Model]:
        kwargs = cls._adjust_kwargs(**kwargs)
        if cls._adjustmentMethod is not None:
            return cls.__createWithGenerateFunc(create=True, attrs=kwargs)
        return cls._modelBuilding(model_class, **kwargs)

    @classmethod
    def _modelCreation(cls, model_class: Type[models.Model], **kwargs) -> models.Model:
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        obj.full_clean()
        obj.save()
        return obj

    @classmethod
    def _modelBuilding(cls, model_class: Type[models.Model], **kwargs) -> models.Model:
        obj = model_class()
        for field, value in kwargs.items():
            setattr(obj, field, value)
        return obj

    @classmethod
    def __createWithGenerateFunc(
        cls, create: bool, attrs: dict
    ) -> models.Model | list[models.Model]:
        if cls._adjustmentMethod is None:
            raise ValueError("generate_func is not defined")
        records = cls._adjustmentMethod(**attrs)
        if isinstance(records, dict):
            if create:
                return cls._modelCreation(cls._meta.model, **records)
            return cls._modelBuilding(cls._meta.model, **records)

        created_objects = []
        for record in records:
            if create:
                created_objects.append(cls._modelCreation(cls._meta.model, **record))
            else:
                created_objects.append(cls._modelBuilding(cls._meta.model, **record))
        return created_objects


def get_field_value(field: models.Field) -> object:
    """
    Returns a suitable value for a given Django model field.
    """
    if field.null:
        if random.choice([True] + 9 * [False]):
            return None

    if isinstance(field, MeasurementField):
        base_unit = field.base_unit
        value = Decimal(str(random.uniform(0, 10_000))[:10])
        return factory.LazyAttribute(lambda _: Measurement(value, base_unit))
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
            return factory.LazyAttribute(lambda _: exrex.getone(regex))
        else:
            return factory.Faker("text", max_nb_chars=max_length)
    elif isinstance(field, models.TextField):
        return factory.Faker("paragraph")
    elif isinstance(field, models.IntegerField):
        return factory.Faker("random_int")
    elif isinstance(field, models.DecimalField):
        max_digits = field.max_digits
        decimal_places = field.decimal_places
        left_digits = max_digits - decimal_places
        return factory.Faker(
            "pydecimal",
            left_digits=left_digits,
            right_digits=decimal_places,
            positive=True,
        )
    elif isinstance(field, models.FloatField):
        return factory.Faker("pyfloat", positive=True)
    elif isinstance(field, models.DateField):
        return factory.Faker("date_between", start_date="-1y", end_date="today")
    elif isinstance(field, models.DateTimeField):
        return factory.Faker(
            "date_time_between", start_date="-1y", end_date="now", tzinfo=timezone.utc
        )
    elif isinstance(field, models.BooleanField):
        return factory.Faker("pybool")
    elif isinstance(field, models.ForeignKey):
        # Create or get an instance of the related model
        if hasattr(field.related_model, "_general_manager_class"):
            related_factory = field.related_model._general_manager_class.Factory
            return related_factory()
        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(field.related_model.objects.all())
            if related_instances:
                return factory.LazyAttribute(lambda _: random.choice(related_instances))
            else:
                raise ValueError(
                    f"No factory found for {field.related_model.__name__} and no instances found"
                )
    elif isinstance(field, models.OneToOneField):
        # Similar to ForeignKey
        if hasattr(field.related_model, "_general_manager_class"):
            related_factory = field.related_model._general_manager_class.Factory
            return related_factory()
        else:
            # If no factory exists, pick a random existing instance
            related_instances = list(field.related_model.objects.all())
            if related_instances:
                return factory.LazyAttribute(lambda _: random.choice(related_instances))
            else:
                raise ValueError(
                    f"No factory found for {field.related_model.__name__} and no instances found"
                )
    elif isinstance(field, models.EmailField):
        return factory.Faker("email")
    elif isinstance(field, models.URLField):
        return factory.Faker("url")
    elif isinstance(field, models.GenericIPAddressField):
        return factory.Faker("ipv4")
    elif isinstance(field, models.UUIDField):
        return factory.Faker("uuid4")
    elif isinstance(field, models.DurationField):
        return factory.Faker("time_delta")
    else:
        return None  # For unsupported field types


def get_m2m_field_value(field):
    """
    Returns a list of instances for a ManyToMany field.
    """
    related_factory = globals().get(f"{field.related_model.__name__}Factory")
    existing_instances = list(field.related_model.objects.all())

    if related_factory:
        # Use existing instances if available, otherwise create new ones
        if existing_instances:
            max_instances = len(existing_instances)
            num_instances = random.randint(0, min(max_instances, 15))
            return random.sample(existing_instances, num_instances)
        else:
            # No existing instances, create a few
            num_to_create = random.randint(1, 3)
            new_instances = [related_factory() for _ in range(num_to_create)]
            return new_instances
    else:
        # No factory exists, use existing instances
        if existing_instances:
            max_instances = len(existing_instances)
            num_instances = random.randint(0, max_instances)
            return random.sample(existing_instances, num_instances)
        else:
            raise ValueError(
                f"No factory found for {field.related_model.__name__} and no instances found"
            )
