"""Warm GraphQL Excel lookups must not reread the workbook per candidate."""

from pathlib import Path
from types import SimpleNamespace
from collections.abc import Iterator
from typing import ClassVar
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from graphql import GraphQLSchema, graphql_sync
from openpyxl import Workbook

from general_manager import bootstrap
from general_manager.api import graphql_resolvers
from general_manager.api.graphql import GraphQL
from general_manager.interface import ExcelInterface
from general_manager.interface import _excel_query_snapshot
from general_manager.interface.capabilities import excel as excel_sync
from general_manager.interface.excel import ExcelCharField, ExcelIntegerField
from general_manager.interface.excel_workbook import ExcelWorkbookAdapter
from general_manager.manager import GeneralManager
from general_manager.manager.meta import GeneralManagerMeta
from general_manager.permission import ManagerBasedPermission


@pytest.fixture
def workbook_case(
    tmp_path: Path, workbook_rows: int
) -> Iterator[tuple[GraphQLSchema, Path, type[GeneralManager]]]:
    workbook_path = tmp_path / "projects.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Projects"
    sheet.append(["code", "name", "group_number"])
    for index in range(workbook_rows):
        # The same visible values in both sizes. Remaining rows do not match.
        group = 1 if index < 3 else 2
        sheet.append([f"{index:04d}", f"Project {index:04d}", group])
    workbook.save(workbook_path)
    workbook.close()

    class WarmExcelProject(GeneralManager):
        class Interface(ExcelInterface):
            code = ExcelCharField(unique=True)
            name = ExcelCharField()
            group_number = ExcelIntegerField()

            class Meta:
                workbook = str(workbook_path)
                sheet = "Projects"
                key = "code"
                header_row = 1

        class Permission(ManagerBasedPermission):
            __read__: ClassVar[list[str]] = ["public"]

    GraphQL.reset_registry()
    try:
        WarmExcelProject.Interface.sync_from_excel()
        GeneralManagerMeta.ensure_attributes_initialized(WarmExcelProject)
        bootstrap.handle_graph_ql([WarmExcelProject])
        yield GraphQL.get_schema().graphql_schema, workbook_path, WarmExcelProject
    finally:
        GraphQL.reset_registry()


