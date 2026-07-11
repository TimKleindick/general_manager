"""Support, read, and query capabilities for ORM-backed interfaces."""

from __future__ import annotations

import inspect
import re
from collections.abc import Hashable
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Type, cast

from django.core.exceptions import FieldDoesNotExist
from django.db import models
from django.db.models import Subquery
from django.db.models.manager import ManagerDescriptor
from django.utils import timezone
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.cache.run_context import current_calculation_run_context
from general_manager.interface.base_interface import AttributeTypedDict, InterfaceBase
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.orm_utils.django_manager_utils import (
    DjangoManagerSelector,
)
from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    FieldDescriptor,
    _iter_reverse_relations,
    build_field_descriptors,
)
from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
    PayloadNormalizer,
)
from general_manager.interface.utils.models import ActiveManager
from simple_history.models import HistoricalChanges
from simple_history.manager import HistoryDescriptor

from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase
    from general_manager.interface.utils.database_interface_protocols import (
        SupportsHistory,
    )
    from .history import OrmHistoryCapability
    from general_manager.manager.general_manager import GeneralManager

type OrmInterfaceClass = type["OrmInterfaceBase[models.Model]"]
type OrmInterfaceInstance = "OrmInterfaceBase[models.Model]"
type FilterKwargs = dict[str, object]
_PayloadNormalizerType = PayloadNormalizer


