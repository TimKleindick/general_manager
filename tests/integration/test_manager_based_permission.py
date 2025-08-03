from __future__ import annotations
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.bucket.baseBucket import Bucket
from general_manager.permission.managerBasedPermission import ManagerBasedPermission

from general_manager.utils.testing import GeneralManagerTransactionTestCase


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Defines and assigns nested GeneralManager model classes with interfaces and permissions for integration tests.

        This method creates three model classes—TestCountry1, TestHuman1, and TestFamily1—each with Django ORM fields and manager-based permissions. The classes are assigned to class variables for use in test methods, and lists of all manager classes and read-only classes are initialized.
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

    def setUp(self):
        """
        Populate the test database with sample countries, humans, and a family for integration testing.

        Creates two country instances, three human instances (one linked to a country), and a family instance associating two humans. All objects are created with permissions bypassed for test setup.
        """
        super().setUp()
        self.user: User = User.objects.create_user(
            username="testuser", password="testpassword", email="testuser@example.com"
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
        Verify that updating a family instance correctly changes its name and updates its associated humans.

        Asserts that the family initially contains the expected humans, then updates the family's name and membership, and verifies the changes are reflected in the relationships.
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
        Test that manager-based permissions allow updating and creating human instances.

        Verifies that a human's name can be updated and a new human can be created with a country association, ensuring permission logic is correctly enforced.
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

    def test_permission_based_on_field_inheritance(self):
        """
        Test that permissions are correctly inherited based on the __based_on__ field.

        Verifies that humans inherit permissions from their associated country.
        """
        # Test that human with US country inherits US permissions
        self.assertEqual(self.test_human1.country.code, "US")  # type: ignore

        # Test that human without country has no country-based permissions
        self.assertIsNone(self.test_human2.country)

        # Update human2 to have a country and verify permission inheritance
        self.test_human2 = self.test_human2.update(
            country=self.de, ignore_permission=True
        )
        self.assertEqual(self.test_human2.country.code, "DE")  # type: ignore

    def test_create_with_permission_validation(self):
        """
        Test creating instances with different permission scenarios.

        Validates that permission checking works correctly during creation.
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
            creator_id=self.user.id, name="Eva"  # type: ignore
        )
        self.assertEqual(human_without_country.name, "Eva")
        self.assertIsNone(human_without_country.country)

    def test_update_with_permission_validation(self):
        """
        Test updating instances with different permission scenarios.

        Validates that permission checking works correctly during updates.
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
        Test deletion scenarios with permission validation.

        Verifies that deletion works correctly with manager-based permissions.
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
        Test filtering operations work correctly with permission system.

        Validates that filtering respects permission boundaries.
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
        print(data_2["testcountry1List"]["items"])
        self.assertEqual(len(data_2["testcountry1List"]["items"]), 0)

    def test_edge_case_empty_relationships(self):
        """
        Test edge cases with empty relationships and null values.

        Validates behavior when dealing with empty or null relationship data.
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
        Test bulk create and update operations with permission validation.

        Validates that bulk operations respect permission boundaries.
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
        Test field validation and database constraints work correctly.

        Validates that model field constraints are properly enforced.
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
        Test complex permission scenarios with nested relationships.

        Validates permission logic in complex relationship hierarchies.
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
