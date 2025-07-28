from __future__ import annotations
from django.db import models
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.interface.readOnlyInterface import ReadOnlyInterface
from general_manager.bucket.baseBucket import Bucket

from general_manager.utils.testing import GeneralManagerTransactionTestCase


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Define and assign GeneralManager model classes for use in integration tests.
        
        This method creates three nested model classes—TestCountry, TestHuman, and TestFamily—each with associated Django model interfaces and relationships. The classes are assigned to class variables for use in test methods, and lists of all manager classes and read-only classes are maintained.
        """
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
            families_list: Bucket[TestFamily]

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

        cls.TestCountry = TestCountry
        cls.TestHuman = TestHuman
        cls.TestFamily = TestFamily
        cls.general_manager_classes = [TestCountry, TestHuman, TestFamily]
        cls.read_only_classes = [TestCountry]

    def setUp(self):
        """
        Prepares the test database with sample country, human, and family data for integration tests.
        
        Synchronizes country data, creates two human instances (one linked to a country), and a family instance associating both humans.
        """
        super().setUp()
        self.TestCountry.Interface.syncData()  # type: ignore

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

    def test_iter(self):
        """
        Tests that all TestHuman instances can be retrieved and their attributes match the dictionary representation.
        """
        humans = self.TestHuman.all()
        self.assertEqual(len(humans), 2)
        for human in humans:
            self.assertEqual(human.name, dict(human)["name"])
            self.assertEqual(human.country, dict(human)["country"])

    def test_manager_connections(self):
        """
        Test that the many-to-many relationship between humans and families is correctly established.
        
        Verifies that the test family includes both test humans in its `humans_list` and that the family appears in a human's `families_list`.
        """
        humans = self.test_family.humans_list

        self.assertEqual(len(humans), 2)
        self.assertIn(self.test_human1, humans)
        self.assertIn(self.test_human2, humans)

        self.assertIn(self.test_family, self.test_human1.families_list)
