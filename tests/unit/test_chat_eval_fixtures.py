from __future__ import annotations

import pytest

from general_manager.api.graphql import GraphQL
from general_manager.chat.evals.fixtures import setup_large_schema, setup_toy_schema
from general_manager.chat.schema_index import clear_schema_index_cache
from general_manager.chat.tools import execute_chat_tool, query
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.utils.path_mapping import PathMap


@pytest.fixture(autouse=True)
def reset_eval_fixture_schema():
    yield
    clear_schema_index_cache()
    GraphQL.reset_registry()
    GeneralManagerMeta.all_classes.clear()
    GeneralManagerMeta.pending_graphql_interfaces.clear()
    GeneralManagerMeta.pending_attribute_initialization.clear()
    PathMap.mapping.clear()
    if hasattr(PathMap, "instance"):
        delattr(PathMap, "instance")


def test_toy_fixture_supports_numeric_filter_pagination_and_total_count() -> None:
    setup_toy_schema()

    result = query(
        manager="MaterialManager",
        filters={"density__gt": 7},
        fields=["name", "density"],
        limit=1,
    )

    assert result == {
        "data": [{"name": "Steel", "density": 7.8}],
        "total_count": 2,
        "has_more": True,
    }


def test_toy_fixture_supports_nested_relation_filters_and_paths() -> None:
    setup_toy_schema()

    parts = query(
        manager="PartManager",
        filters={"material__name__icontains": "alu"},
        fields=["name", {"material": ["name"]}],
    )
    projects = query(
        manager="ProjectManager",
        filters={"parts__material__name__icontains": "cob"},
        fields=["name", {"parts": ["name", {"material": ["name"]}]}],
    )
    path = execute_chat_tool(
        "find_path",
        {"from_manager": "MaterialManager", "to_manager": "ProjectManager"},
        None,
    )

    assert parts["data"] == [{"name": "Bearing", "material": {"name": "Aluminum"}}]
    assert projects["data"] == [
        {"name": "Apollo", "parts": [{"name": "Gear", "material": {"name": "Cobalt"}}]}
    ]
    assert path == ["material", "parts"]


def test_toy_fixture_returns_empty_page_for_unmatched_exact_filter() -> None:
    setup_toy_schema()

    result = query(
        manager="ProjectManager",
        filters={"parts__name": "Bolt"},
        fields=["name"],
    )

    assert result == {"data": [], "total_count": 0, "has_more": False}


def test_large_fixture_rejects_non_positive_manager_count() -> None:
    with pytest.raises(ValueError, match="manager_count must be positive"):
        setup_large_schema(manager_count=0)


def test_large_fixture_supports_chained_query_and_path_lookup() -> None:
    setup_large_schema(manager_count=4, chain_length=3)

    schema = GraphQL.get_schema()
    assert schema is not None
    result = schema.execute(
        """
        query {
          syntheticmanager01List(filter: {status: "active"}, pageSize: 1) {
            items {
              name
              code
              nextItem {
                name
                nextItem {
                  code
                }
              }
            }
            pageInfo {
              totalCount
            }
          }
        }
        """
    )
    path = execute_chat_tool(
        "find_path",
        {
            "from_manager": "SyntheticManager01",
            "to_manager": "SyntheticManager03",
        },
        None,
    )

    assert result.errors is None
    assert result.data == {
        "syntheticmanager01List": {
            "items": [
                {
                    "name": "SyntheticManager01 record",
                    "code": "SM01-001",
                    "nextItem": {
                        "name": "SyntheticManager02 record",
                        "nextItem": {"code": "SM03-001"},
                    },
                }
            ],
            "pageInfo": {"totalCount": 1},
        }
    }
    assert path == ["next_item", "next_item"]


def test_large_fixture_clamps_chain_length_to_manager_count() -> None:
    setup_large_schema(manager_count=3, chain_length=99)

    result = query(
        manager="SyntheticManager03",
        filters={"status": "inactive"},
        fields=["name"],
    )
    path = execute_chat_tool(
        "find_path",
        {
            "from_manager": "SyntheticManager01",
            "to_manager": "SyntheticManager03",
        },
        None,
    )

    assert result == {"data": [], "total_count": 0, "has_more": False}
    assert path == ["next_item", "next_item"]
