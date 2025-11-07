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
        Select the manager for the model that represents active records when soft-delete is enabled, otherwise the model's default manager; the returned manager is bound to the configured database alias if one is set.

        Returns:
            models.Manager[HistoryModelT]: A manager for the model, bound to the configured database alias; when soft-delete is enabled this manager yields only active records.
        """
        if self.use_soft_delete:
            manager = self._soft_delete_active_manager()
        else:
            manager = self.model._default_manager
        manager = self._with_database_alias(manager)
        return cast(models.Manager[HistoryModelT], manager)

    def all_manager(self) -> models.Manager[HistoryModelT]:
        """
        Selects a manager that exposes all model rows, including soft-deleted ones when available.

        Returns:
            A manager that includes inactive (soft-deleted) rows if `use_soft_delete` is True and the model defines `all_objects`; otherwise the model's default manager.
        """
        if self.use_soft_delete and hasattr(self.model, "all_objects"):
            manager: models.Manager[HistoryModelT] = self.model.all_objects  # type: ignore[attr-defined]
        else:
            manager = self.model._default_manager
        manager = self._with_database_alias(manager)
        return cast(models.Manager[HistoryModelT], manager)

    def _soft_delete_active_manager(self) -> models.Manager[HistoryModelT]:
        """
        Provide a manager that yields only rows with `is_active=True`, creating and caching a filtered manager when necessary.

        If the model defines `all_objects`, the model's default manager is returned (the model is assumed to provide its own all-objects behavior). Otherwise, a lightweight Manager subclass that filters querysets by `is_active=True` is constructed, attached to the model, cached on the selector instance, and returned.

        Returns:
            models.Manager[HistoryModelT]: A manager bound to the selector's model that filters by `is_active=True`. If `all_objects` exists on the model, returns the model's default manager; otherwise returns a cached filtered manager instance.
        """
        if hasattr(self.model, "all_objects"):
            return cast(models.Manager[HistoryModelT], self.model._default_manager)
        if self.cached_active is None:
            base_manager = self.model._default_manager

            class _FilteredManager(models.Manager[HistoryModelT]):  # type: ignore[misc]
                def get_queryset(self_inner) -> models.QuerySet[HistoryModelT]:
                    """
                    Return a queryset of the model filtered to only active rows.

                    If the manager instance has a `_db` attribute, the queryset is routed to that database before filtering.

                    Returns:
                        QuerySet[HistoryModelT]: A queryset containing only rows where `is_active` is True, bound to the manager's `_db` when present.
                    """
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
        """
        Apply the selector's configured database alias to the given manager.

        If this selector has a database_alias set, return the manager bound to that alias via manager.db_manager(database_alias); otherwise return the original manager unchanged.

        Parameters:
            manager (models.Manager[HistoryModelT]): The manager to possibly bind to a database alias.

        Returns:
            models.Manager[HistoryModelT]: The manager bound to the configured database alias if one is set, otherwise the original manager.
        """
        if not self.database_alias:
            return manager
        return cast(
            models.Manager[HistoryModelT], manager.db_manager(self.database_alias)
        )
