"""Utilities for working with Django managers in database-backed interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Optional, TypeVar, cast

from django.db import models

HistoryModelT = TypeVar("HistoryModelT", bound=models.Model)


@dataclass
class DjangoManagerSelector(Generic[HistoryModelT]):
    """
    Helper encapsulating selection of active/all managers with optional soft-delete handling.
    """

    model: type[HistoryModelT]
    database_alias: Optional[str]
    use_soft_delete: bool
    cached_active: Optional[models.Manager[HistoryModelT]] = None

    def active_manager(self) -> models.Manager[HistoryModelT]:
        """
        Return a manager that filters out inactive rows when soft delete is enabled.
        """
        if self.use_soft_delete:
            manager = self._soft_delete_active_manager()
        else:
            manager = self.model._default_manager
        manager = self._with_database_alias(manager)
        return cast(models.Manager[HistoryModelT], manager)

    def all_manager(self) -> models.Manager[HistoryModelT]:
        """
        Return a manager that includes inactive rows when available.
        """
        if self.use_soft_delete and hasattr(self.model, "all_objects"):
            manager: models.Manager[HistoryModelT] = self.model.all_objects  # type: ignore[attr-defined]
        else:
            manager = self.model._default_manager
        manager = self._with_database_alias(manager)
        return cast(models.Manager[HistoryModelT], manager)

    def _soft_delete_active_manager(self) -> models.Manager[HistoryModelT]:
        if hasattr(self.model, "all_objects"):
            return cast(models.Manager[HistoryModelT], self.model._default_manager)
        if self.cached_active is None:
            base_manager = self.model._default_manager

            class _FilteredManager(models.Manager[HistoryModelT]):  # type: ignore[misc]
                def get_queryset(self_inner) -> models.QuerySet[HistoryModelT]:
                    queryset = base_manager.get_queryset()
                    if getattr(self_inner, "_db", None):
                        queryset = queryset.using(self_inner._db)
                    return queryset.filter(is_active=True)

            manager: models.Manager[HistoryModelT] = _FilteredManager()
            manager.model = self.model  # type: ignore[attr-defined]
            self.cached_active = manager
        return self.cached_active

    def _with_database_alias(
        self, manager: models.Manager[HistoryModelT]
    ) -> models.Manager[HistoryModelT]:
        if not self.database_alias:
            return manager
        return cast(
            models.Manager[HistoryModelT], manager.db_manager(self.database_alias)
        )
