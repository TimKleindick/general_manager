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
