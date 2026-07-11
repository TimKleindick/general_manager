# type: ignore

from __future__ import annotations
from datetime import datetime, timedelta
from django.db import connection, models
from django.core.exceptions import ValidationError, FieldError
from django.contrib.auth.models import User
from django.utils.crypto import get_random_string
from django.utils import timezone
from typing import ClassVar
from unittest.mock import patch
from django.test.utils import CaptureQueriesContext
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import DatabaseInterface, ReadOnlyInterface
from general_manager.api.property import graph_ql_property
from general_manager.bucket.database_bucket import DatabaseBucket
from general_manager.bucket.base_bucket import Bucket
from general_manager.cache.run_context import CalculationRunContext
from general_manager.cache.signals import pre_data_change

from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
    run_registered_startup_hooks,
)
from general_manager.manager.meta import (
    GeneralManagerMeta,
    InvalidManagerStateError,
)


HISTORICAL_READ_QUERY_BUDGETS = {
    "history_hit": 1,
    "history_miss": 2,
    "recent_live": 1,
}


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Create and attach three nested GeneralManager test models to the test class.

        Defines TestCountry (read-only interface with seeded country data), TestHuman (database interface with optional foreign-key to TestCountry), and TestFamily (database interface with a many-to-many bucket to TestHuman and soft-delete enabled). Assigns the created classes to cls.TestCountry, cls.TestHuman, cls.TestFamily and collects them in cls.general_manager_classes for use by tests.
        """

        class TestCountry(GeneralManager):
            _data: ClassVar[list[dict[str, str]]] = [
                {"code": "US", "name": "United States"},
                {"code": "DE", "name": "Germany"},
            ]
            code: str
            name: str
            humans_list: Bucket[TestHuman]

            class Interface(ReadOnlyInterface):
                code = models.CharField(max_length=2, unique=True)
                name = models.CharField(max_length=50)

        class TestHuman(GeneralManager):
            name: str
            country: TestCountry | None
            families_list: Bucket[TestFamily]

            @graph_ql_property(filterable=True, sortable=True)
            def name_length_python(self) -> int:
                return len(self.name)

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                country = models.ForeignKey(
                    "general_manager.TestCountry",
                    on_delete=models.CASCADE,
                    related_name="humans",
                    null=True,
                    blank=True,
                )

        class TestFamily(GeneralManager):
            name: str
            humans_list: Bucket[TestHuman]

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                humans = models.ManyToManyField(
                    "general_manager.TestHuman",
                    related_name="families",
                )

                class Meta:
                    use_soft_delete = True

        class TestSupplier(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)

        class TestProjectQualityStatus(GeneralManager):
            name: str
            processing_supplier_list: Bucket[TestSupplier]
            standard_part_supplier_list: Bucket[TestSupplier]
            direct_delivery_supplier_list: Bucket[TestSupplier]

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                processing_supplier = models.ManyToManyField(
                    "general_manager.TestSupplier",
                    blank=True,
                    related_name="processing_supplier",
                )
                standard_part_supplier = models.ManyToManyField(
                    "general_manager.TestSupplier",
                    blank=True,
                    related_name="standard_part_supplier",
                )
                direct_delivery_supplier = models.ManyToManyField(
                    "general_manager.TestSupplier",
                    blank=True,
                    related_name="direct_delivery_supplier",
                )

        class ChangeRequest(GeneralManager):
            title: str
            change_request_approval: ChangeRequestApproval
            changerequestfeasibility_list: Bucket[ChangeRequestFeasibility]
            change_request_reviews_list: Bucket[ChangeRequestReview]

            class Interface(DatabaseInterface):
                title = models.CharField(max_length=100)

        class ChangeRequestApproval(GeneralManager):
            approved_by: str
            change_request: ChangeRequest

            class Interface(DatabaseInterface):
                approved_by = models.CharField(max_length=100)
                change_request = models.OneToOneField(
                    "general_manager.ChangeRequest",
                    on_delete=models.CASCADE,
                )

        class ChangeRequestFeasibility(GeneralManager):
            score: int
            change_request: ChangeRequest

            class Interface(DatabaseInterface):
                score = models.IntegerField(default=0)
                change_request = models.ForeignKey(
                    "general_manager.ChangeRequest",
                    on_delete=models.CASCADE,
                )

        class ChangeRequestReview(GeneralManager):
            summary: str
            change_request: ChangeRequest

            class Interface(DatabaseInterface):
                summary = models.CharField(max_length=100)
                change_request = models.ForeignKey(
                    "general_manager.ChangeRequest",
                    on_delete=models.CASCADE,
                    related_name="change_request_reviews",
                )

        class CustomInitRecord(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)

                def __init__(self, *args, **kwargs):
                    self.initialized_by_interface = True
                    super().__init__(*args, **kwargs)

        cls.TestCountry = TestCountry
        cls.TestHuman = TestHuman
        cls.TestFamily = TestFamily
        cls.TestSupplier = TestSupplier
        cls.TestProjectQualityStatus = TestProjectQualityStatus
        cls.ChangeRequest = ChangeRequest
        cls.ChangeRequestApproval = ChangeRequestApproval
        cls.ChangeRequestFeasibility = ChangeRequestFeasibility
        cls.ChangeRequestReview = ChangeRequestReview
        cls.CustomInitRecord = CustomInitRecord
        cls.general_manager_classes = [
            TestCountry,
            TestHuman,
            TestFamily,
            TestSupplier,
            TestProjectQualityStatus,
            ChangeRequest,
            ChangeRequestApproval,
            ChangeRequestFeasibility,
            ChangeRequestReview,
            CustomInitRecord,
        ]

    def setUp(self):
        """
        Prepare test fixtures by creating a user, two humans, and a family linking them.

        Creates a User with username "human-owner-1"; creates two TestHuman instances named "Alice" (linked to the TestCountry with code "US" if present) and "Bob"; and creates a TestFamily named "Smith Family" that includes both humans. All records are created with creator_id set to None and ignore permission checks.
        """
        super().setUp()
        self.User1 = User.objects.create(username="human-owner-1")

        self.test_human1 = self.TestHuman.create(
            creator_id=None,
            name="Alice",
            country=self.TestCountry.filter(code="US").first(),
            ignore_permission=True,
        )

        self.test_human2 = self.TestHuman.create(
            creator_id=None,
            name="Bob",
            ignore_permission=True,
        )

        self.test_family = self.TestFamily.create(
            creator_id=None,
            name="Smith Family",
            humans=[self.test_human1, self.test_human2],
            ignore_permission=True,
        )

        self.change_request = self.ChangeRequest.create(
            creator_id=None,
            title="Assess impact",
            ignore_permission=True,
        )
        self.change_request_feasibility = self.ChangeRequestFeasibility.create(
            creator_id=None,
            score=5,
            change_request=self.change_request,
            ignore_permission=True,
        )
        self.change_request_approval = self.ChangeRequestApproval.create(
            creator_id=None,
            approved_by="Reviewer",
            change_request=self.change_request,
            ignore_permission=True,
        )
        self.change_request_review = self.ChangeRequestReview.create(
            creator_id=None,
            summary="Initial review",
            change_request=self.change_request,
            ignore_permission=True,
        )
        self.other_change_request = self.ChangeRequest.create(
            creator_id=None,
            title="Unrelated request",
            ignore_permission=True,
        )

    def tearDown(self):
        """
        Cleans up the test database by deleting all instances of TestCountry, TestHuman, and TestFamily.

        This ensures that each test starts with a clean state.
        """
        self.TestCountry.Interface._model._meta.model.objects.all().delete()
        self.TestHuman.Interface._model._meta.model.objects.all().delete()
        self.TestFamily.Interface._model._meta.model.objects.all().delete()
        self.TestProjectQualityStatus.Interface._model._meta.model.objects.all().delete()
        self.TestSupplier.Interface._model._meta.model.objects.all().delete()
        self.ChangeRequest.Interface._model._meta.model.objects.all().delete()
        self.ChangeRequestApproval.Interface._model._meta.model.objects.all().delete()
        self.ChangeRequestFeasibility.Interface._model._meta.model.objects.all().delete()
        self.ChangeRequestReview.Interface._model._meta.model.objects.all().delete()
        self.CustomInitRecord.Interface._model._meta.model.objects.all().delete()
        super().tearDown()

    def test_iter(self):
        """
        Verify retrieval of all TestHuman records and that each instance's `name` and `country` match its dictionary representation.

        Asserts there are exactly two records and for each record checks `human.name == dict(human)["name"]` and `human.country == dict(human)["country"]`.
        """
        humans = self.TestHuman.all()
        self.assertEqual(len(humans), 2)
        for human in humans:
            self.assertEqual(human.name, dict(human)["name"])
            self.assertEqual(human.country, dict(human)["country"])

    def test_trusted_orm_hydration_uses_loaded_row_without_query(self):
        row = self.TestHuman.Interface._model.objects.get(
            pk=self.test_human1.identification["id"]
        )

        with self.assertNumQueries(0):
            manager = self.TestHuman._from_trusted_orm_instance(row)
            self.assertEqual(manager.identification, {"id": row.pk})
            self.assertEqual(manager.name, "Alice")

        self.assertIs(manager._interface._instance, row)
        self.assertTrue(manager._manager_state_valid)

    def test_trusted_orm_hydration_normalizes_search_date(self):
        row = self.TestHuman.Interface._model.objects.get(
            pk=self.test_human1.identification["id"]
        )
        naive_search_date = datetime(2026, 1, 1, 12, 30)

        manager = self.TestHuman._from_trusted_orm_instance(
            row,
            search_date=naive_search_date,
        )

        self.assertEqual(manager.identification, {"id": row.pk})
        self.assertIsNotNone(manager._interface._search_date.tzinfo)

    def test_public_constructor_still_rejects_invalid_external_input(self):
        with self.assertRaises(ValueError):
            self.TestHuman(id="not-an-integer")

    def test_create_path_does_not_use_trusted_hydration(self):
        with patch.object(
            self.TestHuman,
            "_from_trusted_orm_instance",
            wraps=self.TestHuman._from_trusted_orm_instance,
        ) as trusted_hydrate:
            created = self.TestHuman.create(
                creator_id=None,
                name="Charlie",
                ignore_permission=True,
            )

        self.assertEqual(created.name, "Charlie")
        trusted_hydrate.assert_not_called()

    def test_bucket_iteration_hydrates_loaded_rows_without_per_row_queries(self):
        self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            ignore_permission=True,
        )

        with self.assertNumQueries(1):
            names = sorted(human.name for human in self.TestHuman.all())

        self.assertEqual(names, ["Alice", "Bob", "Charlie"])

    def test_run_context_reuses_trusted_bucket_rows_without_per_row_queries(self):
        self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            ignore_permission=True,
        )
        first_bucket = self.TestHuman.filter(name__in=["Alice", "Bob", "Charlie"])
        second_bucket = self.TestHuman.filter(name__in=["Alice", "Bob", "Charlie"])

        with CalculationRunContext(), self.assertNumQueries(1):
            first_names = sorted(human.name for human in first_bucket)
            second_names = sorted(human.name for human in second_bucket)

        self.assertEqual(first_names, ["Alice", "Bob", "Charlie"])
        self.assertEqual(second_names, ["Alice", "Bob", "Charlie"])

    def test_run_context_prefetches_cached_many_to_many_source_rows(self):
        self.TestFamily.create(
            creator_id=None,
            name="Johnson Family",
            humans=[self.test_human2],
            ignore_permission=True,
        )
        families_bucket = self.TestFamily.filter(
            name__in=["Smith Family", "Johnson Family"]
        )

        with CalculationRunContext(), self.assertNumQueries(2):
            families = sorted(families_bucket, key=lambda family: family.name)
            human_names_by_family = {
                family.name: sorted(human.name for human in family.humans_list)
                for family in families
            }

        self.assertEqual(
            human_names_by_family,
            {
                "Johnson Family": ["Bob"],
                "Smith Family": ["Alice", "Bob"],
            },
        )

    def test_run_context_many_to_many_accessor_falls_back_without_cached_source_row(
        self,
    ):
        family_id = self.test_family.identification["id"]

        with CalculationRunContext():
            family = self.TestFamily(id=family_id)
            human_names = sorted(human.name for human in family.humans_list)

        self.assertEqual(human_names, ["Alice", "Bob"])

    def test_manager_field_access_reuses_resolved_attribute_value(self):
        human = self.TestHuman.filter(name="Alice").first()
        original_accessor = human._attributes["name"]
        access_count = 0

        def wrapped_accessor(interface):
            nonlocal access_count
            access_count += 1
            return original_accessor(interface)

        self.TestHuman._attributes["name"] = wrapped_accessor
        self.addCleanup(
            self.TestHuman._attributes.__setitem__, "name", original_accessor
        )

        self.assertEqual(human.name, "Alice")
        self.assertEqual(human.name, "Alice")
        self.assertEqual(access_count, 1)

    def test_gm_created_bucket_run_cache_does_not_compile_sql_for_signature(self):
        first_bucket = self.TestHuman.filter(name__in=["Alice", "Bob"])
        second_bucket = self.TestHuman.filter(name__in=["Alice", "Bob"])

        with (
            patch.object(
                first_bucket._data.query,
                "sql_with_params",
                side_effect=AssertionError("first bucket signature compiled SQL"),
            ),
            patch.object(
                second_bucket._data.query,
                "sql_with_params",
                side_effect=AssertionError("second bucket signature compiled SQL"),
            ),
            CalculationRunContext(),
            self.assertNumQueries(1),
        ):
            first_names = sorted(human.name for human in first_bucket)
            second_names = sorted(human.name for human in second_bucket)

        self.assertEqual(first_names, ["Alice", "Bob"])
        self.assertEqual(second_names, ["Alice", "Bob"])

    def test_run_context_reuses_constructed_gm_filter_bucket(self):
        query_capability = self.TestHuman.Interface.require_capability("query")
        original_build_bucket = query_capability._build_bucket

        with (
            CalculationRunContext(),
            patch.object(
                query_capability,
                "_build_bucket",
                wraps=original_build_bucket,
            ) as mocked_build_bucket,
        ):
            first_bucket = self.TestHuman.filter(name="Alice")
            second_bucket = self.TestHuman.filter(name="Alice")

        self.assertEqual(mocked_build_bucket.call_count, 1)
        self.assertIsNot(first_bucket, second_bucket)
        first_bucket.filters["name"].append("mutated")
        self.assertEqual(second_bucket.filters["name"], ["Alice"])

    def test_run_context_distinguishes_recent_search_date_query_buckets(self):
        search_date = timezone.now()

        with CalculationRunContext():
            live_bucket = self.TestHuman.filter(name="Alice")
            dated_bucket = self.TestHuman.filter(
                name="Alice",
                search_date=search_date,
            )

        self.assertIsNone(live_bucket._search_date)
        self.assertEqual(dated_bucket._search_date, search_date)

    def test_pre_change_query_cache_is_cleared_after_mutation(self):
        pre_change_names = []

        def populate_pre_change_cache(sender, instance, action, **_kwargs):
            if sender is self.TestHuman and action == "update":
                pre_change_names.extend(
                    human.name for human in self.TestHuman.filter(name="Alice")
                )

        pre_data_change.connect(populate_pre_change_cache, weak=False)
        self.addCleanup(pre_data_change.disconnect, populate_pre_change_cache)

        with CalculationRunContext():
            self.test_human1.update(name="Alice Updated", ignore_permission=True)

            self.assertEqual(pre_change_names, ["Alice"])
            self.assertEqual(
                [human.name for human in self.TestHuman.filter(name="Alice")],
                [],
            )
            self.assertEqual(
                [human.name for human in self.TestHuman.filter(name="Alice Updated")],
                ["Alice Updated"],
            )

    def test_database_bucket_truthiness_does_not_count_rows(self):
        bucket = self.TestHuman.filter(name="Alice")

        with patch.object(
            bucket._data,
            "count",
            side_effect=AssertionError("truthiness should not count rows"),
        ):
            self.assertTrue(bucket)

    def test_orm_filter_reuses_payload_normalizer(self):
        from general_manager.interface.capabilities.orm_utils.payload_normalizer import (
            PayloadNormalizer,
        )

        if hasattr(self.TestHuman.Interface, "_payload_normalizer"):
            delattr(self.TestHuman.Interface, "_payload_normalizer")
        original_init = PayloadNormalizer.__init__
        init_count = 0

        def wrapped_init(normalizer, model):
            nonlocal init_count
            init_count += 1
            original_init(normalizer, model)

        with patch.object(PayloadNormalizer, "__init__", wrapped_init):
            self.TestHuman.filter(name="Alice")
            self.TestHuman.filter(name="Bob")

        self.assertEqual(init_count, 1)

    def test_live_queryset_with_search_date_uses_historical_lookup(self):
        old_buffer_seconds = self.TestHuman.Interface.historical_lookup_buffer_seconds
        self.TestHuman.Interface.historical_lookup_buffer_seconds = 0
        self.addCleanup(
            setattr,
            self.TestHuman.Interface,
            "historical_lookup_buffer_seconds",
            old_buffer_seconds,
        )

        human_id = self.test_human1.identification["id"]
        created_history = (
            self.TestHuman.Interface._model.history.filter(id=human_id)
            .order_by("history_date")
            .last()
        )
        self.assertIsNotNone(created_history)

        self.test_human1.update(name="Alice Updated", ignore_permission=True)
        live_queryset = self.TestHuman.Interface._model.objects.filter(pk=human_id)
        bucket = DatabaseBucket(
            live_queryset,
            self.TestHuman,
            search_date=created_history.history_date,
        )

        historical_manager = next(iter(bucket))

        self.assertEqual(historical_manager.name, "Alice")
        self.assertEqual(self.TestHuman(id=human_id).name, "Alice Updated")

    def test_trusted_hydration_preserves_custom_interface_initializer(self):
        self.CustomInitRecord.create(
            creator_id=None,
            name="Custom",
            ignore_permission=True,
        )

        manager = next(iter(self.CustomInitRecord.all()))

        self.assertTrue(manager._interface.initialized_by_interface)
        self.assertEqual(manager.name, "Custom")

    def test_deferred_queryset_rows_use_full_interface_load(self):
        model = self.TestHuman.Interface._model
        bucket = DatabaseBucket(
            model.objects.only("id").filter(pk=self.test_human1.identification["id"]),
            self.TestHuman,
        )

        manager = next(iter(bucket))

        self.assertEqual(manager._interface._instance.get_deferred_fields(), set())
        self.assertEqual(manager.name, "Alice")

    def test_soft_delete_behavior(self):
        """
        Verify that soft-deleted TestFamily instances are excluded from default queries but retrievable when including inactive records.

        Soft-delete the test family, assert it is not present in TestFamily.all(), and assert it is returned by TestFamily.filter(include_inactive=True).
        """
        family_id = self.test_family.identification["id"]

        # Soft delete the family
        self.test_family.delete(ignore_permission=True)

        with self.assertRaises(InvalidManagerStateError):
            _ = self.test_family.name
        with self.assertRaises(InvalidManagerStateError):
            dict(self.test_family)

        # Verify it is excluded from standard queries
        families_after_delete = self.TestFamily.all()
        self.assertNotIn(
            family_id,
            [f.identification["id"] for f in families_after_delete],
        )

        # Verify it can be accessed when including soft-deleted records
        all_families = self.TestFamily.filter(include_inactive=True)
        self.assertIn(
            family_id,
            [f.id for f in all_families],
        )

    def test_soft_deleted_manager_cannot_mutate_again(self):
        self.test_family.delete(ignore_permission=True)

        with self.assertRaises(InvalidManagerStateError):
            self.test_family.update(name="Mutated After Delete", ignore_permission=True)
        with self.assertRaises(InvalidManagerStateError):
            self.test_family.delete(ignore_permission=True)

    def test_update_invalidates_run_scoped_orm_identity_cache(self):
        with CalculationRunContext():
            cached_human = self.TestHuman(id=self.test_human1.id)
            self.assertEqual(cached_human.name, "Alice")

            cached_human.update(name="Alice Updated", ignore_permission=True)

            self.assertEqual(cached_human.name, "Alice Updated")
            self.assertEqual(
                self.TestHuman(id=self.test_human1.id).name,
                "Alice Updated",
            )

    def test_delete_invalidates_run_scoped_orm_identity_cache(self):
        human_id = self.test_human2.id

        with CalculationRunContext():
            cached_human = self.TestHuman(id=human_id)
            self.assertEqual(cached_human.name, "Bob")

            cached_human.delete(ignore_permission=True)

            with self.assertRaises(self.TestHuman.Interface._model.DoesNotExist):
                self.TestHuman(id=human_id)

    def test_soft_delete_invalidates_run_scoped_orm_identity_cache(self):
        family_id = self.test_family.id

        with CalculationRunContext():
            cached_family = self.TestFamily(id=family_id)
            self.assertTrue(cached_family.is_active)

            cached_family.delete(ignore_permission=True)

            self.assertFalse(self.TestFamily(id=family_id).is_active)

    def test_manager_connections(self):
        """
        Test that the many-to-many relationship between humans and families is correctly established.

        Verifies that the test family includes both test humans in its `humans_list` and that the family appears in a human's `families_list`.
        """
        humans = self.test_family.humans_list

        self.assertEqual(len(humans), 2)
        self.assertIn(self.test_human1, humans)
        self.assertIn(self.test_human2, humans)

    def test_history_property_returns_queryset_scoped_to_manager_id(self):
        self.test_human1.update(name="Alice Updated", ignore_permission=True)
        self.test_human2.update(name="Bob Updated", ignore_permission=True)

        history_ids = list(
            self.test_human1.history.order_by("history_date").values_list(
                "id", flat=True
            )
        )

        self.assertGreaterEqual(len(history_ids), 2)
        self.assertEqual(
            set(history_ids),
            {self.test_human1.identification["id"]},
        )

        self.assertIn(self.test_family, self.test_human1.families_list)

    def test_create_with_validation(self):
        """
        Test creating instances with various validation scenarios.
        """
        # Test successful creation with all fields
        human = self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            country=self.TestCountry.filter(code="DE").first(),
            ignore_permission=True,
        )
        self.assertEqual(human.name, "Charlie")
        self.assertEqual(human.country.code, "DE")

        # Test creation with None country (should be allowed)
        human_no_country = self.TestHuman.create(
            creator_id=None,
            name="David",
            country=None,
            ignore_permission=True,
        )
        self.assertEqual(human_no_country.name, "David")
        self.assertIsNone(human_no_country.country)

    def test_foreign_key_id_accessor_returns_raw_id_without_resolving_relation(self):
        """
        Foreign-key ID helpers expose the raw column value without building the related manager.
        """
        us_country_pk = self.TestCountry.Interface._model.objects.get(code="US").pk

        with patch.object(
            self.TestCountry,
            "__init__",
            side_effect=AssertionError("country relation should not resolve"),
        ):
            self.assertEqual(self.test_human1.country_id, us_country_pk)
            self.assertIsNone(self.test_human2.country_id)

        self.assertIs(self.TestHuman.country_id, int)

    def test_filter_operations(self):
        """
        Test various filter operations on GeneralManager instances.
        """
        # Test filtering by exact match
        us_humans = self.TestHuman.filter(country__code="US")
        self.assertEqual(len(us_humans), 1)
        self.assertEqual(us_humans[0].name, "Alice")

        # Test filtering by name
        alice = self.TestHuman.filter(name="Alice")
        self.assertEqual(len(alice), 1)
        self.assertEqual(alice[0].name, "Alice")

        # Test filtering with no results
        no_match = self.TestHuman.filter(name="NonExistent")
        self.assertEqual(len(no_match), 0)

        # Test filtering countries
        us_country = self.TestCountry.filter(code="US")
        self.assertEqual(len(us_country), 1)
        self.assertEqual(us_country[0].name, "United States")

    def test_filter_supports_snake_case_reverse_relation_aliases(self):
        snake_case_matches = self.ChangeRequest.filter(
            change_request_feasibility__id=self.change_request_feasibility.id
        )
        legacy_matches = self.ChangeRequest.filter(
            changerequestfeasibility__id=self.change_request_feasibility.id
        )
        explicit_related_name_matches = self.ChangeRequest.filter(
            change_request_reviews__id=self.change_request_review.id
        )

        self.assertEqual(len(snake_case_matches), 1)
        self.assertEqual(snake_case_matches[0].id, self.change_request.id)
        self.assertEqual(len(legacy_matches), 1)
        self.assertEqual(legacy_matches[0].id, self.change_request.id)
        self.assertEqual(len(explicit_related_name_matches), 1)
        self.assertEqual(explicit_related_name_matches[0].id, self.change_request.id)

    def test_reverse_one_to_one_relation_uses_snake_case_accessor(self):
        approval = self.change_request.change_request_approval

        self.assertEqual(approval.id, self.change_request_approval.id)
        self.assertEqual(approval.approved_by, "Reviewer")

    def test_missing_reverse_one_to_one_relation_returns_none(self):
        self.assertIsNone(self.other_change_request.change_request_approval)

    def test_exclude_supports_snake_case_reverse_relation_aliases(self):
        remaining = self.ChangeRequest.exclude(
            change_request_feasibility__id=self.change_request_feasibility.id
        )

        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].id, self.other_change_request.id)

    def test_filter_rejects_bucket_style_reverse_relation_roots(self):
        with self.assertRaises(FieldError):
            self.ChangeRequest.filter(
                change_request_feasibility_list__id=self.change_request_feasibility.id
            )

    def test_reverse_relation_bucket_uses_snake_case_attribute_name(self):
        bucket = self.change_request.change_request_feasibility_list

        self.assertEqual(len(bucket), 1)
        self.assertEqual(bucket[0].id, self.change_request_feasibility.id)

    def test_filter_with_search_date_returns_historical_state(self):
        base_time = timezone.now() - timedelta(days=10)

        with patch("django.utils.timezone.now", return_value=base_time):
            human = self.TestHuman.create(
                creator_id=None,
                name="Chrono Base",
                ignore_permission=True,
            )

        with patch(
            "django.utils.timezone.now", return_value=base_time + timedelta(hours=1)
        ):
            human.update(
                name="Chrono Updated",
                ignore_permission=True,
            )

        search_date = base_time + timedelta(minutes=30)
        with patch(
            "django.utils.timezone.now",
            return_value=search_date + timedelta(seconds=10),
        ):
            historical_bucket = self.TestHuman.filter(
                name="Chrono Base",
                search_date=search_date,
            )
            self.assertEqual(len(historical_bucket), 1)
            self.assertEqual(historical_bucket[0].name, "Chrono Base")

            empty_bucket = self.TestHuman.filter(
                name="Chrono Updated",
                search_date=search_date,
            )
            self.assertEqual(len(empty_bucket), 0)

    def test_first_and_last_operations(self):
        """
        Test first() and last() operations on QuerySets.
        """
        # Test first() on existing data
        first_human = self.TestHuman.all().first()
        self.assertIsNotNone(first_human)
        self.assertIn(first_human.name, ["Alice", "Bob"])

        # Test first() on filtered data
        us_human = self.TestHuman.filter(country__code="US").first()
        self.assertIsNotNone(us_human)
        self.assertEqual(us_human.name, "Alice")

        # Test first() on empty result
        empty_result = self.TestHuman.filter(name="NonExistent").first()
        self.assertIsNone(empty_result)

        # Test last() on countries
        last_country = self.TestCountry.all().last()
        self.assertIsNotNone(last_country)
        self.assertIn(last_country.code, ["US", "DE"])

    def test_all_operations(self):
        """
        Test all() operations and iteration over results.
        """
        # Test all humans
        all_humans = self.TestHuman.all()
        self.assertEqual(len(all_humans), 2)
        human_names = [h.name for h in all_humans]
        self.assertIn("Alice", human_names)
        self.assertIn("Bob", human_names)

        # Test all countries
        all_countries = self.TestCountry.all()
        self.assertEqual(len(all_countries), 2)
        country_codes = [c.code for c in all_countries]
        self.assertIn("US", country_codes)
        self.assertIn("DE", country_codes)

        # Test all families
        all_families = self.TestFamily.all()
        self.assertEqual(len(all_families), 1)
        self.assertEqual(all_families[0].name, "Smith Family")

    def test_update_operations(self):
        """
        Test update operations on GeneralManager instances.
        """
        # Test updating human name
        original_name = self.test_human1.name
        test_human1 = self.test_human1.update(
            name="Alice Updated", ignore_permission=True
        )
        self.assertIs(test_human1, self.test_human1)
        self.assertEqual(self.test_human1.name, "Alice Updated")
        self.assertEqual(test_human1.name, "Alice Updated")
        self.assertNotEqual(test_human1.name, original_name)

        # Test updating country relationship
        de_country = self.TestCountry.filter(code="DE").first()
        test_human2 = self.test_human2.update(
            country=de_country, ignore_permission=True
        )
        self.assertIs(test_human2, self.test_human2)
        self.assertEqual(self.test_human2.country.code, "DE")
        self.assertEqual(test_human2.country.code, "DE")

        # Test updating family name
        test_family = self.test_family.update(
            name="Updated Family Name", ignore_permission=True
        )
        self.assertIs(test_family, self.test_family)
        self.assertEqual(self.test_family.name, "Updated Family Name")
        self.assertEqual(test_family.name, "Updated Family Name")

    def test_delete_operations(self):
        """
        Verifies that deleting a human removes them from query results and related collections.

        Creates a human named "Charlie", deletes it, asserts the total human count decreases by one, and asserts the deleted human no longer appears among remaining humans or in any family memberships.
        """
        # Create additional test data for deletion
        test_human3 = self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            country=self.TestCountry.filter(code="DE").first(),
            ignore_permission=True,
        )

        # Test deleting a human
        human_count_before = len(self.TestHuman.all())
        test_human3.delete(ignore_permission=True)
        human_count_after = len(self.TestHuman.all())
        self.assertEqual(human_count_after, human_count_before - 1)
        self.assertIn("id", test_human3.identification)
        with self.assertRaises(InvalidManagerStateError):
            _ = test_human3.name
        with self.assertRaises(InvalidManagerStateError):
            dict(test_human3)

        # Verify the deleted human is not in any families
        remaining_humans = self.TestHuman.all()
        for human in remaining_humans:
            self.assertNotEqual(human.name, "Charlie")

    def test_get_historical_records_for_deleted_manager(self):
        historical_human = self.TestHuman.create(
            creator_id=self.User1.pk,
            name="Historian",
            ignore_permission=True,
        )
        human_id = historical_human.identification["id"]
        snapshot = timezone.now()

        historical_human.delete(
            creator_id=self.User1.pk,
            history_comment="cleanup",
            ignore_permission=True,
        )

        with self.assertRaises(self.TestHuman.Interface._model.DoesNotExist):  # type: ignore[attr-defined]
            self.TestHuman(id=human_id)

        with patch(
            "django.utils.timezone.now", return_value=snapshot + timedelta(seconds=10)
        ):
            with CaptureQueriesContext(connection) as queries:
                historical_view = self.TestHuman(id=human_id, search_date=snapshot)

        self.assertEqual(historical_view.name, "Historian")
        self.assertEqual(len(queries), HISTORICAL_READ_QUERY_BUDGETS["history_hit"])

    def test_get_historical_record_for_soft_deleted_manager(self):
        base_time = timezone.now() - timedelta(days=10)
        with patch("django.utils.timezone.now", return_value=base_time):
            historical_family = self.TestFamily.create(
                creator_id=None,
                name="Historical Family",
                ignore_permission=True,
            )

        family_id = historical_family.identification["id"]
        search_date = base_time + timedelta(hours=1)
        historical_family.delete(ignore_permission=True)

        with patch(
            "django.utils.timezone.now",
            return_value=search_date + timedelta(seconds=10),
        ):
            with CaptureQueriesContext(connection) as queries:
                historical_view = self.TestFamily(
                    id=family_id,
                    search_date=search_date,
                )

        self.assertEqual(historical_view.name, "Historical Family")
        self.assertEqual(len(queries), HISTORICAL_READ_QUERY_BUDGETS["history_hit"])

    def test_get_historical_record_after_delete_with_manager_lookup(self):
        historical_human = self.TestHuman.create(
            creator_id=self.User1.pk,
            name="Historian",
            ignore_permission=True,
        )
        human_id = historical_human.identification["id"]
        snapshot = timezone.now()

        historical_human.delete(
            creator_id=self.User1.pk,
            history_comment="cleanup",
            ignore_permission=True,
        )

        with patch(
            "django.utils.timezone.now", return_value=snapshot + timedelta(seconds=10)
        ):
            historical_view = self.TestHuman(id=human_id, search_date=snapshot)

        self.assertEqual(historical_view.name, "Historian")

    def test_get_historical_record_scopes_to_pk(self):
        base_time = timezone.now() - timedelta(days=10)

        with patch("django.utils.timezone.now", return_value=base_time):
            human_a = self.TestHuman.create(
                creator_id=None,
                name="Alice Base",
                ignore_permission=True,
            )
            human_b = self.TestHuman.create(
                creator_id=None,
                name="Bob Base",
                ignore_permission=True,
            )

        human_a_id = human_a.identification["id"]

        with patch(
            "django.utils.timezone.now", return_value=base_time + timedelta(hours=1)
        ):
            human_a = human_a.update(
                name="Alice Updated",
                ignore_permission=True,
            )

        with patch(
            "django.utils.timezone.now", return_value=base_time + timedelta(hours=2)
        ):
            human_b.update(
                name="Bob Updated",
                ignore_permission=True,
            )

        search_date = base_time + timedelta(hours=3)
        with patch(
            "django.utils.timezone.now",
            return_value=search_date + timedelta(seconds=10),
        ):
            with CaptureQueriesContext(connection) as queries:
                historical_view = self.TestHuman(
                    id=human_a_id,
                    search_date=search_date,
                )

        self.assertEqual(historical_view.name, "Alice Updated")
        self.assertEqual(historical_view.identification["id"], human_a_id)
        self.assertEqual(len(queries), HISTORICAL_READ_QUERY_BUDGETS["history_hit"])

    def test_get_data_raises_when_historical_missing_for_active_instance(self):
        base_time = timezone.now() - timedelta(days=10)

        with patch("django.utils.timezone.now", return_value=base_time):
            human = self.TestHuman.create(
                creator_id=None,
                name="Time Traveler",
                ignore_permission=True,
            )

        search_date = base_time - timedelta(days=1)

        with patch(
            "django.utils.timezone.now",
            return_value=base_time + timedelta(seconds=10),
        ):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(self.TestHuman.Interface._model.DoesNotExist):  # type: ignore[attr-defined]
                    self.TestHuman(
                        id=human.identification["id"],
                        search_date=search_date,
                    )
        self.assertEqual(len(queries), HISTORICAL_READ_QUERY_BUDGETS["history_miss"])

    def test_get_data_history_miss_for_missing_live_row_keeps_two_queries(self):
        search_date = timezone.now() - timedelta(days=10)
        missing_pk = 987654321

        with patch(
            "django.utils.timezone.now",
            return_value=search_date + timedelta(seconds=10),
        ):
            with CaptureQueriesContext(connection) as queries:
                with self.assertRaises(self.TestHuman.Interface._model.DoesNotExist):  # type: ignore[attr-defined]
                    self.TestHuman(id=missing_pk, search_date=search_date)

        self.assertEqual(len(queries), HISTORICAL_READ_QUERY_BUDGETS["history_miss"])

    def test_recent_search_date_keeps_one_live_query(self):
        human = self.TestHuman.create(
            creator_id=None,
            name="Recent",
            ignore_permission=True,
        )
        search_date = timezone.now()

        with patch(
            "django.utils.timezone.now",
            return_value=search_date + timedelta(seconds=1),
        ):
            with CaptureQueriesContext(connection) as queries:
                manager = self.TestHuman(
                    id=human.identification["id"],
                    search_date=search_date,
                )

        self.assertEqual(manager.name, "Recent")
        self.assertEqual(len(queries), HISTORICAL_READ_QUERY_BUDGETS["recent_live"])

    def test_custom_interface_dispatch_keeps_live_first_fallback(self):
        base_time = timezone.now() - timedelta(days=1)
        with patch("django.utils.timezone.now", return_value=base_time):
            human = self.TestHuman.create(
                creator_id=None,
                name="Custom History",
                ignore_permission=True,
            )
        search_date = base_time + timedelta(hours=1)
        interface_cls = self.TestHuman.Interface
        original_get_capability_handler = interface_cls.get_capability_handler

        def custom_get_capability_handler(_cls, name):
            return original_get_capability_handler(name)

        with patch.object(
            interface_cls,
            "get_capability_handler",
            classmethod(custom_get_capability_handler),
        ):
            with patch(
                "django.utils.timezone.now",
                return_value=search_date + timedelta(seconds=10),
            ):
                with CaptureQueriesContext(connection) as queries:
                    manager = self.TestHuman(
                        id=human.identification["id"],
                        search_date=search_date,
                    )

        self.assertEqual(manager.name, "Custom History")
        self.assertEqual(len(queries), 2)

    def test_old_history_hit_honors_configured_database_alias(self):
        human = self.TestHuman.create(
            creator_id=None,
            name="Aliased History",
            ignore_permission=True,
        )
        search_date = timezone.now()
        human.delete(ignore_permission=True)
        interface_cls = self.TestHuman.Interface
        original_database = interface_cls.database
        interface_cls.database = "default"
        try:
            with patch(
                "django.utils.timezone.now",
                return_value=search_date + timedelta(seconds=10),
            ):
                with CaptureQueriesContext(connection) as queries:
                    manager = self.TestHuman(
                        id=human.identification["id"],
                        search_date=search_date,
                    )
        finally:
            interface_cls.database = original_database

        self.assertEqual(manager.name, "Aliased History")
        self.assertEqual(len(queries), 1)

    def test_old_history_read_reuses_run_context_cache(self):
        base_time = timezone.now() - timedelta(days=10)
        with patch("django.utils.timezone.now", return_value=base_time):
            human = self.TestHuman.create(
                creator_id=None,
                name="Cached History",
                ignore_permission=True,
            )
        search_date = base_time + timedelta(hours=1)

        with patch(
            "django.utils.timezone.now",
            return_value=search_date + timedelta(seconds=10),
        ):
            with CalculationRunContext(), CaptureQueriesContext(connection) as queries:
                first = self.TestHuman(
                    id=human.identification["id"],
                    search_date=search_date,
                )
                second = self.TestHuman(
                    id=human.identification["id"],
                    search_date=search_date,
                )

        self.assertEqual(first.name, "Cached History")
        self.assertEqual(second.name, "Cached History")
        self.assertEqual(len(queries), 1)

    def test_history_read_cache_clears_after_mutation(self):
        base_time = timezone.now() - timedelta(days=10)
        with patch("django.utils.timezone.now", return_value=base_time):
            human = self.TestHuman.create(
                creator_id=None,
                name="Before Mutation",
                ignore_permission=True,
            )
        search_date = base_time + timedelta(hours=1)

        with (
            patch(
                "django.utils.timezone.now",
                return_value=search_date + timedelta(seconds=10),
            ),
            CalculationRunContext(),
            CaptureQueriesContext(connection) as queries,
        ):
            first = self.TestHuman(
                id=human.identification["id"],
                search_date=search_date,
            )
            human.update(name="After Mutation", ignore_permission=True)
            second = self.TestHuman(
                id=human.identification["id"],
                search_date=search_date,
            )

        self.assertEqual(first.name, "Before Mutation")
        self.assertEqual(second.name, "Before Mutation")
        self.assertGreaterEqual(len(queries), 2)

    def test_bucket_operations(self):
        """
        Verify many-to-many relationship bucket behavior: read bucket contents, add a related item through the bucket update, and confirm the forward and reverse relations reflect the change.
        """
        # Test accessing humans_list bucket
        humans_bucket = self.test_family.humans_list
        self.assertEqual(len(humans_bucket), 2)

        # Test accessing families_list bucket
        families_bucket = self.test_human1.families_list
        self.assertEqual(len(families_bucket), 1)
        self.assertEqual(families_bucket[0].name, "Smith Family")

        # Test adding to bucket
        new_human = self.TestHuman.create(
            creator_id=None,
            name="Eve",
            ignore_permission=True,
        )

        # Add human to family
        updated_family = self.test_family.update(
            humans=[*self.test_family.humans_list, new_human],
            ignore_permission=True,
        )
        self.assertIs(updated_family, self.test_family)
        updated_humans = updated_family.humans_list
        self.assertEqual(len(self.test_family.humans_list), 3)
        self.assertEqual(len(updated_humans), 3)
        self.assertIn(new_human, updated_humans)

        # Verify reverse relationship
        self.assertIn(self.test_family, new_human.families_list)

    def test_dict_conversion(self):
        """
        Test conversion of GeneralManager instances to dictionaries.
        """
        # Test human to dict conversion
        human_dict = dict(self.test_human1)
        self.assertIn("name", human_dict)
        self.assertIn("country", human_dict)
        self.assertEqual(human_dict["name"], self.test_human1.name)

        # Test country to dict conversion
        country = self.TestCountry.filter(code="US").first()
        country_dict = dict(country)
        self.assertIn("code", country_dict)
        self.assertIn("name", country_dict)
        self.assertEqual(country_dict["code"], "US")
        self.assertEqual(country_dict["name"], "United States")

        # Test family to dict conversion
        family_dict = dict(self.test_family)
        self.assertIn("name", family_dict)
        self.assertEqual(family_dict["name"], self.test_family.name)

    def test_readonly_interface_sync(self):
        """
        Test ReadOnlyInterface sync_data functionality.
        """
        # Test that sync_data creates the expected country records

        # Clear existing data and resync
        self.TestCountry.Interface._model._meta.model.objects.all().delete()
        run_registered_startup_hooks(interfaces=[self.TestCountry.Interface])

        countries_after_sync = self.TestCountry.all()
        self.assertEqual(len(countries_after_sync), 2)

        # Verify specific country data
        us_country = self.TestCountry.filter(code="US").first()
        de_country = self.TestCountry.filter(code="DE").first()

        self.assertIsNotNone(us_country)
        self.assertIsNotNone(de_country)
        self.assertEqual(us_country.name, "United States")
        self.assertEqual(de_country.name, "Germany")

    def test_foreign_key_relationships(self):
        """
        Test foreign key relationships and related field access.
        """
        # Test accessing country from human
        self.assertEqual(self.test_human1.country.code, "US")
        self.assertEqual(self.test_human1.country.name, "United States")

        # Test reverse relationship (humans from country)
        us_country = self.TestCountry.filter(code="US").first()
        related_humans = us_country.humans_list.all()
        self.assertEqual(len(related_humans), 1)
        self.assertEqual(related_humans[0].name, "Alice")

        # Test null country relationship
        self.assertIsNone(self.test_human2.country)

    def test_many_to_many_relationships(self):
        """
        Test many-to-many relationships between humans and families.
        """
        # Create additional family for testing
        self.TestFamily.create(
            creator_id=None,
            name="Johnson Family",
            humans=[self.test_human2],
            ignore_permission=True,
        )

        # Verify relationships
        bob_families = self.test_human2.families_list
        self.assertEqual(len(bob_families), 2)
        family_names = [f.name for f in bob_families]
        self.assertIn("Smith Family", family_names)
        self.assertIn("Johnson Family", family_names)

        # Test that family can have multiple humans
        smith_family_humans = self.test_family.humans_list
        self.assertEqual(len(smith_family_humans), 2)
        human_names = [h.name for h in smith_family_humans]
        self.assertIn("Alice", human_names)
        self.assertIn("Bob", human_names)

    def test_multiple_many_to_many_fields_to_same_manager_stay_independent(self):
        """
        Verify direct M2M buckets scope reads to their own relation field.
        """
        supplier_1 = self.TestSupplier.create(
            creator_id=None,
            name="Supplier 1",
            ignore_permission=True,
        )
        supplier_2 = self.TestSupplier.create(
            creator_id=None,
            name="Supplier 2",
            ignore_permission=True,
        )
        supplier_3 = self.TestSupplier.create(
            creator_id=None,
            name="Supplier 3",
            ignore_permission=True,
        )
        status = self.TestProjectQualityStatus.create(
            creator_id=None,
            name="Quality",
            ignore_permission=True,
        )

        status.update(
            processing_supplier_list=[supplier_1, supplier_2],
            standard_part_supplier_list=[supplier_2],
            direct_delivery_supplier_list=[supplier_3],
            ignore_permission=True,
        )

        self.assertEqual(
            list(status.processing_supplier_list), [supplier_1, supplier_2]
        )
        self.assertEqual(list(status.standard_part_supplier_list), [supplier_2])
        self.assertEqual(list(status.direct_delivery_supplier_list), [supplier_3])

        status.update(
            direct_delivery_supplier_list=[supplier_1],
            ignore_permission=True,
        )

        self.assertEqual(
            list(status.processing_supplier_list), [supplier_1, supplier_2]
        )
        self.assertEqual(list(status.standard_part_supplier_list), [supplier_2])
        self.assertEqual(list(status.direct_delivery_supplier_list), [supplier_1])

    def test_many_to_many_bucket_uses_direct_relation_without_reverse_metadata(self):
        """
        Verify direct many-to-many buckets do not depend on reverse metadata scans.
        """
        self.TestHuman.create(
            creator_id=None,
            name="Unrelated",
            ignore_permission=True,
        )
        original_get_fields = self.TestHuman.Interface._model._meta.get_fields

        def get_fields_without_family_reverse(*args, **kwargs):
            return [
                field
                for field in original_get_fields(*args, **kwargs)
                if getattr(field, "related_model", None)
                != self.TestFamily.Interface._model
            ]

        with patch.object(
            self.TestHuman.Interface._model._meta,
            "get_fields",
            side_effect=get_fields_without_family_reverse,
        ):
            self.TestFamily.Interface._field_descriptors = None
            if "_attributes" in vars(self.TestFamily):
                delattr(self.TestFamily, "_attributes")

            humans = self.test_family.humans_list

        self.assertEqual(len(humans), 2)
        self.assertIn(self.test_human1, humans)
        self.assertIn(self.test_human2, humans)
        self.TestFamily.Interface._field_descriptors = None
        if "_attributes" in vars(self.TestFamily):
            delattr(self.TestFamily, "_attributes")
        GeneralManagerMeta.ensure_attributes_initialized(self.TestFamily)

    def test_edge_cases_and_error_handling(self):
        """
        Test edge cases and error handling scenarios.
        """
        # Test filtering with invalid field
        with self.assertRaises(FieldError):
            self.TestHuman.filter(nonexistent_field="value")

        # Test empty string names
        self.assertRaises(
            ValidationError,
            lambda: self.TestHuman.create(
                creator_id=None,
                name="",
                ignore_permission=True,
            ),
        )

        # Test creating family with empty humans list
        empty_family = self.TestFamily.create(
            creator_id=None,
            name="Empty Family",
            humans=[],
            ignore_permission=True,
        )
        self.assertEqual(len(empty_family.humans_list), 0)

    def test_permissions_and_creator_tracking(self):
        """
        Test permission system and creator tracking functionality.
        """
        password = get_random_string(12)
        User.objects.create_user(
            username="testuser",
            password=password,
            id=1,
        )
        # Test creation with creator_id
        human_with_creator = self.TestHuman.create(
            creator_id=1,
            name="Frank",
            ignore_permission=True,
        )
        self.assertEqual(human_with_creator.name, "Frank")

        # Test that ignore_permission parameter works
        family_with_permission = self.TestFamily.create(
            creator_id=None,
            name="Permission Family",
            humans=[human_with_creator],
            ignore_permission=True,
        )
        self.assertEqual(family_with_permission.name, "Permission Family")

    def test_queryset_chaining(self):
        """
        Test chaining of QuerySet operations.
        """
        # Create additional test data
        self.TestHuman.create(
            creator_id=None,
            name="Grace",
            country=self.TestCountry.filter(code="DE").first(),
            ignore_permission=True,
        )

        # Test chaining filters
        de_humans = self.TestHuman.filter(country__code="DE").filter(
            name__startswith="G"
        )
        self.assertEqual(len(de_humans), 1)
        self.assertEqual(de_humans[0].name, "Grace")

        # Test complex filtering
        humans_with_country = self.TestHuman.filter(country__isnull=False)
        self.assertEqual(len(humans_with_country), 2)  # Alice and Grace

        humans_without_country = self.TestHuman.filter(country__isnull=True)
        self.assertEqual(len(humans_without_country), 1)  # Bob

    def test_data_integrity_and_constraints(self):
        """
        Verify database constraints: uniqueness of country codes and expected relationships to countries.

        Asserts that all TestCountry.code values are unique and that querying TestHuman for the country with code "US" returns the expected related human ("Alice").
        """
        # Test unique constraint on country code
        countries = self.TestCountry.all()
        country_codes = [c.code for c in countries]
        self.assertEqual(
            len(country_codes), len(set(country_codes))
        )  # All codes should be unique

        # Test cascade deletion behavior
        country_to_delete = self.TestCountry.filter(code="US").first()

        # Since countries are read-only, test that related humans handle country deletion gracefully
        # This is more of a constraint verification than actual deletion
        related_humans = self.TestHuman.filter(country=country_to_delete)
        self.assertEqual(len(related_humans), 1)
        self.assertEqual(related_humans[0].name, "Alice")

    def test_group_by_foreign_key(self):
        grouped = self.TestHuman.all().group_by("country")

        self.assertEqual(grouped.count(), 2)
        groups_by_country = {
            group.country.code if group.country is not None else None: group
            for group in grouped
        }
        self.assertEqual(groups_by_country["US"]._data.count(), 1)
        self.assertEqual(groups_by_country[None]._data.count(), 1)

    def test_group_by_run_context_avoids_per_group_queries(self):
        with CalculationRunContext(), self.assertNumQueries(1):
            grouped = self.TestHuman.all().group_by("name")
            counts = [group._data.count() for group in grouped]

        self.assertEqual(sorted(counts), [1, 1])

    def test_python_property_snapshot_clears_on_manager_mutation(self):
        with CalculationRunContext():
            bucket = self.TestHuman.all().filter(name_length_python__gte=5)
            self.assertEqual([human.name for human in bucket], ["Alice"])
            self.test_human1.update(name="Alicia", ignore_permission=True)
            with self.assertNumQueries(1):
                refreshed_names = [human.name for human in bucket]

        self.assertEqual(refreshed_names, ["Alicia"])

    def test_group_by_one_to_one_relation(self):
        first_request = self.ChangeRequest.create(
            title="First",
            ignore_permission=True,
        )
        second_request = self.ChangeRequest.create(
            title="Second",
            ignore_permission=True,
        )
        first_approval = self.ChangeRequestApproval.create(
            approved_by="Alice",
            change_request=first_request,
            ignore_permission=True,
        )
        second_approval = self.ChangeRequestApproval.create(
            approved_by="Bob",
            change_request=second_request,
            ignore_permission=True,
        )

        grouped = self.ChangeRequestApproval.filter(
            id__in=[first_approval.id, second_approval.id]
        ).group_by("change_request")

        self.assertEqual(grouped.count(), 2)
        self.assertEqual(
            {group.change_request.title for group in grouped},
            {"First", "Second"},
        )
        self.assertTrue(all(group._data.count() == 1 for group in grouped))

    def test_group_by_reverse_one_to_one_relation(self):
        request_without_approval = self.ChangeRequest.create(
            title="Without approval",
            ignore_permission=True,
        )

        grouped = self.ChangeRequest.filter(
            id__in=[self.change_request.id, request_without_approval.id]
        ).group_by("change_request_approval")

        self.assertEqual(grouped.count(), 2)
        groups_by_approval = {
            group.change_request_approval.approved_by
            if group.change_request_approval is not None
            else None: group
            for group in grouped
        }
        self.assertEqual(
            groups_by_approval[self.change_request_approval.approved_by]._data.count(),
            1,
        )
        self.assertEqual(groups_by_approval[None]._data.count(), 1)

    def test_factory_creates_instances(self) -> None:
        """
        Test that the Factory class creates instances of the GeneralManager with correct default attributes.
        """
        factory_instance = self.TestHuman.Factory.create_batch(1)[0]
        self.assertIsInstance(factory_instance, self.TestHuman)
        stored = self.TestHuman.Interface._model.objects.get(
            pk=factory_instance.identification["id"]
        )
        self.assertIsNotNone(stored)


class ReverseRelationFilterAliasIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class ChangeRequest(GeneralManager):
            title: str

            class Interface(DatabaseInterface):
                title = models.CharField(max_length=100)

        class ChangeRequestFeasibility(GeneralManager):
            summary: str
            change_request: ChangeRequest
            change_request_team_list: Bucket[ChangeRequestTeam]

            class Interface(DatabaseInterface):
                summary = models.CharField(max_length=100)
                change_request = models.ForeignKey(
                    "general_manager.ChangeRequest",
                    on_delete=models.CASCADE,
                )

        class ChangeRequestTeam(GeneralManager):
            name: str
            size: int
            change_request_feasibility: ChangeRequestFeasibility

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)
                size = models.IntegerField(default=0)
                change_request_feasibility = models.ForeignKey(
                    "general_manager.ChangeRequestFeasibility",
                    on_delete=models.CASCADE,
                )

        class ReviewQueue(GeneralManager):
            name: str

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=100)

        class ReviewQueueEntry(GeneralManager):
            summary: str
            review_queue: ReviewQueue

            class Interface(DatabaseInterface):
                summary = models.CharField(max_length=100)
                review_queue = models.ForeignKey(
                    "general_manager.ReviewQueue",
                    on_delete=models.CASCADE,
                    related_name="review_queue_entries",
                )

        cls.ChangeRequest = ChangeRequest
        cls.ChangeRequestFeasibility = ChangeRequestFeasibility
        cls.ChangeRequestTeam = ChangeRequestTeam
        cls.ReviewQueue = ReviewQueue
        cls.ReviewQueueEntry = ReviewQueueEntry
        cls.general_manager_classes = [
            ChangeRequest,
            ChangeRequestFeasibility,
            ChangeRequestTeam,
            ReviewQueue,
            ReviewQueueEntry,
        ]

    def setUp(self):
        super().setUp()
        self.change_request = self.ChangeRequest.create(
            title="Primary request",
            ignore_permission=True,
        )
        self.other_change_request = self.ChangeRequest.create(
            title="Secondary request",
            ignore_permission=True,
        )
        self.feasibility = self.ChangeRequestFeasibility.create(
            summary="Feasible",
            change_request=self.change_request,
            ignore_permission=True,
        )
        self.other_feasibility = self.ChangeRequestFeasibility.create(
            summary="Also feasible",
            change_request=self.other_change_request,
            ignore_permission=True,
        )
        self.team = self.ChangeRequestTeam.create(
            name="Core team",
            size=6,
            change_request_feasibility=self.feasibility,
            ignore_permission=True,
        )
        self.small_team = self.ChangeRequestTeam.create(
            name="Support team",
            size=2,
            change_request_feasibility=self.other_feasibility,
            ignore_permission=True,
        )
        self.review_queue = self.ReviewQueue.create(
            name="Primary queue",
            ignore_permission=True,
        )
        self.review_queue_entry = self.ReviewQueueEntry.create(
            summary="Entry",
            review_queue=self.review_queue,
            ignore_permission=True,
        )

    def test_filter_accepts_snake_case_reverse_relation_alias(self):
        aliased = self.ChangeRequest.filter(
            change_request_feasibility__id=self.feasibility.id
        )
        legacy = self.ChangeRequest.filter(
            changerequestfeasibility__id=self.feasibility.id
        )

        self.assertEqual([item.id for item in aliased], [self.change_request.id])
        self.assertEqual([item.id for item in aliased], [item.id for item in legacy])

    def test_exclude_accepts_snake_case_reverse_relation_alias(self):
        remaining = self.ChangeRequest.exclude(
            change_request_feasibility__id=self.feasibility.id
        )

        self.assertEqual(
            [item.id for item in remaining], [self.other_change_request.id]
        )

    def test_filter_accepts_nested_snake_case_reverse_relation_aliases(self):
        bucket = self.ChangeRequest.filter(
            change_request_feasibility__change_request_team__size__gte=5
        )

        self.assertEqual([item.id for item in bucket], [self.change_request.id])

    def test_filter_rejects_list_suffix_reverse_relation_alias(self):
        with self.assertRaises(FieldError):
            self.ChangeRequest.filter(
                change_request_feasibility_list__id=self.feasibility.id
            )

    def test_filter_preserves_explicit_related_name_root(self):
        queue_bucket = self.ReviewQueue.filter(
            review_queue_entries__id=self.review_queue_entry.id
        )

        self.assertEqual([item.id for item in queue_bucket], [self.review_queue.id])
