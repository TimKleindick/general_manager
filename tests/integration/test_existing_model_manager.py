# type: ignore

from __future__ import annotations

from datetime import timedelta
from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.db import connections, models
from django.utils import timezone

from general_manager.interface import ExistingModelInterface
from general_manager.interface.capabilities.orm.mutations import (
    OrmMutationCapability,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class AlwaysPassRule:
    def __init__(self) -> None:
        """
        Initialize the AlwaysPassRule, setting up its internal call counter.

        Sets the `calls` attribute to 0 to track how many times `evaluate` is invoked.
        """
        self.calls = 0

    def evaluate(self, obj: models.Model) -> bool:
        """
        Evaluate the rule against a Django model instance.

        Increments the rule's internal call counter (`self.calls`) and always evaluates as passing.

        Parameters:
            obj (models.Model): The model instance being evaluated.

        Returns:
            `True` (this rule always passes).
        """
        self.calls += 1
        return True

    def get_error_message(self) -> dict[str, list[str]]:
        """
        Return an empty mapping of field names to lists of validation error messages.

        Returns:
            dict[str, list[str]]: An empty dictionary indicating no validation errors.
        """
        return {}


class ExistingModelIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """
        Dynamically define and attach a LegacyCustomer Django model, an ExistingModelInterface, and a GeneralManager subclass to the test class for integration tests.

        This sets up:
        - LegacyCustomer: a Django model with fields `name`, `notes`, `is_active`, `changed_by` (FK to User), and `owners` (M2M to User) and Meta.app_label = "general_manager".
        - A single AlwaysPassRule instance used to track rule evaluations.
        - CustomerInterface: an ExistingModelInterface bound to LegacyCustomer with the rule included in its Meta.rules.
        - CustomerManager: a GeneralManager using CustomerInterface, exposing a class-level `_rule_tracker` referencing the rule and an inner Factory with name "Legacy Customer".
        The created classes are attached to the test class as `LegacyCustomer`, `CustomerInterface`, `CustomerManager`, and `general_manager_classes` (list containing CustomerManager).

        Parameters:
            cls: The test class to which the generated model, interface, and manager are attached.
        """

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
        super().setUpClass()

    def setUp(self) -> None:
        """
        Prepare test fixtures for each test: reset the rule tracker, create two User instances, and create two LegacyCustomer-backed manager instances.

        This sets the following attributes on self:
        - user1, user2: created User records.
        - customer_a, customer_b: instances created via CustomerManager.create with predefined fields and history_comment for customer_a.

        All created records are persisted to the test database.
        """
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
        """
        Clean up created test data and run superclass teardown.

        Deletes all LegacyCustomer records, removes self.user1 and self.user2 from the database if they exist, and then calls the superclass tearDown.
        """
        self.LegacyCustomer.objects.all().delete()
        for user in (getattr(self, "user1", None), getattr(self, "user2", None)):
            if user is None or user.pk is None:
                continue
            qs = User.objects.filter(pk=user.pk)
            qs._raw_delete(qs.db)
        super().tearDown()

    def test_create_and_attribute_access(self) -> None:
        """
        Verify that creating a LegacyCustomer via the manager persists fields, records a creation history entry, and exposes related owners and attributes through the manager API.

        Asserts that:
        - the persisted model's last history entry has reason "created customer",
        - the manager instance's `name` is "Acme Corp",
        - dict-like access to the manager yields `"notes": "important"`,
        - `owners_list` returns a list containing `user1`.
        """
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
        """
        Verifies that updating a manager-backed instance applies field changes, updates its owners list, and records the provided history reason.

        Asserts that the instance's name is changed, the owners_list contains the given user IDs, and the last historical record for the instance has the expected history_change_reason.
        """
        updated = self.customer_a.update(
            creator_id=self.user2.pk,
            history_comment="renamed",
            name="Acme International",
            owners_id_list=[self.user1.pk, self.user2.pk],
            ignore_permission=True,
        )
        self.assertIs(updated, self.customer_a)
        self.assertEqual(self.customer_a.name, "Acme International")
        self.assertEqual(updated.name, "Acme International")
        owners = sorted(user.pk for user in updated.owners_list)
        self.assertEqual(owners, sorted([self.user1.pk, self.user2.pk]))
        history = (
            self.LegacyCustomer.history.filter(id=updated.identification["id"])
            .order_by("history_date")
            .last()
        )  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "renamed")  # type: ignore[union-attr]

    def test_non_upload_create_rolls_back_when_many_to_many_fails(self) -> None:
        attempted_name = "Failed M2M Create"
        original_apply_many_to_many = OrmMutationCapability.apply_many_to_many
        failure = RuntimeError("M2M failed")

        def apply_many_to_many_then_fail(*args: object, **kwargs: object) -> None:
            original_apply_many_to_many(*args, **kwargs)
            raise failure

        with (
            patch.object(
                OrmMutationCapability,
                "apply_many_to_many",
                autospec=True,
                side_effect=apply_many_to_many_then_fail,
            ),
            self.assertRaisesRegex(RuntimeError, "M2M failed"),
        ):
            self.CustomerManager.create(
                creator_id=self.user1.pk,
                name=attempted_name,
                owners_id_list=[self.user2.pk],
                ignore_permission=True,
            )

        self.assertEqual(
            {
                "live": self.LegacyCustomer.objects.filter(
                    name=attempted_name
                ).exists(),
                "historical": self.LegacyCustomer.history.filter(
                    name=attempted_name
                ).exists(),
            },
            {"live": False, "historical": False},
        )

    def test_non_upload_update_rolls_back_when_many_to_many_fails(self) -> None:
        customer_id = self.customer_a.identification["id"]
        baseline = self.LegacyCustomer.objects.get(pk=customer_id)
        baseline_state = {
            "name": baseline.name,
            "owner_ids": list(
                baseline.owners.order_by("pk").values_list("pk", flat=True)
            ),
            "changed_by_id": baseline.changed_by_id,
            "history_count": self.LegacyCustomer.history.filter(id=customer_id).count(),
        }
        original_apply_many_to_many = OrmMutationCapability.apply_many_to_many
        failure = RuntimeError("M2M failed")

        def apply_many_to_many_then_fail(*args: object, **kwargs: object) -> None:
            original_apply_many_to_many(*args, **kwargs)
            raise failure

        with (
            patch.object(
                OrmMutationCapability,
                "apply_many_to_many",
                autospec=True,
                side_effect=apply_many_to_many_then_fail,
            ),
            self.assertRaisesRegex(RuntimeError, "M2M failed"),
        ):
            self.customer_a.update(
                creator_id=self.user2.pk,
                name="Failed M2M Update",
                owners_id_list=[self.user2.pk],
                ignore_permission=True,
            )

        persisted = self.LegacyCustomer.objects.get(pk=customer_id)
        persisted_state = {
            "name": persisted.name,
            "owner_ids": list(
                persisted.owners.order_by("pk").values_list("pk", flat=True)
            ),
            "changed_by_id": persisted.changed_by_id,
            "history_count": self.LegacyCustomer.history.filter(id=customer_id).count(),
        }
        self.assertEqual(persisted_state, baseline_state)

    def test_non_upload_create_rolls_back_when_history_reason_fails(self) -> None:
        attempted_name = "Failed History Create"

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations."
                "call_update_change_reason",
                side_effect=RuntimeError("history failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "history failed"),
        ):
            self.CustomerManager.create(
                creator_id=self.user1.pk,
                history_comment="create reason",
                name=attempted_name,
                ignore_permission=True,
            )

        self.assertEqual(
            {
                "live": self.LegacyCustomer.objects.filter(
                    name=attempted_name
                ).exists(),
                "historical": self.LegacyCustomer.history.filter(
                    name=attempted_name
                ).exists(),
            },
            {"live": False, "historical": False},
        )

    def test_non_upload_update_rolls_back_when_history_reason_fails(self) -> None:
        customer_id = self.customer_a.identification["id"]
        baseline = self.LegacyCustomer.objects.get(pk=customer_id)
        baseline_state = {
            "name": baseline.name,
            "owner_ids": list(
                baseline.owners.order_by("pk").values_list("pk", flat=True)
            ),
            "changed_by_id": baseline.changed_by_id,
            "history_count": self.LegacyCustomer.history.filter(id=customer_id).count(),
        }

        with (
            patch(
                "general_manager.interface.capabilities.orm.mutations."
                "call_update_change_reason",
                side_effect=RuntimeError("history failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "history failed"),
        ):
            self.customer_a.update(
                creator_id=self.user2.pk,
                history_comment="update reason",
                name="Failed History Update",
                owners_id_list=[self.user2.pk],
                ignore_permission=True,
            )

        persisted = self.LegacyCustomer.objects.get(pk=customer_id)
        persisted_state = {
            "name": persisted.name,
            "owner_ids": list(
                persisted.owners.order_by("pk").values_list("pk", flat=True)
            ),
            "changed_by_id": persisted.changed_by_id,
            "history_count": self.LegacyCustomer.history.filter(id=customer_id).count(),
        }
        self.assertEqual(persisted_state, baseline_state)

    def test_delete_marks_customer_inactive(self) -> None:
        """
        Verifies that deleting a manager-backed customer marks the underlying legacy record inactive and records a deactivation history entry.

        The test deletes the manager instance with a history comment, reloads the persisted LegacyCustomer via `all_objects`, asserts `is_active` is False, and asserts the most recent history entry's change reason equals "manual block (deactivated)".
        """
        self.customer_b.delete(
            creator_id=self.user1.pk,
            history_comment="manual block",
            ignore_permission=True,
        )
        reloaded = self.LegacyCustomer.all_objects.get(
            pk=self.customer_b.identification["id"]
        )
        self.assertFalse(reloaded.is_active)
        history = (
            self.LegacyCustomer.history.filter(id=reloaded.pk)
            .order_by("history_date")
            .last()
        )  # type: ignore[attr-defined]
        self.assertEqual(history.history_change_reason, "manual block (deactivated)")  # type: ignore[union-attr]

    def test_get_historical_records_for_deleted_manager(self) -> None:
        historical_customer = self.CustomerManager.create(
            creator_id=self.user1.pk,
            history_comment="baseline",
            name="Historical LLC",
            notes="historical",
            owners_id_list=[self.user1.pk],
            ignore_permission=True,
        )
        self.user1.first_name = "Updated"
        self.user1.save()
        snapshot = timezone.now()
        historical_customer.delete(
            creator_id=self.user1.pk,
            history_comment="cleanup",
            ignore_permission=True,
        )

        with patch(
            "django.utils.timezone.now", return_value=snapshot + timedelta(seconds=10)
        ):
            historical_view = self.CustomerManager(
                id=historical_customer.identification["id"],
                search_date=snapshot,
            )

        self.user1.first_name = "Updated2"
        self.user1.save()

        self.assertEqual(historical_view.name, "Historical LLC")
        self.assertTrue(historical_view.is_active)
        self.assertEqual(dict(historical_view)["notes"], "historical")
        owners = list(historical_view.owners_list)
        self.assertEqual(owners[0].pk, self.user1.pk)

    def test_filter_exclude_and_all(self) -> None:
        """
        Verifies that `all()`, `filter()`, and `exclude()` on the manager return manager-wrapped objects and yield correct subsets.

        Asserts that:
        - `all()` returns two manager instances.
        - `filter(name="Acme Corp")` returns a single manager instance whose identification matches `self.customer_a`.
        - `exclude(name="Acme Corp")` returns a single manager instance whose identification matches `self.customer_b`.
        """
        all_customers = self.CustomerManager.all()
        self.assertEqual(len(all_customers), 2)
        self.assertTrue(
            all(
                isinstance(customer, self.CustomerManager) for customer in all_customers
            )
        )
        self.assertIsInstance(all_customers[0], self.CustomerManager)
        self.assertIsInstance(all_customers[1], self.CustomerManager)

        acme = self.CustomerManager.filter(name="Acme Corp")
        self.assertEqual(len(acme), 1)
        self.assertTrue(
            all(isinstance(customer, self.CustomerManager) for customer in acme)
        )
        self.assertEqual(acme[0].identification, self.customer_a.identification)
        self.assertIsInstance(acme[0], self.CustomerManager)

        everyone_else = self.CustomerManager.exclude(name="Acme Corp")
        self.assertEqual(len(everyone_else), 1)
        self.assertTrue(
            all(
                isinstance(customer, self.CustomerManager) for customer in everyone_else
            )
        )
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


class ExistingModelMultiDatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    databases: ClassVar[set[str]] = {"default", "secondary"}

    @classmethod
    def setUpClass(cls) -> None:
        alias = "secondary"
        cls._secondary_original_config = connections.databases.get(alias)
        cls._secondary_had_cached_connection = hasattr(
            connections._connections,
            alias,
        )
        cls._secondary_original_connection = (
            getattr(connections._connections, alias)
            if cls._secondary_had_cached_connection
            else None
        )
        if cls._secondary_had_cached_connection:
            del connections[alias]
        connections.databases[alias] = {
            **connections.databases["default"],
            "NAME": ":memory:",
        }

        class MultiDatabaseRecord(models.Model):
            name = models.CharField(max_length=64)
            owners = models.ManyToManyField(User, blank=True)

            class Meta:
                app_label = "general_manager"

        class MultiDatabaseInterface(ExistingModelInterface):
            model = MultiDatabaseRecord
            database = "secondary"

        class MultiDatabaseManager(GeneralManager):
            Interface = MultiDatabaseInterface

        cls.MultiDatabaseRecord = MultiDatabaseRecord
        cls.MultiDatabaseOwnersHistory = MultiDatabaseRecord.history.model.owners.model
        cls.MultiDatabaseManager = MultiDatabaseManager
        cls.general_manager_classes = [MultiDatabaseManager]
        super().setUpClass()
        secondary = connections["secondary"]
        secondary.connect()
        with secondary.schema_editor() as editor:
            editor.create_model(ContentType)
            editor.create_model(Permission)
            editor.create_model(Group)
            editor.create_model(User)
            editor.create_model(cls.MultiDatabaseRecord)
            editor.create_model(cls.MultiDatabaseRecord.history.model)
            editor.create_model(cls.MultiDatabaseOwnersHistory)

    @classmethod
    def tearDownClass(cls) -> None:
        secondary = connections["secondary"]
        try:
            with secondary.schema_editor() as editor:
                editor.delete_model(cls.MultiDatabaseOwnersHistory)
                editor.delete_model(cls.MultiDatabaseRecord.history.model)
                editor.delete_model(cls.MultiDatabaseRecord)
                editor.delete_model(User)
                editor.delete_model(Group)
                editor.delete_model(Permission)
                editor.delete_model(ContentType)
        finally:
            try:
                super().tearDownClass()
            finally:
                secondary.close()
                if hasattr(connections._connections, "secondary"):
                    del connections["secondary"]
                if cls._secondary_original_config is None:
                    connections.databases.pop("secondary", None)
                else:
                    connections.databases["secondary"] = cls._secondary_original_config
                if cls._secondary_had_cached_connection:
                    connections._connections.secondary = (  # type: ignore[attr-defined]
                        cls._secondary_original_connection
                    )

    def test_create_keeps_history_reason_and_rollback_on_configured_alias(
        self,
    ) -> None:
        original_apply_many_to_many = OrmMutationCapability.apply_many_to_many
        failure = RuntimeError("post-history failure")

        def apply_many_to_many_then_fail(*args: object, **kwargs: object) -> None:
            original_apply_many_to_many(*args, **kwargs)
            raise failure

        with (
            patch.object(
                OrmMutationCapability,
                "apply_many_to_many",
                autospec=True,
                side_effect=apply_many_to_many_then_fail,
            ),
            self.assertRaisesRegex(RuntimeError, "post-history failure"),
        ):
            self.MultiDatabaseManager.create(
                history_comment="rolled back create",
                name="Rolled Back",
                ignore_permission=True,
            )

        self.assertFalse(
            self.MultiDatabaseRecord.objects.using("secondary")
            .filter(name="Rolled Back")
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("secondary")
            .filter(name="Rolled Back")
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("default")
            .filter(name="Rolled Back")
            .exists()
        )

        created = self.MultiDatabaseManager.create(
            history_comment="created on secondary",
            name="Persisted",
            ignore_permission=True,
        )
        record_id = created.identification["id"]
        self.assertTrue(
            self.MultiDatabaseRecord.objects.using("secondary")
            .filter(pk=record_id, name="Persisted")
            .exists()
        )
        self.assertEqual(
            list(
                self.MultiDatabaseRecord.history.using("secondary")
                .filter(id=record_id)
                .values_list("history_change_reason", flat=True)
            ),
            ["created on secondary"],
        )
        self.assertFalse(
            self.MultiDatabaseRecord.objects.using("default")
            .filter(pk=record_id)
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("default")
            .filter(id=record_id)
            .exists()
        )

    def test_update_keeps_history_reason_and_rollback_on_configured_alias(
        self,
    ) -> None:
        record = self.MultiDatabaseRecord(name="Original")
        self.MultiDatabaseRecord.objects.using("secondary").bulk_create([record])
        manager = self.MultiDatabaseManager(id=record.pk)
        original_apply_many_to_many = OrmMutationCapability.apply_many_to_many
        failure = RuntimeError("post-history failure")

        def apply_many_to_many_then_fail(*args: object, **kwargs: object) -> None:
            original_apply_many_to_many(*args, **kwargs)
            raise failure

        with (
            patch.object(
                OrmMutationCapability,
                "apply_many_to_many",
                autospec=True,
                side_effect=apply_many_to_many_then_fail,
            ),
            self.assertRaisesRegex(RuntimeError, "post-history failure"),
        ):
            manager.update(
                history_comment="rolled back update",
                name="Rolled Back",
                ignore_permission=True,
            )

        record.refresh_from_db(using="secondary")
        self.assertEqual(record.name, "Original")
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("secondary")
            .filter(id=record.pk)
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("default")
            .filter(id=record.pk)
            .exists()
        )

        manager.update(
            history_comment="updated on secondary",
            name="Persisted",
            ignore_permission=True,
        )
        record.refresh_from_db(using="secondary")
        self.assertEqual(record.name, "Persisted")
        self.assertEqual(
            list(
                self.MultiDatabaseRecord.history.using("secondary")
                .filter(id=record.pk)
                .values_list("history_change_reason", flat=True)
            ),
            ["updated on secondary"],
        )
        self.assertFalse(
            self.MultiDatabaseRecord.objects.using("default")
            .filter(pk=record.pk)
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("default")
            .filter(id=record.pk)
            .exists()
        )

    def test_many_to_many_create_keeps_all_artifacts_on_configured_alias(
        self,
    ) -> None:
        owner = User.objects.using("secondary").create(username="secondary-owner")
        original_apply_many_to_many = OrmMutationCapability.apply_many_to_many
        failure = RuntimeError("post-history failure")

        def apply_many_to_many_then_fail(*args: object, **kwargs: object) -> None:
            original_apply_many_to_many(*args, **kwargs)
            raise failure

        with (
            patch.object(
                OrmMutationCapability,
                "apply_many_to_many",
                autospec=True,
                side_effect=apply_many_to_many_then_fail,
            ),
            self.assertRaisesRegex(RuntimeError, "post-history failure"),
        ):
            self.MultiDatabaseManager.create(
                history_comment="rolled back with owner",
                name="Rolled Back With Owner",
                owners_id_list=[owner.pk],
                ignore_permission=True,
            )

        live_through = self.MultiDatabaseRecord.owners.through
        rollback_artifacts = {
            "secondary_live": self.MultiDatabaseRecord.objects.using("secondary")
            .filter(name="Rolled Back With Owner")
            .count(),
            "secondary_live_m2m": live_through.objects.using("secondary").count(),
            "secondary_history_reasons": list(
                self.MultiDatabaseRecord.history.using("secondary")
                .filter(name="Rolled Back With Owner")
                .values_list("history_change_reason", flat=True)
            ),
            "secondary_history_m2m": self.MultiDatabaseOwnersHistory.objects.using(
                "secondary"
            ).count(),
            "default_live": self.MultiDatabaseRecord.objects.using("default")
            .filter(name="Rolled Back With Owner")
            .count(),
            "default_live_m2m": live_through.objects.using("default").count(),
            "default_history_reasons": list(
                self.MultiDatabaseRecord.history.using("default")
                .filter(name="Rolled Back With Owner")
                .values_list("history_change_reason", flat=True)
            ),
            "default_history_m2m": self.MultiDatabaseOwnersHistory.objects.using(
                "default"
            ).count(),
        }
        self.assertEqual(
            rollback_artifacts,
            {
                "secondary_live": 0,
                "secondary_live_m2m": 0,
                "secondary_history_reasons": [],
                "secondary_history_m2m": 0,
                "default_live": 0,
                "default_live_m2m": 0,
                "default_history_reasons": [],
                "default_history_m2m": 0,
            },
        )

        created = self.MultiDatabaseManager.create(
            history_comment="created with owner on secondary",
            name="Persisted With Owner",
            owners_id_list=[owner.pk],
            ignore_permission=True,
        )
        record_id = created.identification["id"]
        self.assertTrue(
            self.MultiDatabaseRecord.objects.using("secondary")
            .filter(pk=record_id, name="Persisted With Owner")
            .exists()
        )
        self.assertTrue(
            live_through.objects.using("secondary")
            .filter(multidatabaserecord_id=record_id, user_id=owner.pk)
            .exists()
        )
        reasons = list(
            self.MultiDatabaseRecord.history.using("secondary")
            .filter(id=record_id)
            .values_list("history_change_reason", flat=True)
        )
        self.assertGreaterEqual(len(reasons), 2)
        self.assertEqual(set(reasons), {"created with owner on secondary"})
        self.assertTrue(
            self.MultiDatabaseOwnersHistory.objects.using("secondary")
            .filter(multidatabaserecord_id=record_id, user_id=owner.pk)
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseRecord.objects.using("default")
            .filter(pk=record_id)
            .exists()
        )
        self.assertFalse(live_through.objects.using("default").exists())
        self.assertFalse(
            self.MultiDatabaseRecord.history.using("default")
            .filter(id=record_id)
            .exists()
        )
        self.assertFalse(
            self.MultiDatabaseOwnersHistory.objects.using("default").exists()
        )
