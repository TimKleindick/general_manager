from django.test import TestCase
from datetime import date, datetime
from unittest.mock import patch

from general_manager.interface.excelDataField import ExcelDataField
from general_manager.measurement import Measurement


class TestExcelDataField(TestCase):
    def test_initialization(self):
        field = ExcelDataField(int, default=0, is_required=False)
        self.assertEqual(field.type, int)
        self.assertEqual(field.default, 0)
        self.assertFalse(field.is_required)
        self.assertFalse(field.is_manager)
        self.assertIs(field.python_type, int)

    def test_cast_basic_types(self):
        field = ExcelDataField(int)
        self.assertEqual(field.cast("5"), 5)
        self.assertEqual(field.cast(3.0), 3)

    def test_cast_general_manager(self):
        class MockGM:
            def __init__(self, id):
                self.id = id

        with patch("general_manager.interface.excelDataField.issubclass", return_value=True):
            field = ExcelDataField(MockGM)
            self.assertEqual(field.cast({"id": 1}).id, 1)
            self.assertEqual(field.cast(2).id, 2)

    def test_cast_date_and_datetime(self):
        field_date = ExcelDataField(date)
        self.assertEqual(field_date.cast("2023-10-01"), date(2023, 10, 1))
        self.assertEqual(field_date.cast(datetime(2023, 10, 1, 12, 0)), date(2023, 10, 1))

        field_dt = ExcelDataField(datetime)
        self.assertEqual(field_dt.cast("2023-10-01T12:00:00"), datetime(2023, 10, 1, 12, 0))
        self.assertEqual(field_dt.cast(date(2023, 10, 1)), datetime(2023, 10, 1))

    def test_cast_measurement(self):
        field = ExcelDataField(Measurement)
        self.assertEqual(field.cast("1 m"), Measurement(1, "m"))
        self.assertEqual(field.cast(Measurement(2, "m")), Measurement(2, "m"))
