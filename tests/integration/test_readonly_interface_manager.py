from django.db.models import CharField, IntegerField, SmallIntegerField, TextField
from decimal import Decimal
from typing import ClassVar, Any
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import ReadOnlyInterface
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)
from general_manager.measurement import Measurement, MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase


def sync_read_only_interface(interface_cls: type[ReadOnlyInterface]) -> None:
    """
    Synchronize the provided ReadOnlyInterface's configured seed data into the database.

    Parameters:
        interface_cls (type[ReadOnlyInterface]): The ReadOnlyInterface class whose data should be synchronized into persistent storage.
    """
    capability = interface_cls.require_capability(
        "read_only_management",
        expected_type=ReadOnlyManagementCapability,
    )
    capability.sync_data(interface_cls)


class ReadOnlyIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Define a TestCountry GeneralManager subclass with a read-only Interface and register it on the test class.

        Creates an inner TestCountry class that exposes two seeded records (codes "US" and "DE") via a class-level `_data` list, defines `code` and `name` fields, and provides a read-only `Interface` with corresponding CharField definitions. Assigns this class to `cls.TestCountry` and adds it to `cls.general_manager_classes` for use by the tests.
        """

        class TestCountry(GeneralManager):
            _data: ClassVar[list[dict[str, str]]] = [
                {"code": "US", "name": "United States"},
                {"code": "DE", "name": "Germany"},
            ]
            code: str
            name: str

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=2, unique=True)
                name = CharField(max_length=50)

                class Meta:
                    app_label = "general_manager"

        cls.TestCountry = TestCountry
        cls.general_manager_classes = [TestCountry]

    def test_sync_populates_database(self):
        countries = self.TestCountry.all()
        self.assertEqual(countries.count(), 2)
        codes = {c.code for c in countries}
        self.assertEqual(codes, {"US", "DE"})

    def test_create_not_allowed(self):
        with self.assertRaises(NotImplementedError):
            self.TestCountry.create(code="FR", name="France", ignore_permission=True)

    def test_update_not_allowed(self):
        country = self.TestCountry.filter(code="US").first()
        self.assertIsNotNone(country)
        with self.assertRaises(NotImplementedError):
            country.update(name="USA", ignore_permission=True)  # type: ignore[arg-type]

    def test_filter_returns_correct_item(self):
        country = self.TestCountry.filter(code="DE").first()
        self.assertIsNotNone(country)
        self.assertEqual(country.name, "Germany")  # type: ignore


class ReadOnlyWithComplexData(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Create and register a Milestone GeneralManager subclass with seeded records and a ReadOnlyInterface for integration tests.

        This class-level setup defines a Milestone model with fields customer_name, name, description, and step, provides initial `_data` seed records, exposes a nested ReadOnlyInterface describing the public fields, and assigns the created class to `cls.Milestone` and `cls.general_manager_classes` for use by tests.
        """

        class Milestone(GeneralManager):
            customer_name: str
            name: str
            description: str
            step: int

            _data: ClassVar[list[dict[str, Any]]] = [
                {
                    "customer_name": "XYZ",
                    "name": "Requested",
                    "description": "",
                    "step": 1,
                },
                {
                    "customer_name": "XYZ",
                    "name": "Nominated",
                    "description": "nominated by customer",
                    "step": 2,
                },
            ]

            class Interface(ReadOnlyInterface):
                customer_name = CharField(max_length=255)
                name = CharField(max_length=255, unique=True)
                description = TextField(max_length=512)
                step = IntegerField()
                is_active = SmallIntegerField(default=1)

        cls.Milestone = Milestone
        cls.general_manager_classes = [Milestone]

    def test_sync_populates_database(self):
        sync_read_only_interface(self.Milestone.Interface)
        milestones = self.Milestone.all()
        self.assertEqual(milestones.count(), 2)
        names = {m.name for m in milestones}
        self.assertEqual(names, {"Requested", "Nominated"})


class ReadOnlyWithMeasurementFields(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Define a Packaging manager with a MeasurementField to verify read-only sync populates backing columns.
        """

        class Packaging(GeneralManager):
            name: str
            total_volume: Measurement

            _data: ClassVar[list[dict[str, Any]]] = [
                {"name": "Small Box", "total_volume": "2 liter"},
                {"name": "Medium Box", "total_volume": Measurement(750, "milliliter")},
            ]

            class Interface(ReadOnlyInterface):
                name = CharField(max_length=50, unique=True)
                total_volume = MeasurementField(base_unit="liter")

                class Meta:
                    app_label = "general_manager"

        cls.Packaging = Packaging
        cls.general_manager_classes = [Packaging]

    def test_sync_handles_measurement_fields(self):
        sync_read_only_interface(self.Packaging.Interface)

        packages = self.Packaging.all()
        self.assertEqual(packages.count(), 2)

        small = self.Packaging.filter(name="Small Box").first()
        medium = self.Packaging.filter(name="Medium Box").first()

        self.assertIsNotNone(small)
        self.assertIsNotNone(medium)

        self.assertEqual(small.total_volume.quantity.magnitude, Decimal("2"))
        self.assertEqual(str(small.total_volume.quantity.units), "liter")

        self.assertAlmostEqual(
            float(medium.total_volume.quantity.magnitude), 750.0, places=6
        )
        self.assertEqual(str(medium.total_volume.quantity.units), "milliliter")

        packaging_model = self.Packaging.Interface._model  # type: ignore[attr-defined]
        small_record = packaging_model.objects.get(name="Small Box")
        medium_record = packaging_model.objects.get(name="Medium Box")

        self.assertEqual(small_record.total_volume_value, Decimal("2"))
        self.assertEqual(small_record.total_volume_unit, "liter")
        self.assertEqual(medium_record.total_volume_value, Decimal("0.75"))
        self.assertEqual(medium_record.total_volume_unit, "milliliter")
