"""Regression coverage for ORM batch extension and error contracts."""

from unittest.mock import patch
from typing import ClassVar

from django.db import models

from general_manager.interface import DatabaseInterface
from general_manager.interface.bundles.database import ORM_WRITABLE_CAPABILITIES
from general_manager.interface.capabilities.configuration import (
    InterfaceCapabilityConfig,
)
from general_manager.interface.capabilities.core.observability import (
    LoggingObservabilityCapability,
)
from general_manager.interface.capabilities.orm import bulk
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.bulk_create import CreateManyError
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class RecordingObserver(LoggingObservabilityCapability):
    events: ClassVar[list[tuple[str, str]]] = []

    def before_operation(self, *, operation, **kwargs):
        self.events.append(("before", operation))

    def after_operation(self, *, operation, **kwargs):
        self.events.append(("after", operation))

    def on_error(self, *, operation, **kwargs):
        self.events.append(("error", operation))


class CreateManyExtensionContracts(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class ReviewItem(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100, unique=True)
                capability_overrides: ClassVar[dict] = {
                    "observability": RecordingObserver
                }
                configured_capabilities = (
                    ORM_WRITABLE_CAPABILITIES,
                    InterfaceCapabilityConfig(RecordingObserver),
                )

            class BulkCreate:
                enabled = True
                local_permissions = True
                local_search = True

        cls.Item = ReviewItem
        cls.general_manager_classes = [ReviewItem]
        super().setUpClass()

    def test_sql_preserves_custom_observability_create_events(self):
        RecordingObserver.events.clear()
        self.Item.create(name="canonical", ignore_permission=True)
        canonical = list(RecordingObserver.events)
        self.assertIn(("before", "create"), canonical)
        self.assertIn(("after", "create"), canonical)
        eligibility = bulk.bulk_create_eligibility(self.Item)
        self.assertFalse(eligibility.eligible)
        self.assertIn("custom observability capability", eligibility.reasons)
        RecordingObserver.events.clear()
        list(self.Item.create_many([{"name": "sql"}], ignore_permission=True))
        actual = list(RecordingObserver.events)
        self.assertIn(("before", "create"), actual)
        self.assertIn(("after", "create"), actual)

    def test_unattributed_insert_failure_does_not_blame_last_record(self):
        original = bulk._validate_unique

        def insert_conflict_after_validation(model, instances, alias):
            original(model, instances, alias)
            model.objects.using(alias).create(name=instances[0].name)

        with (
            patch.dict(
                self.Item.Interface._capability_handlers,
                {"observability": LoggingObservabilityCapability()},
            ),
            patch.object(bulk, "_validate_unique", insert_conflict_after_validation),
        ):
            with self.assertRaises(CreateManyError) as raised:
                list(
                    self.Item.create_many(
                        [{"name": "first-conflicts"}, {"name": "second-valid"}],
                        ignore_permission=True,
                    )
                )
        error = raised.exception
        self.assertEqual(self.Item.Interface._model.objects.count(), 0)
        self.assertIsNone(error.failure_index)

    def test_custom_observer_receives_canonical_validation_error(self):
        RecordingObserver.events.clear()
        with self.assertRaises(CreateManyError):
            list(self.Item.create_many([{"name": "x" * 101}], ignore_permission=True))
        self.assertIn(("error", "create"), RecordingObserver.events)
        self.assertEqual(self.Item.Interface._model.objects.count(), 0)
