"""Category filters use observed public values, rather than record labels."""

from __future__ import annotations

from general_manager.chat.graphql_contract import clear_contract_cache
import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import ClassVar

import graphene
import pytest
from django.contrib.auth.models import AnonymousUser

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_resolvers import create_root_list_resolver
from general_manager.api.graphql_search import create_filter_options
from general_manager.api.graphql_units import attach_unit_contracts
from general_manager.chat.planned.models import EvidenceRequirement, SchemaBinding
from general_manager.chat.providers.base import ToolCallEvent
from general_manager.chat.tools import ScopeChatContext, execute_chat_tool, query
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission.manager_based_permission import (
    AdditiveManagerPermission,
)
from general_manager.utils.filter_parser import create_filter_function
from tests.unit.test_chat_planned_scheduler import _task
from tests.unit.test_chat_planned_tool_feedback import _record_rounds, _runner
from tests.unit import test_chat_graphql_native as native_tools
from tests.utils.simple_manager_interface import BaseTestInterface, SimpleBucket


@pytest.fixture(params=[("Cargo", "class-x"), ("Portfolio", "division-42")])
def category_schema(request, monkeypatch):
    GraphQL.reset_registry()
    for registry in (
        "all_classes",
        "pending_graphql_interfaces",
        "pending_attribute_initialization",
    ):
        monkeypatch.setattr(
            GeneralManagerMeta, registry, list(getattr(GeneralManagerMeta, registry))
        )
    manager, actual = request.param
    calls = []
    rows = [
        {"id": 1, "name": "Display Alpha", "family": actual, "rank": 1},
        {"id": 2, "name": "Display Beta", "family": actual, "rank": 2},
        {"id": 3, "name": actual, "family": "other", "rank": 3},
    ]

    class InventoryRows(SimpleBucket):
        def filter(self, **kwargs):
            return InventoryRows(
                self._manager_class,
                [
                    row
                    for row in self._data
                    if all(
                        create_filter_function(key, value)(row)
                        for key, value in kwargs.items()
                    )
                ],
            )

        def exclude(self, **kwargs):
            return InventoryRows(
                self._manager_class,
                [
                    row
                    for row in self._data
                    if not all(
                        create_filter_function(key, value)(row)
                        for key, value in kwargs.items()
                    )
                ],
            )

    class InventoryInterface(BaseTestInterface):
        input_fields: ClassVar = {"id": Input(int)}

        @classmethod
        def get_attribute_types(cls):
            return {
                "id": {"type": int},
                "name": {"type": str},
                "family": {"type": str},
                "rank": {"type": int},
            }

        @classmethod
        def filter(cls, **kwargs):
            calls.append(kwargs)
            return InventoryRows(
                cls._parent_class, [SimpleNamespace(**row) for row in rows]
            ).filter(**kwargs)

    class Inventory(GeneralManager):
        Interface = InventoryInterface
        # Use the same default policy as normal manager bootstrap, independently
        # of other tests mutating the base GeneralManager.Permission attribute.
        Permission = AdditiveManagerPermission
        chat_exposed = True

    CategoryFilter = create_filter_options(
        Inventory, {}, GraphQL._map_field_to_graphene_read
    )

    class HiddenCategory(graphene.ObjectType):
        family = graphene.String()

    class Item(graphene.ObjectType):
        class Meta:
            name = manager

        id = graphene.Int()
        name = graphene.String()
        family = graphene.String()
        rank = graphene.Int()
        secret = graphene.Field(HiddenCategory)

    class PageInfo(graphene.ObjectType):
        total_count = graphene.Int(required=True)

    class Page(graphene.ObjectType):
        items = graphene.List(Item, required=True)
        pageInfo = graphene.Field(PageInfo, required=True)

    class PublicQuery(graphene.ObjectType):
        exposed_items = graphene.Field(
            Page,
            filter=CategoryFilter(),
            page_size=graphene.Int(),
            page=graphene.Int(),
            resolver=create_root_list_resolver(Inventory),
        )

    GraphQL.manager_registry = {
        manager: Inventory,
        "HiddenCategory": SimpleNamespace(chat_exposed=False),
    }
    GraphQL.graphql_type_registry = {manager: Item, "HiddenCategory": HiddenCategory}
    GraphQL._schema = graphene.Schema(query=PublicQuery)
    attach_unit_contracts(
        GraphQL._schema.graphql_schema, {manager: Inventory}, {manager: Item}
    )
    yield SimpleNamespace(
        manager=manager,
        actual=actual,
        calls=calls,
        rows=rows,
        query_type=PublicQuery,
        context=ScopeChatContext(user=AnonymousUser()),
    )
    GraphQL.reset_registry()


