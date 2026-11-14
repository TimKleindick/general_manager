"""Grouping eligibility and permission validation regressions."""

from types import SimpleNamespace
from typing import Annotated
from unittest.mock import MagicMock

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


def test_group_control_validation_does_not_repeat_member_authorization():
    """Contributor filtering and per-metric checks precede this syntax boundary."""
    first, second = object(), object()

    class Source:
        _manager_class = object
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            yield first
            yield second

    source = Source()
    graphql_groups._validate_group_request(
        source,
        ["customer_id", "name"],
        [{"field": "amount"}],
        None,
    )
    assert source.iterations == 0


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
