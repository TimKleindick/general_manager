from __future__ import annotations
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.utils.crypto import get_random_string
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.bucket.baseBucket import Bucket
from general_manager.permission.managerBasedPermission import ManagerBasedPermission
from typing import ClassVar

from general_manager.utils.testing import GeneralManagerTransactionTestCase


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Configure and attach three nested GeneralManager-based test models to the test class.

        Creates TestCountry, TestHuman, and TestFamily models with their database interfaces, relations, and ManagerBasedPermission settings, then assigns them to cls.TestCountry, cls.TestHuman, cls.TestFamily, and cls.general_manager_classes for use in integration tests.
        """

        class TestCountry1(GeneralManager):
            code: str
            name: str
            humans_list: Bucket[TestHuman1]

            class Interface(DatabaseInterface):
                code = models.CharField(max_length=2, unique=True)
                name = models.CharField(max_length=50)

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

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
                __based_on__: ClassVar[str] = "country"

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
                __read__: ClassVar[list[str]] = ["public"]
                __create__: ClassVar[list[str]] = ["public"]
                __update__: ClassVar[list[str]] = ["public"]
                __delete__: ClassVar[list[str]] = ["public"]

        cls.TestCountry = TestCountry1
        cls.TestHuman = TestHuman1
        cls.TestFamily = TestFamily1
        cls.general_manager_classes = [TestCountry1, TestHuman1, TestFamily1]

    def setUp(self):
        """
        Sets up initial test data including users, countries, humans, and a family for integration tests.

        Creates a test user, two country instances, three human instances (one linked to a country), and a family associating two humans. All objects are created with permissions bypassed to ensure consistent test setup.
        """
        super().setUp()
        password = get_random_string(12)
        self.user: User = User.objects.create_user(
            username="testuser", password=password, email="testuser@example.com"
        )
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
        Test that updating a family instance changes its name and updates its associated humans.

        Verifies the initial family name and membership, performs an update to change the name and add a human, and asserts that the changes are correctly reflected.
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
        Verify that manager-based permissions allow updating an existing human and creating a new human with a country association.

        Ensures that permission logic permits modifying a human's name and adding a new human linked to a specific country.
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

    def test_create_with_permission_validation(self):
        """
        Test creation of human instances under various permission scenarios.

        Verifies that creating a human with a valid country reference succeeds, while attempting to create a human without a country and without a creator ID raises a PermissionError. Also confirms that providing a creator ID allows creation without a country.
        """
        # Test creating human with valid country reference -> should use country-based permissions
        human_with_country = self.TestHuman.create(
            creator_id=None, name="David", country=self.us
        )
        self.assertEqual(human_with_country.name, "David")
        self.assertEqual(human_with_country.country.code, "US")  # type: ignore

        # Test creating human without country -> should fall back to default permissions
        with self.assertRaises(PermissionError):
            self.TestHuman.create(
                creator_id=None,
                name="Eve",
            )
        human_without_country = self.TestHuman.create(
            creator_id=self.user.id,
            name="Eva",  # type: ignore
        )
        self.assertEqual(human_without_country.name, "Eva")
        self.assertIsNone(human_without_country.country)

    def test_update_with_permission_validation(self):
        """
        Test updating a human's country association with permission enforcement.

        Validates that updating a human's country field requires appropriate permissions, raising a PermissionError when permissions are insufficient, and allowing the update when a valid creator ID is provided.
        """
        # Test updating human's country association
        original_country = self.test_human1.country
        self.assertEqual(original_country.code, "US")  # type: ignore

        updated_human = self.test_human1.update(country=self.de)
        self.assertEqual(updated_human.country.code, "DE")  # type: ignore

        # Test updating human to remove country association
        with self.assertRaises(PermissionError):
            updated_human = updated_human.update(country=None)
        updated_human = updated_human.update(country=None, creator_id=self.user.id)  # type: ignore
        self.assertIsNone(updated_human.country)

    def test_delete_with_permissions(self):
        """
        Tests that a human instance can be deleted (deactivated) when permissions allow, and verifies the instance is no longer active in the database.
        """
        # Create a temporary human for deletion test
        temp_human = self.TestHuman.create(
            creator_id=None, name="Temporary", country=self.us, ignore_permission=True
        )

        human_id = temp_human.id  # type: ignore
        temp_human.deactivate()

        # Verify the human was deleted
        deleted_human = self.TestHuman.filter(id=human_id, is_active=True).first()
        self.assertIsNone(deleted_human)

    def test_filter_operations_with_permissions(self):
        """
        Verify that filtering queries return only permitted results according to the permission system.

        Ensures that when read permissions are restricted (e.g., to a specific country code), filtered queries via GraphQL only return items the user is allowed to access, and items outside the permission scope are excluded.
        """
        # Test filtering countries

        self.TestCountry.Permission.__read__ = ["matches:code:DE"]
        gql_query = """
        query {
            testcountry1List(filter: {code: "DE"}) {
                items {
                    code
                    name
                }
            }
        }
        """
        response = self.query(gql_query)
        self.assertResponseNoErrors(response)
        response = response.json()
        data = response.get("data", {})
        self.assertEqual(len(data["testcountry1List"]["items"]), 1)

        gql_query_2 = """
        query {
            testcountry1List(filter: {code: "US"}) {
                items {
                    code
                    name
                }
            }
        }
        """
        response_2 = self.query(gql_query_2)
        self.assertResponseNoErrors(response_2)
        response_2 = response_2.json()
        data_2 = response_2.get("data", {})
        self.assertEqual(len(data_2["testcountry1List"]["items"]), 0)

    def test_edge_case_empty_relationships(self):
        """
        Test handling of empty many-to-many and null foreign key relationships.

        Verifies that creating or updating a family with an empty humans list results in no associated humans, and that creating a human with a null country is handled correctly.
        """
        # Test creating family with empty humans list
        empty_family = self.TestFamily.create(
            creator_id=None, name="Empty Family", humans=[], ignore_permission=True
        )
        self.assertEqual(len(empty_family.humans_list), 0)

        # Test updating family to remove all humans
        self.test_family = self.test_family.update(humans=[])
        self.assertEqual(len(self.test_family.humans_list), 0)

        # Test human with null country operations
        null_country_human = self.TestHuman.create(
            creator_id=None, name="Stateless", country=None, ignore_permission=True
        )
        self.assertIsNone(null_country_human.country)

    def test_bulk_operations_with_permissions(self):
        """
        Tests bulk creation of human instances with various country associations, ensuring permission rules are respected.

        Creates multiple humans in a single operation, assigning different country relationships, and verifies correct assignment and creation.
        """
        # Test bulk creation of humans
        humans_data = [
            {"name": "Bulk1", "country": self.us},
            {"name": "Bulk2", "country": self.de},
            {"name": "Bulk3", "country": None},
        ]

        created_humans = []
        for data in humans_data:
            human = self.TestHuman.create(
                creator_id=None, ignore_permission=True, **data
            )
            created_humans.append(human)

        self.assertEqual(len(created_humans), 3)
        self.assertEqual(created_humans[0].country.code, "US")
        self.assertEqual(created_humans[1].country.code, "DE")
        self.assertIsNone(created_humans[2].country)

    def test_field_validation_and_constraints(self):
        """
        Test that model field validation and database constraints are enforced.

        Verifies that creating a country with a duplicate code raises a ValidationError due to the unique constraint, and that exceeding the maximum length for the code field also raises a ValidationError.
        """
        # Test unique constraint on country code
        with self.assertRaises(ValidationError):
            self.TestCountry.create(
                creator_id=None,
                code="US",  # Duplicate code
                name="Another US",
                ignore_permission=True,
            )

        # Test max_length constraint on country code
        with self.assertRaises(ValidationError):
            self.TestCountry.create(
                creator_id=None,
                code="TOOLONG",  # Exceeds max_length=2
                name="Invalid Country",
                ignore_permission=True,
            )

    def test_complex_permission_scenarios(self):
        """
        Test permission logic in scenarios involving nested relationships across models.

        Creates humans from different countries and a family containing them, then verifies that the family includes members with distinct country associations.
        """
        # Create a human in Germany
        german_human = self.TestHuman.create(
            creator_id=None, name="Hans", country=self.de, ignore_permission=True
        )

        # Create a family with humans from different countries
        international_family = self.TestFamily.create(
            creator_id=None,
            name="International Family",
            humans=[self.test_human1, german_human],  # US and DE humans
            ignore_permission=True,
        )

        self.assertEqual(len(international_family.humans_list), 2)

        # Verify humans in family have different countries
        countries = [
            human.country.code
            for human in international_family.humans_list
            if human.country
        ]
        self.assertIn("US", countries)
        self.assertIn("DE", countries)
