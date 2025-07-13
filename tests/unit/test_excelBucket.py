from django.test import TestCase
from general_manager.bucket.excelBucket import ExcelBucket
from general_manager.interface.excelInterface import ExcelInterface
from general_manager.interface.excelDataField import ExcelDataField


class DummyExcelInterface(ExcelInterface):
    input_fields = {}
    data_fields = {"a": ExcelDataField(int), "b": ExcelDataField(str)}


class DummyManager:
    Interface = DummyExcelInterface

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __eq__(self, other):
        return isinstance(other, DummyManager) and self.kwargs == other.kwargs


class TestExcelBucket(TestCase):
    def setUp(self):
        self.data = [
            {"a": 1, "b": "x"},
            {"a": 2, "b": "y"},
            {"a": 3, "b": "z"},
        ]
        self.bucket = ExcelBucket(DummyManager, self.data)  # type: ignore

    def test_iteration_and_length(self):
        values = list(self.bucket)
        self.assertEqual(len(values), 3)
        self.assertIsInstance(values[0], DummyManager)
        self.assertEqual(len(self.bucket), 3)
        self.assertEqual(self.bucket.count(), 3)

    def test_filter_and_exclude(self):
        filtered = self.bucket.filter(a__gt=1)
        self.assertEqual(len(filtered), 2)
        excluded = filtered.exclude(b__contains="z")
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded.first().kwargs["a"], 2)  # type: ignore

    def test_first_last_all(self):
        self.assertEqual(self.bucket.first(), DummyManager(a=1, b="x"))
        self.assertEqual(self.bucket.last(), DummyManager(a=3, b="z"))
        self.assertIs(self.bucket.all(), self.bucket)

    def test_getitem_and_contains(self):
        self.assertEqual(self.bucket[1], DummyManager(a=2, b="y"))
        sliced = self.bucket[1:]
        self.assertIsInstance(sliced, ExcelBucket)
        self.assertEqual(len(sliced), 2)
        self.assertIn(DummyManager(a=2, b="y"), self.bucket)

    def test_sort(self):
        sorted_bucket = self.bucket.sort("a", reverse=True)
        self.assertEqual(sorted_bucket.first().kwargs["a"], 3)  # type: ignore
