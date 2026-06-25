"""Tests for ORM Django manager selection utilities."""

from __future__ import annotations

from django.db import models
import pytest

import general_manager.interface.capabilities.orm_utils.django_manager_utils as manager_utils
from general_manager.interface.capabilities.orm_utils.django_manager_utils import (
    DjangoManagerSelector,
)


class SelectorPlainModel(models.Model):
    """Model without soft-delete support for manager selector tests."""

    name = models.CharField(max_length=20)

    class Meta:
        app_label = "general_manager"
        managed = False


class SelectorSoftDeleteModel(models.Model):
    """Soft-delete model without an explicit all-rows manager."""

    name = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "general_manager"
        managed = False


class SelectorAllObjectsModel(models.Model):
    """Soft-delete model with an explicit all-rows manager."""

    objects = models.Manager()
    all_objects = models.Manager()
    name = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "general_manager"
        managed = False


class SelectorMalformedAllObjectsModel(models.Model):
    """Model whose all_objects attribute exists but is not a manager."""

    name = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        app_label = "general_manager"
        managed = False


SelectorMalformedAllObjectsModel.all_objects = object()


def test_django_manager_selector_public_exports() -> None:
    """The utility module should expose only the manager selector."""
    assert manager_utils.__all__ == ["DjangoManagerSelector"]


def test_active_manager_without_soft_delete_uses_default_manager() -> None:
    """Non-soft-delete active reads should use the default manager."""
    selector = DjangoManagerSelector(
        model=SelectorPlainModel,
        database_alias=None,
        use_soft_delete=False,
    )

    manager = selector.active_manager()

    assert manager is SelectorPlainModel._default_manager
    assert selector.cached_active is None


def test_active_manager_with_all_objects_uses_default_manager() -> None:
    """Models with all_objects own their active/default split."""
    selector = DjangoManagerSelector(
        model=SelectorAllObjectsModel,
        database_alias=None,
        use_soft_delete=True,
    )

    manager = selector.active_manager()

    assert manager is SelectorAllObjectsModel._default_manager
    assert selector.cached_active is None


def test_all_manager_with_soft_delete_uses_all_objects() -> None:
    """Soft-delete all-row reads should prefer all_objects when available."""
    selector = DjangoManagerSelector(
        model=SelectorAllObjectsModel,
        database_alias=None,
        use_soft_delete=True,
    )

    manager = selector.all_manager()

    assert manager is SelectorAllObjectsModel.all_objects


def test_all_manager_without_soft_delete_uses_default_manager() -> None:
    """All-row reads fall back to the default manager without soft delete."""
    selector = DjangoManagerSelector(
        model=SelectorPlainModel,
        database_alias=None,
        use_soft_delete=False,
    )

    manager = selector.all_manager()

    assert manager is SelectorPlainModel._default_manager


def test_generated_active_manager_filters_active_rows_and_is_cached() -> None:
    """Soft-delete models without all_objects get a cached filtering manager."""
    selector = DjangoManagerSelector(
        model=SelectorSoftDeleteModel,
        database_alias=None,
        use_soft_delete=True,
    )

    first = selector.active_manager()
    second = selector.active_manager()

    assert first is second
    assert selector.cached_active is first
    assert first.model is SelectorSoftDeleteModel
    query_text = str(first.get_queryset().query)
    assert "is_active" in query_text


def test_generated_active_manager_honors_database_alias() -> None:
    """Generated managers should preserve the configured alias through queries."""
    selector = DjangoManagerSelector(
        model=SelectorSoftDeleteModel,
        database_alias="replica",
        use_soft_delete=True,
    )

    manager = selector.active_manager()

    assert manager._db == "replica"
    assert manager.get_queryset().db == "replica"


def test_cached_active_manager_is_reused_as_source_without_validation() -> None:
    """Caller-provided cached managers should be trusted as the source manager."""
    cached_manager = SelectorPlainModel._default_manager
    selector = DjangoManagerSelector(
        model=SelectorSoftDeleteModel,
        database_alias=None,
        use_soft_delete=True,
        cached_active=cached_manager,
    )

    manager = selector.active_manager()

    assert manager is cached_manager
    assert selector.cached_active is cached_manager


def test_active_manager_rebinds_cached_source_on_each_aliased_call() -> None:
    """The selector should cache the source manager, not the aliased result."""
    selector = DjangoManagerSelector(
        model=SelectorSoftDeleteModel,
        database_alias="replica",
        use_soft_delete=True,
    )

    first = selector.active_manager()
    second = selector.active_manager()

    assert selector.cached_active is not None
    assert first is not selector.cached_active
    assert second is not selector.cached_active
    assert first is not second
    assert first._db == "replica"
    assert second._db == "replica"


def test_existing_manager_honors_database_alias() -> None:
    """Existing Django managers should be rebound with db_manager."""
    selector = DjangoManagerSelector(
        model=SelectorPlainModel,
        database_alias="replica",
        use_soft_delete=False,
    )

    manager = selector.active_manager()

    assert manager is not SelectorPlainModel._default_manager
    assert manager._db == "replica"


def test_empty_database_alias_returns_original_manager() -> None:
    """An empty alias is treated the same as no alias."""
    selector = DjangoManagerSelector(
        model=SelectorPlainModel,
        database_alias="",
        use_soft_delete=False,
    )

    manager = selector.active_manager()

    assert manager is SelectorPlainModel._default_manager


def test_all_objects_detection_is_attribute_presence_based() -> None:
    """Any all_objects attribute is selected before manager validity checks."""
    selector = DjangoManagerSelector(
        model=SelectorMalformedAllObjectsModel,
        database_alias=None,
        use_soft_delete=True,
    )

    manager = selector.all_manager()

    assert manager is SelectorMalformedAllObjectsModel.all_objects


def test_malformed_all_objects_fails_when_alias_binding_uses_it() -> None:
    """Malformed all_objects values should fail only when used as managers."""
    selector = DjangoManagerSelector(
        model=SelectorMalformedAllObjectsModel,
        database_alias="replica",
        use_soft_delete=True,
    )

    with pytest.raises(AttributeError) as exc_info:
        selector.all_manager()

    assert exc_info.value.name == "db_manager"
