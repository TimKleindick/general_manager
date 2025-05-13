from django.test import TestCase
from general_manager.auxiliary.noneToZero import noneToZero


class TestNoneToZero(TestCase):

    def test_none_to_zero(self):
        """
        Tests the noneToZero function to ensure it correctly converts None to 0.

        Verifies that the function returns 0 when given None, and returns the original value for non-None inputs.
        """
        self.assertEqual(noneToZero(None), 0)
        self.assertEqual(noneToZero(5), 5)
