from django.db import models
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.interface.readOnlyInterface import ReadOnlyInterface

from general_manager.utils.testing import GeneralManagerTransactionTestCase


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class TestCountry(GeneralManager):
            _data = [
                {"code": "US", "name": "United States"},
                {"code": "DE", "name": "Germany"},
            ]
            code: str
            name: str

            class Interface(ReadOnlyInterface):
                code = models.CharField(max_length=2, unique=True)
                name = models.CharField(max_length=50)

        class TestHuman(GeneralManager):
            name: str
            country: TestCountry | None

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                country = models.ForeignKey(
                    "general_manager.TestCountry",
                    on_delete=models.CASCADE,
                    related_name="humans",
                    null=True,
                    blank=True,
                )

        cls.TestCountry = TestCountry
        cls.TestHuman = TestHuman
        cls.general_manager_classes = [TestCountry, TestHuman]
        cls.read_only_classes = [TestCountry]

    def setUp(self):
        super().setUp()
        self.TestCountry.Interface.syncData()  # type: ignore

        self.test_human1 = self.TestHuman.Interface.create(
            creator_id=None,
            name="Alice",
            country=self.TestCountry.filter(code="US").first(),
        )

        self.test_human2 = self.TestHuman.Interface.create(
            creator_id=None,
            name="Bob",
        )

    def test_iter(self):
        humans = self.TestHuman.all()
        self.assertEqual(len(humans), 2)
        for human in humans:
            self.assertEqual(human.name, dict(human)["name"])
            self.assertEqual(human.country, dict(human)["country"])
