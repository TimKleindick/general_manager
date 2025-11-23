"""History-specific ORM capability helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TYPE_CHECKING, ClassVar, cast

from django.db import models

from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.utils.database_interface_protocols import (
    SupportsHistory,
)

from .support import get_support_capability

if TYPE_CHECKING:  # pragma: no cover
    from general_manager.interface.orm_interface import OrmInterfaceBase


class OrmHistoryCapability(BaseCapability):
    """Lookup historical records for ORM-backed interfaces."""

    name: ClassVar[CapabilityName] = "history"

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
        if not isinstance(instance, SupportsHistory):
            return None
        history_manager = cast(SupportsHistory, instance).history
        database_alias = get_support_capability(interface_cls).get_database_alias(
            interface_cls
        )
        if database_alias:
            history_manager = history_manager.using(database_alias)
        historical = (
            cast(models.QuerySet, history_manager.filter(history_date__lte=search_date))
            .order_by("history_date")
            .last()
        )
        return historical

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
