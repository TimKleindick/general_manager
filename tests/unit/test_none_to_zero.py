from django.test import SimpleTestCase
from general_manager.utils.none_to_zero import none_to_zero
from general_manager.measurement import Measurement


class TestNoneToZero(SimpleTestCase):
    def test_none_to_zero(self):
        """
        Tests the none_to_zero function to ensure it correctly converts None to 0.

        Verifies that the function returns 0 when given None, and returns the original value for non-None inputs.
        """
        self.assertEqual(none_to_zero(None), 0)
        self.assertEqual(none_to_zero(5), 5)
        self.assertEqual(none_to_zero(3.14), 3.14)
        self.assertEqual(none_to_zero(Measurement(5, "kg")), Measurement(5, "kg"))

    def test_falsey_non_none_values_are_preserved(self):
        """Falsey numeric values are not replaced when they are not None."""
        self.assertEqual(none_to_zero(0), 0)
        self.assertEqual(none_to_zero(0.0), 0.0)

    def test_measurement_instance_is_returned_unchanged(self):
        """Measurement values are passed through without copying or coercion."""
        measurement = Measurement(0, "kg")

        result = none_to_zero(measurement)

        self.assertIs(result, measurement)
