"""Unchanged ORM updates must not create duplicate audit snapshots (#10677)."""

from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.test import override_settings

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class MemoryviewBinaryField(models.BinaryField):
    """Exercise database drivers that return binary columns as memoryviews."""

    def from_db_value(self, value, _expression, _connection):
        return memoryview(value)


class NoopUpdateTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class NoopTarget(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50, unique=True)

        class NoopRecord(GeneralManager):
            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50, unique=True)
                quantity = models.IntegerField(default=1)
                price = models.DecimalField(max_digits=10, decimal_places=2)
                start_date = models.DateField()
                details = models.JSONField(default=dict, blank=True)
                mass = MeasurementField(base_unit="kg", null=True, blank=True)
                attachment = models.FileField(blank=True)
                binary = MemoryviewBinaryField(default=b"original")
                changed_by = models.ForeignKey(
                    "auth.User", on_delete=models.PROTECT, null=True, blank=True
                )
                modified_at = models.DateTimeField(auto_now=True)
                target = models.ForeignKey(
                    "general_manager.NoopTarget",
                    on_delete=models.PROTECT,
                    null=True,
                    blank=True,
                )
                targets = models.ManyToManyField(
                    "general_manager.NoopTarget", related_name="records", blank=True
                )
                coded_targets = models.ManyToManyField(
                    "general_manager.NoopTarget",
                    through="general_manager.NoopCodedMembership",
                    related_name="coded_records",
                    blank=True,
                )

        class NoopCodedMembership(GeneralManager):
            class Interface(DatabaseInterface):
                source = models.ForeignKey(
                    "general_manager.NoopRecord",
                    to_field="name",
                    on_delete=models.CASCADE,
                )
                target = models.ForeignKey(
                    "general_manager.NoopTarget",
                    to_field="name",
                    on_delete=models.CASCADE,
                )

        cls.Target = NoopTarget
        cls.Record = NoopRecord
        cls.general_manager_classes = [NoopTarget, NoopRecord, NoopCodedMembership]

    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create(username="noop-actor")
        self.target = self.Target.create(name="first", ignore_permission=True)
        self.other = self.Target.create(name="second", ignore_permission=True)
        self.record = self.Record.create(
            name="Stable",
            price=Decimal("12.50"),
            start_date=date(2026, 9, 8),
            details={"a": 1, "b": [2]},
            mass="1 kg",
            target=self.target,
            targets=[self.target, self.other],
            creator_id=self.user.pk,
            ignore_permission=True,
        )
        self.model = self.Record.Interface._model

    def test_identical_and_empty_updates_preserve_row_and_history(self):
        original = self.model.objects.values().get(pk=self.record.id)
        history_ids = list(self.record.history.values_list("history_id", flat=True))
        another_user = get_user_model().objects.create(username="noop-other-actor")
        for payload in (
            {"name": "Stable"},
            {},
            {"name": models.NOT_PROVIDED},
            {"creator_id": another_user.pk},
            {"quantity": "1", "price": "12.50", "start_date": "2026-09-08"},
            {"details": {"b": [2], "a": 1}},
            {"mass": "1 kg"},
            {"target": self.target},
            {"target_id": str(self.target.id)},
            {"targets_id_list": [str(self.other.id), self.target.id, self.target.id]},
        ):
            with self.subTest(payload=payload):
                result = self.record.update(ignore_permission=True, **payload)
                self.assertIs(result, self.record)
                self.assertEqual(
                    self.model.objects.values().get(pk=self.record.id), original
                )
                self.assertEqual(
                    list(self.record.history.values_list("history_id", flat=True)),
                    history_ids,
                )

    def test_real_field_and_relationship_changes_are_saved(self):
        for payload in (
            {"name": "Changed"},
            {"target": self.other},
            {"target": None},
            {"targets": [self.other]},
            {"targets": []},
        ):
            with self.subTest(payload=payload):
                count = self.record.history.count()
                self.record.update(
                    creator_id=self.user.pk, ignore_permission=True, **payload
                )
                self.assertGreater(self.record.history.count(), count)
                self.assertEqual(
                    self.record.history.first().history_user_id, self.user.pk
                )
                count = self.record.history.count()
                self.record.update(
                    creator_id=self.user.pk, ignore_permission=True, **payload
                )
                self.assertEqual(self.record.history.count(), count)
        self.assertEqual(self.record.name, "Changed")
        self.assertIsNone(self.record.target)
        self.assertEqual(
            list(self.model.objects.get(pk=self.record.id).targets.all()), []
        )

    def test_explicit_comment_records_an_intentional_audit_entry(self):
        original = self.record.history.first()
        count = self.record.history.count()
        self.record.update(
            name="Stable",
            history_comment="Manually verified",
            creator_id=self.user.pk,
            ignore_permission=True,
        )
        self.assertEqual(self.record.history.count(), count + 1)
        self.assertEqual(
            self.record.history.first().history_change_reason, "Manually verified"
        )
        original.refresh_from_db()
        self.assertIsNone(original.history_change_reason)

    def test_json_type_changes_are_not_mistaken_for_equal_python_values(self):
        for details in ({"nested": [1]}, {"nested": [True]}, {"nested": [1.0]}):
            with self.subTest(details=details):
                count = self.record.history.count()
                self.record.update(
                    details=details, creator_id=self.user.pk, ignore_permission=True
                )
                self.assertEqual(self.record.history.count(), count + 1)
                actual = self.model.objects.get(pk=self.record.id).details["nested"][0]
                self.assertIs(type(actual), type(details["nested"][0]))

    def test_primary_key_change_preserves_requested_relationships(self):
        new_id = self.record.id + 1000
        self.record._interface.update(
            id=new_id, name="Copied", targets_id_list=[self.target.id, self.other.id]
        )
        self.assertSetEqual(
            set(self.model.objects.get(pk=new_id).targets.values_list("pk", flat=True)),
            {self.target.id, self.other.id},
        )

    def test_many_to_many_uses_custom_source_and_target_fields(self):
        self.record.update(coded_targets_id_list=["first"], ignore_permission=True)
        count = self.record.history.count()
        self.record.update(
            coded_targets_id_list=["first", "first"], ignore_permission=True
        )
        self.assertEqual(self.record.history.count(), count)
        self.record.update(coded_targets_id_list=["second"], ignore_permission=True)
        self.assertGreater(self.record.history.count(), count)
        new_id = self.record.id + 1000
        self.record._interface.update(
            id=new_id, name="Copied", coded_targets_id_list=["second"]
        )
        self.assertEqual(
            list(
                self.model.objects.get(pk=new_id).coded_targets.values_list(
                    "name", flat=True
                )
            ),
            ["second"],
        )

    def test_replacement_file_with_same_name_still_saves(self):
        with TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            self.record.update(
                attachment=SimpleUploadedFile("record.txt", b"before"),
                ignore_permission=True,
            )
            count = self.record.history.count()
            self.record.update(
                attachment=SimpleUploadedFile("record.txt", b"after"),
                ignore_permission=True,
            )
            self.assertEqual(self.record.history.count(), count + 1)
            with self.model.objects.get(pk=self.record.id).attachment.open(
                "rb"
            ) as saved:
                self.assertEqual(saved.read(), b"after")

    def test_binary_and_measurement_changes_are_saved(self):
        for payload in (
            {"binary": memoryview(b"changed")},
            {"mass": "2 kg"},
            {"mass": "2000 g"},
        ):
            with self.subTest(payload=payload):
                count = self.record.history.count()
                self.record.update(
                    creator_id=self.user.pk, ignore_permission=True, **payload
                )
                self.assertEqual(self.record.history.count(), count + 1)
                self.record.update(
                    creator_id=self.user.pk, ignore_permission=True, **payload
                )
                self.assertEqual(self.record.history.count(), count + 1)

    def test_nested_mutation_does_not_replace_outer_comparison(self):
        outer_count = self.record.history.count()
        target_count = self.target.history.count()

        def clean(_instance):
            self.target.update(name="Nested change", ignore_permission=True)

        with patch.object(self.model, "clean", autospec=True, side_effect=clean):
            self.record.update(name="Stable", ignore_permission=True)
        self.assertEqual(self.record.history.count(), outer_count)
        self.assertEqual(self.target.history.count(), target_count + 1)

    def test_noop_still_validates_and_saves_changes_made_by_clean(self):
        count = self.record.history.count()
        with patch.object(self.model, "clean", side_effect=ValidationError("Rejected")):
            with self.assertRaisesMessage(ValidationError, "Rejected"):
                self.record.update(name="Stable", ignore_permission=True)
        self.assertEqual(self.record.history.count(), count)

        def clean(instance):
            instance.details["validated"] = True

        with patch.object(
            self.model, "clean", autospec=True, side_effect=clean
        ) as validate:
            self.record.update(
                name="Stable", creator_id=self.user.pk, ignore_permission=True
            )
        self.assertEqual(validate.call_count, 1)
        self.assertTrue(self.record.details["validated"])
        self.assertEqual(self.record.history.count(), count + 1)
