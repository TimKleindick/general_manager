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


@pytest.mark.parametrize("workbook_rows", [160])
def test_full_excel_pages_reuse_raw_generation_without_rehashing_each_output(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    schema, workbook_path, _ = workbook_case
    original_read_bytes = Path.read_bytes
    workbook_reads = 0

    def count_workbook_read(path: Path) -> bytes:
        nonlocal workbook_reads
        if path == workbook_path:
            workbook_reads += 1
        return original_read_bytes(path)

    query = """
        { warmExcelProjectList(
            filter: {groupNumber_Exact: 2} page: 1 pageSize: 100
          ) {
            items { code name groupNumber }
            pageInfo { totalCount }
          }
        }
    """
    with (
        patch.object(
            Path, "read_bytes", autospec=True, side_effect=count_workbook_read
        ),
        patch.object(ExcelWorkbookAdapter, "read_rows", autospec=True) as parse,
    ):
        for index in (1, 2):
            with excel_sync.ExcelReadCapability.read_snapshot_scope():
                result = graphql_sync(
                    schema, query, context_value=SimpleNamespace(user=AnonymousUser())
                )
            assert result.errors is None
            assert result.data == {
                "warmExcelProjectList": {
                    "items": [
                        {
                            "code": f"{row:04d}",
                            "name": f"Project {row:04d}",
                            "groupNumber": 2,
                        }
                        for row in range(3, 103)
                    ],
                    "pageInfo": {"totalCount": 157},
                }
            }
            assert workbook_reads == index
    parse.assert_not_called()


@pytest.mark.parametrize("workbook_rows", [16])
def test_scope_detects_content_change_at_same_size_and_mtime(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    import os
    from zipfile import ZIP_STORED, ZipFile

    _, path, manager = workbook_case

    def stored_workbook(name: bytes) -> bytes:
        from io import BytesIO

        result = BytesIO()
        with ZipFile(path) as source, ZipFile(result, "w", ZIP_STORED) as target:
            for entry in source.namelist():
                payload = source.read(entry)
                if entry == "xl/worksheets/sheet1.xml":
                    assert b"Project 0000" in payload
                    payload = payload.replace(b"Project 0000", name)
                target.writestr(entry, payload)
        return result.getvalue()

    first = stored_workbook(b"Project 0000")
    second = stored_workbook(b"Renamed 0000")
    assert len(first) == len(second)
    path.write_bytes(first)
    stamp = path.stat()
    with excel_sync.ExcelReadCapability.read_snapshot_scope():
        assert manager(code="0000").name == "Project 0000"
        path.write_bytes(second)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert manager(code="0000").name == "Project 0000"
    with excel_sync.ExcelReadCapability.read_snapshot_scope():
        assert manager(code="0000").name == "Renamed 0000"


@pytest.mark.django_db
@pytest.mark.parametrize("workbook_rows", [16])
def test_managed_write_invalidates_scoped_raw_generation(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    _, _, manager = workbook_case
    with excel_sync.ExcelReadCapability.read_snapshot_scope():
        row = manager(code="0000")
        assert row.name == "Project 0000"
        with excel_sync.ExcelReadCapability.read_snapshot_scope():
            row.update(name="Changed by GM", ignore_permission=True)
            assert manager(code="0000").name == "Changed by GM"
        assert manager(code="0000").name == "Changed by GM"


@pytest.mark.parametrize("workbook_rows", [16])
def test_failed_structure_refresh_keeps_stock_detail_behavior(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from openpyxl import load_workbook

    _, path, manager = workbook_case
    workbook = load_workbook(path)
    workbook["Projects"]["A1"] = "bad_header"
    workbook.save(path)
    workbook.close()

    # Stock detail has always retained the last good raw row; bucket refresh
    # must still report invalid source structure instead of pinning stale data.
    with excel_sync.ExcelReadCapability.read_snapshot_scope():
        assert manager(code="0000").name == "Project 0000"
        with pytest.raises(excel_sync.ExcelStructureError):
            list(manager.all())


@pytest.mark.parametrize("workbook_rows", [16])
def test_opt_in_reader_and_sync_share_one_verified_observation(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from general_manager.interface.excel_workbook import workbook_read_fingerprint

    _, path, manager = workbook_case
    calls = 0
    original = Path.read_bytes

    def count_read(file: Path) -> bytes:
        nonlocal calls
        if file == path:
            calls += 1
        return original(file)

    class CustomReader(excel_sync.ExcelReadCapability):
        query_read_snapshots: ClassVar[bool] = True

        def get_data(self, interface_instance):
            row = self.get_query_read_snapshot(interface_instance)
            if row is None:
                return super().get_data(interface_instance)
            interface_instance._excel_observed_snapshot = row
            return dict(row.values)

    class CustomSync(excel_sync.ExcelSyncCapability):
        def sync_from_excel(self, interface_cls, *, force=False):
            if not force:
                workbook_read_fingerprint(interface_cls.excel_meta.workbook)
            return super().sync_from_excel(interface_cls, force=force)

    with (
        patch.dict(
            manager.Interface._capability_handlers,
            {"read": CustomReader(), "excel_sync": CustomSync()},
        ),
        patch.object(Path, "read_bytes", autospec=True, side_effect=count_read),
        excel_sync.ExcelReadCapability.read_snapshot_scope(),
    ):
        rows = list(manager.filter(group_number=1))
        assert [row.name for row in rows] == [f"Project 000{i}" for i in range(3)]
        assert calls == 1


@pytest.mark.parametrize("workbook_rows", [16])
def test_snapshot_read_runs_field_permissions_for_each_actor(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    schema, _, _ = workbook_case
    original_permission = graphql_resolvers.check_read_permission
    query = """
        { warmExcelProjectList(
            filter: {groupNumber_Exact: 1} orderBy: [{field: name, direction: ASC}]
          ) {
            items { code name }
            pageInfo { totalCount }
          }
        }
    """

    def permission(instance, info, name, **kwargs):
        if name == "name" and instance.identification["code"] != info.context.allowed:
            return False
        return original_permission(instance, info, name, **kwargs)

    with patch.object(graphql_resolvers, "check_read_permission", permission):
        for allowed in ("0000", "0001", "0000"):
            with excel_sync.ExcelReadCapability.read_snapshot_scope():
                result = graphql_sync(
                    schema,
                    query,
                    context_value=SimpleNamespace(
                        user=AnonymousUser(), allowed=allowed
                    ),
                )
            assert result.errors is None
            assert result.data == {
                "warmExcelProjectList": {
                    "items": [{"code": allowed, "name": f"Project {allowed}"}],
                    "pageInfo": {"totalCount": 1},
                }
            }


@pytest.mark.parametrize("workbook_rows", [16])
def test_graphql_query_scope_survives_async_view_completion(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from asgiref.sync import sync_to_async
    from graphql import ExecutionResult

    from general_manager.api.graphql_view import GeneralManagerGraphQLView

    _, path, manager = workbook_case
    view = GeneralManagerGraphQLView(schema=GraphQL.get_schema())
    original = Path.read_bytes
    reads = 0

    def count_read(file: Path) -> bytes:
        nonlocal reads
        if file == path:
            reads += 1
        return original(file)

    def read_name() -> object:
        return manager(code="0000").name

    async def run_query() -> ExecutionResult:
        name = manager(code="0000").name
        callback_name = await sync_to_async(read_name, thread_sensitive=True)()
        return ExecutionResult(data={"name": name, "callbackName": callback_name})

    def execute_query(*_args: object) -> object:
        assert manager(code="0000").name == "Project 0000"
        return run_query()

    with (
        patch.object(view, "execute_graphql_request", side_effect=execute_query),
        patch.object(Path, "read_bytes", autospec=True, side_effect=count_read),
    ):
        result = view._execute_and_complete(
            object(), {}, "{ warmExcelProjectList { items { name } } }", {}, None, False
        )
    assert result is not None and result.data == {
        "name": "Project 0000",
        "callbackName": "Project 0000",
    }
    assert reads == 1


@pytest.mark.parametrize("workbook_rows", [16])
def test_custom_sync_dispatch_is_not_implicitly_opted_in(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    _, _, manager = workbook_case
    original = manager.Interface.sync_from_excel
    with (
        patch.object(manager.Interface, "sync_from_excel", wraps=original) as sync,
        excel_sync.ExcelReadCapability.read_snapshot_scope(),
    ):
        row = manager(code="0000")
        assert row.name == "Project 0000"
        assert row.group_number == 1
        assert sync.call_count == 2


@pytest.mark.parametrize("workbook_rows", [16])
def test_pinned_read_does_not_take_a_newer_shared_derived_value(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from contextvars import Context

    from openpyxl import load_workbook

    from general_manager.cache.cache_decorator import cached

    _, path, manager = workbook_case
    calculations = 0

    @cached(cache="dependency")
    def project_display_name() -> str:
        nonlocal calculations
        calculations += 1
        return str(manager(code="0000").name)

    with excel_sync.ExcelReadCapability.read_snapshot_scope():
        assert manager(code="0000").name == "Project 0000"
        assert project_display_name() == "Project 0000"
        assert project_display_name() == "Project 0000"
        # Matching generations still use the ordinary shared cache.
        assert calculations == 1

        def refresh_from_another_read() -> None:
            workbook = load_workbook(path)
            workbook["Projects"]["B2"] = "Revised project"
            workbook.save(path)
            workbook.close()
            manager.Interface.sync_from_excel()
            assert project_display_name() == "Revised project"

        # Separate request context, with the same shared workbook and result caches.
        Context().run(refresh_from_another_read)
        assert project_display_name() == "Project 0000"

    assert project_display_name() == "Revised project"
    assert calculations == 3


@pytest.mark.parametrize("workbook_rows", [16])
def test_unrelated_worker_cannot_borrow_read_scope(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context

    _, path, manager = workbook_case
    original = Path.read_bytes
    reads = 0

    def count_read(file: Path) -> bytes:
        nonlocal reads
        if file == path:
            reads += 1
        return original(file)

    def read_name() -> object:
        return manager(code="0000").name

    with (
        patch.object(Path, "read_bytes", autospec=True, side_effect=count_read),
        excel_sync.ExcelReadCapability.read_snapshot_scope(),
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        assert read_name() == "Project 0000"
        assert worker.submit(copy_context().run, read_name).result() == "Project 0000"
        assert read_name() == "Project 0000"
        assert reads == 2


@pytest.mark.parametrize("workbook_rows", [16])
def test_workbook_io_does_not_hold_operation_state_mutex(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from general_manager.interface._excel_read_scope import _scope

    _, path, manager = workbook_case
    original = Path.read_bytes

    def read_workbook_bytes(file: Path) -> bytes:
        if file == path:
            scope = _scope()
            assert scope is not None

            def acquire_state() -> bool:
                acquired = scope.lock.acquire(blocking=False)
                if acquired:
                    scope.lock.release()
                return acquired

            # FileLock is already held here. The operation state lock must not
            # be held against a force/write arriving in the other view thread.
            assert worker.submit(acquire_state).result()
        return original(file)

    with (
        patch.object(
            Path, "read_bytes", autospec=True, side_effect=read_workbook_bytes
        ),
        ThreadPoolExecutor(max_workers=1) as worker,
        excel_sync.ExcelReadCapability.read_snapshot_scope(),
    ):
        assert manager(code="0000").name == "Project 0000"


@pytest.mark.parametrize("workbook_rows", [16])
def test_force_refresh_discards_an_inflight_scope_reservation(
    workbook_case: tuple[GraphQLSchema, Path, type[GeneralManager]],
) -> None:
    from typing import cast

    from general_manager.interface._excel_read_scope import (
        peek_read_generation,
        read_generation,
    )

    _, path, manager = workbook_case
    interface = cast(type[ExcelInterface], manager.Interface)
    original = Path.read_bytes
    forced = False

    def force_during_first_check(file: Path) -> bytes:
        nonlocal forced
        if file == path and not forced:
            forced = True
            interface.sync_from_excel(force=True)
        return original(file)

    with (
        patch.object(
            Path, "read_bytes", autospec=True, side_effect=force_during_first_check
        ),
        excel_sync.ExcelReadCapability.read_snapshot_scope(),
    ):
        assert read_generation(interface) is None
        assert forced and peek_read_generation(interface) is None
        # The next read is fresh and can pin normally.
        assert read_generation(interface) is not None
