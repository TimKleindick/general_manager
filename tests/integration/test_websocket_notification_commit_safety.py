"""Integration coverage for commit-safe websocket data-change notifications."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

from django.db import transaction

from general_manager.api import bulk_data_change_notifications
from general_manager.api.graphql import GraphQL
from general_manager.api.remote_invalidation import emit_remote_invalidation
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from tests.utils.simple_manager_interface import BaseTestInterface


class ExpectedRollback(RuntimeError):
    """Expected exception used to exercise transaction rollbacks."""


def _force_rollback() -> None:
    """Raise the test-only exception that marks an intentional rollback."""
    raise ExpectedRollback


class WebsocketNotificationCommitSafetyTests(GeneralManagerTransactionTestCase):
    """Verify both websocket publishers share Django commit semantics."""

    Project: ClassVar[type[GeneralManager]]

    @classmethod
    def setUpClass(cls) -> None:
        """Register a lightweight manager with both websocket publishers enabled."""

        class Project(GeneralManager):
            identification: ClassVar[dict[str, object]] = {"id": 1}
            Interface = BaseTestInterface

            class RemoteAPI:
                enabled = True
                base_path = "/remote"
                resource_name = "projects"
                allow_update = True
                websocket_invalidation = True

        cls.Project = Project
        cls.general_manager_classes = [Project]
        super().setUpClass()

    def setUp(self) -> None:
        """Prepare a shared record of messages sent to external transports."""
        super().setUp()
        self.sent: list[tuple[str, str, dict[str, object]]] = []

    @contextmanager
    def _patched_publishers(self) -> Iterator[None]:
        """Record real publisher delivery while preserving Django commit hooks."""

        async def graphql_group_send(group: str, message: dict[str, object]) -> None:
            self.sent.append(("graphql", group, message))

        async def remote_group_send(group: str, message: dict[str, object]) -> None:
            self.sent.append(("remote", group, message))

        graphql_layer = SimpleNamespace(group_send=graphql_group_send)
        remote_layer = SimpleNamespace(group_send=remote_group_send)
        with (
            patch.object(
                GraphQL,
                "manager_registry",
                {self.Project.__name__: self.Project},
            ),
            patch.object(GraphQL, "_get_channel_layer", return_value=graphql_layer),
            patch(
                "general_manager.api.remote_invalidation._get_channel_layer_safe",
                return_value=remote_layer,
            ),
        ):
            yield

    def _emit_both(self, *, action: str = "create") -> None:
        """Schedule matching GraphQL and RemoteAPI messages on the real transaction."""
        project = self.Project()
        GraphQL._handle_data_change(
            sender=self.Project,
            instance=project,
            action=action,
            database_alias="default",
        )
        emit_remote_invalidation(
            self.Project,
            instance=project,
            action=action,
            database_alias="default",
        )

    def test_both_publish_only_after_outer_commit(self) -> None:
        """Both publishers defer transport delivery until the outer commit."""
        with self._patched_publishers():
            with transaction.atomic():
                self._emit_both()
                self.assertEqual(self.sent, [])
            self.assertEqual(
                [(kind, message["action"]) for kind, _, message in self.sent],
                [("graphql", "create"), ("graphql", "create"), ("remote", "create")],
            )

    def test_outer_rollback_discards_both_notifications(self) -> None:
        """Rolling back the outer transaction discards both pending publishers."""
        with self._patched_publishers():
            with self.assertRaises(ExpectedRollback):
                with transaction.atomic():
                    self._emit_both()
                    raise ExpectedRollback
            self.assertEqual(self.sent, [])

    def test_savepoint_rollback_discards_both_notifications(self) -> None:
        """Rolling back an inner savepoint discards both pending publishers."""
        with self._patched_publishers(), transaction.atomic():
            try:
                with transaction.atomic():
                    self._emit_both()
                    _force_rollback()
            except ExpectedRollback:
                pass
            self.assertEqual(self.sent, [])
        self.assertEqual(self.sent, [])

    def test_bulk_flushes_one_refresh_per_system_after_commit(self) -> None:
        """Bulk delivery waits for commit and reduces each system to one refresh."""
        with self._patched_publishers(), bulk_data_change_notifications():
            with transaction.atomic():
                self._emit_both(action="update")
                self._emit_both(action="update")
                self.assertEqual(self.sent, [])
            self.assertEqual(self.sent, [])
        self.assertEqual(
            [(kind, message["action"]) for kind, _, message in self.sent],
            [("graphql", "refresh"), ("remote", "refresh")],
        )
