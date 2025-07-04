from django.test import SimpleTestCase
from general_manager.auxiliary.noneToZero import noneToZero
from general_manager.measurement import Measurement


class TestNoneToZero(SimpleTestCase):

    def test_none_to_zero(self):
        """
        Tests the noneToZero function to ensure it correctly converts None to 0.

        Verifies that the function returns 0 when given None, and returns the original value for non-None inputs.
        """
        self.assertEqual(noneToZero(None), 0)
        self.assertEqual(noneToZero(5), 5)
        self.assertEqual(noneToZero(3.14), 3.14)
        self.assertEqual(noneToZero(Measurement(5, "kg")), Measurement(5, "kg"))
