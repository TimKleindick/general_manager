"""Utilities for working with Django managers in database-backed interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar, cast

from django.db import models

HistoryModelT = TypeVar("HistoryModelT", bound=models.Model)


class _ModelWithAllObjects(Protocol[HistoryModelT]):
    """Model class shape used by soft-delete models with an all-rows manager."""

    all_objects: models.Manager[HistoryModelT]


@dataclass
class DjangoManagerSelector(Generic[HistoryModelT]):
    """
    Helper encapsulating selection of active/all managers with optional soft-delete handling.

    The selector is used by ORM-backed interfaces to consistently choose either
    the model's default manager, the model's explicit ``all_objects`` manager,
    or a generated ``is_active=True`` filtering manager. Returned managers are
    bound to ``database_alias`` with ``db_manager()`` when a truthy alias is
    provided. The selector does not validate model shape beyond the attributes it
    reads and does not query the database by itself. The ``all_objects`` check is
    attribute-presence based; malformed values fail later when used as managers.
    A caller-provided ``cached_active`` manager is trusted as the generated
    active-manager source and is not checked against ``model`` or
    ``database_alias``.
    """

    model: type[HistoryModelT]
    database_alias: str | None
    use_soft_delete: bool
    cached_active: models.Manager[HistoryModelT] | None = None

    def active_manager(self) -> models.Manager[HistoryModelT]:
        """
        Select the manager used for ordinary active-row reads.

        Returns:
            Manager bound to the configured database alias. When
            ``use_soft_delete`` is false, this is the model default manager.
            When soft-delete is enabled and the model has ``all_objects``, this
            still uses the default manager because the model is expected to
            implement its own active/default split. Otherwise a generated or
            caller-supplied cached manager filters ``is_active=True``. With a
            truthy alias, the selected source manager is rebound on each call;
            the selector caches only the unaliased generated/caller-supplied
            source manager.

        Raises:
            AttributeError: Propagated when required model manager attributes are
                missing or malformed.
        """
        if self.use_soft_delete:
            manager = self._soft_delete_active_manager()
        else:
            manager = cast(models.Manager[HistoryModelT], self.model._default_manager)
        return self._with_database_alias(manager)

    def all_manager(self) -> models.Manager[HistoryModelT]:
        """
        Select the manager used for all-row reads.

        Soft-delete models with an ``all_objects`` attribute use that attribute
        value as the source manager; every other case uses the model default
        manager. The check does not validate that ``all_objects`` is a Django
        manager. The returned manager is bound to ``database_alias`` when the
        alias is truthy.

        Returns:
            Manager that should expose all rows visible through the selected
            source manager.

        Raises:
            AttributeError: Propagated when required model manager attributes are
                missing or malformed.
        """
        if self.use_soft_delete and hasattr(self.model, "all_objects"):
            model_with_all_objects = cast(
                _ModelWithAllObjects[HistoryModelT], self.model
            )
            manager = model_with_all_objects.all_objects
        else:
            manager = cast(models.Manager[HistoryModelT], self.model._default_manager)
        return self._with_database_alias(manager)

    def _soft_delete_active_manager(self) -> models.Manager[HistoryModelT]:
        """
        Provide the source manager for active soft-delete reads.

        If the model defines ``all_objects`` by attribute presence, the model's
        default manager is returned because the model is assumed to provide its
        own active/default manager behavior. Otherwise, an existing
        ``cached_active`` source manager is reused as-is. If no cached manager is
        present, a lightweight Manager subclass that filters querysets by
        ``is_active=True`` is constructed, assigned this selector's model, cached
        on the selector instance, and returned. The generated manager starts from
        the model's default manager queryset and preserves a database alias set
        later by ``db_manager()``.

        Returns:
            Default manager for models with ``all_objects``; otherwise the
            cached generated or caller-supplied active manager.

        Raises:
            AttributeError: Propagated when required model manager attributes are
                missing or malformed.
        """
        if hasattr(self.model, "all_objects"):
            return cast(models.Manager[HistoryModelT], self.model._default_manager)
        if self.cached_active is None:
            base_manager = self.model._default_manager

            class _FilteredManager(models.Manager[HistoryModelT]):
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
            manager.model = self.model
            self.cached_active = manager
        return self.cached_active

    def _with_database_alias(
        self, manager: models.Manager[HistoryModelT]
    ) -> models.Manager[HistoryModelT]:
        """
        Apply the selector's configured database alias to the given manager.

        If ``database_alias`` is truthy, return ``manager.db_manager(alias)``.
        If it is ``None`` or an empty string, return the original manager
        unchanged.

        Parameters:
            manager: Manager to possibly bind to a database alias.

        Returns:
            Manager bound to the configured alias when one is set, otherwise the
            original manager.

        Raises:
            AttributeError: Propagated if a truthy alias is configured and the
                manager does not provide ``db_manager``.
        """
        if not self.database_alias:
            return manager
        return manager.db_manager(self.database_alias)


__all__ = ["DjangoManagerSelector"]
