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
        super().setUpClass()

        class TestProject(GeneralManager):
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
            project: TestProject

            class Interface(CalculationInterface):
                project = Input(TestProject, possible_values=lambda: TestProject.all())

            @graphQlProperty
            def budget_left(self) -> Measurement:
                return self.project.budget - self.project.actual_costs

            @graphQlProperty
            def budget_used(self) -> Measurement:
                return self.project.actual_costs / self.project.budget * 100

            @graphQlProperty
            def is_over_budget(self) -> bool:
                return self.project.actual_costs > self.project.budget

        cls.TestProject = TestProject
        cls.TestCommercials = TestCommercials
        cls.general_manager_classes = [TestProject, TestCommercials]

    def setUp(self) -> None:
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
            actual_costs=Measurement(1600, "EUR"),
        )

    def test_budget_left(self):
        commercials1 = self.TestCommercials(project=self.project1)
        commercials2 = self.TestCommercials(project=self.project2)
        commercials3 = self.TestCommercials(project=self.project3)

        self.assertEqual(commercials1.budget_left, Measurement(800, "EUR"))
        self.assertCacheMiss()
        self.assertEqual(commercials2.budget_left, Measurement(1500, "EUR"))
        self.assertCacheMiss()
        self.assertEqual(commercials3.budget_left, Measurement(-100, "EUR"))
        self.assertCacheMiss()

        for commercials in self.TestCommercials.all():
            self.assertTrue(commercials.budget_left)
            self.assertCacheHit()
