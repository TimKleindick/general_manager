from django.db.models import CharField
from typing import ClassVar
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.read_only_interface import ReadOnlyInterface
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class ReadOnlyIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Define a TestCountry GeneralManager subclass with a read-only Interface and register it on the test class.

        Creates an inner TestCountry class that exposes two seeded records (codes "US" and "DE") via a class-level `_data` list, defines `code` and `name` fields, and provides a read-only `Interface` with corresponding CharField definitions. Assigns this class to `cls.TestCountry` and adds it to `cls.general_manager_classes` and `cls.read_only_classes` for use by the tests.
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
        cls.read_only_classes = [TestCountry]

    def setUp(self):
        super().setUp()
        self.TestCountry.Interface.syncData()  # type: ignore

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
