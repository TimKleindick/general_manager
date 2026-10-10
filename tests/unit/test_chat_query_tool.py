from __future__ import annotations

from types import SimpleNamespace
from copy import deepcopy
from graphql import parse
from unittest.mock import patch

import graphene
import pytest
from tests.utils.chat_schema import registered_schema, install_registered_schema
from django.test import SimpleTestCase
from django.test.utils import override_settings

from general_manager.api.graphql import GraphQL
from general_manager.chat.tools import (
    ManagerNotChatExposedError,
    execute_chat_tool,
    get_tool_definitions,
    query,
)
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

    @property
    def graphql_schema(self):
        return registered_schema().graphql_schema

    def execute(self, query_text: str, context_value=None, variable_values=None):  # type: ignore[no-untyped-def]
        self.calls.append(
            {
                "query": " ".join(query_text.split()),
                "context": context_value,
                "variables": variable_values,
            }
        )
        result = deepcopy(self.result)
        if result.data and variable_values:
            root = parse(query_text).definitions[0].selection_set.selections[0]
            args = {
                item.name.value: variable_values[item.value.name.value]
                for item in root.arguments
            }
            if "page" in args and "pageSize" in args:
                page, size = args["page"], args["pageSize"]
                for value in result.data.values():
                    value["items"] = value["items"][(page - 1) * size : page * size]
        return result


