from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase
from django.utils.crypto import get_random_string

from general_manager.permission.audit import (
    PermissionAuditEvent,
    DatabaseAuditLogger,
    FileAuditLogger,
    configure_audit_logger,
    configure_audit_logger_from_settings,
    get_audit_logger,
)
from general_manager.permission.base_permission import BasePermission
from general_manager.permission.mutation_permission import MutationPermission


class RecordingAuditLogger:
    def __init__(self) -> None:
        self.events: list[PermissionAuditEvent] = []

    def record(self, event: PermissionAuditEvent) -> None:
        self.events.append(event)


class DummyAuditLogger(RecordingAuditLogger):
    pass


class AuditDummyPermission(BasePermission):
    def check_permission(
        self,
        action: str,
        attribute: str,
    ) -> bool:
        return True

    def get_permission_filter(self) -> list[dict[str, dict[str, str]]]:
        return []

    def describe_permissions(
        self,
        action: str,
        attribute: str,
    ) -> tuple[str, ...]:
        return (f"{action}:{attribute}",)


class AuditMutationPermission(MutationPermission):
    __mutate__: ClassVar[list[str]] = ["public"]
    field: ClassVar[list[str]] = ["public"]


class PermissionAuditTests(TransactionTestCase):
    def setUp(self) -> None:
        User = get_user_model()
        self.user = User.objects.create_user(
            username="regular",
            email="regular@example.com",
            password=get_random_string(12),
        )
        self.superuser = User.objects.create_superuser(
            username="super",
            email="super@example.com",
            password=get_random_string(12),
        )
        self.anonymous = AnonymousUser()
        configure_audit_logger(None)

    def tearDown(self) -> None:
        configure_audit_logger(None)

    def test_audit_event_emitted_for_create(self) -> None:
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class DummyManager:
            __name__ = "DummyManager"

        AuditDummyPermission.check_create_permission(
            {"field": "value"}, DummyManager, self.user
        )
        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertEqual(event.action, "create")
        self.assertEqual(event.attributes, ("field",))
        self.assertTrue(event.granted)
        self.assertFalse(event.bypassed)
        self.assertEqual(event.manager, "DummyManager")
        self.assertEqual(event.permissions, ("create:field",))

    def test_superuser_bypass_logged(self) -> None:
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class DummyManager:
            __name__ = "DummyManager"

        AuditDummyPermission.check_create_permission(
            {"field": "value"}, DummyManager, self.superuser
        )
        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertTrue(event.bypassed)
        self.assertTrue(event.granted)
        self.assertEqual(event.attributes, ("field",))

    def test_mutation_permission_audit(self) -> None:
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        AuditMutationPermission.check({"field": "value"}, self.user)
        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertEqual(event.action, "mutation")
        self.assertEqual(event.attributes, ("field",))
        self.assertTrue(event.granted)
        self.assertTupleEqual(event.permissions, ("public", "public"))

    def test_configure_audit_logger_from_settings_mapping(self) -> None:
        class DummySettings:
            GENERAL_MANAGER: ClassVar[dict[str, str]] = {
                "AUDIT_LOGGER": "tests.unit.test_permission_audit.DummyAuditLogger"
            }

        configure_audit_logger_from_settings(DummySettings)
        logger = get_audit_logger()
        self.assertIsInstance(logger, DummyAuditLogger)

    def test_configure_audit_logger_from_settings_direct(self) -> None:
        class DummySettings:
            AUDIT_LOGGER = DummyAuditLogger()

        configure_audit_logger_from_settings(DummySettings)
        logger = get_audit_logger()
        self.assertIs(logger, DummySettings.AUDIT_LOGGER)

    def test_file_audit_logger_persists_events(self) -> None:
        event = PermissionAuditEvent(
            action="create",
            attributes=("field",),
            granted=True,
            user=self.user,
            manager="Dummy",
        )
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "audit.log"
            logger = FileAuditLogger(path, batch_size=1, flush_interval=0.1)
            logger.record(event)
            logger.flush()
            payloads = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(payloads), 1)
            self.assertEqual(payloads[0]["action"], "create")
            self.assertEqual(payloads[0]["attributes"], ["field"])
            self.assertTrue(payloads[0]["granted"])

    def test_database_audit_logger_persists_events(self) -> None:
        event = PermissionAuditEvent(
            action="update",
            attributes=("field",),
            granted=False,
            user=self.user,
            manager="Dummy",
            permissions=("rule",),
        )
        logger = DatabaseAuditLogger(batch_size=1, flush_interval=0.1)
        logger.record(event)
        logger.flush()
        model = logger.model
        rows = list(
            model.objects.using("default").values_list(
                "action", "granted", "permissions"
            )
        )
        self.assertTrue(rows)
        action, granted, permissions = rows[-1]
        self.assertEqual(action, "update")
        self.assertFalse(granted)
        self.assertEqual(permissions, ["rule"])

    def test_file_logger_configuration(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "audit.log"
            logger = FileAuditLogger(path, batch_size=5, flush_interval=2.0)
            self.assertIsInstance(logger, FileAuditLogger)

    def test_database_logger_configuration(self) -> None:
        logger = DatabaseAuditLogger(
            using="default", table_name="gm_permission_audit_custom", batch_size=42
        )
        self.assertEqual(logger._using, "default")
        self.assertEqual(logger.table_name, "gm_permission_audit_custom")
