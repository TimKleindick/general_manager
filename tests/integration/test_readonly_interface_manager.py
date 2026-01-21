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
from general_manager.utils.testing import (
    GeneralManagerTransactionTestCase,
    run_registered_startup_hooks,
)


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
        run_registered_startup_hooks(interfaces=[self.Milestone.Interface])
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
        run_registered_startup_hooks(interfaces=[self.Packaging.Interface])

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
        run_registered_startup_hooks(interfaces=[self.Size.Interface])
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
        run_registered_startup_hooks(interfaces=[self.Packaging.Interface])

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
            run_registered_startup_hooks(interfaces=[self.Size.Interface])
            self.assertEqual(self.Size.Interface._model.objects.count(), 0)
            with self.assertRaises(ReadOnlyRelationLookupError):
                run_registered_startup_hooks(interfaces=[self.Packaging.Interface])
        finally:
            self.Size._data = original_size_data

    def test_foreign_key_lookup_multiple_matches_fails(self):
        run_registered_startup_hooks(interfaces=[self.Size.Interface])
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
                run_registered_startup_hooks(interfaces=[self.Packaging.Interface])
        finally:
            self.Packaging._data = original_data


class ReadOnlyNestedRelationLookupTests(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Set up Region, Country, and City managers for nested foreign-key lookups.

        Region and Country are read-only interfaces with a foreign key from Country to Region.
        City is a read-only interface with a foreign key to Country, and its seed data resolves
        the Country relation using a nested lookup dict.
        """

        class Region(GeneralManager):
            code: str
            name: str

            _data: ClassVar[list[dict[str, Any]]] = [
                {"code": "EU", "name": "Europe"},
                {"code": "NA", "name": "North America"},
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=2, unique=True)
                name = CharField(max_length=50)

                class Meta:
                    app_label = "general_manager"

        class Country(GeneralManager):
            code: str
            name: str
            region: Region

            _data: ClassVar[list[dict[str, Any]]] = [
                {"code": "DE", "name": "Germany", "region": {"code": "EU"}},
                {"code": "US", "name": "United States", "region": {"code": "NA"}},
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=2, unique=True)
                name = CharField(max_length=50)
                region = models.ForeignKey(
                    "Region",
                    on_delete=models.CASCADE,
                )

                class Meta:
                    app_label = "general_manager"

        class City(GeneralManager):
            name: str
            country: Country

            _data: ClassVar[list[dict[str, Any]]] = [
                {
                    "name": "Berlin",
                    "country": {"code": "DE", "region": {"code": "EU"}},
                }
            ]

            class Interface(ReadOnlyInterface):
                name = CharField(max_length=50, unique=True)
                country = models.ForeignKey(
                    "Country",
                    on_delete=models.CASCADE,
                )

                class Meta:
                    app_label = "general_manager"

        cls.Region = Region
        cls.Country = Country
        cls.City = City
        cls.general_manager_classes = [Region, Country, City]

    def setUp(self) -> None:
        super().setUp()
        self.Region.Interface._model.all_objects.all().delete()
        self.Country.Interface._model.all_objects.all().delete()
        self.City.Interface._model.all_objects.all().delete()

    def test_nested_foreign_key_lookup_resolves(self) -> None:
        run_registered_startup_hooks(interfaces=[self.City.Interface])
        city = self.City.filter(name="Berlin").first()
        self.assertIsNotNone(city)
        self.assertEqual(city.country.code, "DE")
        self.assertEqual(city.country.region.code, "EU")


class ReadOnlyManyToManyTests(GeneralManagerTransactionTestCase):
    """Integration tests for M2M field handling in read-only interfaces."""

    @classmethod
    def setUpClass(cls):
        """Set up Tag, Category, and Product managers with M2M relationships."""

        class Tag(GeneralManager):
            name: str

            _data: ClassVar[list[dict[str, Any]]] = [
                {"name": "featured"},
                {"name": "sale"},
                {"name": "new"},
            ]

            class Interface(ReadOnlyInterface):
                name = CharField(max_length=50, unique=True)

                class Meta:
                    app_label = "general_manager"

        class Category(GeneralManager):
            code: str
            title: str

            _data: ClassVar[list[dict[str, Any]]] = [
                {"code": "ELEC", "title": "Electronics"},
                {"code": "BOOK", "title": "Books"},
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=10, unique=True)
                title = CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"

        class Product(GeneralManager):
            sku: str
            name: str
            category: Category
            tags: list[Tag]

            _data: ClassVar[list[dict[str, Any]]] = []
            _default_data: ClassVar[list[dict[str, Any]]] = [
                {
                    "sku": "PROD001",
                    "name": "Laptop",
                    "category": {"code": "ELEC"},
                    "tags": [{"name": "featured"}, {"name": "sale"}],
                },
                {
                    "sku": "PROD002",
                    "name": "Novel",
                    "category": {"code": "BOOK"},
                    "tags": [{"name": "new"}],
                },
                {
                    "sku": "PROD003",
                    "name": "Tablet",
                    "category": {"code": "ELEC"},
                    "tags": [],
                },
            ]

            class Interface(ReadOnlyInterface):
                sku = CharField(max_length=20, unique=True)
                name = CharField(max_length=200)
                category = models.ForeignKey(
                    Category.Interface._model,
                    on_delete=models.CASCADE,
                )
                tags = models.ManyToManyField(Tag.Interface._model)

                class Meta:
                    app_label = "general_manager"

        cls.Tag = Tag
        cls.Category = Category
        cls.Product = Product
        cls.general_manager_classes = [Tag, Category, Product]

    def setUp(self) -> None:
        """Clear data and reset to defaults before each test."""
        self.Product._data = list(self.Product._default_data)
        super().setUp()
        self.Tag.Interface._model.all_objects.all().delete()
        self.Category.Interface._model.all_objects.all().delete()
        self.Product.Interface._model.all_objects.all().delete()

    def test_m2m_with_dict_lookups_resolves_correctly(self) -> None:
        """Verify M2M fields can use dict lookups to resolve related instances."""
        run_registered_startup_hooks(interfaces=[self.Tag.Interface])
        run_registered_startup_hooks(interfaces=[self.Category.Interface])
        run_registered_startup_hooks(interfaces=[self.Product.Interface])

        laptop = self.Product.Interface._model.objects.filter(sku="PROD001").first()
        self.assertIsNotNone(laptop)
        self.assertEqual(laptop.name, "Laptop")

        tag_names = set(laptop.tags.all().values_list("name", flat=True))
        self.assertEqual(tag_names, {"featured", "sale"})

    def test_m2m_with_empty_list_creates_no_relations(self) -> None:
        """Verify M2M field with empty list creates instance with no relations."""
        run_registered_startup_hooks(interfaces=[self.Tag.Interface])
        run_registered_startup_hooks(interfaces=[self.Category.Interface])
        run_registered_startup_hooks(interfaces=[self.Product.Interface])

        tablet = self.Product.Interface._model.objects.filter(sku="PROD003").first()
        self.assertIsNotNone(tablet)
        self.assertEqual(tablet.tags.count(), 0)

    def test_m2m_updates_existing_relations(self) -> None:
        """Verify M2M field updates clear old relations and set new ones."""
        run_registered_startup_hooks(interfaces=[self.Tag.Interface])
        run_registered_startup_hooks(interfaces=[self.Category.Interface])
        run_registered_startup_hooks(interfaces=[self.Product.Interface])

        novel = self.Product.Interface._model.objects.filter(sku="PROD002").first()
        self.assertEqual(novel.tags.count(), 1)

        self.Product._data = [
            {
                "sku": "PROD001",
                "name": "Laptop",
                "category": {"code": "ELEC"},
                "tags": [{"name": "featured"}, {"name": "sale"}],
            },
            {
                "sku": "PROD002",
                "name": "Novel",
                "category": {"code": "BOOK"},
                "tags": [{"name": "featured"}, {"name": "new"}],
            },
            {
                "sku": "PROD003",
                "name": "Tablet",
                "category": {"code": "ELEC"},
                "tags": [],
            },
        ]

        run_registered_startup_hooks(interfaces=[self.Product.Interface])

        novel.refresh_from_db()
        tag_names = set(novel.tags.all().values_list("name", flat=True))
        self.assertEqual(tag_names, {"featured", "new"})

    def test_m2m_with_none_value_creates_no_relations(self) -> None:
        """Verify M2M field with None value is treated as empty."""
        self.Product._data = [
            {
                "sku": "PROD999",
                "name": "Test",
                "category": {"code": "ELEC"},
                "tags": None,
            }
        ]

        run_registered_startup_hooks(interfaces=[self.Tag.Interface])
        run_registered_startup_hooks(interfaces=[self.Category.Interface])
        run_registered_startup_hooks(interfaces=[self.Product.Interface])

        product = self.Product.Interface._model.objects.filter(sku="PROD999").first()
        self.assertIsNotNone(product)
        self.assertEqual(product.tags.count(), 0)

    def test_m2m_non_list_raises_format_error(self) -> None:
        """Verify M2M field with non-list/non-None value raises error."""
        from general_manager.interface.utils.errors import (
            InvalidReadOnlyDataFormatError,
        )

        self.Product._data = [
            {
                "sku": "PROD999",
                "name": "Test",
                "category": {"code": "ELEC"},
                "tags": "invalid",
            }
        ]

        run_registered_startup_hooks(interfaces=[self.Tag.Interface])
        run_registered_startup_hooks(interfaces=[self.Category.Interface])

        with self.assertRaises(InvalidReadOnlyDataFormatError):
            run_registered_startup_hooks(interfaces=[self.Product.Interface])


class ReadOnlyDependencyOrderingIntegrationTests(GeneralManagerTransactionTestCase):
    """Integration tests for dependency-ordered startup hooks."""

    @classmethod
    def setUpClass(cls):
        """Set up Country, State, City hierarchy with dependencies."""

        class Country(GeneralManager):
            code: str
            name: str

            _data: ClassVar[list[dict[str, str]]] = [
                {"code": "US", "name": "United States"},
                {"code": "CA", "name": "Canada"},
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=2, unique=True)
                name = CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"

        class State(GeneralManager):
            code: str
            name: str
            country: Country

            _data: ClassVar[list[dict[str, Any]]] = [
                {"code": "NY", "name": "New York", "country": {"code": "US"}},
                {"code": "CA", "name": "California", "country": {"code": "US"}},
                {"code": "ON", "name": "Ontario", "country": {"code": "CA"}},
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=5, unique=True)
                name = CharField(max_length=100)
                country = models.ForeignKey(
                    Country.Interface._model,
                    on_delete=models.CASCADE,
                )

                class Meta:
                    app_label = "general_manager"

        class City(GeneralManager):
            name: str
            state: State

            _data: ClassVar[list[dict[str, Any]]] = [
                {"name": "New York City", "state": {"code": "NY"}},
                {"name": "Los Angeles", "state": {"code": "CA"}},
                {"name": "Toronto", "state": {"code": "ON"}},
            ]

            class Interface(ReadOnlyInterface):
                name = CharField(max_length=200)
                state = models.ForeignKey(
                    State.Interface._model,
                    on_delete=models.CASCADE,
                )

                class Meta:
                    app_label = "general_manager"
                    unique_together = (("name", "state"),)

        cls.Country = Country
        cls.State = State
        cls.City = City
        cls.general_manager_classes = [Country, State, City]

    def test_sync_respects_dependency_order(self) -> None:
        """Verify sync automatically resolves dependencies in correct order."""
        self.City.Interface._model.all_objects.all().delete()
        self.State.Interface._model.all_objects.all().delete()
        self.Country.Interface._model.all_objects.all().delete()

        run_registered_startup_hooks(interfaces=[self.City.Interface])

        self.assertEqual(self.Country.Interface._model.objects.count(), 2)
        self.assertEqual(self.State.Interface._model.objects.count(), 3)
        self.assertEqual(self.City.Interface._model.objects.count(), 3)

        nyc = self.City.filter(name="New York City").first()
        self.assertIsNotNone(nyc)
        self.assertEqual(nyc.state.code, "NY")
        self.assertEqual(nyc.state.country.code, "US")

    def test_dependency_resolver_identifies_related_interfaces(self) -> None:
        """Verify dependency resolver correctly identifies related read-only interfaces."""
        from general_manager.interface.capabilities.read_only import (
            ReadOnlyManagementCapability,
        )

        capability = self.City.Interface.require_capability(
            "read_only_management",
            expected_type=ReadOnlyManagementCapability,
        )

        resolver = capability.get_startup_hook_dependency_resolver(self.City.Interface)
        dependencies = resolver(self.City.Interface)

        self.assertIn(self.State.Interface, dependencies)

        state_dependencies = resolver(self.State.Interface)
        self.assertIn(self.Country.Interface, state_dependencies)

    def test_circular_dependency_handling(self) -> None:
        """Verify system handles potential circular references gracefully."""
        run_registered_startup_hooks(interfaces=[self.City.Interface])
        run_registered_startup_hooks(interfaces=[self.City.Interface])
        self.assertEqual(self.City.Interface._model.objects.count(), 3)


class ReadOnlyActivationManagerTests(GeneralManagerTransactionTestCase):
    """Integration tests for all_objects activation handling."""

    @classmethod
    def setUpClass(cls):
        """Set up simple Status manager."""

        class Status(GeneralManager):
            code: str
            label: str

            _data: ClassVar[list[dict[str, str]]] = [
                {"code": "ACTIVE", "label": "Active"},
                {"code": "INACTIVE", "label": "Inactive"},
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=20, unique=True)
                label = CharField(max_length=100)

                class Meta:
                    app_label = "general_manager"

        cls.Status = Status
        cls.general_manager_classes = [Status]

    def test_uses_all_objects_for_activation_when_available(self) -> None:
        """Verify sync uses all_objects manager for is_active updates."""
        run_registered_startup_hooks(interfaces=[self.Status.Interface])
        self.assertEqual(self.Status.Interface._model.objects.count(), 2)
        status = self.Status.Interface._model.objects.get(code="ACTIVE")
        status.is_active = False
        status.save()

        self.assertEqual(self.Status.Interface._model.objects.count(), 1)
        self.assertEqual(self.Status.Interface._model.all_objects.count(), 2)

        run_registered_startup_hooks(interfaces=[self.Status.Interface])
        self.assertEqual(self.Status.Interface._model.objects.count(), 2)

    def test_processed_pks_bulk_activation(self) -> None:
        """Verify bulk is_active=True update for processed PKs."""
        self.Status.Interface._model.all_objects.all().update(is_active=False)
        self.assertEqual(self.Status.Interface._model.objects.count(), 0)

        run_registered_startup_hooks(interfaces=[self.Status.Interface])
        self.assertEqual(self.Status.Interface._model.objects.count(), 2)


class ReadOnlyRecursionPreventionIntegrationTests(GeneralManagerTransactionTestCase):
    """Integration tests for sync recursion prevention with real DB access."""

    @classmethod
    def setUpClass(cls):
        """Set up Alpha/Beta managers with circular relations."""

        class Alpha(GeneralManager):
            code: str
            beta: "Beta | None"

            _data: ClassVar[list[dict[str, Any]]] = [{"code": "A1", "beta": None}]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=10, unique=True)
                beta = models.ForeignKey(
                    "general_manager.Beta",
                    on_delete=models.SET_NULL,
                    null=True,
                    blank=True,
                )

                class Meta:
                    app_label = "general_manager"

        class Beta(GeneralManager):
            code: str
            alpha: "Alpha | None"

            _data: ClassVar[list[dict[str, Any]]] = [{"code": "B1", "alpha": None}]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=10, unique=True)
                alpha = models.ForeignKey(
                    "general_manager.Alpha",
                    on_delete=models.SET_NULL,
                    null=True,
                    blank=True,
                )

                class Meta:
                    app_label = "general_manager"

        cls.Alpha = Alpha
        cls.Beta = Beta
        cls.general_manager_classes = [Alpha, Beta]

    def setUp(self) -> None:
        super().setUp()
        self.Alpha.Interface._model.all_objects.all().delete()
        self.Beta.Interface._model.all_objects.all().delete()

    def test_sync_handles_circular_relations(self) -> None:
        """Verify circular relations do not cause recursion errors."""
        run_registered_startup_hooks(managers=[self.Alpha])
        self.assertEqual(self.Alpha.Interface._model.objects.count(), 1)
        self.assertEqual(self.Beta.Interface._model.objects.count(), 1)


class ReadOnlySchemaConcreteFieldsIntegrationTests(GeneralManagerTransactionTestCase):
    """Integration tests for schema validation with non-concrete fields."""

    @classmethod
    def setUpClass(cls):
        """Set up Tag and Item managers with M2M relation."""

        class Tag(GeneralManager):
            name: str

            _data: ClassVar[list[dict[str, str]]] = [
                {"name": "alpha"},
                {"name": "beta"},
            ]

            class Interface(ReadOnlyInterface):
                name = CharField(max_length=50, unique=True)

                class Meta:
                    app_label = "general_manager"

        class Item(GeneralManager):
            code: str
            tags: list[Tag]

            _data: ClassVar[list[dict[str, Any]]] = [
                {"code": "ITEM1", "tags": [{"name": "alpha"}]}
            ]

            class Interface(ReadOnlyInterface):
                code = CharField(max_length=20, unique=True)
                tags = models.ManyToManyField(Tag.Interface._model)

                class Meta:
                    app_label = "general_manager"

        cls.Tag = Tag
        cls.Item = Item
        cls.general_manager_classes = [Tag, Item]

    def setUp(self) -> None:
        super().setUp()
        self.Tag.Interface._model.all_objects.all().delete()
        self.Item.Interface._model.all_objects.all().delete()

    def test_schema_validation_ignores_m2m_fields(self) -> None:
        """Verify ensure_schema_is_up_to_date ignores non-concrete M2M fields."""
        capability = self.Item.Interface.require_capability(
            "read_only_management",
            expected_type=ReadOnlyManagementCapability,
        )
        warnings = capability.ensure_schema_is_up_to_date(
            self.Item.Interface,
            self.Item,
            self.Item.Interface._model,
        )
        self.assertEqual(warnings, [])