class ChatQueryToolTests(SimpleTestCase):
    def setUp(self) -> None:
        GraphQL.reset_registry()
        GeneralManagerMeta.all_classes.clear()
        GeneralManagerMeta.pending_graphql_interfaces.clear()
        GeneralManagerMeta.pending_attribute_initialization.clear()

        class PartManager(GeneralManager):
            chat_exposed = True

        class HiddenManager(GeneralManager):
            chat_exposed = False

        class MaterialType(graphene.ObjectType):
            name = graphene.String()

        class PartType(graphene.ObjectType):
            name = graphene.String()
            material = graphene.Field(MaterialType)

        class PartFilter(graphene.InputObjectType):
            name__icontains = graphene.String()
            density__gt = graphene.Float()
            material__name = graphene.String()
            active = graphene.Boolean()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        self.PartManager = PartManager
        self.HiddenManager = HiddenManager
        GraphQL.manager_registry = {
            "PartManager": PartManager,
            "HiddenManager": HiddenManager,
        }
        install_registered_schema()

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
            filters={"name_Icontains": "st", "active": True},
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
            "complete": False,
        }
        assert schema.calls[0]["context"] is context
        query_text = str(schema.calls[0]["query"])
        assert "partmanagerList" in query_text
        assert {"name_Icontains": "st", "active": True} in schema.calls[0][
            "variables"
        ].values()
        assert 2 in schema.calls[0]["variables"].values()
        assert len(schema.calls) == 2
        assert "items { name material { name } }" in query_text
        assert "pageInfo { totalCount }" in query_text

    def test_query_rejects_hidden_managers(self) -> None:
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(
            ValueError, match=r"Manager 'HiddenManager' is not chat-exposed\."
        ):
            query(manager="HiddenManager", filters={}, fields=["name"])

    def test_query_rejects_manager_without_chat_exposed_before_execute(self) -> None:
        class DefaultHiddenManager:
            pass

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.manager_registry["DefaultHiddenManager"] = DefaultHiddenManager  # type: ignore[assignment]
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ManagerNotChatExposedError):
            query(manager="DefaultHiddenManager", filters={}, fields=["name"])

        assert schema.calls == []

    def test_query_surfaces_graphql_errors(self) -> None:
        GraphQL._schema = _RecordingSchema(  # type: ignore[assignment]
            _Result(errors=[SimpleNamespace(message="bad filter")])
        )

        with pytest.raises(ValueError, match="bad filter"):
            query(manager="PartManager", filters={}, fields=["name"])

    def test_query_rejects_invalid_limits_before_execute(self) -> None:
        for limit in ["5", 1.5, True, False, 0, -1]:
            with self.subTest(limit=limit):
                schema = _RecordingSchema(_Result(data={}))
                GraphQL._schema = schema  # type: ignore[assignment]

                with pytest.raises(
                    ValueError, match=r"^limit must be a positive integer$"
                ):
                    query(
                        manager="PartManager",
                        filters={},
                        fields=["name"],
                        limit=limit,  # type: ignore[arg-type]
                    )

                assert schema.calls == []

    def test_query_rejects_invalid_offsets_before_execute(self) -> None:
        for offset in ["1", 1.5, True, False, -1]:
            with self.subTest(offset=offset):
                schema = _RecordingSchema(_Result(data={}))
                GraphQL._schema = schema  # type: ignore[assignment]

                with pytest.raises(
                    ValueError, match=r"^offset must be a non-negative integer$"
                ):
                    query(
                        manager="PartManager",
                        filters={},
                        fields=["name"],
                        offset=offset,  # type: ignore[arg-type]
                    )

                assert schema.calls == []

    def test_execute_chat_tool_validates_query_pagination_before_dispatch(
        self,
    ) -> None:
        with patch("general_manager.chat.tools.query") as query_mock:
            with pytest.raises(
                ValueError, match=r"^offset must be a non-negative integer$"
            ):
                execute_chat_tool(
                    "query",
                    {
                        "manager": "PartManager",
                        "fields": ["name"],
                        "offset": "not-an-integer",
                    },
                    None,
                )

        query_mock.assert_not_called()

    @override_settings(GENERAL_MANAGER={"CHAT": {"tool_strategy": "direct"}})
    def test_execute_chat_tool_validates_direct_query_pagination_before_dispatch(
        self,
    ) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        install_registered_schema()

        with patch("general_manager.chat.tools.query") as query_mock:
            with pytest.raises(ValueError, match=r"^limit must be a positive integer$"):
                execute_chat_tool(
                    "query_partmanager",
                    {"fields": ["name"], "limit": 0},
                    None,
                )

        query_mock.assert_not_called()

    @override_settings(GENERAL_MANAGER={"CHAT": {"tool_strategy": "direct"}})
    def test_direct_query_tool_schema_restricts_pagination_values(self) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        install_registered_schema()

        tools = {tool["name"]: tool for tool in get_tool_definitions()}
        properties = tools["query_partmanager"]["input_schema"]["properties"]

        assert properties["limit"]["minimum"] == 1
        assert properties["offset"]["minimum"] == 0

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
        assert {"density_Gt": 7} in schema.calls[0]["variables"].values()

    def test_query_preserves_indexed_graphql_filter_keys_that_are_already_shaped(
        self,
    ) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            density__gt = graphene.Float()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
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
        assert {"density_Gt": 7} in schema.calls[0]["variables"].values()

    def test_query_uses_native_relation_lookup_filters(self) -> None:
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
            filters={"material_Name": "Steel"},
            fields=["name"],
        )

        assert result["data"] == [{"name": "Bolt"}]
        assert {"material_Name": "Steel"} in schema.calls[0]["variables"].values()

    def test_query_uses_exact_native_operator_name(
        self,
    ) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            density__gt = graphene.Float()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
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
            filters={"density_Gt": 5},
            fields=["name"],
        )

        assert result["data"] == [{"name": "Bolt"}]
        query_text = str(schema.calls[0]["query"])
        assert {"density_Gt": 5} in schema.calls[0]["variables"].values()
        assert "densityGt: 5" not in query_text

    def test_query_expands_wildcard_field_selection_to_indexed_scalar_fields(
        self,
    ) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()
            density = graphene.Float()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [{"name": "Bolt", "density": 5.0}],
                        "pageInfo": {"totalCount": 1},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]

        result = query(
            manager="PartManager",
            filters={},
            fields=["*"],
        )

        assert result["data"] == [{"name": "Bolt", "density": 5.0}]
        query_text = str(schema.calls[0]["query"])
        assert "items { name density }" in query_text
        assert "*" not in query_text

    def test_query_rejects_field_selection_injection(self) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(ValueError, match="Unknown GraphQL field"):
            query(
                manager="PartManager",
                filters={},
                fields=["name } } mutation Dangerous { createPart { success } } #"],
            )

    def test_query_rejects_unknown_scalar_field_when_schema_is_indexed(
        self,
    ) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(ValueError, match="Unknown GraphQL field"):
            query(manager="PartManager", filters={}, fields=["missing"])

    def test_query_rejects_unknown_filter_when_schema_filter_exists(self) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            name = graphene.String()

        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = _RecordingSchema(_Result(data={}))  # type: ignore[assignment]

        with pytest.raises(ValueError, match="is not defined"):
            query(manager="PartManager", filters={"status": "active"}, fields=["name"])

    def test_query_rejects_scalar_filter_mapping_before_execute(self) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            name = graphene.String()

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ValueError, match="cannot represent"):
            query(
                manager="PartManager",
                filters={"name": {"unknown": "x"}},
                fields=["name"],
            )

        assert schema.calls == []

    def test_query_rejects_scalar_list_filter_mapping_before_execute(self) -> None:
        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            name__in = graphene.List(graphene.String)

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ValueError, match="cannot represent"):
            query(
                manager="PartManager",
                filters={"name_In": [{"bad } injected {": "x"}]},
                fields=["name"],
            )

        assert schema.calls == []

    def test_query_rejects_malformed_nested_filter_before_execute(self) -> None:
        class MaterialFilter(graphene.InputObjectType):
            name = graphene.String()

        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            material = graphene.InputField(MaterialFilter)

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ValueError, match="is not defined"):
            query(
                manager="PartManager",
                filters={"material": {"bad } injected {": "x"}},
                fields=["name"],
            )

        assert schema.calls == []

    def test_query_rejects_unknown_nested_filter_when_schema_filter_exists(
        self,
    ) -> None:
        class MaterialFilter(graphene.InputObjectType):
            name = graphene.String()

        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            material = graphene.InputField(MaterialFilter)

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ValueError, match="is not defined"):
            query(
                manager="PartManager",
                filters={"material": {"status": "active"}},
                fields=["name"],
            )

        assert schema.calls == []

    def test_query_rejects_nested_scalar_filter_mapping_before_execute(self) -> None:
        class MaterialFilter(graphene.InputObjectType):
            name = graphene.String()

        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            material = graphene.InputField(MaterialFilter)

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ValueError, match="cannot represent"):
            query(
                manager="PartManager",
                filters={"material": {"name": {"unknown": "x"}}},
                fields=["name"],
            )

        assert schema.calls == []

    def test_query_rejects_nested_scalar_list_filter_mapping_before_execute(
        self,
    ) -> None:
        class MaterialFilter(graphene.InputObjectType):
            name__in = graphene.List(graphene.String)

        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            material = graphene.InputField(MaterialFilter)

        schema = _RecordingSchema(_Result(data={}))
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        with pytest.raises(ValueError, match="cannot represent"):
            query(
                manager="PartManager",
                filters={"material": {"name_In": [{"bad } injected {": "x"}]}},
                fields=["name"],
            )

        assert schema.calls == []

    def test_query_allows_valid_nested_relation_filter(self) -> None:
        class MaterialFilter(graphene.InputObjectType):
            name = graphene.String()

        class PartType(graphene.ObjectType):
            name = graphene.String()

        class PartFilter(graphene.InputObjectType):
            material = graphene.InputField(MaterialFilter)

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
        GraphQL.graphql_type_registry = {"PartManager": PartType}
        GraphQL.graphql_filter_type_registry = {"PartManager": PartFilter}
        GraphQL._schema = schema  # type: ignore[assignment]

        result = query(
            manager="PartManager",
            filters={"material": {"name": "Steel"}},
            fields=["name"],
        )

        assert result["data"] == [{"name": "Bolt"}]
        assert {"material": {"name": "Steel"}} in schema.calls[0]["variables"].values()

    def test_query_allows_valid_nested_relation_selection(self) -> None:
        class MaterialType(graphene.ObjectType):
            name = graphene.String()

        class PartType(graphene.ObjectType):
            name = graphene.String()
            material = graphene.Field(MaterialType)

        GraphQL.graphql_type_registry = {
            "PartManager": PartType,
            "MaterialManager": MaterialType,
        }
        GraphQL.manager_registry.setdefault("MaterialManager", self.PartManager)
        schema = _RecordingSchema(
            _Result(
                data={
                    "partmanagerList": {
                        "items": [{"name": "Bolt", "material": {"name": "Steel"}}],
                        "pageInfo": {"totalCount": 1},
                    }
                }
            )
        )
        GraphQL._schema = schema  # type: ignore[assignment]

        result = query(
            manager="PartManager",
            filters={},
            fields=["name", {"material": ["name"]}],
        )

        assert result["data"] == [{"name": "Bolt", "material": {"name": "Steel"}}]
        assert "material { name }" in str(schema.calls[0]["query"])

    def test_query_rejects_python_filter_alias(self) -> None:
        schema = _RecordingSchema(_Result(data={}))
        GraphQL._schema = schema
        with pytest.raises(ValueError, match="is not defined"):
            query(manager="PartManager", filters={"density__gt": 5}, fields=["name"])
        assert schema.calls == []

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
            "complete": False,
        }
        assert 2 in schema.calls[0]["variables"].values()
        assert len(schema.calls) == 2

    @override_settings(
        GENERAL_MANAGER={
            "CHAT": {
                "enabled": True,
                "provider": "tests.unit.test_chat_bootstrap.NoopProvider",
                "query_timeout_seconds": 2,
            }
        }
    )
    def test_query_ignores_boolean_planned_statement_timeout(self) -> None:
        events: list[str] = []

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
        original_execute = schema.execute

        def execute(query_text: str, context_value=None, variable_values=None):  # type: ignore[no-untyped-def]
            events.append("execute")
            return original_execute(
                query_text, context_value=context_value, variable_values=variable_values
            )

        schema.execute = execute  # type: ignore[method-assign]
        GraphQL._schema = schema  # type: ignore[assignment]

        class _Cursor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, list[int]]] = []

            def execute(self, sql: str, params: list[int]) -> None:
                events.append("set_timeout")
                self.calls.append((sql, params))

            def __enter__(self) -> "_Cursor":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
                del exc_type, exc, tb
                return None

        cursor = _Cursor()

        class _Atomic:
            def __enter__(self) -> None:
                events.append("atomic_enter")

            def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
                del exc_type, exc, tb
                events.append("atomic_exit")
                return None

        with (
            patch(
                "general_manager.chat.tools.connection",
                SimpleNamespace(vendor="postgresql", cursor=lambda: cursor),
            ),
            patch("django.db.transaction.atomic", return_value=_Atomic()),
        ):
            result = query(
                manager="PartManager",
                filters={},
                fields=["name"],
                context=SimpleNamespace(planned_query_timeout_ms=True),
            )

        assert result["data"] == [{"name": "Bolt"}]
        assert events == ["atomic_enter", "set_timeout", "execute", "atomic_exit"]
        assert cursor.calls == [("SET LOCAL statement_timeout = %s", [2000])]
