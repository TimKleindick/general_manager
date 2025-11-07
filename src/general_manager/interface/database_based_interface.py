"""Database-backed interface implementation for GeneralManager classes."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta
from typing import Any, Callable, ClassVar, Generic, Type, TypeVar, cast

from django.db import models, transaction
from django.db.models import NOT_PROVIDED, Subquery
from django.utils import timezone

from general_manager.factory.auto_factory import AutoFactory
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
from general_manager.manager.input import Input
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.interface.database_interface_protocols import (
    SupportsActivation,
    SupportsHistory,
)
from general_manager.interface.utils.django_manager_utils import (
    DjangoManagerSelector,
)
from general_manager.interface.utils.errors import (
    DuplicateFieldNameError,
    InvalidFieldTypeError,
    InvalidFieldValueError,
    MissingActivationSupportError,
    UnknownFieldError,
)
from general_manager.interface.utils.field_descriptors import (
    FieldDescriptor,
    build_field_descriptors,
)
from general_manager.interface.models import (
    GeneralManagerBasisModel,
    GeneralManagerModel,
    SoftDeleteGeneralManagerModel,
    SoftDeleteMixin,
    get_full_clean_methode,
)
from general_manager.interface.utils.payload_normalizer import PayloadNormalizer
from simple_history.models import HistoricalChanges
from simple_history.utils import update_change_reason  # type: ignore
from general_manager.rule import Rule

HistoryModelT = TypeVar("HistoryModelT", bound=models.Model)
WritableModelT = TypeVar("WritableModelT", bound=models.Model)


class DBBasedInterface(InterfaceBase, Generic[HistoryModelT]):
    """Interface implementation that persists data using Django ORM models."""

    _model: Type[HistoryModelT]
    input_fields: ClassVar[dict[str, Input]] = {"id": Input(int)}
    database: ClassVar[str | None] = None
    _active_manager: ClassVar[models.Manager[models.Model] | None] = None
    _field_descriptors: ClassVar[dict[str, FieldDescriptor] | None] = None
    historical_lookup_buffer_seconds: ClassVar[int] = 5
    _search_date: datetime | None

    @classmethod
    def _get_database_alias(cls) -> str | None:
        """
        Get the configured database alias for ORM operations.

        Returns:
            The database alias string, or `None` if no alias is configured.
        """
        return getattr(cls, "database", None)

    @classmethod
    def _get_manager(cls, *, only_active: bool = True) -> models.Manager[HistoryModelT]:
        """
        Return the model manager for the interface's model, bound to the configured database alias.

        Parameters:
            only_active (bool): If True, return the active manager (respecting soft-delete); if False, return the manager that exposes all records.

        Returns:
            manager (django.db.models.Manager[HistoryModelT]): The selected Django model manager for the interface's model.
        """
        selector = DjangoManagerSelector[HistoryModelT](
            model=cls._model,
            database_alias=cls._get_database_alias(),
            use_soft_delete=getattr(cls, "_use_soft_delete", False),
            cached_active=cast(
                models.Manager[HistoryModelT] | None,
                getattr(cls, "_active_manager", None),
            ),
        )
        manager = selector.active_manager() if only_active else selector.all_manager()
        cls._active_manager = selector.cached_active  # type: ignore[attr-defined]
        return manager

    @classmethod
    def _get_queryset(cls) -> models.QuerySet[HistoryModelT]:
        """
        Get a QuerySet for the interface's model from the active manager and configured database alias.

        Returns:
            queryset (models.QuerySet[HistoryModelT]): A QuerySet of active model instances bound to the configured database alias.
        """
        manager = cls._get_manager(only_active=True)
        queryset: models.QuerySet[HistoryModelT] = manager.all()  # type: ignore[assignment]
        return cast(models.QuerySet[HistoryModelT], queryset)

    @classmethod
    def _payload_normalizer(cls) -> PayloadNormalizer:
        """
        Create a PayloadNormalizer configured for this interface's Django model.

        Returns:
            PayloadNormalizer: An instance bound to the interface's `_model` used to normalize and validate payloads for database operations.
        """
        return PayloadNormalizer(cast(Type[models.Model], cls._model))

    @classmethod
    def _get_field_descriptors(cls) -> dict[str, FieldDescriptor]:
        """
        Lazily build and return the mapping of field names to FieldDescriptor objects for this interface class.

        Returns:
            dict[str, FieldDescriptor]: Mapping from attribute name to its FieldDescriptor. The mapping is cached on the class after first construction.
        """
        if cls._field_descriptors is None:
            cls._field_descriptors = build_field_descriptors(cls)
        return cls._field_descriptors

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

    def get_data(self) -> HistoryModelT:
        """
        Load the model instance backing this interface, optionally resolving historical state as of the interface's search date.

        If a search date is set and is earlier than the current time by at least the class's
        historical_lookup_buffer_seconds, the method will attempt to return the most recent
        historical record at or before that timestamp; otherwise it returns the live instance.
        If no matching instance (live or historical) is found, the model's DoesNotExist is raised.

        Returns:
            HistoryModelT: The live model instance or a historical snapshot at or before the configured search date.
        """
        manager = self.__class__._get_manager(
            only_active=not getattr(self.__class__, "_use_soft_delete", False)
        )
        model_cls = self.__class__._model
        instance: HistoryModelT | None
        missing_error: Exception | None = None
        try:
            instance = cast(HistoryModelT, manager.get(pk=self.pk))
        except model_cls.DoesNotExist as error:  # type: ignore[attr-defined]
            instance = None
            missing_error = error
        if self._search_date is not None:
            if self._search_date <= timezone.now() - timedelta(
                seconds=self.__class__.historical_lookup_buffer_seconds
            ):
                historical: HistoryModelT | None
                if instance is not None:
                    historical = self.get_historical_record(instance, self._search_date)
                else:
                    historical = self.__class__._get_historical_record_by_pk(
                        self.pk, self._search_date
                    )
                if historical is not None:
                    return historical
        if instance is not None:
            return instance
        if missing_error is not None:
            raise missing_error
        raise model_cls.DoesNotExist  # type: ignore[attr-defined]

    @classmethod
    def filter(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Builds a DatabaseBucket from a queryset filtered by Django-style lookup kwargs.

        Filter keys are normalized using the interface's PayloadNormalizer before applying them. A special keyword argument `include_inactive` (default False) may be provided to use the manager that includes inactive records.

        Parameters:
            kwargs (Any): Django-style filter lookups; may include `include_inactive` (bool) to include inactive records.

        Returns:
            DatabaseBucket: A bucket wrapping the filtered queryset and the filter definitions used.
        """

        include_inactive = kwargs.pop("include_inactive", False)
        normalizer = cls._payload_normalizer()
        normalized_kwargs = normalizer.normalize_filter_kwargs(kwargs)
        queryset_base = cls._get_queryset()
        if include_inactive:
            queryset_base = cls._get_manager(only_active=False).all()
        queryset = queryset_base.filter(**normalized_kwargs)

        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            cls._parent_class,
            cls.__create_filter_definitions(**normalized_kwargs),
        )

    @classmethod
    def exclude(cls, **kwargs: Any) -> DatabaseBucket:
        """
        Exclude model instances matching the provided Django-style lookup expressions and wrap the resulting queryset in a DatabaseBucket.

        Parameters:
            **kwargs (Any): Django-style exclusion lookup expressions (field lookups and query expressions).

        Returns:
            DatabaseBucket: Bucket wrapping the queryset after applying the exclusions.
        """
        include_inactive = kwargs.pop("include_inactive", False)
        normalizer = cls._payload_normalizer()
        normalized_kwargs = normalizer.normalize_filter_kwargs(kwargs)
        queryset_base = cls._get_queryset()
        if include_inactive:
            queryset_base = cls._get_manager(only_active=False).all()
        queryset = queryset_base.exclude(**normalized_kwargs)

        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            cls._parent_class,
            cls.__create_filter_definitions(**normalized_kwargs),
        )

    @staticmethod
    def __create_filter_definitions(**kwargs: Any) -> dict[str, Any]:
        """
        Build a filter-definition mapping from Django-style kwargs.

        Parameters:
            **kwargs (Any): Filter expressions provided by the caller.

        Returns:
            dict[str, Any]: Mapping of filter names to their values.
        """
        filter_definitions: dict[str, Any] = {}
        for key, value in kwargs.items():
            filter_definitions[key] = value
        return filter_definitions

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
        if not isinstance(instance, SupportsHistory):
            return None

        history_source = cast(SupportsHistory, instance)
        history_manager = history_source.history
        database_alias = cls._get_database_alias()
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            cast(models.QuerySet, history_manager.filter(history_date__lte=search_date))
            .order_by("history_date")
            .last()
        )
        return cast(HistoryModelT, historical)

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
        if search_date is None or not hasattr(cls._model, "history"):
            return None
        history_manager = cls._model.history  # type: ignore[attr-defined]
        database_alias = cls._get_database_alias()
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            history_manager.filter(id=pk, history_date__lte=search_date)
            .order_by("history_date")
            .last()
        )
        return cast(HistoryModelT, historical)

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
    def get_attributes(cls) -> dict[str, Callable[[DBBasedInterface], Any]]:
        """
        Builds a mapping of attribute names to accessor callables for this interface.

        The mapping includes accessors for model fields, custom fields, foreign-key relations, many-to-many relations, and reverse relations; each value is a callable that accepts a DBBasedInterface instance and returns the attribute's value.

        Returns:
            dict[str, Callable[[DBBasedInterface], Any]]: Mapping from attribute name to an accessor callable.
        """
        descriptors = cls._get_field_descriptors()
        return {name: descriptor.accessor for name, descriptor in descriptors.items()}

    def _resolve_many_to_many(
        self: DBBasedInterface, field_call: str, field_name: str
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
        for field_name in DBBasedInterface._get_custom_fields(model):
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

    @staticmethod
    def _collect_model_fields(
        interface: interfaceBaseClass,
    ) -> tuple[dict[str, Any], type | None]:
        """
        Collects field-like attributes and an optional Meta class from an interface class.

        Parameters:
                interface (type): The interface class to inspect for model field definitions and an optional `Meta` inner class.

        Returns:
                model_fields (dict[str, Any]): Mapping of attribute names (excluding dunder names and the `Factory` attribute) to their values.
                meta_class (type | None): The `Meta` inner class found on the interface, or `None` if absent.
        """
        model_fields: dict[str, Any] = {}
        meta_class: type | None = None
        for attr_name, attr_value in interface.__dict__.items():
            if attr_name.startswith("__"):
                continue
            if attr_name == "Meta" and isinstance(attr_value, type):
                meta_class = attr_value
            elif attr_name == "Factory":
                continue
            else:
                model_fields[attr_name] = attr_value
        return model_fields, meta_class

    @staticmethod
    def _apply_meta_configuration(
        meta_class: type | None,
    ) -> tuple[type | None, bool, list[Any] | None]:
        """
        Extract soft-delete and rules configuration from a Meta-like class and return the cleaned Meta class along with the extracted values.

        If provided, removes `use_soft_delete` and `rules` attributes from `meta_class` and returns them separately.

        Parameters:
                meta_class (type | None): A Meta-like class or None.

        Returns:
                tuple[type | None, bool, list[Any] | None]: A tuple of (meta_class or None, use_soft_delete flag, rules list or None).
        """
        use_soft_delete = False
        rules: list[Any] | None = None
        if meta_class is None:
            return None, use_soft_delete, rules
        if hasattr(meta_class, "use_soft_delete"):
            use_soft_delete = meta_class.use_soft_delete
            delattr(meta_class, "use_soft_delete")
        if hasattr(meta_class, "rules"):
            rules = cast(list[Rule], meta_class.rules)
            delattr(meta_class, "rules")
        return meta_class, use_soft_delete, rules

    @staticmethod
    def _determine_model_bases(
        base_model_class: type[GeneralManagerBasisModel],
        use_soft_delete: bool,
    ) -> tuple[type[models.Model], ...]:
        """
        Select base model classes, adding soft-delete support when requested.

        When `use_soft_delete` is False, the original `base_model_class` is returned as the sole base.
        When `use_soft_delete` is True, returns a tuple that ensures the resulting model inherits soft-delete behavior:
        - If the provided base already is or can be replaced by the centrally defined soft-delete general manager model, that model is returned alone.
        - If the provided base already implements SoftDeleteMixin, it is returned alone.
        - Otherwise, returns (SoftDeleteMixin, base_model_class) so the new model gains soft-delete capabilities.

        Parameters:
            base_model_class: The candidate base model class to evaluate for inheritance.
            use_soft_delete: If True, include soft-delete-capable base(s) in the returned tuple.

        Returns:
            A tuple of model base classes to use when constructing the final model, arranged to provide soft-delete behavior when requested.
        """
        if not use_soft_delete:
            return (base_model_class,)
        if (
            base_model_class is GeneralManagerModel
            or base_model_class is GeneralManagerBasisModel
        ) and issubclass(SoftDeleteGeneralManagerModel, base_model_class):
            return (SoftDeleteGeneralManagerModel,)
        if issubclass(base_model_class, SoftDeleteMixin):
            return (base_model_class,)
        return (cast(type[models.Model], SoftDeleteMixin), base_model_class)

    @staticmethod
    def _finalize_model_class(
        model: type[GeneralManagerBasisModel],
        *,
        meta_class: type | None,
        use_soft_delete: bool,
        rules: list[Any] | None,
    ) -> None:
        """
        Finalize a dynamically constructed Django model class by applying Meta-derived configuration.

        When a Meta-like class and rules are provided, attach the rules to the model's _meta and set a model-level full_clean method. When use_soft_delete is true and a Meta-like class is present, record the soft-delete flag on the model's _meta.

        Parameters:
            model (type[GeneralManagerBasisModel]): The dynamically created Django model class to modify.
            meta_class (type | None): The Meta-like class extracted from the interface; when None no Meta-based modifications are applied.
            use_soft_delete (bool): Whether the model should be marked as supporting soft-delete; applied only if meta_class is present.
            rules (list[Any] | None): Validation or business rules to attach to model._meta; applied only if meta_class is present.
        """
        if meta_class and rules:
            model._meta.rules = rules  # type: ignore[attr-defined]
            model.full_clean = get_full_clean_methode(model)  # type: ignore[assignment]
        if meta_class and use_soft_delete:
            model._meta.use_soft_delete = use_soft_delete  # type: ignore[attr-defined]

    @staticmethod
    def _build_interface_class(
        interface: interfaceBaseClass,
        model: type[GeneralManagerBasisModel],
        use_soft_delete: bool,
    ) -> newlyCreatedInterfaceClass:
        """
        Create a new interface subclass bound to a specific Django model and soft-delete configuration.

        Parameters:
            interface (type): Base interface class to inherit from.
            model (type[GeneralManagerBasisModel]): The Django model type the new interface will manage.
            use_soft_delete (bool): Whether the interface should be marked to use soft-delete semantics.

        Returns:
            type: A new interface class that inherits from `interface`, with `_model` set to `model`, `_use_soft_delete` set to `use_soft_delete`, and `_field_descriptors` initialized to `None`.
        """
        interface_cls = type(interface.__name__, (interface,), {})
        interface_cls._model = model  # type: ignore[attr-defined]
        interface_cls._use_soft_delete = use_soft_delete  # type: ignore[attr-defined]
        interface_cls._field_descriptors = None  # type: ignore[attr-defined]
        return interface_cls

    @staticmethod
    def _build_factory_class(
        *,
        name: str,
        factory_definition: type | None,
        interface_cls: newlyCreatedInterfaceClass,
        model: type[GeneralManagerBasisModel],
    ) -> type[AutoFactory]:
        """
        Create a new AutoFactory subclass wired to a specific interface and model.

        Parameters:
            name (str): Base name used to form the new factory class name ("{name}Factory").
            factory_definition (type | None): Optional existing factory class whose non-dunder attributes will be copied into the new factory.
            interface_cls (type): Interface class to attach to the factory as the `interface` attribute.
            model (type[GeneralManagerBasisModel]): Django model class to set as `Meta.model` on the factory.

        Returns:
            type[AutoFactory]: A new AutoFactory subclass named "{name}Factory" with the provided attributes, `interface`, and `Meta` configured.
        """
        factory_attributes: dict[str, Any] = {}
        if factory_definition:
            for attr_name, attr_value in factory_definition.__dict__.items():
                if not attr_name.startswith("__"):
                    factory_attributes[attr_name] = attr_value
        factory_attributes["interface"] = interface_cls
        factory_attributes["Meta"] = type("Meta", (), {"model": model})
        return type(f"{name}Factory", (AutoFactory,), factory_attributes)

    @classmethod
    def _pre_create(
        cls,
        name: generalManagerClassName,
        attrs: attributes,
        interface: interfaceBaseClass,
        base_model_class: type[GeneralManagerBasisModel] = GeneralManagerModel,
    ) -> tuple[attributes, interfaceBaseClass, relatedClass]:
        """
        Create a concrete Django model, its corresponding interface class, and an associated factory, then attach them to the provided attributes mapping.

        This method collects model field definitions from the given interface, applies Meta-derived configuration (including optional soft-delete and validation rules), constructs a new model type, finalizes its class metadata, builds an interface class bound to that model, and produces a factory class for creating instances. The attrs mapping is updated with keys for "__module__", "Meta" (if present), "Interface", and "Factory" before being returned.

        Parameters:
                name: The desired name for the generated model and related classes.
                attrs: Attribute mapping to populate for the new manager class; will be mutated to include module, Interface, and Factory entries.
                interface: The interface base class whose declared fields and metadata will drive model construction.
                base_model_class: The base Django model type to inherit from when constructing the new model (defaults to GeneralManagerModel).

        Returns:
                tuple: (attrs, interface_cls, model) where `attrs` is the updated attributes mapping, `interface_cls` is the newly created interface class bound to the model, and `model` is the constructed Django model class.
        """
        model_fields, meta_class = cls._collect_model_fields(interface)
        model_fields["__module__"] = attrs.get("__module__")
        meta_class, use_soft_delete, rules = cls._apply_meta_configuration(meta_class)
        if meta_class:
            model_fields["Meta"] = meta_class
        base_classes = cls._determine_model_bases(base_model_class, use_soft_delete)
        model = cast(
            type[GeneralManagerBasisModel],
            type(name, base_classes, model_fields),
        )
        cls._finalize_model_class(
            model,
            meta_class=meta_class,
            use_soft_delete=use_soft_delete,
            rules=rules,
        )
        attrs["_interface_type"] = interface._interface_type
        interface_cls = cls._build_interface_class(interface, model, use_soft_delete)
        attrs["Interface"] = interface_cls

        # Build the associated factory class
        manager_factory = cast(type | None, attrs.pop("Factory", None))
        factory_definition = manager_factory or getattr(interface, "Factory", None)
        attrs["Factory"] = cls._build_factory_class(
            name=name,
            factory_definition=factory_definition,
            interface_cls=interface_cls,
            model=model,
        )

        return attrs, interface_cls, model

    @staticmethod
    def _post_create(
        new_class: newlyCreatedGeneralManagerClass,
        interface_class: newlyCreatedInterfaceClass,
        model: relatedClass,
    ) -> None:
        """
        Finalize dynamic wiring between a generated general manager, its interface, and the model.

        Sets the interface's _parent_class and the model's _general_manager_class, then attempts to assign
        interface-provided managers to the generated class as `objects` and, when soft-delete is enabled
        and available on the model, `all_objects`.

        Parameters:
            new_class (newlyCreatedGeneralManagerClass): The generated GeneralManager subclass.
            interface_class (newlyCreatedInterfaceClass): The concrete interface class for the model.
            model (relatedClass): The Django model associated with the manager.
        """
        interface_class._parent_class = new_class
        model._general_manager_class = new_class  # type: ignore
        try:
            new_class.objects = interface_class._get_manager()  # type: ignore[attr-defined]
        except AttributeError:
            pass
        if getattr(interface_class, "_use_soft_delete", False) and hasattr(
            model, "all_objects"
        ):
            new_class.all_objects = interface_class._get_manager(  # type: ignore[attr-defined]
                only_active=False
            )

    @classmethod
    def handle_interface(
        cls,
    ) -> tuple[classPreCreationMethod, classPostCreationMethod]:
        """
        Provide pre- and post-creation hooks for dynamic interface class creation.

        Returns:
            tuple: A pair (pre_create, post_create) where `pre_create` prepares attributes and configuration before a manager class is created, and `post_create` finalizes wiring and attaches managers after creation.
        """
        return cls._pre_create, cls._post_create

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


class WritableDBBasedInterface(DBBasedInterface[WritableModelT]):
    """DB-based interface with write capabilities for models supporting persistence."""

    _model: Type[WritableModelT]
    _interface_type = "database"

    @classmethod
    def create(
        cls, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Create a new model instance using the provided field values.

        Parameters:
            creator_id (int | None): ID of the user to record as the change author, or None to leave unset.
            history_comment (str | None): Optional comment to attach to the instance history.
            **kwargs: Field values used to populate the model; many-to-many relations may be provided as `<field>_id_list`.

        Returns:
            dict[str, Any]: Primary key of the newly created instance wrapped in a dict.

        Raises:
            UnknownFieldError: If kwargs contain names that do not correspond to model fields.
        """
        normalizer = cls._payload_normalizer()
        payload = dict(kwargs)
        normalizer.validate_keys(payload)
        simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(payload)
        normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
        normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
        instance = cls._assign_simple_attributes(cls._model(), normalized_simple)
        pk = cls._save_with_history(instance, creator_id, history_comment)
        cls._apply_many_to_many(instance, normalized_many, history_comment)
        return {"id": pk}

    def update(
        self, creator_id: int | None, history_comment: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """
        Apply provided field values to the current instance and persist the change with optional history metadata.

        Parameters:
            creator_id: ID of the user recording the change; used to set `changed_by_id` on the saved record.
            history_comment: Optional comment to attach as the change reason in the instance's history.
            **kwargs: Field names and values to apply. Many-to-many updates may be supplied using the `<relation>_id_list` convention.

        Returns:
            dict: A mapping containing the updated instance primary key as {"id": <pk>}.

        Raises:
            UnknownFieldError: If any provided kwarg does not correspond to a model field.
        """
        normalizer = self._payload_normalizer()
        payload = dict(kwargs)
        normalizer.validate_keys(payload)
        simple_kwargs, many_to_many_kwargs = normalizer.split_many_to_many(payload)
        normalized_simple = normalizer.normalize_simple_values(simple_kwargs)
        normalized_many = normalizer.normalize_many_values(many_to_many_kwargs)
        manager = self.__class__._get_manager(only_active=False)
        instance = self._assign_simple_attributes(
            manager.get(pk=self.pk), normalized_simple
        )
        pk = self._save_with_history(instance, creator_id, history_comment)
        self._apply_many_to_many(instance, normalized_many, history_comment)
        return {"id": pk}

    def delete(
        self, creator_id: int | None, history_comment: str | None = None
    ) -> dict[str, Any]:
        """
        Delete the current model instance, performing either a soft or hard delete.

        Parameters:
            creator_id (int | None): Identifier of the user performing the action, or None.
            history_comment (str | None): Optional comment to attach to the history entry.

        Returns:
            dict[str, Any]: Dictionary containing the key `"id"` with the primary key of the deleted instance.
        """
        manager = self.__class__._get_manager(only_active=False)
        instance = manager.get(pk=self.pk)
        if getattr(self.__class__, "_use_soft_delete", False):
            if not isinstance(instance, SupportsActivation):
                raise MissingActivationSupportError(instance.__class__.__name__)
            instance.is_active = False
            history_comment = (
                f"{history_comment} (deactivated)" if history_comment else "Deactivated"
            )
            return {
                "id": self._save_with_history(instance, creator_id, history_comment)
            }

        history_comment = (
            f"{history_comment} (deleted)" if history_comment else "Deleted"
        )
        try:
            instance.changed_by_id = creator_id  # type: ignore[attr-defined]
        except AttributeError:
            pass
        update_change_reason(instance, history_comment)
        database_alias = self.__class__._get_database_alias()
        atomic_context = (
            transaction.atomic(using=database_alias)
            if database_alias
            else transaction.atomic()
        )
        with atomic_context:
            if database_alias:
                instance.delete(using=database_alias)
            else:
                instance.delete()
        return {"id": self.pk}

    def deactivate(
        self, creator_id: int | None, history_comment: str | None = None
    ) -> dict[str, Any]:
        """
        Deprecated compatibility method that removes the current instance, preserving the model's history and soft-delete semantics.

        This method is deprecated and will emit a DeprecationWarning when called. It performs the same removal behavior as the interface's deletion path, including history change reasons and soft-delete handling when configured.

        Returns:
            result (dict[str, Any]): Dictionary containing the primary key of the affected instance, e.g. `{"id": pk}`.
        """
        warnings.warn(
            "deactivate() is deprecated; use delete() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.delete(creator_id=creator_id, history_comment=history_comment)

    @staticmethod
    def _apply_many_to_many(
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
        for key, value in many_to_many_kwargs.items():
            field_name = key.removesuffix("_id_list")
            getattr(instance, field_name).set(value)
        update_change_reason(instance, history_comment)
        return instance

    @staticmethod
    def _assign_simple_attributes(
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
        for key, value in kwargs.items():
            if value is NOT_PROVIDED:
                continue
            try:
                setattr(instance, key, value)
            except ValueError as error:
                raise InvalidFieldValueError(key, value) from error
            except TypeError as error:
                raise InvalidFieldTypeError(key, error) from error
        return instance

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
        database_alias = cls._get_database_alias()
        if database_alias:
            instance._state.db = database_alias  # type: ignore[attr-defined]
        atomic_context = (
            transaction.atomic(using=database_alias)
            if database_alias
            else transaction.atomic()
        )
        with atomic_context:
            try:
                instance.changed_by_id = creator_id  # type: ignore[attr-defined]
            except AttributeError:
                pass
            instance.full_clean()
            if database_alias:
                instance.save(using=database_alias)
            else:
                instance.save()
            if history_comment:
                update_change_reason(instance, history_comment)

        return instance.pk
