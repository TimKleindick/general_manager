"""History-specific ORM capability helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING, ClassVar, cast

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
    """Raised when historical lookups are requested but not supported."""

    def __init__(self, interface_name: str) -> None:
        super().__init__(f"{interface_name} does not support historical queries.")


class OrmHistoryCapability(BaseCapability):
    """Lookup historical records for ORM-backed interfaces."""

    name: ClassVar[CapabilityName] = "history"

    @staticmethod
    def _get_instance_pk_filter(instance: Any) -> dict[str, Any] | None:
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
        interface_cls: type["OrmInterfaceBase"], instance: Any
    ) -> dict[str, Any] | None:
        pk_value = None
        if hasattr(instance, "pk"):
            pk_value = instance.pk
        elif hasattr(instance, "id"):
            pk_value = instance.id
        elif hasattr(instance, "identification"):
            identification = getattr(instance, "identification", None)
            if isinstance(identification, dict):
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
        interface_cls: type["OrmInterfaceBase"],
        history_manager: SupportsHistoryQuery,
    ) -> SupportsHistoryQuery:
        database_alias = get_support_capability(interface_cls).get_database_alias(
            interface_cls
        )
        if database_alias:
            return history_manager.using(database_alias)
        return history_manager

    def get_historical_record(
        self,
        interface_cls: type["OrmInterfaceBase"],
        instance: Any,
        search_date: datetime | None = None,
    ) -> Any | None:
        """
        Retrieve the model's historical record for the given instance as of the specified date.

        Parameters:
            interface_cls (type[OrmInterfaceBase]): The ORM interface class used to resolve capability-specific settings (for example, a database alias).
            instance (Any): The object that must implement SupportsHistory; if it does not, the function returns None.
            search_date (datetime | None): The cutoff date; the function returns the most recent historical record whose `history_date` is less than or equal to this value.

        Returns:
            Any | None: The historical model instance that was current at `search_date`, or `None` if no matching historical record exists or the instance does not support history.
        """
        history_manager: SupportsHistoryQuery
        pk_filter: dict[str, Any] | None
        if isinstance(instance, SupportsHistory):
            history_manager = cast(SupportsHistory, instance).history
            pk_filter = self._get_instance_pk_filter(instance)
        else:
            pk_filter = self._get_model_pk_filter(interface_cls, instance)
            if pk_filter is None:
                return None
            history_manager = cast(SupportsHistory, interface_cls._model).history
        history_manager = self._apply_database_alias(interface_cls, history_manager)
        filter_kwargs: dict[str, Any] = {}
        if search_date is not None:
            filter_kwargs["history_date__lte"] = search_date
        if pk_filter is not None:
            filter_kwargs.update(pk_filter)
        historical = (
            cast(models.QuerySet, history_manager.filter(**filter_kwargs))
            .order_by("history_date")
            .last()
        )
        return historical

    def get_historical_queryset(
        self,
        interface_cls: type["OrmInterfaceBase"],
        search_date: datetime,
    ) -> models.QuerySet:
        """
        Retrieve a queryset representing the historical state as of the given date.

        Parameters:
            interface_cls (type["OrmInterfaceBase"]): ORM interface whose underlying model provides the historical manager.
            search_date (datetime): Cutoff datetime for historical snapshot lookup.

        Returns:
            models.QuerySet: QuerySet representing the state at `search_date`.

        Raises:
            HistoryNotSupportedError: If the model does not expose a history manager.
        """
        if not hasattr(interface_cls._model, "history"):
            raise HistoryNotSupportedError(interface_cls.__name__)
        history_manager = interface_cls._model.history  # type: ignore[attr-defined]
        history_manager = self._apply_database_alias(interface_cls, history_manager)
        return cast(models.QuerySet, history_manager.as_of(search_date))

    def get_historical_record_by_pk(
        self,
        interface_cls: type["OrmInterfaceBase"],
        pk: Any,
        search_date: datetime | None,
    ) -> Any | None:
        """
        Retrieve the most recent historical record for the model identified by `pk` with a history date not later than `search_date`.

        Parameters:
            interface_cls (type["OrmInterfaceBase"]): ORM interface whose underlying model provides the historical manager.
            pk (Any): Primary key of the target model record.
            search_date (datetime | None): Cutoff datetime; only history records with `history_date` <= this value are considered. If `None`, the function returns `None`.

        Returns:
            Any | None: The historical model instance with the latest `history_date` that is <= `search_date`, or `None` if no such record exists or if the model has no history manager.
        """
        if search_date is None or not hasattr(interface_cls._model, "history"):
            return None
        history_manager = interface_cls._model.history  # type: ignore[attr-defined]
        database_alias = get_support_capability(interface_cls).get_database_alias(
            interface_cls
        )
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            history_manager.filter(id=pk, history_date__lte=search_date)
            .order_by("history_date")
            .last()
        )
        return historical
