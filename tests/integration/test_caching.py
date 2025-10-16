from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.manager import GeneralManager, Input
from django.db.models.fields import CharField, IntegerField
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

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                number = IntegerField(null=True, blank=True)
                budget = MeasurementField(
                    base_unit="EUR",
                )
                actual_costs = MeasurementField(
                    base_unit="EUR",
                )

                class Meta:
                    app_label = "general_manager"

            class Permission(ManagerBasedPermission):
                __create__ = ["public"]

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
                Returns the remaining budget for the project as a Measurement.
                
                Calculates the difference between the project's budget and its actual costs.
                """
                return self.project.budget - self.project.actual_costs

            @graphQlProperty
            def budget_used(self) -> Measurement:
                """
                Return the percentage of the project's budget that has been used.
                
                Returns:
                    Measurement: The ratio of actual costs to budget, expressed as a percentage.
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
                Return True when another project shares the same name.
                
                Uses a filtered bucket to determine whether multiple projects match the current project's name.
                """
                matching_count = TestProjectForCommercials.filter(
                    name=self.project.name
                ).count()
                return matching_count > 1

            @graphQlProperty
            def other_project_count(self) -> int:
                """
                Return the number of projects whose numbers differ from the current project's number.
                
                Relies on an ``exclude`` lookup to ensure cache invalidation works for exclusion-based dependencies.
                """
                return TestProjectForCommercials.exclude(
                    number=self.project.number
                ).count()

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
        )
        self.project2 = self.TestProject.create(
            name="Another Project",
            number=2,
            budget=Measurement(2000, "EUR"),
            actual_costs=Measurement(500, "EUR"),
        )
        self.project3 = self.TestProject.create(
            name="Third Project",
            number=3,
            budget=Measurement(1500, "EUR"),
            actual_costs=Measurement(1800, "EUR"),
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
