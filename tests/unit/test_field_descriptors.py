from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from general_manager.interface.capabilities.orm_utils.field_descriptors import (
    _direct_reverse_accessor,
    _general_manager_many_accessor,
)


def test_general_manager_many_accessor_uses_explicit_relation_field_name() -> None:
    manager_class = Mock()
    filter_result = object()
    manager_class.filter.return_value = filter_result

    related_model = Mock()
    relation_field = SimpleNamespace(name="reviewer")
    related_model._meta.get_field.return_value = relation_field

    accessor = _general_manager_many_accessor(
        accessor_name="reviewassignment_set",
        related_model=related_model,
        general_manager_class=manager_class,
        source_model=object(),
        relation_field_name="reviewer",
    )

    interface_instance = SimpleNamespace(pk=42)
    result = accessor(interface_instance)

    related_model._meta.get_field.assert_called_once_with("reviewer")
    manager_class.filter.assert_called_once_with(reviewer=42)
    assert result is filter_result


def test_direct_reverse_accessor_filters_default_manager_by_relation_field() -> None:
    default_manager = Mock()
    queryset = object()
    default_manager.filter.return_value = queryset
    related_model = SimpleNamespace(_default_manager=default_manager)

    accessor = _direct_reverse_accessor(
        related_model=related_model,
        relation_field_name="requester",
    )

    interface_instance = SimpleNamespace(pk=7)
    result = accessor(interface_instance)

    default_manager.filter.assert_called_once_with(requester=7)
    assert result is queryset
