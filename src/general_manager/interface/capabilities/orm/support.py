"""Support, read, and query capabilities for ORM-backed interfaces."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, TYPE_CHECKING, Callable, ClassVar, Type, cast

from django.db import models
from django.db.models import Subquery
from django.utils import timezone
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.interface.base_interface import InterfaceBase
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.orm_utils.django_manager_utils import (
    DjangoManagerSelector,
)
from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    FieldDescriptor,
    build_field_descriptors,
)
from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
    PayloadNormalizer,
)
from simple_history.models import HistoricalChanges

from ._compat import call_with_observability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase
    from general_manager.interface.utils.database_interface_protocols import (
        SupportsHistory,
    )
    from .history import OrmHistoryCapability


class OrmPersistenceSupportCapability(BaseCapability):
    """Expose shared helpers to work with Django ORM models."""

    name: ClassVar[CapabilityName] = "orm_support"

    def get_database_alias(self, interface_cls: type["OrmInterfaceBase"]) -> str | None:
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
        interface_cls: type["OrmInterfaceBase"],
        *,
        only_active: bool = True,
    ) -> models.Manager:
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
        interface_cls._active_manager = selector.cached_active  # type: ignore[attr-defined]
        return manager

    def get_queryset(self, interface_cls: type["OrmInterfaceBase"]) -> models.QuerySet:
        """
        Retrieve an active queryset for the interface's model.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The interface class whose underlying Django model will be queried.

        Returns:
            models.QuerySet: A Django QuerySet containing the model's active records.
        """
        manager = self.get_manager(interface_cls, only_active=True)
        queryset: models.QuerySet = manager.all()  # type: ignore[assignment]
        return queryset

    def get_payload_normalizer(
        self, interface_cls: type["OrmInterfaceBase"]
    ) -> PayloadNormalizer:
        """
        Return a PayloadNormalizer configured for the interface's Django model.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class providing the `_model` attribute.

        Returns:
            PayloadNormalizer: A normalizer instance bound to the interface's Django `models.Model`.
        """
        return PayloadNormalizer(cast(Type[models.Model], interface_cls._model))

    def get_field_descriptors(
        self, interface_cls: type["OrmInterfaceBase"]
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
            interface_cls._field_descriptors = descriptors  # type: ignore[attr-defined]
        return descriptors

    def resolve_many_to_many(
        self,
        interface_instance: "OrmInterfaceBase",
        field_call: str,
        field_name: str,
    ) -> models.QuerySet[Any]:
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
            models.QuerySet[Any]: A queryset of the related target records or their historical snapshots when applicable.
        """
        manager = getattr(interface_instance._instance, field_call)
        queryset = manager.all()
        model_cls = getattr(queryset, "model", None)
        interface_cls = interface_instance.__class__
        if isinstance(model_cls, type) and issubclass(model_cls, HistoricalChanges):
            target_field = interface_cls._model._meta.get_field(field_name)  # type: ignore[attr-defined]
            target_model = getattr(target_field, "related_model", None)
            if target_model is None:
                return manager.none()
            django_target_model = cast(Type[models.Model], target_model)
            related_attr = None
            for rel_field in model_cls._meta.get_fields():  # type: ignore[attr-defined]
                related_model = getattr(rel_field, "related_model", None)
                if related_model == target_model:
                    related_attr = rel_field.name
                    break
            if related_attr is None:
                return django_target_model._default_manager.none()
            related_id_field = f"{related_attr}_id"
            related_ids_query = queryset.values_list(related_id_field, flat=True)
            if (
                not hasattr(target_model, "history")
                or interface_instance._search_date is None  # type: ignore[attr-defined]
            ):
                return django_target_model._default_manager.filter(
                    pk__in=Subquery(related_ids_query)
                )
            target_history_model = cast("Type[SupportsHistory]", target_model)

            related_ids = list(related_ids_query)
            if not related_ids:
                return django_target_model._default_manager.none()  # type: ignore[return-value]
            return cast(
                models.QuerySet[Any],
                target_history_model.history.as_of(
                    interface_instance._search_date
                ).filter(  # type: ignore[attr-defined]
                    pk__in=related_ids
                ),
            )

        return queryset


