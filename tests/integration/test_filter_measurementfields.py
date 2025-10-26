# type: ignore
from django.db import models
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.measurement import Measurement, MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from django.core.exceptions import ValidationError


class DatabaseIntegrationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Defines a test model `TestHuman` with a measurement field and assigns it to class variables for use in integration tests.
        """

        class TestHuman(GeneralManager):
            name: str
            height: Measurement

            class Interface(DatabaseInterface):
                name = models.CharField(max_length=50)
                height = MeasurementField(base_unit="cm")

        cls.TestHuman = TestHuman
        cls.general_manager_classes = [TestHuman]

    def setUp(self):
        """
        Creates two test `TestHuman` instances with predefined names and heights for use in integration tests.
        """
        super().setUp()
        self.test_human1 = self.TestHuman.create(
            creator_id=None,
            name="Alice",
            height=Measurement(170, "cm"),
            ignore_permission=True,  # Ignore permission for testing
        )

        self.test_human2 = self.TestHuman.create(
            creator_id=None,
            name="Bob",
            height=Measurement(180, "cm"),
            ignore_permission=True,  # Ignore permission for testing
        )

    def test_measurement_fields(self):
        """
        Test creation, retrieval, and filtering of model instances with a measurement field.

        Verifies that measurement values are stored and represented with correct units, and that filtering by measurement values in different units returns the expected instances.
        """
        humans = self.TestHuman.all()
        self.assertEqual(len(humans), 2)

        # Check if the height is stored correctly
        self.assertEqual(humans[0].height, "170 cm")
        self.assertEqual(humans[1].height, "180 cm")

        # Test filtering by measurement field
        filtered_humans = self.TestHuman.filter(height="170 cm")
        self.assertEqual(len(filtered_humans), 1)
        human = filtered_humans.first()
        self.assertEqual(human.name, "Alice")
        filtered_humans_2 = self.TestHuman.filter(height="1.7 m")
        self.assertEqual(len(filtered_humans_2), 1)
        human_2 = filtered_humans_2.first()
        self.assertEqual(human_2, human)

    def test_measurement_field_filtering(self):
        # Test filtering by measurement field (greater than or equal to)
        """
        Test filtering of model instances using comparison operators on a measurement field.

        Verifies that filtering by greater than, greater than or equal, less than, and less than or equal conditions on the measurement field returns the correct set of instances, including correct handling of unit conversions.
        """
        filtered_humans = self.TestHuman.filter(height__gte="165 cm")
        self.assertEqual(len(filtered_humans), 2)
        filtered_humans = self.TestHuman.filter(height__gt="180 cm")
        self.assertEqual(len(filtered_humans), 0)
        filtered_humans = self.TestHuman.filter(height__lt="2 m")
        self.assertEqual(len(filtered_humans), 2)
        filtered_humans = self.TestHuman.filter(height__lte="170 cm")
        self.assertEqual(len(filtered_humans), 1)
        human = filtered_humans.first()
        self.assertEqual(human.name, "Alice")

    def test_measurement_field_operations(self):
        # Test addition of measurements
        """
        Test arithmetic operations on the measurement field of a model instance.

        Verifies that addition, subtraction, multiplication, and division operations on the `height` measurement field correctly update its value and maintain unit consistency.
        """
        human = self.TestHuman.create(
            creator_id=None,
            name="Charlie",
            height=Measurement(170, "cm"),
            ignore_permission=True,  # Ignore permission for testing
        )
        human.height += Measurement(10, "cm")
        self.assertEqual(human.height, "180 cm")

        # Test subtraction of measurements
        human.height -= Measurement(5, "cm")
        self.assertEqual(human.height, "175 cm")

        # Test multiplication of measurement value
        human.height *= 2
        self.assertEqual(human.height, "350 cm")

        # Test division of measurement value
        human.height /= 2
        self.assertEqual(human.height, "175 cm")

        updated_human = human.update(height=human.height, ignore_permission=True)
        self.assertEqual(updated_human.height, "175 cm")

    def test_measurement_field_validation(self):
        """
        Test that invalid measurement values for the height field raise a ValidationError.

        Verifies that creating a TestHuman instance with an incompatible unit or a null value for the height field results in a ValidationError.
        """
        with self.assertRaises(ValidationError):
            self.TestHuman.Interface.create(
                creator_id=None,
                name="Charlie",
                height=Measurement(170, "liter"),
            )

        with self.assertRaises(ValidationError):
            self.TestHuman.Interface.create(
                creator_id=None,
                name="Dave",
                height=None,
            )
