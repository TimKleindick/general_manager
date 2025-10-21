from datetime import date, datetime, timedelta
from django.utils import timezone
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.manager import GeneralManager, Input
from django.db.models.fields import CharField, IntegerField, DateField, DateTimeField
from typing import ClassVar
from general_manager.measurement import MeasurementField, Measurement
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.api.property import graphQlProperty
from general_manager.permission.managerBasedPermission import ManagerBasedPermission


class CachingTestCase(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Set up test manager classes for project and commercials with computed budget properties.

        Defines and assigns two `GeneralManager` subclasses for use in caching tests:
        - `TestProjectForCommercials`: Represents a project with name, number, budget, and actual costs.
        - `TestCommercials`: References a project and exposes computed properties for budget left, budget used (as percent), and over-budget status.

        Stores references to these classes as class attributes for use in test methods.
        """
        super().setUpClass()

        class TestProjectForCommercials(GeneralManager):
            name: str
            number: int | None
            budget: Measurement
            actual_costs: Measurement
            start_date: date
            completion_at: datetime

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True, blank=True)
                budget = MeasurementField(
                    base_unit="EUR",
                )
                actual_costs = MeasurementField(
                    base_unit="EUR",
                )
                start_date = DateField()
                completion_at = DateTimeField()

                class Meta:
                    app_label = "general_manager"

            class Permission(ManagerBasedPermission):
                __create__: ClassVar[list[str]] = ["public"]

        class TestCommercials(GeneralManager):
            project: TestProjectForCommercials

            class Interface(CalculationInterface):
                project = Input(
                    TestProjectForCommercials,
                    possible_values=lambda: TestProjectForCommercials.all(),
                )

            @graphQlProperty
            def budget_left(self) -> Measurement:
                """
                Compute the project's remaining budget.
                
                Returns:
                    Measurement: The project's budget minus its actual costs.
                """
                return self.project.budget - self.project.actual_costs

            @graphQlProperty
            def budget_used(self) -> Measurement:
                """
                Compute the project's used budget as a percentage.
                
                Returns:
                    Measurement: Fraction of the project's budget that has been consumed, expressed as a percentage.
                """
                return (self.project.actual_costs / self.project.budget).to("percent")

            @graphQlProperty
            def is_over_budget(self) -> bool:
                """
                Return True if the project's actual costs exceed its budget, otherwise False.
                """
                return self.project.actual_costs > self.project.budget

            @graphQlProperty
            def has_duplicate_name(self) -> bool:
                """
                Determine whether another project has the same name as this instance.
                
                Returns:
                    True if more than one project exists with the same name, False otherwise.
                """
                matching_count = TestProjectForCommercials.filter(
                    name=self.project.name
                ).count()
                return matching_count > 1

            @graphQlProperty
            def other_project_count(self) -> int:
                """
                Return the count of projects whose `number` differs from this instance's project's `number`.
                
                Returns:
                    int: Number of projects with a different `number` than this instance's project.
                """
                return TestProjectForCommercials.exclude(
                    number=self.project.number
                ).count()

            @graphQlProperty
            def has_budget_buffer(self) -> bool:
                """
                Indicates whether the project has a positive remaining budget.
                
                Returns:
                    `true` if the project's remaining budget is greater than zero EUR, `false` otherwise.
                """
                return self.budget_left > Measurement(0, "EUR")

            @graphQlProperty
            def similar_name_count(self) -> int:
                """
                Count projects whose names contain the first word of this instance's associated project's name.
                
                Returns:
                    int: Number of projects whose `name` contains that first word.
                """
                search_term = self.project.name.split()[0]
                return TestProjectForCommercials.filter(
                    name__contains=search_term
                ).count()

            @graphQlProperty
            def active_project_count(self) -> int:
                """
                Count all active projects to ensure deactivation triggers cache invalidation.
                """
                return TestProjectForCommercials.filter(is_active=True).count()

            @graphQlProperty
            def same_name_excluding_self(self) -> int:
                """
                Count projects that have the same name as the current project's name, excluding the current project by its number.
                
                Returns:
                    int: Number of matching projects excluding the current project.
                """
                return (
                    TestProjectForCommercials.filter(name=self.project.name)
                    .exclude(number=self.project.number)
                    .count()
                )

            @graphQlProperty
            def project_keyword_number_range_count(self) -> int:
                """
                Count projects whose name contains "Project" and whose number is between 1 and 3 inclusive.
                
                Returns:
                    count (int): Number of projects matching the filters.
                """
                return TestProjectForCommercials.filter(
                    name__contains="Project",
                    number__gte=1,
                    number__lte=3,
                    number__in=[1, 2, 3],
                ).count()

            @graphQlProperty
            def recent_project_window_count(self) -> int:
                """
                Count projects whose start_date falls within seven days before or after this instance's project.start_date and whose completion_at is no later than seven days after this instance's project.completion_at.
                
                Returns:
                	int: Number of projects matching the date window and completion threshold.
                """
                window_start = (self.project.start_date - timedelta(days=7)).isoformat()
                window_end = (self.project.start_date + timedelta(days=7)).isoformat()
                completion_threshold = (
                    self.project.completion_at + timedelta(days=7)
                ).isoformat()
                return TestProjectForCommercials.filter(
                    start_date__gte=window_start,
                    start_date__lte=window_end,
                    completion_at__lte=completion_threshold,
                ).count()

            @graphQlProperty
            def staged_bucket_count(self) -> int:
                """
                Count TestProjectForCommercials that match a specific sequence of chained filters relative to this instance's project.
                
                Applies a contains filter on the name, a minimum-number filter, an exclusion by actual_costs, and a start_date upper bound based on this instance's project start_date.
                
                Returns:
                    count (int): Number of projects matching the chained filter and exclude criteria.
                """
                bucket = TestProjectForCommercials.filter(name__contains="Project")
                bucket = bucket.filter(number__gte=1)
                bucket = bucket.exclude(actual_costs__gte=Measurement(1000, "EUR"))
                bucket = bucket.filter(
                    start_date__lte=self.project.start_date.isoformat()
                )
                return bucket.count()

        cls.TestProject = TestProjectForCommercials
        cls.TestCommercials = TestCommercials
        cls.general_manager_classes = [TestProjectForCommercials, TestCommercials]

    def setUp(self) -> None:
        """
        Creates three test project instances with predefined budgets and actual costs for use in caching tests.
        """
        super().setUp()

        self.project1 = self.TestProject.create(
            name="Test Project",
            number=1,
            budget=Measurement(1000, "EUR"),
            actual_costs=Measurement(200, "EUR"),
            start_date=date(2024, 1, 1),
            completion_at=timezone.make_aware(datetime(2024, 1, 10, 12, 0)),
        )
        self.project2 = self.TestProject.create(
            name="Another Project",
            number=2,
            budget=Measurement(2000, "EUR"),
            actual_costs=Measurement(500, "EUR"),
            start_date=date(2024, 1, 5),
            completion_at=timezone.make_aware(datetime(2024, 1, 15, 12, 0)),
        )
        self.project3 = self.TestProject.create(
            name="Third Project",
            number=3,
            budget=Measurement(1500, "EUR"),
            actual_costs=Measurement(1800, "EUR"),
            start_date=date(2023, 12, 1),
            completion_at=timezone.make_aware(datetime(2024, 1, 20, 12, 0)),
        )

    def test_budget_left(self):
        """
        Tests that the `budget_left` property on `TestCommercials` instances returns the correct value and verifies caching behavior by asserting cache misses on first access and cache hits on subsequent accesses.
        """
        commercials1 = self.TestCommercials(project=self.project1)
        commercials2 = self.TestCommercials(project=self.project2)
        commercials3 = self.TestCommercials(project=self.project3)

        self.assertEqual(commercials1.budget_left, Measurement(800, "EUR"))
        self.assertCacheMiss()
        self.assertEqual(commercials2.budget_left, Measurement(1500, "EUR"))
        self.assertCacheMiss()
        self.assertEqual(commercials3.budget_left, Measurement(-300, "EUR"))
        self.assertCacheMiss()

        for commercials in self.TestCommercials.all():
            self.assertTrue(commercials.budget_left)
            self.assertCacheHit()

    def test_caching_each_attribute_individually(self):
        """
        Test that each computed property on TestCommercials is cached independently.

        Verifies that accessing the `budget_used` property on different TestCommercials instances results in cache misses on first access and cache hits on subsequent accesses. Also checks that accessing `budget_left` after `budget_used` results in a cache miss, confirming that caching is per attribute.
        """
        commercials1 = self.TestCommercials(project=self.project1)
        commercials2 = self.TestCommercials(project=self.project2)
        commercials3 = self.TestCommercials(project=self.project3)

        self.assertEqual(commercials1.budget_used, Measurement(20, "percent"))
        self.assertCacheMiss()
        self.assertEqual(commercials2.budget_used, Measurement(25, "percent"))
        self.assertCacheMiss()
        self.assertEqual(
            commercials3.budget_used,
            Measurement(120, "percent"),
        )
        self.assertCacheMiss()

        for commercials in self.TestCommercials.all():
            self.assertTrue(commercials.budget_used)
            self.assertCacheHit()
            self.assertTrue(commercials.budget_left)
            self.assertCacheMiss()

    def test_cache_invalidation_after_related_update(self):
        """
        Ensure cached values are invalidated when a dependent project changes while unrelated caches remain intact.
        """
        commercials1 = self.TestCommercials(project=self.project1)
        commercials2 = self.TestCommercials(project=self.project2)

        self.assertEqual(commercials1.budget_left, Measurement(800, "EUR"))
        self.assertCacheMiss()
        self.assertEqual(commercials2.budget_left, Measurement(1500, "EUR"))
        self.assertCacheMiss()

        self.assertEqual(commercials1.budget_left, Measurement(800, "EUR"))
        self.assertCacheHit()
        self.assertEqual(commercials2.budget_left, Measurement(1500, "EUR"))
        self.assertCacheHit()

        self.project1 = self.project1.update(
            actual_costs=Measurement(600, "EUR"), ignore_permission=True
        )

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertEqual(refreshed_commercials1.budget_left, Measurement(400, "EUR"))
        self.assertCacheMiss()

        self.assertEqual(refreshed_commercials1.budget_left, Measurement(400, "EUR"))
        self.assertCacheHit()

        self.assertEqual(commercials2.budget_left, Measurement(1500, "EUR"))
        self.assertCacheHit()

    def test_filter_dependency_invalidation(self):
        """
        Verify that caches depending on ``filter`` lookups are invalidated when matching data changes.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertFalse(commercials1.has_duplicate_name)
        self.assertCacheMiss()
        self.assertFalse(commercials1.has_duplicate_name)
        self.assertCacheHit()

        self.project2 = self.project2.update(
            name="Test Project", ignore_permission=True
        )

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertTrue(refreshed_commercials1.has_duplicate_name)
        self.assertCacheMiss()

        self.assertTrue(refreshed_commercials1.has_duplicate_name)
        self.assertCacheHit()

    def test_exclude_dependency_invalidation(self):
        """
        Confirm that caches depending on ``exclude`` lookups are invalidated when excluded values change.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.other_project_count, 2)
        self.assertCacheMiss()
        self.assertEqual(commercials1.other_project_count, 2)
        self.assertCacheHit()

        self.project2 = self.project2.update(number=1, ignore_permission=True)

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertEqual(refreshed_commercials1.other_project_count, 1)
        self.assertCacheMiss()

        self.assertEqual(refreshed_commercials1.other_project_count, 1)
        self.assertCacheHit()

    def test_chained_graphql_properties_invalidation(self):
        """
        Ensure properties that depend on other cached properties are invalidated correctly.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertTrue(commercials1.has_budget_buffer)
        self.assertCacheMiss()
        self.assertTrue(commercials1.has_budget_buffer)
        self.assertCacheHit()
        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertTrue(refreshed_commercials1.has_budget_buffer)
        self.assertCacheHit()

        self.project1 = self.project1.update(
            actual_costs=Measurement(1200, "EUR"), ignore_permission=True
        )

        self.assertFalse(commercials1.has_budget_buffer)
        self.assertCacheMiss()
        self.assertEqual(commercials1.budget_left, Measurement(-200, "EUR"))
        self.assertCacheHit()

    def test_contains_lookup_invalidation(self):
        """
        Verify that ``contains`` lookups trigger invalidation when the matching set changes.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.similar_name_count, 1)
        self.assertCacheMiss()
        self.assertEqual(commercials1.similar_name_count, 1)
        self.assertCacheHit()

        self.project2 = self.project2.update(
            name="Test Another Project", ignore_permission=True
        )

        self.assertEqual(commercials1.similar_name_count, 2)
        self.assertCacheMiss()
        self.assertEqual(commercials1.similar_name_count, 2)
        self.assertCacheHit()

        self.project3 = self.project3.update(
            name="Not matching Project", ignore_permission=True
        )
        self.assertEqual(commercials1.similar_name_count, 2)
        self.assertCacheHit()

    def test_deactivation_invalidation(self):
        """
        Ensure deactivating a project invalidates caches that depend on active records.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.active_project_count, 3)
        self.assertCacheMiss()
        self.assertEqual(commercials1.active_project_count, 3)
        self.assertCacheHit()

        self.project3 = self.project3.deactivate(ignore_permission=True)

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertEqual(refreshed_commercials1.active_project_count, 2)
        self.assertCacheMiss()
        self.assertEqual(refreshed_commercials1.active_project_count, 2)
        self.assertCacheHit()

    def test_combined_filter_and_exclude_invalidation(self):
        """
        Ensure caches depending on both filter and exclude lookups refresh on updates and creations.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.same_name_excluding_self, 0)
        self.assertCacheMiss()
        self.assertEqual(commercials1.same_name_excluding_self, 0)
        self.assertCacheHit()

        self.project2.update(name="Test Project", ignore_permission=True)

        self.assertEqual(commercials1.same_name_excluding_self, 1)
        self.assertCacheMiss()
        self.assertEqual(commercials1.same_name_excluding_self, 1)
        self.assertCacheHit()

        self.project2.update(number=1, ignore_permission=True)

        self.assertEqual(commercials1.same_name_excluding_self, 0)
        self.assertCacheMiss()
        self.assertEqual(commercials1.same_name_excluding_self, 0)
        self.assertCacheHit()

        project4 = self.TestProject.create(
            name="Test Project",
            number=4,
            budget=Measurement(500, "EUR"),
            actual_costs=Measurement(100, "EUR"),
            start_date=date(2024, 1, 3),
            completion_at=timezone.make_aware(datetime(2024, 1, 12, 12, 0)),
        )

        self.assertEqual(commercials1.same_name_excluding_self, 1)
        self.assertCacheMiss()
        self.assertEqual(commercials1.same_name_excluding_self, 1)
        self.assertCacheHit()

        # Also ensure newly created project reports expected count
        commercials4 = self.TestCommercials(project=project4)
        self.assertEqual(commercials4.same_name_excluding_self, 2)
        self.assertCacheMiss()
        self.assertEqual(commercials4.same_name_excluding_self, 2)
        self.assertCacheHit()

    def test_complex_filter_invalidation(self):
        """
        Ensure caches built from multiple filter keywords and comparison operators are invalidated correctly.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.project_keyword_number_range_count, 3)
        self.assertCacheMiss()
        self.assertEqual(commercials1.project_keyword_number_range_count, 3)
        self.assertCacheHit()

        self.project2.update(number=4, ignore_permission=True)

        self.assertEqual(commercials1.project_keyword_number_range_count, 2)
        self.assertCacheMiss()
        self.assertEqual(commercials1.project_keyword_number_range_count, 2)
        self.assertCacheHit()

        self.project3 = self.project3.update(
            name="Third Initiative", ignore_permission=True
        )

        self.assertEqual(commercials1.project_keyword_number_range_count, 1)
        self.assertCacheMiss()
        self.assertEqual(commercials1.project_keyword_number_range_count, 1)
        self.assertCacheHit()

        self.TestProject.create(
            name="Project Phoenix",
            number=4,
            budget=Measurement(1200, "EUR"),
            actual_costs=Measurement(300, "EUR"),
            start_date=date(2024, 1, 3),
            completion_at=timezone.make_aware(datetime(2024, 1, 12, 12, 0)),
        )

        self.assertEqual(commercials1.project_keyword_number_range_count, 1)
        self.assertCacheHit()

        self.TestProject.create(
            name="Project Phoenix",
            number=3,
            budget=Measurement(1200, "EUR"),
            actual_costs=Measurement(300, "EUR"),
            start_date=date(2024, 1, 3),
            completion_at=timezone.make_aware(datetime(2024, 1, 12, 12, 0)),
        )

        self.assertEqual(commercials1.project_keyword_number_range_count, 2)
        self.assertCacheMiss()

    def test_datetime_range_filter_invalidation(self):
        """
        Verify caches using date and datetime comparison operators refresh after relevant updates.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.recent_project_window_count, 2)
        self.assertCacheMiss()
        self.assertEqual(commercials1.recent_project_window_count, 2)
        self.assertCacheHit()

        self.project2 = self.project2.update(
            start_date=date(2024, 2, 1),
            completion_at=timezone.make_aware(datetime(2024, 2, 10, 12, 0)),
            ignore_permission=True,
        )

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        result = refreshed_commercials1.recent_project_window_count
        self.assertCacheMiss()
        self.assertEqual(result, 1)
        self.assertEqual(refreshed_commercials1.recent_project_window_count, 1)
        self.assertCacheHit()

        self.project3 = self.project3.update(
            start_date=date(2023, 12, 28),
            completion_at=timezone.make_aware(datetime(2024, 1, 9, 12, 0)),
            ignore_permission=True,
        )

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertEqual(refreshed_commercials1.recent_project_window_count, 2)
        self.assertCacheMiss()
        self.assertEqual(refreshed_commercials1.recent_project_window_count, 2)
        self.assertCacheHit()

    def test_staged_bucket_chain_invalidation(self):
        """
        Ensure chained bucket filter and exclude operations trigger invalidation when dependent data changes.
        """
        commercials1 = self.TestCommercials(project=self.project1)

        self.assertEqual(commercials1.staged_bucket_count, 1)
        self.assertCacheMiss()
        self.assertEqual(commercials1.staged_bucket_count, 1)
        self.assertCacheHit()

        self.project2 = self.project2.update(
            start_date=date(2023, 12, 25),
            actual_costs=Measurement(900, "EUR"),
            ignore_permission=True,
        )

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertEqual(refreshed_commercials1.staged_bucket_count, 2)
        self.assertCacheMiss()
        self.assertEqual(refreshed_commercials1.staged_bucket_count, 2)
        self.assertCacheHit()

        self.project2 = self.project2.update(
            actual_costs=Measurement(1500, "EUR"), ignore_permission=True
        )

        refreshed_commercials1 = self.TestCommercials(project=self.project1)
        self.assertEqual(refreshed_commercials1.staged_bucket_count, 1)
        self.assertCacheMiss()
        self.assertEqual(refreshed_commercials1.staged_bucket_count, 1)
        self.assertCacheHit()