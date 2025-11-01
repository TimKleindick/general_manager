# type: ignore

from __future__ import annotations

from typing import ClassVar

from django.contrib.auth.models import User
from django.db import models

from general_manager.interface.existing_model_interface import ExistingModelInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class AlwaysPassRule:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, obj: models.Model) -> bool:
        self.calls += 1
        return True

    def get_error_message(self) -> dict[str, list[str]]:
        return {}


class ExistingModelIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class LegacyCustomer(models.Model):
            name = models.CharField(max_length=64)
            notes = models.TextField(blank=True)
            is_active = models.BooleanField(default=True)
            changed_by = models.ForeignKey(
                User, on_delete=models.PROTECT, null=True, blank=True
            )
            owners = models.ManyToManyField(
                User, related_name="legacy_customers", blank=True
            )

            class Meta:
                app_label = "general_manager"

        rule = AlwaysPassRule()

        class CustomerInterface(ExistingModelInterface):
            model = LegacyCustomer

            class Meta:
                rules: ClassVar[list[AlwaysPassRule]] = [rule]

        class CustomerManager(GeneralManager):
            Interface = CustomerInterface
            _rule_tracker: ClassVar[AlwaysPassRule] = rule

            class Factory:
                name = "Legacy Customer"

        cls.LegacyCustomer = LegacyCustomer
        cls.CustomerInterface = CustomerInterface
        cls.CustomerManager = CustomerManager
        cls.general_manager_classes = [CustomerManager]
        cls.read_only_classes: list[type[GeneralManager]] = []
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.CustomerManager._rule_tracker.calls = 0
        self.user1 = User.objects.create(username="legacy-owner-1")
        self.user2 = User.objects.create(username="legacy-owner-2")
        self.customer_a = self.CustomerManager.create(
            creator_id=self.user1.pk,
            history_comment="created customer",
            name="Acme Corp",
            notes="important",
            owners_id_list=[self.user1.pk],
            ignore_permission=True,
        )
        self.customer_b = self.CustomerManager.create(
            creator_id=self.user1.pk,
            name="Beta LLC",
            ignore_permission=True,
        )

    def tearDown(self) -> None:
        self.LegacyCustomer.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()

    def test_create_and_attribute_access(self) -> None:
        stored = self.LegacyCustomer.objects.get(
            pk=self.customer_a.identification["id"]
        )
        self.assertEqual(
            stored.history.last().history_change_reason, "created customer"
        )  # type: ignore[attr-defined]
        self.assertEqual(self.customer_a.name, "Acme Corp")
        self.assertEqual(dict(self.customer_a)["notes"], "important")
        self.assertEqual(list(self.customer_a.owners_list), [self.user1])

    def test_update_applies_changes_and_history(self) -> None:
        updated = self.customer_a.update(
            creator_id=self.user2.pk,
            history_comment="renamed",
            name="Acme International",
            owners_id_list=[self.user1.pk, self.user2.pk],
            ignore_permission=True,
        )
        self.assertEqual(updated.name, "Acme International")
        owners = sorted(user.pk for user in updated.owners_list)
        self.assertEqual(owners, sorted([self.user1.pk, self.user2.pk]))
        history = (
            self.LegacyCustomer.history.filter(id=updated.identification["id"])
            .order_by("history_date")
            .last()
        )  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "renamed")  # type: ignore[union-attr]

    def test_deactivate_marks_customer_inactive(self) -> None:
        deactivated = self.customer_b.deactivate(
            creator_id=self.user1.pk,
            history_comment="manual block",
            ignore_permission=True,
        )
        reloaded = self.LegacyCustomer.objects.get(pk=deactivated.identification["id"])
        self.assertFalse(deactivated.is_active)
        self.assertFalse(reloaded.is_active)
        history = (
            self.LegacyCustomer.history.filter(id=reloaded.pk)
            .order_by("history_date")
            .last()
        )  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "manual block (deactivated)")  # type: ignore[union-attr]

    def test_filter_exclude_and_all(self) -> None:
        all_customers = self.CustomerManager.all()
        self.assertEqual(len(all_customers), 2)
        self.assertIsInstance(all_customers[0], self.CustomerManager)
        self.assertIsInstance(all_customers[1], self.CustomerManager)

        acme = self.CustomerManager.filter(name="Acme Corp")
        self.assertEqual(len(acme), 1)
        self.assertEqual(acme[0].identification, self.customer_a.identification)
        self.assertIsInstance(acme[0], self.CustomerManager)

        everyone_else = self.CustomerManager.exclude(name="Acme Corp")
        self.assertEqual(len(everyone_else), 1)
        self.assertEqual(
            everyone_else[0].identification, self.customer_b.identification
        )
        self.assertIsInstance(everyone_else[0], self.CustomerManager)

    def test_rule_execution_on_full_clean(self) -> None:
        instance = self.LegacyCustomer(
            name="Manual",
            changed_by=None,
        )
        instance.full_clean()
        self.assertGreater(self.CustomerManager._rule_tracker.calls, 0)

    def test_factory_creates_instances(self) -> None:
        factory_instance = self.CustomerManager.Factory.create_batch(1)[0]
        self.assertIsInstance(factory_instance, self.CustomerManager)
        self.assertEqual(factory_instance.name, "Legacy Customer")
        stored = self.LegacyCustomer.objects.get(
            pk=factory_instance.identification["id"]
        )
        self.assertIsNotNone(stored)
