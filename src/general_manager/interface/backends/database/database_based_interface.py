"""Database-backed interface implementation for GeneralManager classes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, ClassVar, Generic, Type, TypeVar, cast

from django.db import models, transaction
from django.db.models import NOT_PROVIDED, Subquery
from django.utils import timezone

from general_manager.interface.base_interface import (
    InterfaceBase,
    classPostCreationMethod,
    classPreCreationMethod,
    generalManagerClassName,
    attributes,
    interfaceBaseClass,
    newlyCreatedGeneralManagerClass,
    newlyCreatedInterfaceClass,
    relatedClass,
    AttributeTypedDict,
)
from general_manager.interface.capabilities.base import CapabilityName, Capability
from general_manager.manager.input import Input
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.interface.database_interface_protocols import (
    SupportsActivation,
    SupportsHistory,
)
from general_manager.interface.utils.errors import (
    DuplicateFieldNameError,
    InvalidFieldTypeError,
    InvalidFieldValueError,
    MissingActivationSupportError,
    UnknownFieldError,
)
from general_manager.interface.utils.field_descriptors import FieldDescriptor
from general_manager.interface.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
)
from general_manager.interface.utils.payload_normalizer import PayloadNormalizer
from simple_history.models import HistoricalChanges
from simple_history.utils import update_change_reason  # type: ignore

HistoryModelT = TypeVar("HistoryModelT", bound=models.Model)
WritableModelT = TypeVar("WritableModelT", bound=models.Model)


class OrmPersistenceInterface(InterfaceBase, Generic[HistoryModelT]):
    """Interface implementation that persists data using Django ORM models."""

    _model: Type[HistoryModelT]
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    database: ClassVar[str | None] = None
    _active_manager: ClassVar[models.Manager[models.Model] | None] = None
    _field_descriptors: ClassVar[dict[str, FieldDescriptor] | None] = None
    historical_lookup_buffer_seconds: ClassVar[int] = 5
    _search_date: datetime | None
    capability_overrides: ClassVar[dict[CapabilityName, type["Capability"]]] = {}
    lifecycle_capability_name: ClassVar[CapabilityName | None] = "orm_lifecycle"

    @staticmethod
    def _default_base_model_class() -> type[GeneralManagerBasisModel]:
        """Database-backed interfaces default to full GeneralManagerModel with history fields."""
        return GeneralManagerModel

    @classmethod
    def _get_database_alias(cls) -> str | None:
        """
        Get the configured database alias for ORM operations.

        Returns:
            The database alias string, or `None` if no alias is configured.
        """
        return cls._orm_support_capability().get_database_alias(cls)

    @classmethod
    def _get_manager(cls, *, only_active: bool = True) -> models.Manager[HistoryModelT]:
        """
        Return the model manager for the interface's model, bound to the configured database alias.

        Parameters:
            only_active (bool): If True, return the active manager (respecting soft-delete); if False, return the manager that exposes all records.

        Returns:
            manager (django.db.models.Manager[HistoryModelT]): The selected Django model manager for the interface's model.
        """
        support = cls._orm_support_capability()
        manager = support.get_manager(cls, only_active=only_active)
        return cast(models.Manager[HistoryModelT], manager)

    @classmethod
    def _get_queryset(cls) -> models.QuerySet[HistoryModelT]:
        """
        Get a QuerySet for the interface's model from the active manager and configured database alias.

        Returns:
            queryset (models.QuerySet[HistoryModelT]): A QuerySet of active model instances bound to the configured database alias.
        """
        support = cls._orm_support_capability()
        queryset = support.get_queryset(cls)
        return cast(models.QuerySet[HistoryModelT], queryset)

    @classmethod
    def _payload_normalizer(cls) -> PayloadNormalizer:
        """
        Create a PayloadNormalizer configured for this interface's Django model.

        Returns:
            PayloadNormalizer: An instance bound to the interface's `_model` used to normalize and validate payloads for database operations.
        """
        support = cls._orm_support_capability()
        return support.get_payload_normalizer(cls)

    @classmethod
    def _get_field_descriptors(cls) -> dict[str, FieldDescriptor]:
        """
        Lazily build and return the mapping of field names to FieldDescriptor objects for this interface class.

        Returns:
            dict[str, FieldDescriptor]: Mapping from attribute name to its FieldDescriptor. The mapping is cached on the class after first construction.
        """
        support = cls._orm_support_capability()
        return support.get_field_descriptors(cls)

    @classmethod
    def _orm_support_capability(cls) -> "OrmPersistenceSupportCapability":
        handler = cls.get_capability_handler("orm_support")
        if isinstance(handler, OrmPersistenceSupportCapability):
            return handler
        return OrmPersistenceSupportCapability()

    @classmethod
    def _history_capability(cls) -> "OrmHistoryCapability":
        handler = cls.get_capability_handler("history")
        if isinstance(handler, OrmHistoryCapability):
            return handler
        return OrmHistoryCapability()

    @classmethod
    def _mutation_capability(cls) -> "OrmMutationCapability":
        handler = cls.get_capability_handler("orm_mutation")
        if isinstance(handler, OrmMutationCapability):
            return handler
        return OrmMutationCapability()

    @classmethod
    def _lifecycle_capability(cls) -> "OrmLifecycleCapability":
        handler = cls.get_capability_handler("orm_lifecycle")
        if isinstance(handler, OrmLifecycleCapability):
            return handler
        return OrmLifecycleCapability()

    def __init__(
        self,
        *args: Any,
        search_date: datetime | None = None,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the interface and load its underlying model instance.

        Positional and keyword arguments are forwarded to the parent interface to establish identification.
        search_date, when provided, causes the instance to be resolved from historical records at or before that timestamp; if omitted, the current database record is loaded.

        Parameters:
            *args: Positional identification arguments forwarded to the parent interface.
            search_date (datetime | None): Timestamp to select a historical record; `None` to use the current record.
            **kwargs: Keyword identification arguments forwarded to the parent interface.
        """
        super().__init__(*args, **kwargs)
        self.pk = self.identification["id"]
        self._search_date = self.normalize_search_date(search_date)
        self._instance: HistoryModelT = self.get_data()

    @staticmethod
    def normalize_search_date(search_date: datetime | None) -> datetime | None:
        """
        Normalize a search_date to a timezone-aware datetime if provided.

        Parameters:
            search_date (datetime | None): The input search date, potentially naive.
        Returns:
            datetime | None: The normalized timezone-aware datetime, or `None` if no date was provided.
        """
        if search_date is not None and timezone.is_naive(search_date):
            search_date = timezone.make_aware(search_date)
        return search_date

    @classmethod
    def get_historical_record(
        cls, instance: HistoryModelT, search_date: datetime | None = None
    ) -> HistoryModelT | None:
        """
        Retrieves the most recent historical record of a model instance at or before a specified date.

        Parameters:
            instance (HistoryModelT): Model instance whose history is queried.
            search_date (datetime | None): Cutoff datetime used to select the historical record.

        Returns:
            HistoryModelT | None: Historical instance as of the specified date, if available.
        """
        handler = cls._history_capability()
        result = handler.get_historical_record(cls, instance, search_date)
        return cast(HistoryModelT | None, result)

    @classmethod
    def _get_historical_record_by_pk(
        cls, pk: Any, search_date: datetime | None
    ) -> HistoryModelT | None:
        """
        Fetch the model's historical snapshot for the given primary key as of the provided search_date.

        Parameters:
            pk (Any): Primary key value of the requested record.
            search_date (datetime | None): A timezone-aware datetime to query history up to; if `None`, no historical lookup is performed.

        Returns:
            HistoryModelT | None: The historical model instance whose history_date is less than or equal to `search_date`, or `None` if no such historical record exists. The configured database alias is respected when querying history.
        """
        handler = cls._history_capability()
        result = handler.get_historical_record_by_pk(cls, pk, search_date)
        return cast(HistoryModelT | None, result)

    @classmethod
    def get_attribute_types(cls) -> dict[str, AttributeTypedDict]:
        """
        Build a mapping of the model's exposed attribute names to their type metadata for this interface.

        Includes direct model fields, custom fields, foreign-key relations, many-to-many relations, and reverse one-to-many relations. Related models that expose a `_general_manager_class` use that class as the attribute type. Many-to-many and reverse relation attributes are exposed with a "_list" suffix. GenericForeignKey fields are omitted.

        Returns:
            dict[str, AttributeTypedDict]: Mapping from attribute name to metadata with keys:
                - `type`: the attribute's Python type or general-manager class for related models (common Django field classes are translated to built-in Python types),
                - `is_derived`: `True` for attributes computed from relations, `False` for direct model fields,
                - `is_required`: `True` if the attribute must be present (e.g., field null is False and no default),
                - `is_editable`: `True` if the field is editable on the model,
                - `default`: the field's default value or `None` when not applicable.

        Raises:
            DuplicateFieldNameError: if a generated attribute name collides with an existing attribute name.
        """
        descriptors = cls._get_field_descriptors()
        return {name: descriptor.metadata for name, descriptor in descriptors.items()}

    @classmethod
    def get_attributes(cls) -> dict[str, Callable[[OrmPersistenceInterface], Any]]:
        """
        Builds a mapping of attribute names to accessor callables for this interface.

        The mapping includes accessors for model fields, custom fields, foreign-key relations, many-to-many relations, and reverse relations; each value is a callable that accepts a OrmPersistenceInterface instance and returns the attribute's value.

        Returns:
            dict[str, Callable[[OrmPersistenceInterface], Any]]: Mapping from attribute name to an accessor callable.
        """
        descriptors = cls._get_field_descriptors()
        return {name: descriptor.accessor for name, descriptor in descriptors.items()}

    def _resolve_many_to_many(
        self: OrmPersistenceInterface, field_call: str, field_name: str
    ) -> models.QuerySet[Any]:
        """
        Resolve a many-to-many relation on the interface's instance and return the related model instances or historical snapshots.

        Given the name of the relation accessor (field_call) and the corresponding model field name (field_name), this returns the related objects. When the relation points to django-simple-history change rows, it resolves underlying related IDs and returns either the current related model instances or their historical records as of the interface's search date.

        Parameters:
            field_call (str): Attribute name on the instance used to obtain the related manager (e.g., "tags").
            field_name (str): The many-to-many field name on the interface's model used to identify the relation.

        Returns:
            models.QuerySet[Any]: A queryset of related model instances (or historical snapshots when applicable); returns an empty queryset if no related objects can be resolved.
        """
        manager = getattr(self._instance, field_call)
        queryset = manager.all()
        model_cls = getattr(queryset, "model", None)
        if isinstance(model_cls, type) and issubclass(model_cls, HistoricalChanges):
            target_field = self._model._meta.get_field(field_name)
            target_model = getattr(target_field, "related_model", None)
            if target_model is None:
                return manager.none()
            target_model = cast(Type[models.Model], target_model)
            related_attr = None
            for rel_field in model_cls._meta.get_fields():  # type: ignore[attr-defined]
                related_model = getattr(rel_field, "related_model", None)
                if related_model == target_model:
                    related_attr = rel_field.name
                    break
            if related_attr is None:
                return target_model._default_manager.none()
            related_id_field = f"{related_attr}_id"
            related_ids_query = queryset.values_list(related_id_field, flat=True)
            if not hasattr(target_model, "history") or self._search_date is None:
                return target_model._default_manager.filter(
                    pk__in=Subquery(related_ids_query)
                )
            target_model = cast(Type[SupportsHistory], target_model)

            related_ids = list(related_ids_query)
            if not related_ids:
                return target_model._default_manager.none()  # type: ignore[return-value]
            return cast(
                models.QuerySet[Any],
                target_model.history.as_of(self._search_date).filter(  # type: ignore[attr-defined]
                    pk__in=related_ids
                ),
            )

        return queryset

    @staticmethod
    def handle_custom_fields(
        model: Type[models.Model] | models.Model,
    ) -> tuple[list[str], list[str]]:
        """
        Identify custom fields on a model and related helper fields to ignore.

        Parameters:
            model (type[models.Model] | models.Model): Model class or instance to inspect.

        Returns:
            tuple[list[str], list[str]]: Names of custom fields and associated helper fields to ignore.
        """
        field_names: list[str] = []
        ignore: list[str] = []
        for field_name in OrmPersistenceInterface._get_custom_fields(model):
            ignore.append(f"{field_name}_value")
            ignore.append(f"{field_name}_unit")
            field_names.append(field_name)
        return field_names, ignore

    @staticmethod
    def _get_custom_fields(model: Type[models.Model] | models.Model) -> list[str]:
        """
        Return names of fields declared directly on the model class.

        Parameters:
            model (type[models.Model] | models.Model): Model class or instance to inspect.

        Returns:
            list[str]: Field names declared as class attributes.
        """
        return [
            field.name
            for field in model.__dict__.values()
            if isinstance(field, models.Field)
        ]

    @classmethod
    def _pre_create(
        cls,
        name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
        base_model_class: type[GeneralManagerBasisModel] = GeneralManagerModel,
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        lifecycle = cls._lifecycle_capability()
        typed_interface = cast(type["OrmPersistenceInterface"], interface)
        return lifecycle.pre_create(
            name=name,
            attrs=attrs,
            interface=typed_interface,
            base_model_class=base_model_class,
        )

    @staticmethod
    def _post_create(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        typed_interface = cast(type["OrmPersistenceInterface"], interface_class)
        lifecycle = typed_interface._lifecycle_capability()
        typed_model = cast(type[GeneralManagerBasisModel] | None, model)
        lifecycle.post_create(
            new_class=new_class,
            interface_class=typed_interface,
            model=typed_model,
        )

    @classmethod
    def get_field_type(cls, field_name: str) -> type:
        """
        Return the interface type used to represent a model field.

        Parameters:
            field_name (str): Name of the model field to inspect.

        Returns:
            type: The related model's general manager class if the field is a relation and the related model exposes `_general_manager_class`; otherwise the Django field class.
        """
        field = cls._model._meta.get_field(field_name)
        if (
            field.is_relation
            and field.related_model
            and hasattr(field.related_model, "_general_manager_class")
        ):
            return field.related_model._general_manager_class  # type: ignore
        return type(field)


class OrmWritableInterface(OrmPersistenceInterface[WritableModelT]):
    """DB-based interface with write capabilities for models supporting persistence."""

    _model: Type[WritableModelT]
    _interface_type = "database"
    capability_overrides: ClassVar[dict[CapabilityName, type["Capability"]]] = {}

    @classmethod
    def _apply_many_to_many(
        cls,
        instance: WritableModelT,
        many_to_many_kwargs: dict[str, list[Any]],
        history_comment: str | None,
    ) -> WritableModelT:
        """
        Apply many-to-many relation values to a model instance.

        Keys in many_to_many_kwargs are expected to end with the suffix "_id_list"; the suffix is removed to obtain the relation attribute name. Each relation is applied via the relation manager's set() method. A change reason is recorded when provided.

        Parameters:
            instance (WritableModelT): The model instance to update.
            many_to_many_kwargs (dict[str, list[Any]]): Mapping from relation keys (with "_id_list" suffix) to lists of related ids.

        Returns:
            WritableModelT: The same instance with updated many-to-many relations.
        """
        mutation = cls._mutation_capability()
        updated = mutation.apply_many_to_many(
            cls,
            instance,
            many_to_many_kwargs=many_to_many_kwargs,
            history_comment=history_comment,
        )
        return cast(WritableModelT, updated)

    @classmethod
    def _assign_simple_attributes(
        cls,
        instance: WritableModelT,
        kwargs: dict[str, Any],
    ) -> WritableModelT:
        """
        Set non-relational writable fields on an instance.

        Skips values equal to `NOT_PROVIDED`, assigns each remaining value on the given instance, and translates assignment `ValueError`/`TypeError` into `InvalidFieldValueError` and `InvalidFieldTypeError`.

        Parameters:
            instance (WritableModelT): The model instance to modify.
            kwargs (dict[str, Any]): Mapping of attribute names to values to apply.

        Returns:
            WritableModelT: The same instance with attributes updated.

        Raises:
            InvalidFieldValueError: If setting an attribute raises a `ValueError`.
            InvalidFieldTypeError: If setting an attribute raises a `TypeError`.
        """
        mutation = cls._mutation_capability()
        updated = mutation.assign_simple_attributes(cls, instance, kwargs)
        return cast(WritableModelT, updated)

    @classmethod
    def _save_with_history(
        cls,
        instance: WritableModelT,
        creator_id: int | None,
        history_comment: str | None,
    ) -> int:
        """
        Persist the given model instance after validation and optionally record the actor and a history comment.

        If the model exposes a `changed_by_id` attribute, `creator_id` will be assigned to it before saving. Validation is performed via `full_clean()` and the instance is saved using the interface's configured database alias when present. If `history_comment` is provided, it is attached to the instance's history/change reason.

        Parameters:
            instance (WritableModelT): Model instance to validate and persist.
            creator_id (int | None): ID to assign to `changed_by_id` on the instance when available.
            history_comment (str | None): Optional change reason to attach to the instance's history.

        Returns:
            int: The primary key of the saved instance.
        """
        mutation = cls._mutation_capability()
        return mutation.save_with_history(
            cls,
            instance,
            creator_id=creator_id,
            history_comment=history_comment,
        )

    @classmethod
    def _update_change_reason(
        cls,
        instance: models.Model,
        history_comment: str | None,
    ) -> None:
        update_change_reason(instance, history_comment)


# Backwards compatibility aliases (to be removed in a future major release)
DBBasedInterface = OrmPersistenceInterface
WritableDBBasedInterface = OrmWritableInterface

from general_manager.interface.capabilities.orm import (
    OrmCreateCapability,
    OrmDeleteCapability,
    OrmHistoryCapability,
    OrmLifecycleCapability,
    OrmMutationCapability,
    OrmPersistenceSupportCapability,
    OrmQueryCapability,
    OrmReadCapability,
    OrmUpdateCapability,
    OrmValidationCapability,
)
from general_manager.interface.capabilities.observability import (
    LoggingObservabilityCapability,
)

OrmPersistenceInterface.capability_overrides.update(
    {
        "orm_support": OrmPersistenceSupportCapability,
        "orm_lifecycle": OrmLifecycleCapability,
        "read": OrmReadCapability,
        "validation": OrmValidationCapability,
        "history": OrmHistoryCapability,
        "query": OrmQueryCapability,
        "observability": LoggingObservabilityCapability,
    }
)
OrmWritableInterface.capability_overrides.update(
    {
        "orm_support": OrmPersistenceSupportCapability,
        "orm_lifecycle": OrmLifecycleCapability,
        "orm_mutation": OrmMutationCapability,
        "read": OrmReadCapability,
        "create": OrmCreateCapability,
        "update": OrmUpdateCapability,
        "delete": OrmDeleteCapability,
        "validation": OrmValidationCapability,
        "history": OrmHistoryCapability,
        "query": OrmQueryCapability,
        "observability": LoggingObservabilityCapability,
    }
)