def _public_runner(manager):
    task = replace(
        _task("task_1"),
        requirements=(
            EvidenceRequirement(
                "schema",
                "schema",
                "exposed contract",
                None,
                schema=SchemaBinding(manager, "full", (), "current"),
            ),
            EvidenceRequirement("query", "query", "category population", None),
        ),
        completion_criteria=("schema", "query"),
    )
    runner = _runner(
        lambda name, args, context: execute_chat_tool(name, args, context), tasks=[task]
    )
    # Real request users avoid the permission layer resolving None as a user ID.
    runner.scope = {"user": AnonymousUser()}
    return runner


def _call(runner, name, **args):
    return asyncio.run(
        runner.execute_tool(
            runner.runtimes["task_1"], ToolCallEvent("call", name, args)
        )
    )


def _lookup(runner, schema, **extra):
    _call(runner, "get_manager_schema", manager=schema.manager, view="full")
    return _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "name", "family", "rank"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
        **extra,
    )


def _binding(*, field="family", path=None):
    return {
        "schema_evidence_id": "task_1:schema:1",
        "query_evidence_id": "task_1:query:2",
        "field": field,
        "filter_path": path or ["filters", "family_Exact"],
    }


def test_unlinked_schema_lookup_reports_repairable_reason_without_query_authority(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _call(runner, "get_manager_schema", manager=schema.manager, view="overview")
    assert not runner.evidence.is_linked("task_1", "task_1:schema:1")
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    assert result == {
        "status": "error",
        "code": "invalid_category_binding",
        "reason": "schema_evidence_not_linked",
    }
    assert not valid and len(schema.calls) == before
    assert not any(r.kind == "query" for r in runner.evidence.records)

    _call(runner, "get_manager_schema", manager=schema.manager, view="full")
    assert runner.evidence.is_linked("task_1", "task_1:schema:2")
    rows, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        category_lookup={"schema_evidence_id": "task_1:schema:2"},
    )
    assert valid and rows["complete"] is True
    assert len(rows["data"]) == 3
    assert len(schema.calls) == before + 1

    # Unknown sources retain the generic rejection and cannot reveal metadata.
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        category_lookup={"schema_evidence_id": "unobserved"},
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and len(schema.calls) == before + 1


def test_category_binding_reports_missing_input_definition_then_accepts_explicit_full(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    runtime = runner.runtimes["task_1"]
    runtime.task = replace(
        runtime.task,
        requirements=tuple(
            replace(r, schema=SchemaBinding(schema.manager, "overview", (), "current"))
            if r.kind == "schema"
            else r
            for r in runtime.task.requirements
        ),
    )
    _call(runner, "get_manager_schema", manager=schema.manager)
    _, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    assert valid
    before = len(schema.calls)
    business = {
        "manager": schema.manager,
        "fields": ["id", "family"],
        "filters": {"family_Exact": schema.actual},
    }
    binding = _binding()
    result, valid = _call(runner, "query", **business, category_bindings=[binding])
    assert not valid and result["reason"] == "filter_input_definition_not_observed"
    assert len(schema.calls) == before

    # Full is explicitly requested and compatible with the declared overview.
    _call(runner, "get_manager_schema", manager=schema.manager, view="full")
    binding["schema_evidence_id"] = "task_1:schema:3"
    binding["filter_path"] = ["family_Exact"]
    result, valid = _call(runner, "query", **business, category_bindings=[binding])
    assert not valid and result["reason"] == "invalid_filter_path"
    assert len(schema.calls) == before
    binding["filter_path"] = ["filters", "family_Exact"]
    result, valid = _call(runner, "query", **business, category_bindings=[binding])
    assert valid and [row["id"] for row in result["data"]] == [1, 2]


def test_unobserved_record_names_cannot_bind_family_or_supply_absence_evidence(
    category_schema,
):
    schema = category_schema
    # The real public query demonstrates why names cannot prove family absence.
    false_absence = query(
        manager=schema.manager,
        fields=["id"],
        filters={"family_In": ["Display Alpha", "Display Beta"]},
        context=schema.context,
    )
    assert false_absence["data"] == [] and false_absence["complete"] is True
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_In": ["Display Alpha", "Display Beta"]},
        category_bindings=[_binding(path=["filters", "family_In"])],
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and len(schema.calls) == before
    assert len(runner.evidence.records) == 2


@pytest.mark.parametrize("native_arguments", [False, True])
def test_actual_category_value_returns_family_members_despite_mismatched_names(
    category_schema, native_arguments
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    lookup, valid = _lookup(runner, schema)
    assert valid and lookup["complete"] is True
    filter_args = (
        {"arguments": {"filter": {"family_Exact": schema.actual}}}
        if native_arguments
        else {"filters": {"family_Exact": schema.actual}}
    )
    path = ["arguments", "filter", "family_Exact"] if native_arguments else None
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "name", "family"],
        category_bindings=[_binding(path=path)],
        **filter_args,
    )
    assert valid and result["complete"] is True
    assert [row["id"] for row in result["data"]] == [1, 2]
    assert all(row["name"] != schema.actual for row in result["data"])