@pytest.mark.parametrize("workbook_rows", [16, 160])
def test_warm_filter_sort_and_paging_do_not_hash_workbook_per_row(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    warm_schema, workbook_path, _ = workbook_case
    original_read_bytes = Path.read_bytes
    workbook_reads = 0

    def count_workbook_read(path: Path) -> bytes:
        nonlocal workbook_reads
        if path == workbook_path:
            workbook_reads += 1
        return original_read_bytes(path)

    with (
        patch.object(
            Path, "read_bytes", autospec=True, side_effect=count_workbook_read
        ),
        patch.object(ExcelWorkbookAdapter, "read_rows", autospec=True) as parse,
    ):
        result = graphql_sync(
            warm_schema,
            """
            {
              warmExcelProjectList(
                filter: {groupNumber_Exact: 1}
                orderBy: [{field: name, direction: DESC}]
                page: 2
                pageSize: 2
              ) {
                items { code name groupNumber }
                pageInfo { totalCount }
              }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": "0000", "name": "Project 0000", "groupNumber": 1}],
            "pageInfo": {"totalCount": 3},
        }
    }
    parse.assert_not_called()
    assert 0 < workbook_reads <= 12


@pytest.mark.parametrize("workbook_rows", [16])
def test_contributor_denial_precedes_snapshot_and_excludes_count(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    schema, _, _ = workbook_case
    checked = []
    original_permission = graphql_resolvers.check_read_permission
    original_snapshot = _excel_query_snapshot.snapshot_for_excel_query_operand

    def permission(instance, info, name, **kwargs):
        if name == "name":
            key = instance.identification["code"]
            assert original_snapshot(instance._interface) is None
            checked.append(key)
            if key in {"0000", "0002"}:
                return False
        return original_permission(instance, info, name, **kwargs)

    with patch.object(graphql_resolvers, "check_read_permission", permission):
        result = graphql_sync(
            schema,
            """
            {
              warmExcelProjectList(
                filter: {groupNumber_Exact: 1}
                orderBy: [{field: name, direction: DESC}]
                page: 1
                pageSize: 1
              ) {
                items { code }
                pageInfo { totalCount }
              }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": "0001"}],
            "pageInfo": {"totalCount": 1},
        }
    }
    assert checked == [f"{index:04d}" for index in range(16)]


@pytest.mark.parametrize("workbook_rows", [16])
def test_workbook_mutation_during_operand_permission_preserves_exact_scope(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from openpyxl import load_workbook

    schema, workbook_path, _ = workbook_case
    original_permission = graphql_resolvers.check_read_permission
    changed = False

    def permission(instance, info, name, **kwargs):
        nonlocal changed
        if name == "name" and not changed:
            changed = True
            workbook = load_workbook(workbook_path)
            sheet = workbook["Projects"]
            sheet["B2"] = "ZZZ"
            sheet.append(["0099", "ZZZZ (not authorized)", 1])
            workbook.save(workbook_path)
            workbook.close()
        return original_permission(instance, info, name, **kwargs)

    with patch.object(graphql_resolvers, "check_read_permission", permission):
        result = graphql_sync(
            schema,
            """
            {
              warmExcelProjectList(
                filter: {groupNumber_Exact: 1}
                orderBy: [{field: name, direction: DESC}]
                page: 1
                pageSize: 1
              ) {
                items { code name }
                pageInfo { totalCount }
              }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    assert changed
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": "0000", "name": "ZZZ"}],
            "pageInfo": {"totalCount": 3},
        }
    }


@pytest.mark.parametrize("workbook_rows", [16])
def test_workbook_revision_changed_during_predicates_retries_ordinary_reads(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from openpyxl import load_workbook

    from general_manager.api import _excel_list_operands

    schema, workbook_path, _ = workbook_case
    original = _excel_list_operands._apply_snapshot_operands
    changed = False

    def replace_after_provisional_selection(*args, **kwargs):
        nonlocal changed
        selected = original(*args, **kwargs)
        workbook = load_workbook(workbook_path)
        sheet = workbook["Projects"]
        sheet["B2"] = "ZZZ"
        workbook.save(workbook_path)
        workbook.close()
        changed = True
        return selected

    with patch.object(
        _excel_list_operands,
        "_apply_snapshot_operands",
        replace_after_provisional_selection,
    ):
        result = graphql_sync(
            schema,
            """
            {
              warmExcelProjectList(
                filter: {groupNumber_Exact: 1}
                orderBy: [{field: name, direction: DESC}]
                page: 1
                pageSize: 1
              ) {
                items { code name }
                pageInfo { totalCount }
              }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    assert changed
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": "0000", "name": "ZZZ"}],
            "pageInfo": {"totalCount": 3},
        }
    }


@pytest.mark.parametrize("workbook_rows", [16])
def test_explicit_field_callback_preceding_public_is_not_skipped(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from general_manager.permission.permission_checks import permission_functions

    schema, _, manager = workbook_case
    checked = []

    def callback(instance, _user, _config):
        checked.append(instance.identification["code"])
        return True

    with (
        patch.dict(
            permission_functions,
            {
                "excelObserveField": {
                    "permission_method": callback,
                    "permission_filter": lambda *_: None,
                }
            },
        ),
        patch.object(
            manager.Permission,
            "name",
            {"read": ["excelObserveField", "public"]},
            create=True,
        ),
    ):
        result = graphql_sync(
            schema,
            '{ warmExcelProjectList(filter: {name_Contains: "Project"}) { items { code } } }',
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    assert result.errors is None
    assert checked == [f"{index:04d}" for index in range(16)]


@pytest.mark.parametrize("workbook_rows", [16])
def test_custom_row_constructor_can_change_later_operand_authorization(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    schema, _, manager = workbook_case
    original = manager.Interface.__init__

    def init(instance, *args, **kwargs):
        original(instance, *args, **kwargs)
        # Main's exact iterator interleaves construction and permission checks.
        # The first row is readable before this later constructor tightens it.
        if instance.identification["code"] == "0001":
            manager.Permission.__read__ = ["isAuthenticated"]

    with (
        patch.object(manager.Permission, "__read__", ["public"]),
        patch.object(manager.Interface, "__init__", init),
    ):
        result = graphql_sync(
            schema,
            """
            {
              warmExcelProjectList(
                filter: {name_Contains: "Project"} page: 1 pageSize: 1
              ) { items { code } pageInfo { totalCount } }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": None}],
            "pageInfo": {"totalCount": 1},
        }
    }


@pytest.mark.parametrize("workbook_rows", [160])
def test_custom_reader_can_opt_in_for_typed_flat_excel_identifiers(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from copy import deepcopy

    _, _, manager = workbook_case
    hits = []

    class ConsumerRead(excel_sync.ExcelReadCapability):
        query_operand_snapshot_fields = frozenset({"code", "group_number"})

        def get_attribute_types(self, interface_cls):
            result = super().get_attribute_types(interface_cls)
            result["group_number"]["is_identifier"] = True
            return result

        def get_data(self, interface_instance):
            snapshot = self.get_query_operand_snapshot(interface_instance)
            if snapshot is None:
                return super().get_data(interface_instance)
            hits.append(snapshot.key)
            interface_instance._excel_observed_snapshot = snapshot
            return deepcopy(snapshot.values)

    with patch.dict(manager.Interface._capability_handlers, {"read": ConsumerRead()}):
        GraphQL.reset_registry()
        bootstrap.handle_graph_ql([manager])
        schema = GraphQL.get_schema().graphql_schema
        result = graphql_sync(
            schema,
            """
            query Page($group: ID!) {
              warmExcelProjectList(
                filter: {groupNumber_Exact: $group}
                orderBy: [{field: code, direction: ASC}]
                page: 2 pageSize: 2
              ) {
                items { code groupNumber }
                pageInfo { totalCount }
              }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
            variable_values={"group": "1"},
        )
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": "0002", "groupNumber": "1"}],
            "pageInfo": {"totalCount": 3},
        }
    }
    assert len(hits) > 160


@pytest.mark.parametrize("workbook_rows", [16])
@pytest.mark.parametrize("limit", [None, 1])
def test_excel_operand_budget_preserves_field_policy(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
    limit: int | None,
) -> None:
    from django.test import override_settings

    from general_manager.api import graphql_contributors
    from general_manager.api.graphql_query_permissions import _LIMIT_ERROR
    from general_manager.permission.permission_checks import permission_functions

    schema, _, manager = workbook_case
    checked = []
    extra_accounting = []
    original = graphql_contributors.additional_local_read_permission

    def callback(instance, _user, _config):
        key = instance.identification["code"]
        checked.append(key)
        return key == "0001"

    def classify(permission, name, **kwargs):
        if name:
            extra_accounting.append(name)
        return original(permission, name, **kwargs)

    with (
        override_settings(GENERAL_MANAGER={"GRAPHQL_QUERY_PERMISSION_LIMIT": limit}),
        patch.dict(
            permission_functions,
            {
                "excelSelectOneField": {
                    "permission_method": callback,
                    "permission_filter": lambda *_: None,
                }
            },
        ),
        patch.object(
            manager.Permission, "name", {"read": ["excelSelectOneField"]}, create=True
        ),
        patch.object(
            graphql_contributors, "additional_local_read_permission", classify
        ),
    ):
        result = graphql_sync(
            schema,
            """
            {
              warmExcelProjectList(filter: {name_Contains: "Project"}, page: 1, pageSize: 1) {
                items { code } pageInfo { totalCount }
              }
            }
            """,
            context_value=SimpleNamespace(user=AnonymousUser()),
        )
    if limit is not None:
        assert result.errors is not None
        assert result.errors[0].message == _LIMIT_ERROR
        assert result.data == {"warmExcelProjectList": None}
        assert checked == ["0000"]
        assert extra_accounting == ["name", "name"]
        return
    assert result.errors is None
    assert result.data == {
        "warmExcelProjectList": {
            "items": [{"code": "0001"}],
            "pageInfo": {"totalCount": 1},
        }
    }
    assert checked == [f"{index:04d}" for index in range(16)]
    assert extra_accounting == []


@pytest.mark.parametrize("workbook_rows", [16])
def test_optional_operand_refresh_conflict_is_a_snapshot_miss(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from general_manager.interface.excel import ExcelWriteConflictError
    from general_manager.interface.excel_workbook import workbook_fingerprint

    _, path, manager = workbook_case
    interface = manager.Interface
    source = interface.excel_meta
    fingerprint = workbook_fingerprint(str(path))
    with patch.object(
        interface,
        "sync_from_excel",
        side_effect=ExcelWriteConflictError.workbook_changed("read"),
    ):
        assert (
            _excel_query_snapshot.collect_excel_operand_rows(
                interface, [], source, fingerprint, frozenset({"name"})
            )
            is None
        )
