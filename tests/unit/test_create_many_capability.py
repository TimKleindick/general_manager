"""Backend-independent batch dispatch through the existing capability registry."""

from collections.abc import Iterator
from itertools import islice
from typing import ClassVar

from django.test import SimpleTestCase

from general_manager.interface import RequestInterface
from general_manager.interface.capabilities.base import CapabilityName
from general_manager.interface.capabilities.builtin import BaseCapability
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.interface.requests import RequestField, RequestQueryOperation
from general_manager.manager.bulk_create import (
    CreateManyBatchResult,
    CreateManyUnsupportedError,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input


class MemoryBatchCapability(BaseCapability):
    name: ClassVar[CapabilityName] = "create_many"

    def create_many(
        self,
        interface_cls,
        records,
        *,
        manager_class,
        creator_id=None,
        history_comment=None,
        ignore_permission=False,
        batch_size=1000,
    ):
        assert manager_class.Interface is interface_cls
        self.options = (creator_id, history_comment, ignore_permission, batch_size)

        def iterate() -> Iterator[CreateManyBatchResult]:
            source = iter(records)
            count = 0
            while batch := tuple(islice(source, batch_size)):
                start = count
                count += len(batch)
                yield CreateManyBatchResult(
                    start, count, batch, count, count, 0, None, True
                )

        return iterate()


def make_manager(with_batch=True, batch_handler=MemoryBatchCapability):
    class RemoteBatch(GeneralManager):
        class Interface(RequestInterface):
            key = Input(type=str)
            value = RequestField(str)
            identification_fields = ("key",)
            configured_capabilities = (
                *RequestInterface.configured_capabilities,
                *((InterfaceCapabilityConfig(batch_handler),) if with_batch else ()),
            )

            class Meta:
                query_operations: ClassVar[dict] = {
                    "detail": RequestQueryOperation(
                        name="detail", method="GET", path="/items/{key}"
                    )
                }

    return RemoteBatch


class CreateManyCapabilityTests(SimpleTestCase):
    def test_non_orm_handler_is_lazy_and_receives_options_without_database_access(self):
        manager = make_manager()
        consumed = []

        def source():
            for key in ("a", "b", "c"):
                consumed.append(key)
                yield {"key": key}

        result = manager.create_many(
            source(),
            creator_id=7,
            history_comment="import",
            ignore_permission=True,
            batch_size=2,
        )
        self.assertEqual(consumed, [])
        first = next(result)
        self.assertEqual(consumed, ["a", "b"])
        self.assertEqual(first.ids, ({"key": "a"}, {"key": "b"}))
        self.assertIsNone(first.database_alias)
        self.assertTrue(first.committed)
        self.assertEqual(
            manager.Interface.get_capability_handler("create_many").options,
            (7, "import", True, 2),
        )
        self.assertEqual(next(result).ids, ({"key": "c"},))
        with self.assertRaises(StopIteration):
            next(result)

    def test_interface_entry_point_uses_its_parent_manager(self):
        manager = make_manager()
        self.assertEqual(
            next(manager.Interface.create_many([{"key": "a"}])).ids, ({"key": "a"},)
        )

    def test_absent_handler_rejects_without_consuming_input(self):
        manager = make_manager(with_batch=False)
        consumed = []

        def source():
            consumed.append(True)
            yield {"key": "a"}

        with self.assertRaisesRegex(
            CreateManyUnsupportedError, "create_many capability"
        ):
            manager.create_many(source())
        self.assertEqual(consumed, [])


def test_overridden_interface_batch_entry_rejects_historical_mutation():
    from datetime import datetime, timezone
    import pytest
    from general_manager.as_of import HistoricalMutationError, as_of
    from general_manager.interface.base_interface import InterfaceBase

    class CustomInterface(InterfaceBase):
        @classmethod
        def create_many(cls, records, **kwargs):
            return iter(())

    with as_of(datetime(2024, 1, 1, tzinfo=timezone.utc)):
        with pytest.raises(HistoricalMutationError):
            CustomInterface.create_many([])


def test_writable_orm_handler_override_is_used_by_manager_dispatch():
    from general_manager.interface import DatabaseInterface

    class ReplaceableBatch(GeneralManager):
        __module__ = "general_manager"

        class Interface(DatabaseInterface):
            capability_overrides: ClassVar[dict] = {
                "create_many": MemoryBatchCapability
            }

    assert isinstance(
        ReplaceableBatch.Interface.get_capability_handler("create_many"),
        MemoryBatchCapability,
    )
    result = next(ReplaceableBatch.create_many([{"key": "replacement"}]))
    assert result.ids == ({"key": "replacement"},)
    assert result.database_alias is None


def test_request_interface_can_replace_its_configured_batch_handler():
    class Replacement(MemoryBatchCapability):
        pass

    manager = make_manager(batch_handler=Replacement)
    assert type(manager.Interface.get_capability_handler("create_many")) is Replacement
    assert next(manager.create_many([{"key": "replacement"}])).ids == (
        {"key": "replacement"},
    )
