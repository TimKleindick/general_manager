from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.tools import query
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta


class _Result:
    def __init__(self, data=None, errors=None) -> None:
        self.data = data
        self.errors = errors


class _RecordingSchema:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def execute(self, query_text: str, context_value=None):  # type: ignore[no-untyped-def]
        self.calls.append({"query": query_text, "context": context_value})
        return self.result


class ChatQueryToolTests(SimpleTestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()

        class PartManager(GeneralManager):
            pass

        class HiddenManager(GeneralManager):
            chat_exposed = False

        self.PartManager = PartManager
        self.HiddenManager = HiddenManager
        GraphQL.manager_registry = {
            "PartManager": PartManager,
            "HiddenManager": HiddenManager,
        }

    def tearDown(self) -> None:
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()
        super().tearDown()

    def test_query_builds_graphql_query_and_shapes_result(self) -> None:
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [
                            {"name": "Bolt", "material": {"name": "Steel"}},
                            {"name": "Screw", "material": {"name": "Steel"}},
                            {"name": "Nut", "material": {"name": "Steel"}},
                        ],
                        "pageInfo": {"totalCount": 5},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]
        context = SimpleNamespace(user="alice")

        result = query(
            manager="PartManager",
            filters={"name__icontains": "st", "active": True},
            fields=["name", {"material": ["name"]}],
            limit=2,
            offset=1,
            context=context,
        )

        assert result == {
            "data": [
                {"name": "Screw", "material": {"name": "Steel"}},
                {"name": "Nut", "material": {"name": "Steel"}},
            ],
            "total_count": 5,
            "has_more": True,
        }
        assert schema.calls[0]["context"] is context
        query_text = str(schema.calls[0]["query"])
        assert "partmanagerList" in query_text
        assert 'filter: {name_Icontains: "st", active: true}' in query_text
        assert "pageSize: 3" in query_text
        assert "items { name material { name } }" in query_text
        assert "pageInfo { totalCount }" in query_text

    def test_query_rejects_hidden_managers(self) -> None:
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(
            ValueError, match=r"Manager 'HiddenManager' is not chat-exposed\."
        ):
            query(manager="HiddenManager", filters={}, fields=["name"])

    def test_query_surfaces_graphql_errors(self) -> None:
        GraphQL._schema = _RecordingSchema(  # type: ignore[assignment]
            _Result(errors=[SimpleNamespace(message="bad filter")])
        )

        with pytest.raises(ValueError, match="bad filter"):
            query(manager="PartManager", filters={}, fields=["name"])

    def test_query_preserves_graphql_filter_keys_that_are_already_shaped(self) -> None:
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [{"name": "Bolt"}],
                        "pageInfo": {"totalCount": 1},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]

        result = query(
            manager="PartManager",
            filters={"density_Gt": 7},
            fields=["name"],
        )

        assert result["data"] == [{"name": "Bolt"}]
        query_text = str(schema.calls[0]["query"])
        assert "density_Gt: 7" in query_text

    def test_query_translates_relation_lookup_filters_to_graphene_names(self) -> None:
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [{"name": "Bolt"}],
                        "pageInfo": {"totalCount": 1},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]

        result = query(
            manager="PartManager",
            filters={"material__name": "Steel"},
            fields=["name"],
        )

        assert result["data"] == [{"name": "Bolt"}]
        query_text = str(schema.calls[0]["query"])
        assert 'material_Name: "Steel"' in query_text

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "max_results": 2,
            }
        }
    )
    def test_query_caps_requested_limit_to_max_results_setting(self) -> None:
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [
                            {"name": "Bolt"},
                            {"name": "Screw"},
                            {"name": "Nut"},
                        ],
                        "pageInfo": {"totalCount": 9},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]

        result = query(
            manager="PartManager",
            filters={},
            fields=["name"],
            limit=5,
            offset=1,
        )

        assert result == {
            "data": [{"name": "Screw"}, {"name": "Nut"}],
            "total_count": 9,
            "has_more": True,
        }
        query_text = str(schema.calls[0]["query"])
        assert "pageSize: 3" in query_text

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "query_timeout_seconds": 2,
            }
        }
    )
    def test_query_sets_statement_timeout_for_postgresql(self) -> None:
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [{"name": "Bolt"}],
                        "pageInfo": {"totalCount": 1},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]

        class _Cursor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, list[int]]] = []

            def execute(self, sql: str, params: list[int]) -> None:
                self.calls.append((sql, params))

            def __enter__(self) -> "_Cursor":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
                del exc_type, exc, tb
                return None

        cursor = _Cursor()

        with patch(
            "general_manager.chat.tools.connection",
            SimpleNamespace(vendor="postgresql", cursor=lambda: cursor),
        ):
            result = query(manager="PartManager", filters={}, fields=["name"])

        assert result["data"] == [{"name": "Bolt"}]
        assert cursor.calls == [("SET LOCAL statement_timeout = %s", [2000])]
