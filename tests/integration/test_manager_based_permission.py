from __future__ import annotations
from django.db import models
from django.contrib.auth.models import User
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.interface.readOnlyInterface import ReadOnlyInterface
from general_manager.bucket.baseBucket import Bucket
from general_manager.permission.managerBasedPermission import ManagerBasedPermission

from general_manager.utils.testing import GeneralManagerTransactionTestCase


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Define and assign GeneralManager model classes for use in integration tests.

        This method creates three nested model classes—TestCountry, TestHuman, and TestFamily—each with associated Django model interfaces and relationships. The classes are assigned to class variables for use in test methods, and lists of all manager classes and read-only classes are maintained.
        """

        class TestCountry1(GeneralManager):
            code: str
            name: str
            humans_list: Bucket[TestHuman1]

            class Interface(DatabaseInterface):
                code = models.CharField(max_length=2, unique=True)
                name = models.CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__ = ["public"]
                __create__ = ["public"]
                __update__ = ["public"]
                __delete__ = ["public"]

        class TestHuman1(GeneralManager):
            name: str
            country: TestCountry1 | None
            families_list: Bucket[TestFamily1]

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                country = models.ForeignKey(
                    "general_manager.TestCountry1",
                    on_delete=models.CASCADE,
                    related_name="humans",
                    null=True,
                    blank=True,
                )

            class Permission(ManagerBasedPermission):
                __based_on__ = "country"

        class TestFamily1(GeneralManager):
            name: str
            humans_list: Bucket[TestHuman1]

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                humans = models.ManyToManyField(
                    "general_manager.TestHuman1",
                    related_name="families",
                )

            class Permission(ManagerBasedPermission):
                __read__ = ["public"]
                __create__ = ["public"]
                __update__ = ["public"]
                __delete__ = ["public"]

        cls.TestCountry = TestCountry1
        cls.TestHuman = TestHuman1
        cls.TestFamily = TestFamily1
        cls.general_manager_classes = [TestCountry1, TestHuman1, TestFamily1]
        cls.read_only_classes = [TestCountry1]

    def setUp(self):
        """
        Prepares the test database with sample country, human, and family data for integration tests.

        Synchronizes country data, creates two human instances (one linked to a country), and a family instance associating both humans.
        """
        super().setUp()

        self.us = self.TestCountry.create(
            creator_id=None,
            code="US",
            name="United States",
            ignore_permission=True,
        )
        self.de = self.TestCountry.create(
            creator_id=None,
            code="DE",
            name="Germany",
            ignore_permission=True,
        )

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

        self.test_human3 = self.TestHuman.create(
            creator_id=None,
            name="Tim",
            ignore_permission=True,
        )

        self.test_family = self.TestFamily.create(
            creator_id=None,
            name="Smith Family",
            humans=[self.test_human1, self.test_human2],
            ignore_permission=True,
        )

    def test_update_family(self):
        """
        Tests the update of a family with three humans and verifies the family and human relationships.

        This test checks that the family is updated successfully, that it contains the correct humans, and that the humans are linked to the family.
        """
        self.assertEqual(self.test_family.name, "Smith Family")
        self.assertIn(self.test_human1, self.test_family.humans_list)
        self.assertIn(self.test_human2, self.test_family.humans_list)

        self.test_family = self.test_family.update(
            name="Johnson Family",
            humans=[self.test_human1, self.test_human2, self.test_human3],
        )
        self.assertEqual(self.test_family.name, "Johnson Family")
        self.assertIn(self.test_human1, self.test_family.humans_list)
        self.assertIn(self.test_human2, self.test_family.humans_list)
        self.assertIn(self.test_human3, self.test_family.humans_list)

    def test_based_on_permissions_public(self):
        """
        Tests the manager-based permissions for the family model.
        """

        self.test_human1 = self.test_human1.update(
            name="Alice Updated",
        )

        self.assertEqual(self.test_human1.name, "Alice Updated")

        new_human = self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            country=self.TestCountry.filter(code="DE").first(),
        )
        self.assertEqual(new_human.name, "Charlie")