class OrmPersistenceSupportCapability(BaseCapability):
    """Expose shared helpers to work with Django ORM models."""

    name: ClassVar[CapabilityName] = "orm_support"

    def get_database_alias(self, interface_cls: OrmInterfaceClass) -> str | None:
        """
        Retrieve the database alias declared on an ORM interface class.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The ORM interface class to inspect for a `database` attribute.

        Returns:
            str | None: The value of the class attribute `database` if present, otherwise `None`.
        """
        return getattr(interface_cls, "database", None)

    def get_manager(
        self,
        interface_cls: OrmInterfaceClass,
        *,
        only_active: bool = True,
    ) -> models.Manager[models.Model]:
        """
        Obtain the Django manager for the interface's model, selecting between the active (soft-delete filtered) or all manager and honoring the interface's database alias.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class providing the Django model and optional metadata.
            only_active (bool): If True (default), return the active manager; if False, return the unfiltered/all manager.

        Returns:
            django.db.models.Manager: The resolved manager for the interface's model.

        Notes:
            This function also caches the resolved active manager onto interface_cls._active_manager.
        """
        soft_delete = is_soft_delete_enabled(interface_cls)
        selector = DjangoManagerSelector(
            model=interface_cls._model,
            database_alias=self.get_database_alias(interface_cls),
            use_soft_delete=soft_delete,
            cached_active=getattr(interface_cls, "_active_manager", None),
        )
        manager = selector.active_manager() if only_active else selector.all_manager()
        interface_cls._active_manager = selector.cached_active
        return manager

    def get_queryset(
        self, interface_cls: OrmInterfaceClass
    ) -> models.QuerySet[models.Model]:
        """
        Retrieve an active queryset for the interface's model.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The interface class whose underlying Django model will be queried.

        Returns:
            models.QuerySet: A Django QuerySet containing the model's active records.
        """
        manager = self.get_manager(interface_cls, only_active=True)
        return manager.all()

    def get_payload_normalizer(
        self, interface_cls: OrmInterfaceClass
    ) -> PayloadNormalizer:
        """
        Return a PayloadNormalizer configured for the interface's Django model.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class providing the `_model` attribute.

        Returns:
            PayloadNormalizer: A normalizer instance bound to the interface's Django `models.Model`.
        """
        normalizer_cls = PayloadNormalizer
        normalizer = getattr(interface_cls, "_payload_normalizer", None)
        if (
            not isinstance(normalizer_cls, type)
            or not isinstance(normalizer, normalizer_cls)
            or (normalizer.model is not interface_cls._model)
        ):
            normalizer = normalizer_cls(interface_cls._model)
            cast(Any, interface_cls)._payload_normalizer = normalizer
        return normalizer

    def get_field_descriptors(
        self, interface_cls: OrmInterfaceClass
    ) -> dict[str, FieldDescriptor]:
        """
        Get or build cached field descriptors for the given ORM interface class.

        If descriptors are not already present on the interface class, this populates
        and caches them on the class as `_field_descriptors`.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The ORM interface class to inspect.

        Returns:
            dict[str, FieldDescriptor]: Mapping of field names to their FieldDescriptor.
        """
        descriptors = getattr(interface_cls, "_field_descriptors", None)
        if descriptors is None:
            descriptors = build_field_descriptors(
                interface_cls,
                resolve_many=self.resolve_many_to_many,
            )
            interface_cls._field_descriptors = descriptors
        return descriptors

    def resolve_many_to_many(
        self,
        interface_instance: OrmInterfaceInstance,
        field_call: str,
        field_name: str,
    ) -> models.QuerySet[models.Model]:
        """
        Resolve a many-to-many relationship for an interface instance and return a queryset of the related target records, using historical snapshots when applicable.

        If the relation's through/model is a HistoricalChanges subclass, the function:
        - Locates the corresponding related attribute on the historical model and collects related IDs.
        - If the target model has no history support or the interface instance has no search date, returns the live target model queryset filtered by those IDs.
        - If the target model supports history and a search date is present, returns the historical snapshot queryset as of that date filtered by those IDs.
        If the target field or related attribute cannot be resolved, an empty queryset for the appropriate model is returned. If the through/model is not historical, the original related manager's queryset is returned.

        Parameters:
            interface_instance (OrmInterfaceBase): The interface wrapper containing the model instance and optional search date.
            field_call (str): Attribute name on the instance to access the related manager (e.g., the many-to-many manager accessor).
            field_name (str): Field name on the interface's model corresponding to the relation target.

        Returns:
            models.QuerySet[models.Model]: A queryset of the related target records or their historical snapshots when applicable.

        Raises:
            AttributeError: If ``field_call`` is not a related-manager
                attribute on the wrapped ORM instance.
            FieldDoesNotExist: If ``field_name`` is not a field on the
                interface model.
        """
        manager = getattr(interface_instance._instance, field_call)
        queryset = manager.all()
        model_cls = getattr(queryset, "model", None)
        interface_cls = interface_instance.__class__
        if isinstance(model_cls, type) and issubclass(model_cls, HistoricalChanges):
            historical_model_cls = cast(type[models.Model], model_cls)
            target_field = interface_cls._model._meta.get_field(field_name)
            target_model = getattr(target_field, "related_model", None)
            if target_model is None:
                return cast(models.QuerySet[models.Model], manager.none())
            django_target_model = cast(Type[models.Model], target_model)
            related_attr = None
            for rel_field in historical_model_cls._meta.get_fields():
                related_model = getattr(rel_field, "related_model", None)
                if related_model == target_model:
                    related_attr = rel_field.name
                    break
            if related_attr is None:
                return cast(
                    models.QuerySet[models.Model],
                    django_target_model._default_manager.none(),
                )
            related_id_field = f"{related_attr}_id"
            related_ids_query = queryset.values_list(related_id_field, flat=True)
            if (
                not hasattr(target_model, "history")
                or interface_instance._search_date is None
            ):
                return cast(
                    models.QuerySet[models.Model],
                    django_target_model._default_manager.filter(
                        pk__in=Subquery(related_ids_query)
                    ),
                )
            target_history_model = cast("Type[SupportsHistory]", target_model)

            related_ids = list(related_ids_query)
            if not related_ids:
                return cast(
                    models.QuerySet[models.Model],
                    django_target_model._default_manager.none(),
                )
            return cast(
                models.QuerySet[models.Model],
                target_history_model.history.as_of(
                    interface_instance._search_date
                ).filter(pk__in=related_ids),
            )

        return cast(models.QuerySet[models.Model], queryset)


