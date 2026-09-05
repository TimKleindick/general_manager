from __future__ import annotations

import pickle
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase
from openpyxl import Workbook

from general_manager.bucket.excel_bucket import (
    ExcelBucket,
    ExcelBucketLookupError,
    ExcelSingleItemRequiredError,
)
from general_manager.cache.cache_tracker import DependencyTracker
from general_manager.cache.dependency_index import serialize_dependency_identifier
from general_manager.interface import ExcelInterface
from general_manager.interface.excel import ExcelCharField, ExcelMeta
from general_manager.manager.general_manager import GeneralManager


def save_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Products"
    sheet.append(["sku", "name"])
    sheet.append(["SKU-1", "Alpha"])
    sheet.append(["SKU-2", "Beta"])
    workbook.save(path)


class PickleProduct(GeneralManager):
    class Interface(ExcelInterface):
        sku = ExcelCharField(unique=True)
        name = ExcelCharField()

        class Meta:
            workbook = "task12-pickle.xlsx"
            sheet = "Products"
            header_row = 1
            key = "sku"


class TempPathMixin:
    def setUp(self) -> None:
        super().setUp()
        self._tempdir = TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)

    def temp_path(self, name: str) -> Path:
        return Path(self._tempdir.name) / name


class ExcelBucketTests(TempPathMixin, SimpleTestCase):
    def test_collection_helpers_preserve_row_order_and_identity(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.all()

        self.assertEqual(bucket.first().sku, "SKU-1")
        self.assertEqual(bucket.last().sku, "SKU-2")
        self.assertEqual(bucket.get(sku="SKU-2").name, "Beta")
        self.assertEqual(bucket[1].sku, "SKU-2")
        self.assertEqual(len(bucket), 2)
        self.assertIn(Product(sku="SKU-1"), bucket)
        self.assertEqual(
            [item.sku for item in bucket.sort("-name", "-sku")],
            ["SKU-2", "SKU-1"],
        )
        self.assertIsNone(bucket.none().first())
        self.assertIsNone(bucket.none().last())

        with self.assertRaises(ExcelSingleItemRequiredError):
            bucket.get()

    def test_filter_returns_matching_managers_and_tracks_dependency(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with DependencyTracker() as dependencies:
            bucket = Product.filter(name="Alpha")
            self.assertIsInstance(bucket, ExcelBucket)
            self.assertEqual([item.sku for item in bucket], ["SKU-1"])

        self.assertIn(
            (
                "Product",
                "filter",
                serialize_dependency_identifier({"name": "Alpha"}),
            ),
            dependencies,
        )

    def test_exact_lookup_tracks_same_dependency_as_bare_field(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with DependencyTracker() as bare_dependencies:
            self.assertEqual(Product.filter(name="Alpha").count(), 1)
        with DependencyTracker() as exact_dependencies:
            self.assertEqual(Product.filter(name__exact="Alpha").count(), 1)

        self.assertEqual(exact_dependencies, bare_dependencies)

    def test_all_tracks_all_dependency(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with DependencyTracker() as dependencies:
            self.assertEqual(Product.all().count(), 2)

        self.assertIn(("Product", "all", ""), dependencies)

    def test_same_manager_union_combines_excel_buckets(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.filter(name="Alpha") | Product.filter(name="Beta")

        self.assertEqual([item.sku for item in bucket], ["SKU-1", "SKU-2"])

    def test_same_manager_instance_union_uses_excel_key(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product(sku="SKU-1") | Product(sku="SKU-2")

        self.assertIsInstance(bucket, ExcelBucket)
        self.assertEqual([item.sku for item in bucket], ["SKU-1", "SKU-2"])

    def test_with_instances_preserves_duplicate_object_references(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        outside = Product(sku="SKU-2")
        subset = Product.filter(name="Alpha").with_instances([outside, outside])

        self.assertEqual(list(subset), [outside, outside])
        self.assertIs(subset[0], outside)

    def test_native_exclude_preserves_each_call_group(self) -> None:
        """Excel exclusions negate a conjunction from each exclude call."""
        path = self.temp_path("rows.xlsx")
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Rows"
        sheet.append(["sku", "a", "b"])
        sheet.append(["SKU-1", "1", "1"])
        sheet.append(["SKU-2", "1", "2"])
        sheet.append(["SKU-3", "2", "2"])
        workbook.save(path)

        class Row(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                a = ExcelCharField()
                b = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Rows"
                    header_row = 1
                    key = "sku"

        self.assertEqual(
            [item.sku for item in Row.exclude(a="1", b="1")], ["SKU-2", "SKU-3"]
        )
        self.assertEqual(
            [item.sku for item in Row.exclude(a="1").exclude(b="1")], ["SKU-3"]
        )
        self.assertEqual(
            [item.sku for item in Row.filter(a="1").filter()], ["SKU-1", "SKU-2"]
        )
        self.assertEqual(
            [item.sku for item in Row.exclude()], ["SKU-1", "SKU-2", "SKU-3"]
        )
        self.assertEqual(
            [item.sku for item in Row.exclude(a="1", b="1").exclude()],
            ["SKU-2", "SKU-3"],
        )

    def test_cross_manager_excel_bucket_union_raises_type_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        class ArchivedProduct(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            TypeError,
            "Cannot union ExcelBucket for Product with ExcelBucket for ArchivedProduct",
        ):
            Product.all() | ArchivedProduct.all()

    def test_unsupported_union_operand_raises_type_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            TypeError,
            "Cannot union ExcelBucket for Product with object",
        ):
            Product.all() | object()  # type: ignore[operator]

    def test_filtered_all_preserves_filter_state(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.filter(name="Alpha").all()

        self.assertEqual([item.sku for item in bucket], ["SKU-1"])

    def test_none_all_preserves_empty_state(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        self.assertEqual(Product.all().none().all().count(), 0)

    def test_keyed_all_preserves_keyed_state(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.all()[1:].all()

        self.assertEqual([item.sku for item in bucket], ["SKU-2"])

    def test_repeated_key_chained_filter_accumulates_constraints(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.filter(name="Alpha").filter(name="Beta")

        self.assertEqual(bucket.count(), 0)

    def test_repeated_key_chained_exclude_accumulates_constraints(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.exclude(name="Alpha").exclude(name="Beta")

        self.assertEqual(bucket.count(), 0)

    def test_repeated_constraints_track_individual_scalar_dependencies(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with DependencyTracker() as dependencies:
            Product.exclude(name="Alpha").exclude(name="Beta").count()

        for value in ("Alpha", "Beta"):
            self.assertIn(
                (
                    "Product",
                    "exclude",
                    serialize_dependency_identifier({"name": value}),
                ),
                dependencies,
            )

    def test_exclude_translates_reserved_id_in_to_excel_key(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.exclude(id__in=[{"sku": "SKU-1"}])

        self.assertEqual([item.sku for item in bucket], ["SKU-2"])

    def test_chained_filter_translates_reserved_id_in_to_excel_key(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.all().filter(id__in=[{"sku": "SKU-1"}])

        self.assertEqual([item.sku for item in bucket], ["SKU-1"])

    def test_chained_exclude_translates_reserved_id_in_to_excel_key(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        bucket = Product.all().exclude(id__in=["SKU-1"])

        self.assertEqual([item.sku for item in bucket], ["SKU-2"])

    def test_filter_invalid_excel_field_lookup_raises_query_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            ExcelBucketLookupError,
            "Unknown Excel field lookup 'missing' for Product",
        ):
            Product.filter(missing="Alpha")

    def test_filter_invalid_excel_lookup_operator_raises_query_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            ExcelBucketLookupError,
            "Unknown Excel field lookup 'name__bogus' for Product",
        ):
            Product.filter(name__bogus="Alpha")

    def test_exclude_invalid_excel_field_lookup_raises_query_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            ExcelBucketLookupError,
            "Unknown Excel field lookup 'missing' for Product",
        ):
            Product.exclude(missing="Alpha")

    def test_exclude_invalid_excel_lookup_operator_raises_query_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            ExcelBucketLookupError,
            "Unknown Excel field lookup 'name__bogus' for Product",
        ):
            Product.exclude(name__bogus="Alpha")

    def test_sort_invalid_excel_field_raises_query_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            ExcelBucketLookupError,
            "Unknown Excel field lookup 'missing' for Product",
        ):
            Product.all().sort("missing")

    def test_tuple_sort_invalid_excel_field_raises_query_error(self) -> None:
        path = self.temp_path("products.xlsx")
        save_workbook(path)

        class Product(GeneralManager):
            class Interface(ExcelInterface):
                sku = ExcelCharField(unique=True)
                name = ExcelCharField()

                class Meta:
                    workbook = str(path)
                    sheet = "Products"
                    header_row = 1
                    key = "sku"

        with self.assertRaisesRegex(
            ExcelBucketLookupError,
            "Unknown Excel field lookup 'missing' for Product",
        ):
            Product.all().sort("name", "missing")


class ExcelBucketPickleTests(TempPathMixin, SimpleTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.workbook_path = self.temp_path("pickle-products.xlsx")
        save_workbook(self.workbook_path)
        self.original_meta = PickleProduct.Interface.excel_meta
        PickleProduct.Interface.excel_meta = ExcelMeta(
            workbook=str(self.workbook_path),
            sheet="Products",
            header_row=1,
            key="sku",
        )
        self.addCleanup(self._restore_meta)

    def _restore_meta(self) -> None:
        PickleProduct.Interface.excel_meta = self.original_meta

    def test_pickle_preserves_excel_constraints_identity_and_order(self) -> None:
        filters = (("sku__in", ["SKU-1", "SKU-2"]),)
        excludes = (("name__exact", "Never"),)
        filter_groups = (
            (("sku__in", ["SKU-1", "SKU-2"]),),
            (("name__contains", "a"),),
        )
        exclude_groups = (
            (("name__exact", "Never"),),
            (("sku__exact", "NOPE"),),
        )
        bucket = ExcelBucket(
            PickleProduct,
            PickleProduct.Interface,
            filters=filters,
            excludes=excludes,
            keys=("SKU-2", "SKU-1"),
            filter_groups=filter_groups,
            exclude_groups=exclude_groups,
        )

        restored = pickle.loads(pickle.dumps(bucket))  # noqa: S301 - local test data

        self.assertIs(restored._manager_class, PickleProduct)
        self.assertIs(restored._interface_cls, PickleProduct.Interface)
        self.assertEqual(restored._filters, filters)
        self.assertEqual(restored._excludes, excludes)
        self.assertEqual(restored._filter_groups, filter_groups)
        self.assertEqual(restored._exclude_groups, exclude_groups)
        self.assertEqual(restored._keys, ("SKU-2", "SKU-1"))
        self.assertEqual([item.sku for item in restored], ["SKU-2", "SKU-1"])
