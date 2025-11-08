# type: ignore

from __future__ import annotations

from typing import Any, ClassVar
from uuid import uuid4

from django.contrib.auth import get_user_model
from factory import LazyFunction, LazyAttribute
from django.db import models

from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import DatabaseInterface
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class AutoFactoryIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """
        Register GeneralManager-backed test models on the test class for AutoFactory integration tests.

        Defines four nested GeneralManager classes (Manufacturer, CarOption, Car, Fleet), each with a DatabaseInterface and Factory configuration used by the integration tests, and assigns them to class attributes (Manufacturer, CarOption, Car, Fleet). Also sets `general_manager_classes` to the list of those managers and initializes `read_only_classes` as an empty list.
        """

        class Manufacturer(GeneralManager):
            name: str
            country: str

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=64)
                country = models.CharField(max_length=32, blank=True)

            class Factory:
                name = LazyAttribute(lambda x: f"Factory Manufacturer {x.country}")
                country = LazyFunction(lambda: "DE")

        class CarOption(GeneralManager):
            label: str

            class Interface(DatabaseInterface):
                label = models.CharField(max_length=64)

            class Factory:
                label = "Factory Option"

        class Car(GeneralManager):
            name: str
            manufacturer: Manufacturer
            options_list: ClassVar[Any]

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=64)
                manufacturer = models.ForeignKey(
                    "general_manager.Manufacturer",
                    on_delete=models.CASCADE,
                    related_name="cars",
                )
                options = models.ManyToManyField(
                    "general_manager.CarOption",
                    related_name="cars",
                    blank=True,
                )
                doors = models.PositiveSmallIntegerField(default=4)

            class Factory:
                name = "Factory Car"
                doors = 4

        class Fleet(GeneralManager):
            label: str
            capacity: int

            class Interface(DatabaseInterface):
                label = models.CharField(max_length=64)
                capacity = models.PositiveIntegerField()

            class Factory:
                @staticmethod
                def _adjustmentMethod(
                    *,
                    label: str = "Fleet",
                    capacity: int = 0,
                    count: int = 1,
                    **extra: Any,
                ) -> list[dict[str, Any]]:
                    """
                    Create a deterministic list of fleet record dictionaries for integration tests.

                    Each record has a "label" of the form "{label}-{index}" and a "capacity" equal to capacity + index.
                    Parameters:
                        label (str): Base label used for each record. Defaults to "Fleet".
                        capacity (int): Starting capacity value for the first record.
                        count (int): Number of records to generate.
                        **extra (Any): Additional fields to include; if `changed_by` is present it will be copied into each record.
                    Returns:
                        list[dict[str, Any]]: List of record dictionaries with keys "label" and "capacity", and "changed_by" if provided.
                    """
                    changed_by = extra.get("changed_by")
                    records: list[dict[str, Any]] = []
                    for index in range(count):
                        record: dict[str, Any] = {
                            "label": f"{label}-{index}",
                            "capacity": capacity + index,
                        }
                        if "changed_by" in extra:
                            record["changed_by"] = changed_by
                        records.append(record)
                    return records

        cls.Manufacturer = Manufacturer
        cls.CarOption = CarOption
        cls.Car = Car
        cls.Fleet = Fleet
        cls.general_manager_classes = [Manufacturer, CarOption, Car, Fleet]
        cls.read_only_classes: list[type[GeneralManager]] = []

    def setUp(self) -> None:
        """
        Create and persist a unique test user and assign it to self.user.

        Creates a user with a UUID-based username using Django's user model and stores the resulting user instance on self.user for use in tests.
        """
        super().setUp()
        user_model = get_user_model()
        username = f"auto-factory-user-{uuid4().hex}"
        self.user = user_model.objects.create_user(username=username)

    def tearDown(self) -> None:
        """
        Remove all objects created during a test run to keep the database state isolated between tests.
        """
        self.Car.Interface._model.objects.all().delete()
        self.CarOption.Interface._model.objects.all().delete()
        self.Manufacturer.Interface._model.objects.all().delete()
        self.Fleet.Interface._model.objects.all().delete()
        super().tearDown()

    def test_factory_create_wraps_model_in_manager(self) -> None:
        """
        AutoFactory should wrap created model instances into their corresponding GeneralManager.

        Creates a manufacturer via the interface, then uses the Car factory to create a car that references
        that manufacturer and verifies both the returned manager and the persisted model state.
        """
        manufacturer = self.Manufacturer.create(
            creator_id=None,
            name="Integration Manufacturer",
            country="DE",
            ignore_permission=True,
        )
        car = self.Car.Factory.create(
            name="Integration Car",
            manufacturer=manufacturer,
            changed_by=self.user,
        )

        self.assertIsInstance(car, self.Car)
        self.assertEqual(car.name, "Integration Car")
        self.assertEqual(
            car.manufacturer.identification["id"],
            manufacturer.identification["id"],
        )

        stored = self.Car.Interface._model.objects.get(pk=car.identification["id"])
        self.assertEqual(stored.manufacturer_id, manufacturer.identification["id"])
        self.assertEqual(stored.name, "Integration Car")

    def test_factory_populates_many_to_many_relations(self) -> None:
        """
        AutoFactory must handle many-to-many assignments provided during creation and expose them via buckets.
        """
        manufacturer = self.Manufacturer.create(
            creator_id=None,
            name="Option Manufacturer",
            country="US",
            ignore_permission=True,
        )
        option_a = self.CarOption.create(
            creator_id=None,
            label="Comfort Package",
            ignore_permission=True,
        )
        option_b = self.CarOption.create(
            creator_id=None,
            label="Safety Package",
            ignore_permission=True,
        )
        option_values = [option_a, option_b]

        car = self.Car.Factory.create(
            name="Car With Packages",
            manufacturer=manufacturer,
            options=option_values,
            changed_by=self.user,
        )

        self.assertIsInstance(car, self.Car)
        stored = self.Car.Interface._model.objects.get(pk=car.identification["id"])
        self.assertEqual(stored.options.count(), 2)
        stored_option_ids = set(stored.options.values_list("pk", flat=True))
        expected_option_ids = {
            option_a.identification["id"],
            option_b.identification["id"],
        }
        self.assertSetEqual(stored_option_ids, expected_option_ids)

        options_bucket = car.options_list
        bucket_ids = {manager.identification["id"] for manager in options_bucket}
        self.assertSetEqual(bucket_ids, expected_option_ids)

    def test_factory_adjustment_method_returns_managers(self) -> None:
        """
        Factories using _adjustmentMethod should return GeneralManager instances and persist each record.
        """
        fleets = self.Fleet.Factory.create(
            label="Fleet", capacity=5, count=3, changed_by=self.user
        )

        self.assertIsInstance(fleets, list)
        self.assertEqual(len(fleets), 3)
        for index, fleet in enumerate(fleets):
            self.assertIsInstance(fleet, self.Fleet)
            self.assertEqual(fleet.label, f"Fleet-{index}")
            stored = self.Fleet.Interface._model.objects.get(
                pk=fleet.identification["id"]
            )
            self.assertEqual(stored.label, f"Fleet-{index}")
            self.assertEqual(stored.capacity, 5 + index)

    def test_factory_uses_lazy_attributes(self) -> None:
        """
        Factories using LazyAttribute should compute values based on other attributes during creation.
        """
        manufacturer = self.Manufacturer.Factory.create(
            country="FR",
            changed_by=self.user,
        )

        self.assertIsInstance(manufacturer, self.Manufacturer)
        self.assertEqual(manufacturer.country, "FR")
        self.assertEqual(manufacturer.name, "Factory Manufacturer FR")

    def test_factory_creates_foreign_objects_when_missing(self) -> None:
        """
        Factories should create and persist related foreign objects if not provided during creation.
        """
        car = self.Car.Factory.create(
            name="Car With New Manufacturer",
            changed_by=self.user,
        )

        self.assertIsInstance(car, self.Car)
        self.assertEqual(car.name, "Car With New Manufacturer")
        self.assertIsInstance(car.manufacturer, self.Manufacturer)

        stored_car = self.Car.Interface._model.objects.get(pk=car.identification["id"])
        self.assertEqual(stored_car.name, "Car With New Manufacturer")
        self.assertEqual(
            stored_car.manufacturer_id, car.manufacturer.identification["id"]
        )