@pytest.mark.parametrize(
    "lookup_args", [{"limit": 1}, {"filters": {"name": "Display Alpha"}}]
)
def test_incomplete_or_filtered_lookup_cannot_authorize_a_category_filter(
    category_schema, lookup_args
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema, **lookup_args)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert result.get("code") == "invalid_category_binding" and not valid
    assert len(schema.calls) == before


@pytest.mark.parametrize("change", ["schema", "exposure"])
def test_changed_schema_or_exposure_revokes_actual_value_binding(
    category_schema, change
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    if change == "schema":
        GraphQL._schema = graphene.Schema(query=schema.query_type, auto_camelcase=False)
    else:
        GraphQL.manager_registry[schema.manager].chat_exposed = False
    clear_contract_cache()
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert result.get("code") == "invalid_category_binding" and not valid
    assert len(schema.calls) == before


@pytest.mark.parametrize(
    "binding",
    [
        _binding(field="name"),
        _binding(field="secret.family"),
        {**_binding(), "query_evidence_id": "other_task:query:2"},
        {**_binding(), "schema_evidence_id": "missing"},
    ],
)
def test_hidden_mismatched_or_foreign_sources_never_authorize_binding(
    category_schema, binding
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_Exact": schema.actual},
        category_bindings=[binding],
    )
    assert result.get("code") == "invalid_category_binding" and not valid
    assert len(schema.calls) == before


def test_exact_user_scalar_queries_remain_valid_even_when_no_record_matches(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"name": "exact user-provided missing identifier"},
    )
    assert valid and result["data"] == [] and result["complete"] is True


@pytest.mark.parametrize("value", [True, None, [1, True], 1.0, float("inf")])
def test_bool_or_null_never_matches_an_observed_integer_category(
    category_schema, value
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"rank": value},
        category_bindings=[_binding(field="rank", path=["filters", "rank"])],
    )
    assert result.get("code") == "invalid_category_binding" and not valid
    assert len(schema.calls) == before


def test_complete_actual_scalar_list_is_bound_without_guessed_labels(category_schema):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_In": [schema.actual, "other"]},
        category_bindings=[_binding(path=["filters", "family_In"])],
    )
    assert valid and [row["id"] for row in result["data"]] == [1, 2, 3]


