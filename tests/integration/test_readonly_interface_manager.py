from django.db.models import CharField
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.readOnlyInterface import ReadOnlyInterface
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class ReadOnlyIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class TestCountry(GeneralManager):
            _data = [
                {"code": "US", "name": "United States"},
                {"code": "DE", "name": "Germany"},
            ]

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
        self.TestCountry.Interface.syncData()

    def test_sync_populates_database(self):
        countries = self.TestCountry.all()
        self.assertEqual(countries.count(), 2)
        codes = {c.code for c in countries}
        self.assertEqual(codes, {"US", "DE"})

    def test_create_not_allowed(self):
        with self.assertRaises(NotImplementedError):
            self.TestCountry.create(
                code="FR", name="France", ignore_permission=True
            )

    def test_update_not_allowed(self):
        country = self.TestCountry.filter(code="US").first()
        self.assertIsNotNone(country)
        with self.assertRaises(NotImplementedError):
            country.update(  # type: ignore[arg-type]
                name="USA", ignore_permission=True
            )

    def test_filter_returns_correct_item(self):
        country = self.TestCountry.filter(code="DE").first()
        self.assertIsNotNone(country)
        self.assertEqual(country.name, "Germany")
