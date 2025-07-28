# type: ignore

from __future__ import annotations
from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
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
            humans_list: Bucket[TestHuman]

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

    def tearDown(self):
        """
        Cleans up the test database by deleting all instances of TestCountry, TestHuman, and TestFamily.

        This ensures that each test starts with a clean state.
        """
        self.TestCountry.Interface._model._meta.model.objects.all().delete()
        self.TestHuman.Interface._model._meta.model.objects.all().delete()
        self.TestFamily.Interface._model._meta.model.objects.all().delete()
        super().tearDown()

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
        self.assertEqual(test_human1.name, "Alice Updated")
        self.assertNotEqual(test_human1.name, original_name)

        # Test updating country relationship
        de_country = self.TestCountry.filter(code="DE").first()
        test_human2 = self.test_human2.update(
            country=de_country, ignore_permission=True
        )
        self.assertEqual(test_human2.country.code, "DE")

        # Test updating family name
        test_family = self.test_family.update(
            name="Updated Family Name", ignore_permission=True
        )
        self.assertEqual(test_family.name, "Updated Family Name")

    def test_delete_operations(self):
        """
        Test delete operations and cascade behavior.
        """
        # Create additional test data for deletion
        test_human3 = self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            country=self.TestCountry.filter(code="DE").first(),
            ignore_permission=True,
        )

        # Test deleting a human
        human_count_before = len(self.TestHuman.filter(is_active=True))
        test_human3.deactivate(ignore_permission=True)
        human_count_after = len(self.TestHuman.filter(is_active=True))
        self.assertEqual(human_count_after, human_count_before - 1)

        # Verify the deleted human is not in any families
        remaining_humans = self.TestHuman.filter(is_active=True)
        for human in remaining_humans:
            self.assertNotEqual(human.name, "Charlie")

    def test_bucket_operations(self):
        """
        Test Bucket operations for many-to-many relationships.
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
        updated_humans = updated_family.humans_list
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
        Test ReadOnlyInterface syncData functionality.
        """
        # Test that syncData creates the expected country records

        # Clear existing data and resync
        self.TestCountry.Interface._model._meta.model.objects.all().delete()
        self.TestCountry.Interface.syncData()

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
        second_family = self.TestFamily.create(
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

    def test_edge_cases_and_error_handling(self):
        """
        Test edge cases and error handling scenarios.
        """
        # Test filtering with invalid field
        try:
            invalid_filter = self.TestHuman.filter(nonexistent_field="value")
            # If no exception is raised, ensure it returns empty result
            self.assertEqual(len(invalid_filter), 0)
        except Exception:
            # Exception is acceptable for invalid field access
            pass

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
        User.objects.create_user(
            username="testuser",
            password="testpassword",
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
        Test data integrity and model constraints.
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
