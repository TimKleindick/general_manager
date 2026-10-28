"""Singular relations must resolve their targets at the source snapshot."""

from typing import Any, ClassVar

from django.db import models
from django.utils import timezone

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class HistoricalRelationEdgesTests(GeneralManagerTransactionTestCase):
    Target: ClassVar[Any]
    Record: ClassVar[Any]
    Profile: ClassVar[Any]

    @classmethod
    def setUpClass(cls) -> None:
        class RelationHistoryTarget(GeneralManager):
            class Interface(DatabaseInterface):
                code = models.CharField(max_length=40, unique=True)
                name = models.CharField(max_length=80)

        class RelationHistoryRecord(GeneralManager):
            class Interface(DatabaseInterface):
                target = models.ForeignKey(
                    "general_manager.RelationHistoryTarget",
                    to_field="code",
                    on_delete=models.SET_NULL,
                    null=True,
                    related_name="coded_records",
                )

        class RelationHistoryProfile(GeneralManager):
            class Interface(DatabaseInterface):
                label = models.CharField(max_length=80)
                target: models.OneToOneField[Any] = models.OneToOneField(
                    "general_manager.RelationHistoryTarget",
                    to_field="code",
                    on_delete=models.CASCADE,
                    related_name="custom_profile",
                    related_query_name="profile_lookup",
                )

        cls.Target = RelationHistoryTarget
        cls.Record = RelationHistoryRecord
        cls.Profile = RelationHistoryProfile
        cls.general_manager_classes = [
            RelationHistoryTarget,
            RelationHistoryRecord,
            RelationHistoryProfile,
        ]

    def test_reverse_one_to_one_uses_old_membership_after_reassignment(self) -> None:
        target = self.Target.Interface._model.objects.create(code="old", name="Old")
        other = self.Target.Interface._model.objects.create(code="new", name="New")
        profile = self.Profile.Interface._model.objects.create(
            target=target, label="Original profile"
        )
        before_change = timezone.now()
        profile.target = other
        profile.label = "Moved profile"
        profile.save()
        self.Profile.Interface._model.objects.create(
            target=target, label="Replacement profile"
        )

        historical = self.Target(target.pk, search_date=before_change)
        related = historical.relation_history_profile
        self.assertIsInstance(related, self.Profile)
        self.assertEqual(related.id, profile.pk)
        self.assertEqual(related.label, "Original profile")
        self.assertEqual(
            self.Target(target.pk).relation_history_profile.label,
            "Replacement profile",
        )
        self.assertIsNone(
            self.Target(other.pk, search_date=before_change).relation_history_profile
        )

    def test_reverse_one_to_one_resolves_deleted_historical_target(self) -> None:
        target = self.Target.Interface._model.objects.create(code="code", name="Old")
        profile = self.Profile.Interface._model.objects.create(
            target=target, label="Deleted profile"
        )
        profile_id = profile.pk
        before_delete = timezone.now()
        profile.delete()

        related = self.Target(
            target.pk, search_date=before_delete
        ).relation_history_profile
        self.assertIsInstance(related, self.Profile)
        self.assertEqual(related.id, profile_id)
        self.assertEqual(related.label, "Deleted profile")
        self.assertIsNone(self.Target(target.pk).relation_history_profile)

    def test_reverse_one_to_one_does_not_fall_back_to_live_target(self) -> None:
        target = self.Target.Interface._model.objects.create(code="code", name="Old")
        before_profile = timezone.now()
        self.Profile.Interface._model.objects.create(target=target, label="New")

        self.assertIsNone(
            self.Target(target.pk, search_date=before_profile).relation_history_profile
        )

    def test_to_field_resolves_historical_target_after_deletion(self) -> None:
        target = self.Target.Interface._model.objects.create(code="code", name="Old")
        record = self.Record.Interface._model.objects.create(target=target)
        target_id = target.pk
        before_delete = timezone.now()
        target.delete()

        historical = self.Record(record.pk, search_date=before_delete)
        related = historical.target
        self.assertIsInstance(related, self.Target)
        self.assertEqual(related.id, target_id)
        self.assertEqual(related.name, "Old")
        self.assertEqual(historical.target_id, "code")
        self.assertIsNone(self.Record(record.pk).target)

    def test_to_field_uses_old_reference_after_reassignment(self) -> None:
        target = self.Target.Interface._model.objects.create(code="old", name="Old")
        other = self.Target.Interface._model.objects.create(code="new", name="New")
        record = self.Record.Interface._model.objects.create(target=target)
        before_change = timezone.now()
        record.target = other
        record.save()
        target_id = target.pk
        target.delete()
        self.Target.Interface._model.objects.create(code="old", name="Reused code")

        related = self.Record(record.pk, search_date=before_change).target
        self.assertIsInstance(related, self.Target)
        self.assertEqual(related.id, target_id)
        self.assertEqual(related.name, "Old")
        self.assertEqual(self.Record(record.pk).target.name, "New")

    def test_nullable_to_field_does_not_fall_back_to_live_reference(self) -> None:
        record = self.Record.Interface._model.objects.create(target=None)
        before_target = timezone.now()
        target = self.Target.Interface._model.objects.create(code="new", name="New")
        record.target = target
        record.save()

        self.assertIsNone(self.Record(record.pk, search_date=before_target).target)
        self.assertEqual(self.Record(record.pk).target.name, "New")
