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


class OrmHistoryCapability(BaseCapability):
    """Lookup historical records for ORM-backed interfaces."""

    name: ClassVar[CapabilityName] = "history"

    def get_historical_record(
        self,
        interface_cls: type["OrmInterfaceBase"],
        instance: Any,
        search_date: datetime | None = None,
    ) -> Any | None:
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
