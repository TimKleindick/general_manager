"""Support, read, and query capabilities for ORM-backed interfaces."""

from __future__ import annotations

from datetime import datetime, timedelta
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
        return getattr(interface_cls, "database", None)

    def get_manager(
        self,
        interface_cls: type["OrmInterfaceBase"],
        *,
        only_active: bool = True,
    ) -> models.Manager:
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
        manager = self.get_manager(interface_cls, only_active=True)
        queryset: models.QuerySet = manager.all()  # type: ignore[assignment]
        return queryset

    def get_payload_normalizer(
        self, interface_cls: type["OrmInterfaceBase"]
    ) -> PayloadNormalizer:
        return PayloadNormalizer(cast(Type[models.Model], interface_cls._model))

    def get_field_descriptors(
        self, interface_cls: type["OrmInterfaceBase"]
    ) -> dict[str, FieldDescriptor]:
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
        descriptors = get_support_capability(interface_cls).get_field_descriptors(
            interface_cls
        )
        return {name: descriptor.accessor for name, descriptor in descriptors.items()}

    def get_field_type(
        self,
        interface_cls: type["OrmInterfaceBase"],
        field_name: str,
    ) -> type:
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
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket:
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
        payload_snapshot = {"kwargs": dict(kwargs)}

        def _perform() -> DatabaseBucket:
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
        self.enabled = enabled

    def setup(self, interface_cls: type[InterfaceBase]) -> None:
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
        return self.enabled

    def set_state(self, enabled: bool) -> None:
        self.enabled = enabled


def get_support_capability(
    interface_cls: type["OrmInterfaceBase"],
) -> OrmPersistenceSupportCapability:
    """
    Convenience helper that resolves the required support capability.

    Keeping this helper colocated with the capability simplifies imports for modules
    that need to look up shared ORM helpers.
    """
    return interface_cls.require_capability(  # type: ignore[return-value]
        "orm_support",
        expected_type=OrmPersistenceSupportCapability,
    )


def is_soft_delete_enabled(interface_cls: type["OrmInterfaceBase"]) -> bool:
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
    from .history import OrmHistoryCapability

    return interface_cls.require_capability(  # type: ignore[return-value]
        "history",
        expected_type=OrmHistoryCapability,
    )
