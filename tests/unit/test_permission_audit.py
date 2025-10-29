from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, ClassVar
from unittest.mock import patch

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
from general_manager.permission.permission_data_manager import PermissionDataManager


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
    @classmethod
    def setUpClass(cls) -> None:
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="regular",
            email="regular@example.com",
            password=get_random_string(12),
        )
        cls.superuser = User.objects.create_superuser(
            username="super",
            email="super@example.com",
            password=get_random_string(12),
        )
        cls.anonymous = AnonymousUser()
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

    def test_audit_event_with_metadata(self) -> None:
        """Test audit event with custom metadata."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        event = PermissionAuditEvent(
            action="create",
            attributes=("field1", "field2"),
            granted=True,
            user=self.user,
            manager="TestManager",
            permissions=("perm1", "perm2"),
            metadata={"request_id": "123", "source": "api"},
        )
        logger.record(event)

        self.assertEqual(len(logger.events), 1)
        recorded = logger.events[0]
        self.assertEqual(recorded.metadata, {"request_id": "123", "source": "api"})
        self.assertEqual(recorded.attributes, ("field1", "field2"))
        self.assertEqual(recorded.permissions, ("perm1", "perm2"))

    def test_audit_event_with_anonymous_user(self) -> None:
        """Test audit event recording with anonymous user."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class DummyManager:
            __name__ = "DummyManager"

        AuditDummyPermission.check_create_permission(
            {"field": "value"}, DummyManager, self.anonymous
        )

        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertEqual(event.user, self.anonymous)
        self.assertIsNone(getattr(event.user, "id", None))

    def test_audit_logging_enabled_function(self) -> None:
        """Test audit_logging_enabled function returns correct state."""
        from general_manager.permission.audit import audit_logging_enabled

        configure_audit_logger(None)
        self.assertFalse(audit_logging_enabled())

        configure_audit_logger(RecordingAuditLogger())
        self.assertTrue(audit_logging_enabled())

        configure_audit_logger(None)
        self.assertFalse(audit_logging_enabled())

    def test_emit_permission_audit_event_when_disabled(self) -> None:
        """Test emit_permission_audit_event does nothing when logging is disabled."""
        from general_manager.permission.audit import emit_permission_audit_event

        configure_audit_logger(None)
        event = PermissionAuditEvent(
            action="read",
            attributes=("field",),
            granted=True,
            user=self.user,
            manager="TestManager",
        )
        # Should not raise any errors
        emit_permission_audit_event(event)

    def test_emit_permission_audit_event_when_enabled(self) -> None:
        """Test emit_permission_audit_event forwards to logger."""
        from general_manager.permission.audit import emit_permission_audit_event

        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        event = PermissionAuditEvent(
            action="delete",
            attributes=("field",),
            granted=False,
            user=self.user,
            manager="TestManager",
        )
        emit_permission_audit_event(event)

        self.assertEqual(len(logger.events), 1)
        self.assertEqual(logger.events[0].action, "delete")
        self.assertFalse(logger.events[0].granted)

    def test_audit_event_emitted_for_update(self) -> None:
        """Test audit event emission for update operations."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        permission_data_manager = PermissionDataManager({"field": "new_value"}, None)
        old_instance = type("DummyManager", (), {"field": "old_value"})()
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager.for_update",
            return_value=permission_data_manager,
        ):
            AuditDummyPermission.check_update_permission(
                {"field": "new_value"}, old_instance, self.user
            )

        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertEqual(event.action, "update")
        self.assertEqual(event.attributes, ("field",))
        self.assertTrue(event.granted)
        self.assertFalse(event.bypassed)

    def test_audit_event_emitted_for_delete(self) -> None:
        """Test audit event emission for delete operations."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        instance = type("DummyManager", (), {})()
        instance.field = "value"
        permission_data_manager = PermissionDataManager({"field": "value"}, None)
        with patch(
            "general_manager.permission.base_permission.PermissionDataManager",
            return_value=permission_data_manager,
        ):
            AuditDummyPermission.check_delete_permission(instance, self.user)

        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertEqual(event.action, "delete")
        self.assertTrue(event.granted)
        self.assertFalse(event.bypassed)

    def test_multiple_create_fields_logged(self) -> None:
        """Test multiple fields in create operation generate separate events."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class DummyManager:
            __name__ = "DummyManager"

        AuditDummyPermission.check_create_permission(
            {"field1": "value1", "field2": "value2", "field3": "value3"},
            DummyManager,
            self.user,
        )

        self.assertEqual(len(logger.events), 3)
        attributes = [event.attributes[0] for event in logger.events]
        self.assertIn("field1", attributes)
        self.assertIn("field2", attributes)
        self.assertIn("field3", attributes)

    def test_superuser_multiple_fields_bypassed(self) -> None:
        """Test superuser bypass is logged for each field."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class DummyManager:
            __name__ = "DummyManager"

        AuditDummyPermission.check_create_permission(
            {"field1": "value1", "field2": "value2"},
            DummyManager,
            self.superuser,
        )

        self.assertEqual(len(logger.events), 2)
        for event in logger.events:
            self.assertTrue(event.bypassed)
            self.assertTrue(event.granted)

    def test_file_audit_logger_multiple_batches(self) -> None:
        """Test FileAuditLogger handles multiple batches correctly."""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "audit.log"
            logger = FileAuditLogger(path, batch_size=2, flush_interval=0.1)

            for i in range(5):
                event = PermissionAuditEvent(
                    action="read",
                    attributes=(f"field{i}",),
                    granted=True,
                    user=self.user,
                    manager="TestManager",
                )
                logger.record(event)

            logger.flush()
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 5)

    def test_file_audit_logger_creates_directory(self) -> None:
        """Test FileAuditLogger creates parent directories."""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "nested" / "dir" / "audit.log"
            logger = FileAuditLogger(path, batch_size=1, flush_interval=0.1)

            event = PermissionAuditEvent(
                action="create",
                attributes=("field",),
                granted=True,
                user=self.user,
                manager="TestManager",
            )
            logger.record(event)
            logger.flush()

            self.assertTrue(path.exists())
            self.assertTrue(path.parent.exists())

    def test_database_audit_logger_multiple_events(self) -> None:
        """Test DatabaseAuditLogger handles multiple events in batch."""
        logger = DatabaseAuditLogger(batch_size=5, flush_interval=0.1)

        for i in range(3):
            event = PermissionAuditEvent(
                action="create",
                attributes=(f"field{i}",),
                granted=True,
                user=self.user,
                manager=f"Manager{i}",
            )
            logger.record(event)

        logger.flush()
        model = logger.model
        count = model.objects.using("default").filter(user_id=self.user.id).count()
        self.assertGreaterEqual(count, 3)

    def test_database_audit_logger_with_none_manager(self) -> None:
        """Test DatabaseAuditLogger handles None manager correctly."""
        event = PermissionAuditEvent(
            action="mutation",
            attributes=("field",),
            granted=True,
            user=self.user,
            manager=None,
        )
        logger = DatabaseAuditLogger(batch_size=1, flush_interval=0.1)
        logger.record(event)
        logger.flush()

        model = logger.model
        rows = list(
            model.objects.using("default")
            .filter(manager__isnull=True)
            .values_list("manager")
        )
        self.assertTrue(any(row[0] is None for row in rows))

    def test_configure_audit_logger_from_settings_with_class_and_options(self) -> None:
        """Test configure from settings with class path and options."""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "test.log"

            class DummySettings:
                GENERAL_MANAGER: ClassVar[dict[str, Any]] = {
                    "AUDIT_LOGGER": {
                        "class": "general_manager.permission.audit.FileAuditLogger",
                        "options": {
                            "path": str(path),
                            "batch_size": 10,
                        },
                    }
                }

            configure_audit_logger_from_settings(DummySettings)
            logger = get_audit_logger()
            self.assertIsInstance(logger, FileAuditLogger)

    def test_configure_audit_logger_from_settings_no_config(self) -> None:
        """Test configure from settings with no audit logger config."""
        from general_manager.permission.audit import _NoOpAuditLogger

        class DummySettings:
            pass

        configure_audit_logger_from_settings(DummySettings)
        logger = get_audit_logger()
        self.assertIsInstance(logger, _NoOpAuditLogger)

    def test_configure_audit_logger_with_none_resets(self) -> None:
        """Test configuring with None resets to no-op logger."""
        from general_manager.permission.audit import _NoOpAuditLogger

        configure_audit_logger(RecordingAuditLogger())
        self.assertNotIsInstance(get_audit_logger(), _NoOpAuditLogger)

        configure_audit_logger(None)
        self.assertIsInstance(get_audit_logger(), _NoOpAuditLogger)

    def test_audit_event_dataclass_defaults(self) -> None:
        """Test PermissionAuditEvent dataclass default values."""
        event = PermissionAuditEvent(
            action="read",
            attributes=("field",),
            granted=True,
            user=self.user,
            manager="TestManager",
        )

        self.assertEqual(event.permissions, ())
        self.assertFalse(event.bypassed)
        self.assertIsNone(event.metadata)

    def test_file_audit_logger_closed_logger_ignores_events(self) -> None:
        """Test that a closed FileAuditLogger ignores new events."""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "audit.log"
            logger = FileAuditLogger(path, batch_size=1, flush_interval=0.1)

            event = PermissionAuditEvent(
                action="create",
                attributes=("field1",),
                granted=True,
                user=self.user,
                manager="TestManager",
            )
            logger.record(event)
            logger.close()

            # Record after close should be ignored
            event2 = PermissionAuditEvent(
                action="create",
                attributes=("field2",),
                granted=True,
                user=self.user,
                manager="TestManager",
            )
            logger.record(event2)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)

    def test_database_audit_logger_empty_batch(self) -> None:
        """Test DatabaseAuditLogger handles empty batch gracefully."""
        logger = DatabaseAuditLogger(batch_size=1, flush_interval=0.1)
        # Flush without recording any events
        logger.flush()
        # Should not raise any errors

    def test_file_audit_logger_empty_batch(self) -> None:
        """Test FileAuditLogger handles empty batch gracefully."""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "audit.log"
            logger = FileAuditLogger(path, batch_size=1, flush_interval=0.1)
            # Flush without recording any events
            logger.flush()
            # File should not exist or be empty
            if path.exists():
                self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_mutation_permission_denied_audit(self) -> None:
        """Test mutation permission audit when permission is denied."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class RestrictedMutationPermission(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAdmin"]

        from general_manager.permission.base_permission import PermissionCheckError

        with self.assertRaises(PermissionCheckError):
            RestrictedMutationPermission.check({"field": "value"}, self.user)

        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertEqual(event.action, "mutation")
        self.assertFalse(event.granted)

    def test_mutation_permission_superuser_bypass(self) -> None:
        """Test mutation permission audit for superuser bypass."""
        logger = RecordingAuditLogger()
        configure_audit_logger(logger)

        class RestrictedMutationPermission(MutationPermission):
            __mutate__: ClassVar[list[str]] = ["isAdmin"]

        RestrictedMutationPermission.check({"field": "value"}, self.superuser)

        self.assertEqual(len(logger.events), 1)
        event = logger.events[0]
        self.assertTrue(event.granted)
        self.assertTrue(event.bypassed)

    def test_audit_event_serialization_with_anonymous_user(self) -> None:
        """Test event serialization correctly handles anonymous users."""
        from general_manager.permission.audit import _serialize_event

        event = PermissionAuditEvent(
            action="read",
            attributes=("field",),
            granted=True,
            user=self.anonymous,
            manager="TestManager",
        )

        serialized = _serialize_event(event)
        self.assertIsNone(serialized["user_id"])
        self.assertIsNotNone(serialized["user"])
        self.assertIn("AnonymousUser", serialized["user"])

    def test_audit_event_serialization_with_authenticated_user(self) -> None:
        """Test event serialization correctly handles authenticated users."""
        from general_manager.permission.audit import _serialize_event

        event = PermissionAuditEvent(
            action="create",
            attributes=("field",),
            granted=True,
            user=self.user,
            manager="TestManager",
        )

        serialized = _serialize_event(event)
        self.assertEqual(serialized["user_id"], str(self.user.id))
        self.assertIsNone(serialized["user"])

    def test_audit_event_serialization_includes_timestamp(self) -> None:
        """Test event serialization includes timestamp."""
        from general_manager.permission.audit import _serialize_event

        event = PermissionAuditEvent(
            action="update",
            attributes=("field",),
            granted=True,
            user=self.user,
            manager="TestManager",
        )

        serialized = _serialize_event(event)
        self.assertIn("timestamp", serialized)
        self.assertIsNotNone(serialized["timestamp"])

    def test_configure_audit_logger_from_settings_callable(self) -> None:
        """Test configure from settings with callable that returns logger."""

        class DummySettings:
            @staticmethod
            def AUDIT_LOGGER() -> DummyAuditLogger:
                return DummyAuditLogger()

        configure_audit_logger_from_settings(DummySettings)
        logger = get_audit_logger()
        self.assertIsInstance(logger, DummyAuditLogger)

    def test_configure_audit_logger_from_settings_class(self) -> None:
        """Test configure from settings with class (not instance)."""

        class DummySettings:
            AUDIT_LOGGER = DummyAuditLogger

        configure_audit_logger_from_settings(DummySettings)
        logger = get_audit_logger()
        self.assertIsInstance(logger, DummyAuditLogger)

    def test_database_audit_logger_custom_table_name(self) -> None:
        """Test DatabaseAuditLogger with custom table name."""
        custom_table = "custom_audit_table_test"
        logger = DatabaseAuditLogger(
            table_name=custom_table,
            batch_size=1,
            flush_interval=0.1,
        )

        self.assertEqual(logger.table_name, custom_table)
        self.assertEqual(logger.model._meta.db_table, custom_table)

    def test_file_logger_no_worker_mode(self) -> None:
        """Test FileAuditLogger in synchronous mode (no worker)."""
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "audit.log"
            logger = FileAuditLogger.__new__(FileAuditLogger)
            logger._path = path
            logger._batch_size = 1
            logger._flush_interval = 0.1
            logger._use_worker = False

            class _ClosedEvent:
                def is_set(self) -> bool:
                    return False

            logger._closed = _ClosedEvent()
            logger._path.parent.mkdir(parents=True, exist_ok=True)

            event = PermissionAuditEvent(
                action="read",
                attributes=("field",),
                granted=True,
                user=self.user,
                manager="TestManager",
            )
            # In no-worker mode, _handle_batch is called directly
            logger._handle_batch([event])

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
