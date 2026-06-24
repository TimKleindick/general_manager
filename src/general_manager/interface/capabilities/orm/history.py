"""History-specific ORM capability helpers."""

from __future__ import annotations

from datetime import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar, cast

from django.db import models

from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.utils.database_interface_protocols import (
    SupportsHistory,
    SupportsHistoryQuery,
)

from .support import get_support_capability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase


class HistoryNotSupportedError(RuntimeError):
    """
    Raised when historical lookups are requested but not supported.

    The error message is ``"{interface_name} does not support historical queries."``.
    """

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} does not support historical queries.")


class OrmHistoryCapability(BaseCapability):
    """Lookup historical records for ORM-backed interfaces."""

    name: ClassVar[CapabilityName] = "history"

    @staticmethod
    def _get_instance_pk_filter(instance: object) -> dict[str, object] | None:
        """
        Return a primary-key filter for a history-bearing model instance.

        The identifier is read from ``instance.pk`` first, then ``instance.id``.
        The lookup key defaults to ``"id"`` and uses ``instance._meta.pk.name``
        when that value is a string. Returns ``None`` when neither identifier is
        present or the identifier value is ``None``.
        """
        pk_value = None
        if hasattr(instance, "pk"):
            pk_value = instance.pk
        elif hasattr(instance, "id"):
            pk_value = instance.id
        if pk_value is None:
            return None
        pk_name = "id"
        instance_meta = getattr(instance, "_meta", None)
        instance_pk = getattr(instance_meta, "pk", None)
        instance_pk_name = getattr(instance_pk, "name", None)
        if isinstance(instance_pk_name, str):
            pk_name = instance_pk_name
        return {pk_name: pk_value}

    @staticmethod
    def _get_model_pk_filter(
        interface_cls: type["OrmInterfaceBase[models.Model]"], instance: object
    ) -> dict[str, object] | None:
        """
        Return a model primary-key filter for manager-like or model-like objects.

        The identifier is read from ``instance.pk`` first, then ``instance.id``,
        then mapping-like ``instance.identification["id"]``. The interface must
        expose ``_model`` as a model class with a ``history`` manager. The lookup
        key defaults to ``"id"`` and uses ``interface_cls._model._meta.pk.name``
        when that value is a string. Returns ``None`` when no identifier is
        available or the interface model has no history manager.
        """
        pk_value = None
        if hasattr(instance, "pk"):
            pk_value = instance.pk
        elif hasattr(instance, "id"):
            pk_value = instance.id
        elif hasattr(instance, "identification"):
            identification = getattr(instance, "identification", None)
            if isinstance(identification, Mapping):
                pk_value = identification.get("id")
        if pk_value is None:
            return None
        model = getattr(interface_cls, "_model", None)
        if (
            model is None
            or not isinstance(model, type)
            or not hasattr(model, "history")
        ):
            return None
        pk_field = getattr(getattr(model, "_meta", None), "pk", None)
        pk_name = getattr(pk_field, "name", "id")
        if not isinstance(pk_name, str):
            pk_name = "id"
        return {pk_name: pk_value}

    @staticmethod
    def _apply_database_alias(
        interface_cls: type["OrmInterfaceBase[models.Model]"],
        history_manager: SupportsHistoryQuery,
    ) -> SupportsHistoryQuery:
        """
        Apply the interface database alias to a history manager when configured.

        The alias is read from ORM support via
        ``get_database_alias(interface_cls)``. A truthy alias returns
        ``history_manager.using(alias)``; a falsey alias returns the original
        history manager.
        """
        database_alias = get_support_capability(interface_cls).get_database_alias(
            interface_cls
        )
        if database_alias:
            return history_manager.using(database_alias)
        return history_manager

    def get_historical_record(
        self,
        interface_cls: type["OrmInterfaceBase[models.Model]"],
        instance: object,
        search_date: datetime | None = None,
    ) -> models.Model | None:
        """
        Retrieve the latest historical record for ``instance`` at ``search_date``.

        The lookup prefers ``instance.history`` when the object exposes a
        django-simple-history manager. Otherwise it falls back to
        ``interface_cls._model.history`` and derives the primary key from
        ``instance.pk``, then ``instance.id``, then ``instance.identification["id"]``.
        A configured database alias is applied before filtering.

        Parameters:
            interface_cls: ORM interface class used to resolve the model history manager and capability settings.
            instance: Model-like or manager-like object to identify in the history table.
            search_date: Optional cutoff date; when provided, only records with
                ``history_date <= search_date`` are considered.

        Returns:
            Historical model instance, or ``None`` when no identifier, history
            manager, or matching historical row is available.
        """
        history_manager: SupportsHistoryQuery
        pk_filter: dict[str, object] | None
        if isinstance(instance, SupportsHistory):
            history_manager = instance.history
            pk_filter = self._get_instance_pk_filter(instance)
        else:
            pk_filter = self._get_model_pk_filter(interface_cls, instance)
            if pk_filter is None:
                return None
            history_manager = cast(SupportsHistory, interface_cls._model).history
        history_manager = self._apply_database_alias(interface_cls, history_manager)
        filter_kwargs: dict[str, object] = {}
        if search_date is not None:
            filter_kwargs["history_date__lte"] = search_date
        if pk_filter is not None:
            filter_kwargs.update(pk_filter)
        historical = (
            cast(models.QuerySet[models.Model], history_manager.filter(**filter_kwargs))
            .order_by("history_date")
            .last()
        )
        return historical

    def get_history_queryset_for_manager(
        self,
        interface_cls: type["OrmInterfaceBase[models.Model]"],
        manager: object,
    ) -> models.QuerySet[models.Model]:
        """
        Return the history queryset scoped to the manager instance's primary key.

        The target identifier is derived from ``manager.pk``, then
        ``manager.id``, then mapping-like ``manager.identification["id"]``. The
        lookup key defaults to ``"id"`` and uses the interface model primary-key
        field name when it is available as a string. A configured database alias
        is applied before the filtered queryset is returned.

        Parameters:
            interface_cls: ORM interface whose underlying model provides the history manager.
            manager: Manager-like object exposing ``pk``, ``id``, or ``identification["id"]`` for the target row.

        Returns:
            History queryset limited to records for the target object.

        Raises:
            HistoryNotSupportedError: If the interface does not expose a history manager or the manager cannot be scoped to a primary key.
        """
        if not hasattr(interface_cls._model, "history"):
            raise HistoryNotSupportedError(interface_cls.__name__)
        pk_filter = self._get_model_pk_filter(interface_cls, manager)
        if pk_filter is None:
            raise HistoryNotSupportedError(interface_cls.__name__)
        history_manager = cast(SupportsHistory, interface_cls._model).history
        history_manager = self._apply_database_alias(interface_cls, history_manager)
        return cast(
            models.QuerySet[models.Model],
            history_manager.filter(**pk_filter),
        )

    def get_historical_queryset(
        self,
        interface_cls: type["OrmInterfaceBase[models.Model]"],
        search_date: datetime,
    ) -> models.QuerySet[models.Model]:
        """
        Retrieve a queryset representing the historical state as of the given date.

        A configured database alias is applied before calling
        ``history.as_of(search_date)``.

        Parameters:
            interface_cls: ORM interface whose underlying model provides the historical manager.
            search_date: Cutoff datetime for historical snapshot lookup.

        Returns:
            QuerySet representing the state at ``search_date``.

        Raises:
            HistoryNotSupportedError: If the model does not expose a history manager.
        """
        if not hasattr(interface_cls._model, "history"):
            raise HistoryNotSupportedError(interface_cls.__name__)
        history_manager = cast(SupportsHistory, interface_cls._model).history
        history_manager = self._apply_database_alias(interface_cls, history_manager)
        return cast(
            models.QuerySet[models.Model],
            history_manager.as_of(search_date),
        )

    def get_historical_record_by_pk(
        self,
        interface_cls: type["OrmInterfaceBase[models.Model]"],
        pk: object,
        search_date: datetime | None,
    ) -> models.Model | None:
        """
        Retrieve the latest historical record for primary key ``pk`` at ``search_date``.

        The lookup uses the model's ``history`` manager, applies any configured
        database alias, filters by ``id=pk`` and ``history_date <= search_date``,
        orders by ``history_date``, and returns the last row.

        Parameters:
            interface_cls: ORM interface whose underlying model provides the historical manager.
            pk: Primary key of the target model record.
            search_date: Cutoff datetime. If ``None``, no history query is run.

        Returns:
            Historical model instance, or ``None`` when ``search_date`` is
            ``None``, the model has no history manager, or no row matches.
        """
        if search_date is None or not hasattr(interface_cls._model, "history"):
            return None
        history_manager = cast(SupportsHistory, interface_cls._model).history
        database_alias = get_support_capability(interface_cls).get_database_alias(
            interface_cls
        )
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            cast(
                models.QuerySet[models.Model],
                history_manager.filter(id=pk, history_date__lte=search_date),
            )
            .order_by("history_date")
            .last()
        )
        return historical
