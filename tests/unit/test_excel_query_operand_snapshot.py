"""Query snapshots belong only to the active getter and its exact interface."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import cast

from general_manager.interface._excel_query_snapshot import (
    read_excel_operand,
    snapshot_for_excel_query_operand,
)
from general_manager.interface.excel import ExcelRowSnapshot
from general_manager.interface.interfaces.excel import ExcelInterface
from general_manager.manager.general_manager import GeneralManager


def test_only_exact_object_and_thread_can_read_an_active_operand_snapshot():
    before = object.__new__(ExcelInterface)
    other = object.__new__(ExcelInterface)
    snapshot = ExcelRowSnapshot("a", {"name": "sample"}, "fingerprint")

    class SimpleManager:
        _interface = before

        @property
        def name(self) -> object:
            nonlocal escaped
            assert snapshot_for_excel_query_operand(other) is None
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert (
                    pool.submit(
                        copy_context().run,
                        snapshot_for_excel_query_operand,
                        before,
                    ).result(timeout=5)
                    is None
                )
            escaped = copy_context()
            return snapshot_for_excel_query_operand(before)

    escaped = copy_context()
    manager = cast(GeneralManager, SimpleManager())
    assert read_excel_operand(manager, "name", snapshot) is snapshot
    assert snapshot_for_excel_query_operand(before) is None
    assert escaped.run(snapshot_for_excel_query_operand, before) is None


def test_only_plain_graphql_scalars_and_flat_membership_values_are_snapshot_operands():
    from datetime import date

    from general_manager.api._excel_list_operands import _passive_expected

    class ExtraString(str):
        pass

    for value in (None, "0003", 7, False, 1.5, ["0003", 7], (1, 2), {1, 2}):
        assert _passive_expected(value)
    for value in (date(2026, 11, 23), ExtraString("0003"), [[7]], {1: "0003"}):
        assert not _passive_expected(value)
