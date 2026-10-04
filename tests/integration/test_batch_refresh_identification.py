"""Refresh callbacks preserve interface identities independently of their backend."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType, SimpleNamespace
from typing import ClassVar

from django.db import transaction

from general_manager.cache.batch_refresh import (
    connect_batch_refresh_receiver,
    flush_batch_refresh_callbacks,
)
from general_manager.interface import RequestInterface
from general_manager.interface.requests import (
    RequestField,
    RequestMutationOperation,
    RequestQueryOperation,
)
from general_manager.manager.bulk_create import create_many_batch_context
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class _OfflineTransport:
    def execute(self, **_: object) -> dict[str, str]:
        return {"tenant": "acme", "code": "widget"}


class BatchRefreshIdentificationTests(GeneralManagerTransactionTestCase):
    Item: ClassVar[type[GeneralManager]]

    @classmethod
    def setUpClass(cls) -> None:
        class CompositeRefreshItem(GeneralManager):
            class Interface(RequestInterface):
                tenant = Input(type=str)
                code = Input(type=str)
                label = RequestField(str, source="code")
                identification_fields = ("tenant", "code")

                class Meta:
                    transport = _OfflineTransport()
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail", method="GET", path="/items/{tenant}/{code}"
                        )
                    }
                    create_operation = RequestMutationOperation(
                        name="create", method="POST", path="/items"
                    )

        cls.Item = CompositeRefreshItem
        cls.general_manager_classes = [CompositeRefreshItem]
        super().setUpClass()

    def test_request_create_delivers_full_composite_identity(self) -> None:
        calls: list[tuple[object, ...]] = []
        registration = connect_batch_refresh_receiver(lambda *args: calls.append(args))
        self.addCleanup(registration.disconnect)

        created = self.Item.create(tenant="acme", code="widget", ignore_permission=True)

        self.assertEqual(
            calls,
            [(self.Item, ({"tenant": "acme", "code": "widget"},), "create", "default")],
        )
        self.assertEqual(calls[0][1][0], created.identification)
        with self.assertRaises(TypeError):
            calls[0][1][0]["tenant"] = "changed"

    def test_deferred_callback_snapshots_mapping_and_mutable_values(self) -> None:
        calls: list[tuple[Mapping[str, object], ...]] = []
        registration = connect_batch_refresh_receiver(
            lambda _sender, ids, _action, _alias: calls.append(ids), on_commit=True
        )
        self.addCleanup(registration.disconnect)
        identity = {"tenant": "acme", "segments": ["widget"]}
        with transaction.atomic():
            registration.receiver(
                self.Item,
                identification=MappingProxyType(identity),
                action="update",
            )
            identity["tenant"] = "changed"
            identity["segments"].append("changed")
            self.assertEqual(calls, [])

        self.assertEqual(calls, [({"tenant": "acme", "segments": ["widget"]},)])

    def test_instance_identity_fallback_is_snapshotted_for_delete(self) -> None:
        calls: list[tuple[Mapping[str, object], ...]] = []
        registration = connect_batch_refresh_receiver(
            lambda _sender, ids, _action, _alias: calls.append(ids), on_commit=True
        )
        self.addCleanup(registration.disconnect)
        identity = {"tenant": "acme", "code": "widget"}
        with transaction.atomic():
            registration.receiver(
                self.Item,
                instance=SimpleNamespace(identification=identity),
                action="delete",
            )
            identity.clear()
        self.assertEqual(calls, [({"tenant": "acme", "code": "widget"},)])

    def test_missing_or_invalid_identity_is_rejected_explicitly(self) -> None:
        calls: list[tuple[object, ...]] = []
        registration = connect_batch_refresh_receiver(lambda *args: calls.append(args))
        self.addCleanup(registration.disconnect)
        for identification in (None, 7, "id"):
            with self.subTest(identification=identification):
                with self.assertRaisesRegex(TypeError, "identification mapping"):
                    registration.receiver(
                        self.Item, identification=identification, action="create"
                    )
        self.assertEqual(calls, [])

    def test_batch_preserves_order_and_snapshots_until_commit(self) -> None:
        calls: list[tuple[Mapping[str, object], ...]] = []
        registration = connect_batch_refresh_receiver(
            lambda _sender, ids, _action, _alias: calls.append(ids), on_commit=True
        )
        self.addCleanup(registration.disconnect)
        identity = {"tenant": "acme", "code": "first"}
        with transaction.atomic():
            with create_many_batch_context(
                "default", caller_owns_transaction=True, manager_class=self.Item
            ) as batch:
                batch.bulk_sql_active = True
                registration.receiver(
                    self.Item, identification=identity, action="create"
                )
                identity["code"] = "second"
                registration.receiver(
                    self.Item, identification=identity, action="create"
                )
                identity.clear()
                flush_batch_refresh_callbacks(batch)
                self.assertEqual(batch.batch_refresh_callbacks, {})
            self.assertEqual(calls, [])
        self.assertEqual(
            calls,
            [
                (
                    {"tenant": "acme", "code": "first"},
                    {"tenant": "acme", "code": "second"},
                )
            ],
        )

    def test_rollback_discards_deferred_composite_identity(self) -> None:
        calls: list[tuple[Mapping[str, object], ...]] = []
        registration = connect_batch_refresh_receiver(
            lambda _sender, ids, _action, _alias: calls.append(ids), on_commit=True
        )
        self.addCleanup(registration.disconnect)
        with transaction.atomic():
            self.Item.create(tenant="acme", code="widget", ignore_permission=True)
            self.assertEqual(calls, [])
            transaction.set_rollback(True)
        self.assertEqual(calls, [])