def test_executor_repairs_unverified_names_before_selecting_actual_query_evidence(
    category_schema, monkeypatch
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    runtime = runner.runtimes["task_1"]
    _record_rounds(
        monkeypatch,
        [
            ToolCallEvent(
                "schema",
                "get_manager_schema",
                {"manager": schema.manager, "view": "full"},
            ),
            ToolCallEvent(
                "lookup",
                "query",
                {
                    "manager": schema.manager,
                    "fields": ["id", "name", "family"],
                    "category_lookup": {"schema_evidence_id": "task_1:schema:1"},
                },
            ),
            ToolCallEvent(
                "wrong",
                "query",
                {
                    "manager": schema.manager,
                    "fields": ["id"],
                    "filters": {"family_Exact": "Display Alpha"},
                    "category_bindings": [_binding()],
                },
            ),
            {"action": "complete", "evidence_ids": ["task_1:query:3"]},
            ToolCallEvent(
                "actual",
                "query",
                {
                    "manager": schema.manager,
                    "fields": ["id", "family"],
                    "filters": {"family_Exact": schema.actual},
                    "category_bindings": [_binding()],
                },
            ),
            {
                "action": "complete",
                "evidence_ids": ["task_1:schema:1", "task_1:query:3"],
            },
        ],
    )
    asyncio.run(runner.run_task(runtime))
    assert runtime.status == "resolved"
    assert len(schema.calls) == 2
    records = runner.evidence.for_task("task_1")
    assert [record.kind for record in records] == ["schema", "query", "query"]
    assert [row["id"] for row in records[-1].payload()["data"]] == [1, 2]
    wrong_result = next(
        message.tool_result
        for message in runtime.tool_history
        if message.tool_call_id == "wrong"
    )
    assert wrong_result == {"status": "error", "code": "invalid_category_binding"}


@pytest.mark.parametrize("mode", ["lookup", "binding"])
def test_exposure_change_during_public_query_rejects_category_evidence_and_cached_retry(
    category_schema, mode
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    if mode == "binding":
        _lookup(runner, schema)
    else:
        _call(runner, "get_manager_schema", manager=schema.manager, view="full")

    def change_after_read(name, args, context):
        result = execute_chat_tool(name, args, context)
        GraphQL.manager_registry[schema.manager].chat_exposed = False
        clear_contract_cache()
        return result

    runner.callbacks = replace(runner.callbacks, execute_tool=change_after_read)
    metadata = (
        {"category_lookup": {"schema_evidence_id": "task_1:schema:1"}}
        if mode == "lookup"
        else {
            "category_bindings": [_binding()],
            "filters": {"family_Exact": schema.actual},
        }
    )
    result, valid = _call(
        runner, "query", manager=schema.manager, fields=["family"], **metadata
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid
    assert len(runner.evidence.records) == (2 if mode == "binding" else 1)
    GraphQL.manager_registry[schema.manager].chat_exposed = True
    clear_contract_cache()
    before = len(schema.calls)
    retry, valid = _call(
        runner, "query", manager=schema.manager, fields=["family"], **metadata
    )
    assert retry == result and not valid
    assert len(schema.calls) == before


def test_backend_failure_keeps_its_existing_error_when_category_lookup_was_requested(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _call(runner, "get_manager_schema", manager=schema.manager, view="full")

    def failed_read(*_):
        message = "private backend failure"
        raise RuntimeError(message)

    runner.callbacks = replace(runner.callbacks, execute_tool=failed_read)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    assert result == {"status": "error", "code": "tool_failed"} and not valid
    assert len(runner.evidence.records) == 1


@pytest.fixture
def unannotated_schema():
    yield from native_tools.native_schema.__wrapped__()


def test_custom_filter_spelling_without_executable_meaning_cannot_bind_category(
    unannotated_schema,
):
    runner = _public_runner("Material")
    _call(runner, "get_manager_schema", manager="Material", view="full")
    _call(
        runner,
        "query",
        manager="Material",
        fields=["code"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    before = len(unannotated_schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager="Material",
        fields=["code"],
        filters={"code": "M0"},
        category_bindings=[_binding(field="code", path=["filters", "code"])],
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and len(unannotated_schema.calls) == before


@pytest.mark.parametrize(
    "extra",
    [
        {"unproved_selector": "ignored"},
        {"offset": True},
        {"arguments": {"page": 2, "pageSize": 1}},
    ],
)
def test_category_lookup_rejects_unknown_or_noninitial_population_selectors(
    category_schema, extra
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _call(runner, "get_manager_schema", manager=schema.manager, view="full")
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
        **extra,
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and schema.calls == []


@pytest.mark.parametrize(
    "selector", ["family_Contains", "family_Icontains", "family_Gte", "name_Exact"]
)
def test_observed_value_cannot_authorize_a_different_field_or_nonmembership_operator(
    category_schema, selector
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={selector: schema.actual},
        category_bindings=[_binding(path=["filters", selector])],
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and len(schema.calls) == before


def test_arbitrary_root_cannot_supply_category_authority_even_with_generated_inputs(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    # Identical field/input spellings cannot prove what a custom resolver executes.
    root = GraphQL._schema.graphql_schema.query_type.fields["exposedItems"]
    root.resolve = lambda *_, **__: {
        "items": schema.rows,
        "page_info": {"total_count": len(schema.rows)},
    }
    clear_contract_cache()
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid


def test_same_snapshot_selected_output_and_input_details_can_bind_without_full_schema(
    category_schema,
):
    schema = category_schema
    overview = execute_chat_tool(
        "get_manager_schema", {"manager": schema.manager}, schema.context
    )
    root = overview["roots"][0]
    filter_type = overview["root_fields"][root]["arguments"]["filter"][
        "type"
    ].removesuffix("!")
    names = (overview["type"], filter_type)
    runner = _public_runner(schema.manager)
    runtime = runner.runtimes["task_1"]
    runtime.task = replace(
        runtime.task,
        requirements=(
            replace(
                runtime.task.requirements[0],
                schema=SchemaBinding(
                    schema.manager, "detail", names, overview["snapshot"]
                ),
            ),
            runtime.task.requirements[1],
        ),
    )
    detail, valid = _call(
        runner,
        "get_manager_schema",
        manager=schema.manager,
        view="detail",
        types=list(names),
        snapshot=overview["snapshot"],
    )
    assert valid and detail["schema_complete"] is False
    assert set(detail["types"]) == set(names)
    _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["family"],
        category_lookup={"schema_evidence_id": "task_1:schema:1"},
    )
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_In": [schema.actual]},
        category_bindings=[_binding(path=["filters", "family_In"])],
    )
    assert valid and [row["id"] for row in result["data"]] == [1, 2]


def test_declared_binding_does_not_silently_ignore_extra_selector_arguments(
    category_schema,
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _lookup(runner, schema)
    before = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id"],
        filters={"family_Exact": schema.actual},
        category_bindings=[_binding()],
        unproved_selector="ignored",
    )
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and len(schema.calls) == before
