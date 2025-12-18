from django.db import models
from django.db.models import CharField, IntegerField, SmallIntegerField, TextField
from decimal import Decimal
from typing import ClassVar, Any
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface import ReadOnlyInterface
from general_manager.interface.capabilities.read_only import (
    ReadOnlyManagementCapability,
)
from general_manager.interface.utils.errors import ReadOnlyRelationLookupError
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
        Set up a Packaging test manager with a Measurement field and register it for integration tests.
        
        Defines an inner GeneralManager subclass `Packaging` that seeds two records with `total_volume` values (one as a string, one as a Measurement), exposes a nested ReadOnlyInterface with a `total_volume` MeasurementField using "liter" as the base unit, and assigns the created manager to `cls.Packaging` and `cls.general_manager_classes` for use by the test cases.
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
        """
        Verify that syncing a read-only interface with Measurement fields correctly populates model instances and their backing value/unit columns.
        
        This test syncs the Packaging read-only interface, asserts two records are created, and checks:
        - Retrieved Packaging instances expose Measurement objects with the expected magnitudes and units:
          - "Small Box": magnitude 2, unit "liter"
          - "Medium Box": magnitude 750 milliliter
        - The underlying database model stores the base/value and unit columns correctly:
          - "Small Box": total_volume_value == Decimal("2"), total_volume_unit == "liter"
          - "Medium Box": total_volume_value == Decimal("0.75"), total_volume_unit == "milliliter"
        """
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


class ReadOnlyRelationLookupTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Set up Size and Packaging test managers and register them for integration tests of read-only relation lookups.
        
        Defines two GeneralManager subclasses:
        - Size: seeds three records in _data with container and Measurement `volume`, exposes a ReadOnlyInterface with a MeasurementField for `volume` and a unique_together constraint on (container, volume).
        - Packaging: exposes a ReadOnlyInterface with a MeasurementField `total_volume` and a ForeignKey `basis_size` to Size.Interface._model, and provides _default_data that references Size records by payload.
        
        Registers the created classes as cls.Size and cls.Packaging and adds them to cls.general_manager_classes for use by the test suite.
        """

        class Size(GeneralManager):
            container: str
            volume: Measurement

            _data: ClassVar[list[dict[str, Any]]] = [
                {"container": "Flasche", "volume": "330 milliliter"},
                {"container": "Flasche", "volume": "500 milliliter"},
                {"container": "Dose", "volume": "330 milliliter"},
            ]

            class Interface(ReadOnlyInterface):
                container = CharField(max_length=50)
                volume = MeasurementField("milliliter")

                class Meta:
                    app_label = "general_manager"
                    unique_together = (("container", "volume"),)

        class Packaging(GeneralManager):
            type: str
            total_volume: Measurement
            basis_size: Size

            _data: ClassVar[list[dict[str, Any]]] = []
            _default_data: ClassVar[list[dict[str, Any]]] = [
                {
                    "type": "Einzelflasche 0.33l",
                    "total_volume": "330 milliliter",
                    "basis_size": {"container": "Flasche", "volume": "330 milliliter"},
                },
                {
                    "type": "Einzelflasche 0.5l",
                    "total_volume": "500 milliliter",
                    "basis_size": {"container": "Flasche", "volume": "500 milliliter"},
                },
                {
                    "type": "Einzeldose 0.33l",
                    "total_volume": "330 milliliter",
                    "basis_size": {"container": "Dose", "volume": "330 milliliter"},
                },
            ]

            class Interface(ReadOnlyInterface):
                type = CharField(max_length=100, unique=True)
                total_volume = MeasurementField("milliliter")
                basis_size = models.ForeignKey(
                    Size.Interface._model,
                    on_delete=models.CASCADE,
                )

                class Meta:
                    app_label = "general_manager"

        cls.Size = Size
        cls.Packaging = Packaging
        cls.general_manager_classes = [Size, Packaging]

    def setUp(self) -> None:
        super().setUp()
        self.Size.Interface._model.all_objects.all().delete()
        self.Packaging.Interface._model.all_objects.all().delete()
        self.Packaging._data = list(self.Packaging._default_data)

    def test_foreign_key_lookup_resolves_unique_match(self):
        capability = self.Size.Interface.require_capability(
            "read_only_management",
            expected_type=ReadOnlyManagementCapability,
        )
        warnings = capability.ensure_schema_is_up_to_date(
            self.Size.Interface,
            self.Size,
            self.Size.Interface._model,
        )
        self.assertEqual(warnings, [])
        self.assertTrue(self.Size._data)
        sync_read_only_interface(self.Size.Interface)
        size_model = self.Size.Interface._model  # type: ignore[attr-defined]
        self.assertTrue(size_model._meta.get_field("is_active").default)
        self.assertEqual(size_model.all_objects.count(), 3)
        self.assertListEqual(
            list(size_model.all_objects.values_list("is_active", flat=True)),
            [True, True, True],
        )
        self.assertEqual(size_model.objects.count(), 3)
        self.assertEqual(
            size_model.objects.filter(
                container="Flasche", volume="330 milliliter"
            ).count(),
            1,
        )
        sync_read_only_interface(self.Packaging.Interface)

        package = self.Packaging.filter(type="Einzelflasche 0.33l").first()
        self.assertIsNotNone(package)
        self.assertEqual(package.basis_size.container, "Flasche")
        self.assertEqual(package.basis_size.volume.quantity.magnitude, Decimal("330"))  # type: ignore[attr-defined]
        self.assertEqual(str(package.basis_size.volume.quantity.units), "milliliter")  # type: ignore[attr-defined]

    def test_foreign_key_lookup_missing_match_fails(self):
        """
        Verifies that syncing a read-only interface with a foreign-key reference fails when the referenced records are missing.
        
        Sets the Size seed data to empty, syncs the Size interface to ensure no Size records exist, and then asserts that syncing the Packaging interface raises ReadOnlyRelationLookupError due to the missing related Size entries.
        """
        original_size_data = self.Size._data
        try:
            self.Size._data = []
            sync_read_only_interface(self.Size.Interface)
            self.assertEqual(self.Size.Interface._model.objects.count(), 0)
            with self.assertRaises(ReadOnlyRelationLookupError):
                sync_read_only_interface(self.Packaging.Interface)
        finally:
            self.Size._data = original_size_data

    def test_foreign_key_lookup_multiple_matches_fails(self):
        sync_read_only_interface(self.Size.Interface)
        original_data = self.Packaging._data
        try:
            self.Packaging._data = [
                {
                    "type": "Ambiguous",
                    "total_volume": "1000 milliliter",
                    "basis_size": {"container": "Flasche"},
                }
            ]
            with self.assertRaises(ReadOnlyRelationLookupError):
                sync_read_only_interface(self.Packaging.Interface)
        finally:
            self.Packaging._data = original_data