class OrmReadCapability(BaseCapability):
    """Fetch ORM instances (or historical snapshots) for interface instances."""

    name: ClassVar[CapabilityName] = "read"

    def get_data(self, interface_instance: "OrmInterfaceBase") -> Any:
        """
        Retrieve the current model instance or a historical snapshot for the given ORM interface instance.

        Parameters:
            interface_instance (OrmInterfaceBase): Interface wrapper containing the primary key (`pk`) and optional `_search_date` used to request a historical snapshot.

        Returns:
            The live model instance or a historical record corresponding to `interface_instance.pk` (type depends on the model/history handler).

        Raises:
            model.DoesNotExist: If no matching live instance or historical record exists.
        """

        def _perform() -> Any:
            interface_cls = interface_instance.__class__
            support = get_support_capability(interface_cls)
            only_active = not is_soft_delete_enabled(interface_cls)
            manager = support.get_manager(
                interface_cls,
                only_active=only_active,
            )
            model_cls = interface_cls._model
            pk = interface_instance.pk
            instance: Any | None
            missing_error: Exception | None = None
            try:
                instance = manager.get(pk=pk)
            except model_cls.DoesNotExist as error:  # type: ignore[attr-defined]
                instance = None
                missing_error = error
            search_date = interface_instance._search_date
            if search_date is not None:
                if search_date <= timezone.now() - timedelta(
                    seconds=interface_cls.historical_lookup_buffer_seconds
                ):
                    historical: Any | None
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
            if instance is not None:
                return instance
            if missing_error is not None:
                raise missing_error
            raise model_cls.DoesNotExist  # type: ignore[attr-defined]

        return call_with_observability(
            interface_instance,
            operation="read",
            payload={"pk": interface_instance.pk},
            func=_perform,
        )

    def get_attribute_types(
        self,
        interface_cls: type["OrmInterfaceBase"],
    ) -> dict[str, dict[str, Any]]:
        """
        Return a mapping of field names to copies of their field descriptor metadata.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The ORM interface class whose field descriptors will be queried.

        Returns:
            dict[str, dict[str, Any]]: A dict mapping each field name to a shallow copy of that field's `metadata` dictionary.
        """
        descriptors = get_support_capability(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {
            name: dict(descriptor.metadata) for name, descriptor in descriptors.items()
        }

    def get_attributes(
        self,
        interface_cls: type["OrmInterfaceBase"],
    ) -> dict[str, Callable[[Any], Any]]:
        """
        Return a mapping of field names to their accessor callables for the given ORM interface class.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The interface class whose model field descriptors will be used.

        Returns:
            dict[str, Callable[[Any], Any]]: A dictionary mapping each field name to a callable that, given an instance, returns that field's value.
        """
        descriptors = get_support_capability(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {name: descriptor.accessor for name, descriptor in descriptors.items()}

    def get_field_type(
        self,
        interface_cls: type["OrmInterfaceBase"],
        field_name: str,
    ) -> type:
        """
        Determine the effective type associated with a model field.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose underlying Django model contains the field.
            field_name (str): Name of the field on the model.

        Returns:
            type: The class used to represent the field's values: the related model's `_general_manager_class` when the field is a relation to a model that exposes that attribute, otherwise the Python type of the field object.
        """
        field = interface_cls._model._meta.get_field(field_name)
        if (
            field.is_relation
            and field.related_model
            and hasattr(field.related_model, "_general_manager_class")
        ):
            return field.related_model._general_manager_class  # type: ignore[attr-defined]
        return type(field)


class OrmQueryCapability(BaseCapability):
    """Expose DatabaseBucket operations via the capability configuration."""

    name: ClassVar[CapabilityName] = "query"

    def filter(
        self,
        interface_cls: type["OrmInterfaceBase"],
        **kwargs: Any,
    ) -> DatabaseBucket:
        """
        Builds a DatabaseBucket representing a queryset filtered by the provided lookup kwargs.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose model and configuration determine queryset construction.
            **kwargs: Lookup expressions passed through the payload normalizer; may include the special key `include_inactive` to include inactive/soft-deleted records.

        Returns:
            DatabaseBucket: A container holding the resulting Django queryset (cast to the model's queryset type), the interface's parent class, and the normalized filter kwargs.
        """
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket:
            """
            Builds a DatabaseBucket for the given interface class using the provided filter kwargs.

            Returns:
                DatabaseBucket: A bucket containing the resulting Django queryset, the interface's parent class, and the normalized filter kwargs.
            """
            include_flag, normalized = self._normalize_kwargs(interface_cls, kwargs)
            return self._build_bucket(
                interface_cls,
                include_inactive=include_flag,
                normalized_kwargs=normalized,
            )

        return call_with_observability(
            interface_cls,
            operation="query.filter",
            payload=payload_snapshot,
            func=_perform,
        )

    def exclude(
        self,
        interface_cls: type["OrmInterfaceBase"],
        **kwargs: Any,
    ) -> DatabaseBucket:
        """
        Builds a DatabaseBucket representing a queryset that excludes records matching the provided filter criteria.

        Parameters:
                interface_cls (type[OrmInterfaceBase]): The ORM interface class whose model and metadata are used to construct the queryset.
                **kwargs: Filter lookup expressions to apply as exclusion criteria. May include the special key `include_inactive` (bool) to control whether inactive/soft-deleted records are considered.

        Returns:
                DatabaseBucket: A container holding the resulting Django queryset, the interface's parent class, and the normalized filter dictionary used for the exclusion.
        """
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket:
            """
            Builds a DatabaseBucket for an exclude query by normalizing the provided filter kwargs.

            Calls the capability's normalization to determine whether inactive records are included and to obtain normalized filters, then constructs a DatabaseBucket representing the queryset with those filters applied as an exclusion.

            Returns:
                DatabaseBucket: The bucket containing the queryset (with excluded matches) and associated metadata.
            """
            include_flag, normalized = self._normalize_kwargs(interface_cls, kwargs)
            return self._build_bucket(
                interface_cls,
                include_inactive=include_flag,
                normalized_kwargs=normalized,
                exclude=True,
            )

        return call_with_observability(
            interface_cls,
            operation="query.exclude",
            payload=payload_snapshot,
            func=_perform,
        )

    def _normalize_kwargs(
        self,
        interface_cls: type["OrmInterfaceBase"],
        kwargs: dict[str, Any],
    ) -> tuple[bool, dict[str, Any]]:
        """
        Extracts an `include_inactive` flag from the provided kwargs and returns it alongside the remaining filter kwargs normalized for the interface's model.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose model and payload normalizer are used for normalization.
            kwargs (dict[str, Any]): Filter keyword arguments; may include the key `"include_inactive"`.

        Returns:
            tuple: A pair where the first item is `True` if `"include_inactive"` was set in `kwargs`, `False` otherwise; the second item is a dict of the remaining filter kwargs after normalization.
        """
        payload = dict(kwargs)
        include_inactive = bool(payload.pop("include_inactive", False))
        support = get_support_capability(interface_cls)
        normalizer = support.get_payload_normalizer(interface_cls)
        normalized_kwargs = normalizer.normalize_filter_kwargs(payload)
        return include_inactive, normalized_kwargs

    def _build_bucket(
        self,
        interface_cls: type["OrmInterfaceBase"],
        *,
        include_inactive: bool,
        normalized_kwargs: dict[str, Any],
        exclude: bool = False,
    ) -> DatabaseBucket:
        """
        Builds a DatabaseBucket containing a queryset for the given interface class filtered or excluded by the provided normalized query kwargs.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): Interface class whose model/queryset is used.
            include_inactive (bool): If True, use the interface's manager that includes inactive (soft-deleted) records.
            normalized_kwargs (dict[str, Any]): Normalized lookup kwargs to apply to the queryset.
            exclude (bool): If True, remove records matching `normalized_kwargs`; otherwise include them.

        Returns:
            DatabaseBucket: Contains the resulting Django queryset for the interface's model, the interface's parent class, and a copy of the normalized kwargs.
        """
        support = get_support_capability(interface_cls)
        queryset_base = support.get_queryset(interface_cls)
        if include_inactive:
            queryset_base = support.get_manager(
                interface_cls,
                only_active=False,
            ).all()
        queryset = (
            queryset_base.exclude(**normalized_kwargs)
            if exclude
            else queryset_base.filter(**normalized_kwargs)
        )
        return DatabaseBucket(
            cast(models.QuerySet[models.Model], queryset),
            interface_cls._parent_class,
            dict(normalized_kwargs),
        )


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
    interface_cls: type["OrmInterfaceBase"],
) -> OrmPersistenceSupportCapability:
    """
    Resolve and return the "orm_support" capability instance for the given interface class.

    Parameters:
        interface_cls (type): The ORM interface class to query for the capability.

    Returns:
        OrmPersistenceSupportCapability: The resolved persistence support capability instance.
    """
    return interface_cls.require_capability(  # type: ignore[return-value]
        "orm_support",
        expected_type=OrmPersistenceSupportCapability,
    )


def is_soft_delete_enabled(interface_cls: type["OrmInterfaceBase"]) -> bool:
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
    interface_cls: type["OrmInterfaceBase"],
) -> OrmHistoryCapability:
    """
    Retrieve the history capability instance associated with the given ORM interface class.

    Parameters:
        interface_cls (type[OrmInterfaceBase]): The ORM interface class to query for its history capability.

    Returns:
        OrmHistoryCapability: The `history` capability instance bound to the provided interface class.
    """
    from .history import OrmHistoryCapability

    return interface_cls.require_capability(  # type: ignore[return-value]
        "history",
        expected_type=OrmHistoryCapability,
    )