class OrmReadCapability(BaseCapability):
    """Fetch ORM instances (or historical snapshots) for interface instances."""

    name: ClassVar[CapabilityName] = "read"

    @staticmethod
    def _supports_history_first(
        interface_cls: OrmInterfaceClass,
        model_cls: type[models.Model],
    ) -> bool:
        """Admit only the default, static-safe historical read integration."""
        from .history import OrmHistoryCapability

        if inspect.getattr_static(
            interface_cls, "get_capability_handler", None
        ) is not inspect.getattr_static(InterfaceBase, "get_capability_handler"):
            return False
        try:
            history_handler = interface_cls.get_capability_handler("history")
            support_handler = interface_cls.get_capability_handler("orm_support")
            soft_delete_handler = interface_cls.get_capability_handler("soft_delete")
            marker = vars(model_cls._meta).get("simple_history_manager_attribute")
            if type(marker) is not str:
                return False
            descriptor = inspect.getattr_static(model_cls, marker)
            database_alias = inspect.getattr_static(interface_cls, "database", None)
        except (AttributeError, TypeError):
            return False
        if (
            type(history_handler) is not OrmHistoryCapability
            or type(support_handler) is not OrmPersistenceSupportCapability
            or type(soft_delete_handler) is not SoftDeleteCapability
            or type(descriptor) is not HistoryDescriptor
            or not isinstance(descriptor.model, type)
            or not issubclass(descriptor.model, HistoricalChanges)
            or (database_alias is not None and type(database_alias) is not str)
        ):
            return False
        try:
            soft_delete = soft_delete_handler.is_enabled()
            default_manager = model_cls._meta.default_manager
            all_objects = inspect.getattr_static(model_cls, "all_objects", None)
        except (AttributeError, TypeError):
            return False
        if soft_delete and all_objects is not None:
            all_manager = (
                all_objects.manager
                if type(all_objects) is ManagerDescriptor
                else all_objects
            )
            return (
                type(default_manager) is ActiveManager
                and type(all_manager) is models.Manager
            )
        return type(default_manager) is models.Manager

    def get_data(self, interface_instance: OrmInterfaceInstance) -> models.Model:
        """
        Retrieve the current model instance or a historical snapshot for the given ORM interface instance.

        Parameters:
            interface_instance (OrmInterfaceBase): Interface wrapper containing the primary key (`pk`) and optional `_search_date` used to request a historical snapshot.

        Returns:
            The live model instance or a historical record corresponding to `interface_instance.pk` (type depends on the model/history handler).

        Raises:
            model.DoesNotExist: If no matching live instance or historical record exists.
        """

        def _perform() -> models.Model:
            interface_cls = interface_instance.__class__
            support = get_support_capability(interface_cls)
            only_active = not is_soft_delete_enabled(interface_cls)
            manager = support.get_manager(
                interface_cls,
                only_active=only_active,
            )
            model_cls = interface_cls._model
            pk = interface_instance.pk
            search_date = interface_instance._search_date
            now = timezone.now() if search_date is not None else None
            historical_cutoff = (
                now - timedelta(seconds=interface_cls.historical_lookup_buffer_seconds)
                if now is not None
                else None
            )
            if (
                search_date is not None
                and historical_cutoff is not None
                and search_date <= historical_cutoff
                and self._supports_history_first(interface_cls, model_cls)
            ):
                history_handler = _history_capability_for(interface_cls)
                historical_first = history_handler.get_historical_record_by_pk(
                    interface_cls,
                    pk,
                    search_date,
                )
                if historical_first is not None:
                    return historical_first
                manager.get(pk=pk)
                raise model_cls.DoesNotExist
            instance: models.Model | None
            missing_error: Exception | None = None
            try:
                instance = manager.get(pk=pk)
            except model_cls.DoesNotExist as error:
                instance = None
                missing_error = error
            if search_date is not None:
                if historical_cutoff is not None and search_date <= historical_cutoff:
                    historical: models.Model | None
                    history_handler = _history_capability_for(interface_cls)
                    if instance is not None:
                        historical = history_handler.get_historical_record(
                            interface_cls,
                            instance,
                            search_date,
                        )
                    else:
                        historical = history_handler.get_historical_record_by_pk(
                            interface_cls,
                            pk,
                            search_date,
                        )
                    if historical is not None:
                        return historical
                    if missing_error is not None:
                        raise missing_error
                    raise model_cls.DoesNotExist
            if instance is not None:
                return instance
            if missing_error is not None:
                raise missing_error
            raise model_cls.DoesNotExist

        context = current_calculation_run_context()
        read_func = (
            _perform
            if context is None
            else lambda: context.get_or_set(
                _orm_instance_cache_key(interface_instance),
                _perform,
            )
        )

        return call_with_observability(
            interface_instance,
            operation="read",
            payload={"pk": interface_instance.pk},
            func=read_func,
        )

    def get_attribute_types(
        self,
        interface_cls: OrmInterfaceClass,
    ) -> dict[str, AttributeTypedDict]:
        """
        Return a mapping of field names to copies of their field descriptor metadata.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The ORM interface class whose field descriptors will be queried.

        Returns:
            dict[str, AttributeTypedDict]: A dict mapping each field name to a shallow copy of that field's `metadata` dictionary.
        """
        descriptors = get_support_capability(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {
            name: cast(AttributeTypedDict, dict(descriptor.metadata))
            for name, descriptor in descriptors.items()
        }

    def get_attributes(
        self,
        interface_cls: OrmInterfaceClass,
    ) -> dict[str, Callable[[OrmInterfaceInstance], object]]:
        """
        Return a mapping of field names to their accessor callables for the given ORM interface class.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The interface class whose model field descriptors will be used.

        Returns:
            dict[str, Callable[[OrmInterfaceBase[models.Model]], object]]: A dictionary mapping each field name to a callable that, given an instance, returns that field's value.
        """
        descriptors = get_support_capability(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {name: descriptor.accessor for name, descriptor in descriptors.items()}

    def get_field_type(
        self,
        interface_cls: OrmInterfaceClass,
        field_name: str,
    ) -> type[object]:
        """
        Determine the effective type associated with a model field.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose underlying Django model contains the field.
            field_name (str): Name of the field on the model.

        Returns:
            type: The Django field class for stored model fields, the related
            model's `_general_manager_class` for managed relations, or the
            descriptor metadata ``"type"`` value for synthetic interface fields.

        Raises:
            FieldDoesNotExist: If the field is not present on the model and no
                synthetic descriptor exists for ``field_name``.
        """
        try:
            field = interface_cls._model._meta.get_field(field_name)
        except FieldDoesNotExist:
            descriptors = get_support_capability(interface_cls).get_field_descriptors(
                interface_cls
            )
            descriptor = descriptors.get(field_name)
            if descriptor is not None:
                return descriptor.metadata["type"]
            raise
        if getattr(field, "name", field_name) != field_name:
            descriptor = (
                get_support_capability(interface_cls)
                .get_field_descriptors(interface_cls)
                .get(field_name)
            )
            if descriptor is not None:
                return descriptor.metadata["type"]
        if (
            field.is_relation
            and field.related_model
            and hasattr(field.related_model, "_general_manager_class")
        ):
            return cast(type[object], field.related_model._general_manager_class)
        return type(field)


class InvalidSearchDateTypeError(TypeError):
    """Compatibility error for callers that validate search_date with a custom message.

    The built-in ORM query path raises ``SearchDateInputError`` for invalid raw
    values and ``SearchDateNormalizationError`` for invalid normalized values.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SearchDateNormalizationError(TypeError):
    """Raised when a normalized search_date is not a datetime."""

    def __init__(self) -> None:
        super().__init__("search_date must be a datetime instance after normalization.")


class SearchDateInputError(TypeError):
    """Raised when a search_date input is not a datetime or date."""

    def __init__(self) -> None:
        super().__init__("search_date must be a datetime or date instance.")


class AmbiguousReverseFilterAliasError(ValueError):
    """Raised when a snake_case reverse filter alias resolves to multiple relations."""

    def __init__(self, alias: str, targets: tuple[str, str]) -> None:
        left, right = targets
        super().__init__(
            "Ambiguous reverse filter alias "
            f"'{alias}' resolves to both '{left}' and '{right}'."
        )


def _to_snake_case(name: str) -> str:
    """Convert a CamelCase class name into snake_case."""
    snake = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", snake)
    return snake.lower()


@lru_cache(maxsize=None)
def _build_reverse_filter_alias_metadata(
    model: type[models.Model],
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    """Build cached alias and ambiguity metadata for reverse filter roots."""
    meta = getattr(model, "_meta", None)
    get_fields = getattr(meta, "get_fields", None)
    if not callable(get_fields):
        return {}, {}
    try:
        all_fields = tuple(get_fields())
    except TypeError:
        return {}, {}

    alias_map: dict[str, str] = {}
    ambiguous_aliases: dict[str, tuple[str, str]] = {}
    # Only include forward field names (not reverse relations) to avoid masking
    # ambiguity when an explicit related_name coincidentally matches an alias.
    model_field_names = {
        field.name
        for field in all_fields
        if not (
            getattr(field, "is_relation", False)
            and getattr(field, "one_to_many", False)
        )
    }

    for reverse_relation in _iter_reverse_relations(model):
        related_model = getattr(reverse_relation, "related_model", None)
        if related_model is None:
            continue

        target_root = reverse_relation.name
        relation_field = getattr(reverse_relation, "field", None)
        explicit_query_root = getattr(relation_field, "_related_query_name", None)
        explicit_accessor = getattr(relation_field, "_related_name", None)
        alias = (
            target_root
            if (
                isinstance(target_root, str)
                and target_root
                and target_root != "+"
                and (explicit_query_root is not None or explicit_accessor is not None)
            )
            else _to_snake_case(related_model.__name__)
        )

        if alias in model_field_names:
            continue

        if alias in ambiguous_aliases:
            continue

        existing = alias_map.get(alias)
        if existing is not None and existing != target_root:
            ambiguous_aliases[alias] = (existing, target_root)
            alias_map.pop(alias, None)
            continue
        alias_map[alias] = target_root

    return alias_map, ambiguous_aliases


def _build_reverse_filter_alias_map(
    model: type[models.Model],
) -> dict[str, str]:
    """Build a cached map of snake_case reverse filter roots to Django lookup roots."""
    alias_map, _ = _build_reverse_filter_alias_metadata(model)
    return alias_map


def _translate_reverse_filter_aliases(
    model: type[models.Model],
    kwargs: FilterKwargs,
) -> FilterKwargs:
    """Rewrite snake_case reverse-relation filter segments to Django's native lookup roots."""
    translated: FilterKwargs = {}

    for key, value in kwargs.items():
        translated[_translate_reverse_filter_key(model, key)] = value

    return translated


@lru_cache(maxsize=8192)
def _translate_reverse_filter_key(model: type[models.Model], key: str) -> str:
    """Rewrite only relation-path segments that are known reverse aliases."""
    parts = key.split("__")
    translated_parts: list[str] = []
    current_model: type[models.Model] | None = model

    for index, part in enumerate(parts):
        if current_model is None:
            translated_parts.extend(parts[index:])
            break

        resolved_name, field = _resolve_filter_segment(current_model, part)
        if field is None:
            translated_parts.extend(parts[index:])
            break

        translated_parts.append(resolved_name)
        if not getattr(field, "is_relation", False):
            translated_parts.extend(parts[index + 1 :])
            break

        current_model = cast(
            type[models.Model] | None, getattr(field, "related_model", None)
        )
    else:
        return "__".join(translated_parts)

    return "__".join(translated_parts)


def _resolve_filter_segment(
    model: type[models.Model],
    segment: str,
) -> tuple[str, models.Field[object, object] | models.ForeignObjectRel | None]:
    """Resolve a lookup segment to a real Django field or reverse relation."""
    meta = getattr(model, "_meta", None)
    get_field = getattr(meta, "get_field", None)
    if not callable(get_field):
        return segment, None

    alias_map, ambiguous_aliases = _build_reverse_filter_alias_metadata(model)
    targets = ambiguous_aliases.get(segment)
    if targets is not None:
        raise AmbiguousReverseFilterAliasError(segment, targets)

    try:
        field = cast(
            "models.Field[object, object] | models.ForeignObjectRel",
            get_field(segment),
        )
    except FieldDoesNotExist:
        actual_name = alias_map.get(segment)
        if actual_name is None:
            return segment, None
        field = cast(
            "models.Field[object, object] | models.ForeignObjectRel",
            get_field(actual_name),
        )
        return actual_name, field
    else:
        return segment, field


class OrmQueryCapability(BaseCapability):
    """Expose DatabaseBucket operations via the capability configuration."""

    name: ClassVar[CapabilityName] = "query"

    @staticmethod
    def _is_default_history_capability(history_handler: object) -> bool:
        handler_type = type(history_handler)
        return (
            handler_type.__module__
            == "general_manager.interface.capabilities.orm.history"
            and handler_type.__name__ == "OrmHistoryCapability"
        )

    def _trusted_query_source_signature(
        self,
        interface_cls: OrmInterfaceClass,
        support: OrmPersistenceSupportCapability,
        *,
        include_inactive: bool,
        search_date: datetime | None,
        historical: bool,
        history_handler: object | None,
        database_alias: str | None,
    ) -> Hashable | None:
        if type(support) is not OrmPersistenceSupportCapability:
            return None
        if historical and not self._is_default_history_capability(history_handler):
            return None
        return (
            "orm-query-source-v1",
            interface_cls,
            interface_cls._parent_class,
            interface_cls._model,
            database_alias,
            include_inactive,
            is_soft_delete_enabled(interface_cls),
            "historical" if historical else "live",
            search_date,
        )

    def _trusted_query_signature(
        self,
        source_signature: Hashable | None,
        *,
        exclude: bool,
        normalized_kwargs: FilterKwargs,
    ) -> Hashable | None:
        if source_signature is None:
            return None
        return (
            source_signature,
            "exclude" if exclude else "filter",
            DatabaseBucket._freeze_trusted_signature_payload(normalized_kwargs),
        )

    @staticmethod
    def _ensure_search_date_input(search_date: object) -> None:
        if not isinstance(search_date, (datetime, date)):
            raise SearchDateInputError

    @staticmethod
    def _ensure_search_date_normalized(search_date: object) -> None:
        if not isinstance(search_date, datetime):
            raise SearchDateNormalizationError

    def filter(
        self,
        interface_cls: OrmInterfaceClass,
        **kwargs: object,
    ) -> DatabaseBucket["GeneralManager"]:
        """
        Builds a DatabaseBucket representing a queryset filtered by the provided lookup kwargs.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose model and configuration determine queryset construction.
            **kwargs: Lookup expressions passed through the payload normalizer; may include `include_inactive` to include inactive/soft-deleted records and `search_date` to scope results to a historical snapshot.

        Returns:
            DatabaseBucket: A container holding the resulting Django queryset (cast to the model's queryset type), the interface's parent class, and the normalized filter kwargs.
        """
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket["GeneralManager"]:
            """
            Builds a DatabaseBucket for the given interface class using the provided filter kwargs.

            Returns:
                DatabaseBucket: A bucket containing the resulting Django queryset, the interface's parent class, and the normalized filter kwargs.
            """
            include_flag, normalized, search_date = self._normalize_kwargs(
                interface_cls, kwargs
            )
            return self._build_or_reuse_bucket(
                interface_cls,
                include_inactive=include_flag,
                normalized_kwargs=normalized,
                search_date=search_date,
            )

        return call_with_observability(
            interface_cls,
            operation="query.filter",
            payload=payload_snapshot,
            func=_perform,
        )

    def exclude(
        self,
        interface_cls: OrmInterfaceClass,
        **kwargs: object,
    ) -> DatabaseBucket["GeneralManager"]:
        """
        Builds a DatabaseBucket representing a queryset that excludes records matching the provided filter criteria.

        Parameters:
                interface_cls (type[OrmInterfaceBase]): The ORM interface class whose model and metadata are used to construct the queryset.
                **kwargs: Filter lookup expressions to apply as exclusion criteria. May include `include_inactive` (bool) to control whether inactive/soft-deleted records are considered and `search_date` to scope results historically.

        Returns:
                DatabaseBucket: A container holding the resulting Django queryset, the interface's parent class, and the normalized filter dictionary used for the exclusion.
        """
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket["GeneralManager"]:
            """
            Builds a DatabaseBucket for an exclude query by normalizing the provided filter kwargs.

            Calls the capability's normalization to determine whether inactive records are included and to obtain normalized filters, then constructs a DatabaseBucket representing the queryset with those filters applied as an exclusion.

            Returns:
                DatabaseBucket: The bucket containing the queryset (with excluded matches) and associated metadata.
            """
            include_flag, normalized, search_date = self._normalize_kwargs(
                interface_cls, kwargs
            )
            return self._build_or_reuse_bucket(
                interface_cls,
                include_inactive=include_flag,
                normalized_kwargs=normalized,
                exclude=True,
                search_date=search_date,
            )

        return call_with_observability(
            interface_cls,
            operation="query.exclude",
            payload=payload_snapshot,
            func=_perform,
        )

    def _normalize_kwargs(
        self,
        interface_cls: OrmInterfaceClass,
        kwargs: FilterKwargs,
    ) -> tuple[bool, FilterKwargs, datetime | None]:
        """
        Extracts an `include_inactive` flag from the provided kwargs and returns it alongside the remaining filter kwargs normalized for the interface's model.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose model and payload normalizer are used for normalization.
            kwargs: Filter keyword arguments; may include the key `"include_inactive"`.

        Returns:
            tuple: A tuple containing: (1) a boolean indicating whether inactive records are included, (2) the normalized filter kwargs, and (3) an optional `search_date` used to scope historical lookups.
        """
        payload = dict(kwargs)
        include_inactive = bool(payload.pop("include_inactive", False))
        search_date = payload.pop("search_date", None)
        if search_date is not None:
            self._ensure_search_date_input(search_date)
        if isinstance(search_date, date) and not isinstance(search_date, datetime):
            search_date = datetime.combine(search_date, time.min)
        normalize_date = getattr(interface_cls, "normalize_search_date", None)
        if callable(normalize_date):
            search_date = normalize_date(search_date)
        if search_date is not None:
            self._ensure_search_date_normalized(search_date)
            search_date = cast(datetime, search_date)
        support = get_support_capability(interface_cls)
        normalizer = support.get_payload_normalizer(interface_cls)
        translated_payload = _translate_reverse_filter_aliases(
            interface_cls._model,
            payload,
        )
        normalized_kwargs = normalizer.normalize_filter_kwargs(translated_payload)
        return include_inactive, normalized_kwargs, search_date

    def _run_scoped_query_bucket_signature(
        self,
        interface_cls: OrmInterfaceClass,
        *,
        include_inactive: bool,
        normalized_kwargs: FilterKwargs,
        exclude: bool,
        search_date: datetime | None,
    ) -> Hashable | None:
        support = get_support_capability(interface_cls)
        if support.__class__ is not OrmPersistenceSupportCapability:
            return None
        if search_date is not None and search_date <= timezone.now() - timedelta(
            seconds=interface_cls.historical_lookup_buffer_seconds
        ):
            return None
        queryset_base = (
            support.get_manager(interface_cls, only_active=False).all()
            if include_inactive
            else support.get_queryset(interface_cls)
        )
        source_signature = self._trusted_query_source_signature(
            interface_cls,
            support,
            include_inactive=include_inactive,
            search_date=search_date,
            historical=False,
            history_handler=None,
            database_alias=queryset_base.db,
        )
        return self._trusted_query_signature(
            source_signature,
            exclude=exclude,
            normalized_kwargs=normalized_kwargs,
        )

    def _build_or_reuse_bucket(
        self,
        interface_cls: OrmInterfaceClass,
        *,
        include_inactive: bool,
        normalized_kwargs: FilterKwargs,
        exclude: bool = False,
        search_date: datetime | None = None,
    ) -> DatabaseBucket["GeneralManager"]:
        context = current_calculation_run_context()
        cache_signature: Hashable | None = None
        if context is not None:
            cache_signature = self._run_scoped_query_bucket_signature(
                interface_cls,
                include_inactive=include_inactive,
                normalized_kwargs=normalized_kwargs,
                exclude=exclude,
                search_date=search_date,
            )
        if context is not None and cache_signature is not None:
            cached = context.get_orm_query_bucket(cache_signature)
            if isinstance(cached, DatabaseBucket):
                return cached._copy_for_run_context_reuse()

        bucket = self._build_bucket(
            interface_cls,
            include_inactive=include_inactive,
            normalized_kwargs=normalized_kwargs,
            exclude=exclude,
            search_date=search_date,
        )
        if context is not None and cache_signature is not None:
            context.set_orm_query_bucket(cache_signature, bucket)
            return bucket._copy_for_run_context_reuse()
        return bucket

    def _build_bucket(
        self,
        interface_cls: OrmInterfaceClass,
        *,
        include_inactive: bool,
        normalized_kwargs: FilterKwargs,
        exclude: bool = False,
        search_date: datetime | None = None,
    ) -> DatabaseBucket["GeneralManager"]:
        """
        Builds a DatabaseBucket containing a queryset for the given interface class filtered or excluded by the provided normalized query kwargs.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose model/queryset is used.
            include_inactive (bool): If True, use the interface's manager that includes inactive (soft-deleted) records.
            normalized_kwargs: Normalized lookup kwargs to apply to the queryset.
            exclude (bool): If True, remove records matching `normalized_kwargs`; otherwise include them.

        Returns:
            DatabaseBucket: Contains the resulting Django queryset for the interface's model, the interface's parent class, and a copy of the normalized kwargs.
        """
        support = get_support_capability(interface_cls)
        queryset_base = support.get_queryset(interface_cls)
        historical = False
        history_handler: object | None = None
        if include_inactive:
            queryset_base = cast(
                models.QuerySet[models.Model],
                support.get_manager(
                    interface_cls,
                    only_active=False,
                ).all(),
            )
        if search_date is not None and search_date <= timezone.now() - timedelta(
            seconds=interface_cls.historical_lookup_buffer_seconds
        ):
            from .history import HistoryNotSupportedError

            try:
                history_handler = _history_capability_for(interface_cls)
            except NotImplementedError as error:
                raise HistoryNotSupportedError(interface_cls.__name__) from error
            queryset_base = history_handler.get_historical_queryset(
                interface_cls,
                search_date,
            )
            historical = True
        queryset = (
            queryset_base.exclude(**normalized_kwargs)
            if exclude
            else queryset_base.filter(**normalized_kwargs)
        )
        bucket = DatabaseBucket(
            queryset,
            interface_cls._parent_class,
            {} if exclude else cast("dict[str, list[object]]", dict(normalized_kwargs)),
            cast("dict[str, list[object]]", dict(normalized_kwargs)) if exclude else {},
            search_date=search_date,
        )
        source_signature = self._trusted_query_source_signature(
            interface_cls,
            support,
            include_inactive=include_inactive,
            search_date=search_date,
            historical=historical,
            history_handler=history_handler,
            database_alias=queryset.db,
        )
        bucket._set_trusted_query_signature(
            self._trusted_query_signature(
                source_signature,
                exclude=exclude,
                normalized_kwargs=normalized_kwargs,
            )
        )
        return bucket


class SoftDeleteCapability(BaseCapability):
    """Track whether soft delete behavior should be applied."""

    name: ClassVar[CapabilityName] = "soft_delete"

    def __init__(self, enabled: bool = False) -> None:
        """
        Initialize the soft-delete capability with a default enabled state.

        Parameters:
                enabled (bool): Initial enabled state for soft-delete; True to enable, False to disable.
        """
        self.enabled = enabled

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
        """
        Initialize the capability's soft-delete state for the given interface class.

        Determines the default enabled state in this order: 1) use interface_cls._soft_delete_default if present; 2) else use interface_cls._model._meta.use_soft_delete if available; 3) otherwise fall back to the capability's current enabled value. Sets self.enabled to the resulting boolean and then calls the base setup with the same interface class.

        Parameters:
            interface_cls (type[InterfaceBase]): The interface class being configured.
        """
        default_marker = object()
        default = getattr(interface_cls, "_soft_delete_default", default_marker)
        if default is default_marker:
            model = getattr(interface_cls, "_model", None)
            meta = getattr(model, "_meta", None) if model is not None else None
            default = (
                getattr(meta, "use_soft_delete", self.enabled) if meta else self.enabled
            )
        self.enabled = bool(default)
        super().setup(interface_cls)

    def is_enabled(self) -> bool:
        """
        Indicates whether soft-delete behavior is enabled for this capability.

        Returns:
            bool: True if soft-delete is enabled, False otherwise.
        """
        return self.enabled

    def set_state(self, enabled: bool) -> None:
        """
        Set whether soft-delete is enabled for this capability.

        Parameters:
            enabled (bool): True to enable soft-delete behavior, False to disable it.
        """
        self.enabled = enabled


def get_support_capability(
    interface_cls: OrmInterfaceClass,
) -> OrmPersistenceSupportCapability:
    """
    Resolve and return the "orm_support" capability instance for the given interface class.

    Parameters:
        interface_cls (type): The ORM interface class to query for the capability.

    Returns:
        OrmPersistenceSupportCapability: The resolved persistence support capability instance.
    """
    return cast(
        OrmPersistenceSupportCapability,
        interface_cls.require_capability(
            "orm_support",
            expected_type=OrmPersistenceSupportCapability,
        ),
    )


def is_soft_delete_enabled(interface_cls: OrmInterfaceClass) -> bool:
    """
    Determine whether soft-delete behavior is enabled for the given interface class.

    Checks the interface's `soft_delete` capability first, then the model's `_meta.use_soft_delete`,
    and finally the interface's `_soft_delete_default`.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The interface class to evaluate.

    Returns:
        bool: `True` if soft-delete is enabled for the interface class, `False` otherwise.
    """
    handler = interface_cls.get_capability_handler("soft_delete")
    if isinstance(handler, SoftDeleteCapability):
        return handler.is_enabled()
    model = getattr(interface_cls, "_model", None)
    if model is not None:
        meta = getattr(model, "_meta", None)
        if meta is not None:
            return bool(getattr(meta, "use_soft_delete", False))
    return bool(getattr(interface_cls, "_soft_delete_default", False))


def _history_capability_for(
    interface_cls: OrmInterfaceClass,
) -> OrmHistoryCapability:
    """
    Retrieve the history capability instance associated with the given ORM interface class.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The ORM interface class to query for its history capability.

    Returns:
        OrmHistoryCapability: The `history` capability instance bound to the provided interface class.
    """
    from .history import OrmHistoryCapability

    return cast(
        OrmHistoryCapability,
        interface_cls.require_capability(
            "history",
            expected_type=OrmHistoryCapability,
        ),
    )


def _orm_instance_cache_key(
    interface_instance: OrmInterfaceInstance,
) -> tuple[object, ...]:
    interface_cls = interface_instance.__class__
    support = get_support_capability(interface_cls)
    only_active = not is_soft_delete_enabled(interface_cls)
    return (
        "orm_instance",
        interface_cls,
        interface_instance.pk,
        support.get_database_alias(interface_cls),
        only_active,
        interface_instance._search_date,
    )


def discard_orm_instance_cache(
    interface_cls: OrmInterfaceClass,
    pk: object,
) -> None:
    """Discard cached ORM reads for one interface class and primary key.

    This helper only acts when a calculation run context is active. It removes
    every cached read prefix for ``("orm_instance", interface_cls, pk)`` so
    subsequent reads in the same run can reload changed or deleted rows. Outside
    a calculation run it is a no-op.

    Parameters:
        interface_cls: ORM interface class whose read cache should be cleared.
        pk: Primary-key value used in the cached read key.

    Returns:
        None.
    """
    context = current_calculation_run_context()
    if context is not None:
        context.discard_prefix(("orm_instance", interface_cls, pk))
