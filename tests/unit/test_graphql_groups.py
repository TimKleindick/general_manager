"""Grouping eligibility and permission validation regressions."""

from types import SimpleNamespace
from typing import Annotated
from unittest.mock import MagicMock, Mock

import pytest

from general_manager.api import graphql_groups


@pytest.mark.parametrize(
    "annotation, eligible",
    [
        (Annotated[str, "label"], True),
        (Annotated[str | None, "label"], True),
        (Annotated[str, "label"] | None, True),
        (Annotated[str | int, "ambiguous"], False),
        (Annotated[list[str], "collection"], False),
    ],
)
def test_group_ordering_unwraps_scalar_metadata(annotation, eligible):
    property_definition = SimpleNamespace(graphql_type_hint=annotation, sortable=True)
    manager = SimpleNamespace(
        Interface=SimpleNamespace(
            get_attribute_types=lambda: {},
            get_graph_ql_properties=lambda: {"display_name": property_definition},
        )
    )
    paths = graphql_groups.group_sortable_field_paths(manager, {})
    assert ("displayName" in paths) is eligible


def test_group_permission_validation_iterates_source_once_and_preserves_key_priority(
    monkeypatch,
):
    first, second = object(), object()

    class Source:
        _manager_class = object
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            yield first
            yield second

    source = Source()
    alias = Mock(
        side_effect=lambda _manager, field: "customer"
        if field == "customer_id"
        else None
    )
    check = Mock(
        side_effect=lambda member, _info, field: not (
            (member is first and field == "amount")
            or (member is second and field == "customer")
        )
    )
    monkeypatch.setattr(graphql_groups, "relation_id_alias_field", alias)
    monkeypatch.setattr(graphql_groups, "check_read_permission", check)
    with pytest.raises(graphql_groups.GroupQueryError, match="'customer_id'"):
        graphql_groups._validate_group_request(
            source,
            ["customer_id", "name"],
            [{"field": "amount"}],
            None,
        )
    assert source.iterations == 1
    assert alias.call_count == 3
    assert any(call.args[2] == "customer" for call in check.call_args_list)


@pytest.mark.parametrize(
    "keys, ordering, message",
    [
        ([], None, "at least one grouping key"),
        (["name"], [{"field": "name", "direction": "SIDEWAYS"}], "must be ASC or DESC"),
    ],
)
def test_invalid_group_controls_do_not_iterate_members(keys, ordering, message):
    source = MagicMock()
    with pytest.raises(graphql_groups.GraphQLError, match=message):
        graphql_groups._validate_group_request(source, keys, ordering, None)
    source.__iter__.assert_not_called()


def test_group_key_eligibility_excludes_parameterized_collections():
    attributes = {
        "name": {"type": str},
        "tags": {"type": list[str]},
        "optional_tags": {"type": list[str] | None},
    }
    manager = SimpleNamespace(
        Interface=SimpleNamespace(get_attribute_types=lambda: attributes)
    )
    assert graphql_groups.eligible_group_key_fields(manager) == {
        "name": attributes["name"]
    }
    assert graphql_groups.eligible_group_key_fields(object) == {}
