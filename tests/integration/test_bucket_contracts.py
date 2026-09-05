"""Shared public Bucket ordering contracts across native backends."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from typing import ClassVar
from uuid import UUID

from django.db.models import CharField, IntegerField
from openpyxl import Workbook

from general_manager.api.property import graph_ql_property
from general_manager.bucket._ordering import normalize_ordering, sort_items
from general_manager.interface import (
    CalculationInterface,
    DatabaseInterface,
    ExcelInterface,
    RequestInterface,
)
from general_manager.interface.excel import ExcelCharField, ExcelIntegerField
from general_manager.interface.requests import (
    RequestField,
    RequestFilter,
    RequestQueryOperation,
    RequestTransportConfig,
)
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class BucketOrderingContracts(GeneralManagerTransactionTestCase):
    """Exercise public ordering rather than helper-only behavior."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = TemporaryDirectory()
        cls.addClassCleanup(cls._tempdir.cleanup)
        workbook_path = Path(cls._tempdir.name) / "rows.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Rows"
        sheet.append(["id", "name", "date"])
        sheet.append([1, "a", 1])
        sheet.append([2, "a", 2])
        sheet.append([3, "b", None])
        workbook.save(workbook_path)

        class DatabaseRow(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=20)
                date = IntegerField(null=True, blank=True)

        class CalculationRow(GeneralManager):
            class Interface(CalculationInterface):
                id = Input(int, possible_values=(1, 2, 3))

            @graph_ql_property(sortable=True)
            def name(self) -> str:
                return {1: "a", 2: "a", 3: "b"}[self.id]

            @graph_ql_property(sortable=True)
            def date(self) -> int | None:
                return {1: 1, 2: 2, 3: None}[self.id]

        class ExcelRow(GeneralManager):
            class Interface(ExcelInterface):
                id = ExcelIntegerField(unique=True)
                name = ExcelCharField()
                date = ExcelIntegerField(required=False)

                class Meta:
                    workbook = str(workbook_path)
                    sheet = "Rows"
                    header_row = 1
                    key = "id"

        class RequestRow(GeneralManager):
            class Interface(RequestInterface):
                id = Input(int)
                name = RequestField(str)
                date = RequestField(int | None)

                class Meta:
                    filters: ClassVar[dict[str, RequestFilter]] = {
                        "name": RequestFilter(
                            remote_name="name", value_type=str, supports_exclude=True
                        )
                    }
                    query_operations: ClassVar[dict[str, RequestQueryOperation]] = {
                        "detail": RequestQueryOperation(
                            name="detail", method="GET", path="/rows/{id}"
                        ),
                        "list": RequestQueryOperation(
                            name="list", method="GET", path="/rows"
                        ),
                    }
                    transport_config = RequestTransportConfig(
                        base_url="https://rows.example.test", timeout=1
                    )

        cls.DatabaseRow = DatabaseRow
        cls.CalculationRow = CalculationRow
        cls.ExcelRow = ExcelRow
        cls.RequestRow = RequestRow
        cls.general_manager_classes = [
            DatabaseRow,
            CalculationRow,
            ExcelRow,
            RequestRow,
        ]
        super().setUpClass()

    def setUp(self) -> None:
        super().setUp()
        self.DatabaseRow.create(name="a", date=1, ignore_permission=True)
        self.DatabaseRow.create(name="a", date=2, ignore_permission=True)
        self.DatabaseRow.create(name="b", date=None, ignore_permission=True)

    def _assert_ordering_contract(self, bucket: object) -> None:
        typed_bucket = bucket
        self.assertEqual(
            [row.id for row in typed_bucket.sort("name", "-date")], [2, 1, 3]
        )
        self.assertEqual([row.id for row in typed_bucket.sort("date")], [1, 2, 3])
        self.assertEqual([row.id for row in typed_bucket.sort("-date")], [2, 1, 3])
        self.assertEqual(
            [row.id for row in typed_bucket.sort("-date").sort("date")],
            [1, 2, 3],
        )
        self.assertEqual(
            [row.id for row in typed_bucket.sort("name", "-date").sort()],
            [2, 1, 3],
        )
        self.assertEqual(
            [row.id for row in typed_bucket.sort("name", "-date").all()],
            [2, 1, 3],
        )
        self.assertEqual(
            [row.id for row in typed_bucket.sort("name", "-date").filter(name="a")],
            [2, 1],
        )
        self.assertEqual(
            [row.id for row in typed_bucket.sort("name", "-date").exclude(name="b")],
            [2, 1],
        )
        self.assertEqual(
            [
                row.id
                for row in typed_bucket[:0]
                .all()
                .filter(name="a")
                .exclude(name="b")
                .sort("name", "-date")
            ],
            [],
        )
        instances = tuple(typed_bucket)
        subset = typed_bucket.with_instances((instances[2], instances[0]))
        self.assertEqual([row.id for row in subset], [3, 1])
        self.assertEqual([row.id for row in subset.sort("name", "-date")], [1, 3])
        exact_instances = typed_bucket[:1].with_instances(
            (instances[-1], instances[-1])
        )
        exact_rows = list(exact_instances)
        self.assertEqual([row.id for row in exact_rows], [3, 3])
        self.assertIs(exact_rows[0], instances[-1])
        self.assertIs(exact_rows[1], instances[-1])
        shuffled = typed_bucket.with_instances(
            (instances[2], instances[0], instances[1])
        )
        self.assertEqual([row.id for row in shuffled.sort("name")], [1, 2, 3])
        duplicate_identity = typed_bucket.with_instances((instances[-1], instances[-1]))
        duplicate_rows = list(duplicate_identity.sort("name"))
        self.assertEqual([row.id for row in duplicate_rows], [3, 3])
        self.assertIs(duplicate_rows[0], instances[-1])
        self.assertIs(duplicate_rows[1], instances[-1])

    def test_database_calculation_and_excel_native_ordering(self) -> None:
        self._assert_ordering_contract(self.DatabaseRow.all())
        self._assert_ordering_contract(self.CalculationRow.all())
        self._assert_ordering_contract(self.ExcelRow.all())

    def test_request_native_ordering_and_exact_subsets(self) -> None:
        rows = (
            self.RequestRow(id=1),
            self.RequestRow(id=2),
            self.RequestRow(id=3),
        )
        for row, name, date_value in zip(
            rows, ("a", "a", "b"), (1, 2, None), strict=True
        ):
            row.name = name
            row.date = date_value
        bucket = self.RequestRow.all()._from_items(rows)
        self._assert_ordering_contract(bucket)

    def test_shared_ordering_uses_complete_manager_identity_for_equal_values(
        self,
    ) -> None:
        """Every identity-bearing in-memory seam resolves equal sort values alike."""
        rows = [
            SimpleNamespace(id=3, name="a", identification={"id": 3}),
            SimpleNamespace(id=1, name="a", identification={"id": 1}),
            SimpleNamespace(id=2, name="a", identification={"id": 2}),
        ]

        ordered = sort_items(rows, normalize_ordering(("name",)))

        self.assertEqual([row.id for row in ordered], [1, 2, 3])

    def test_shared_ordering_uses_typed_complete_identity_components(self) -> None:
        """Numeric, nested, snapshot, and UUID identifiers must not use repr ties."""
        nested_first = SimpleNamespace(identification={"id": 2})
        nested_second = SimpleNamespace(identification={"id": 1})
        rows = [
            SimpleNamespace(
                id="decimal-ten",
                name="a",
                identification={"id": Decimal("10")},
            ),
            SimpleNamespace(
                id="decimal-two",
                name="a",
                identification={"id": Decimal("2")},
            ),
            SimpleNamespace(
                id="nested-two",
                name="a",
                identification={"owner": nested_first},
            ),
            SimpleNamespace(
                id="nested-one",
                name="a",
                identification={"owner": nested_second},
            ),
        ]
        snapshot_rows = [
            SimpleNamespace(
                id="newer",
                name="a",
                identification={"id": 1},
                _effective_search_date=date(2024, 2, 1),
            ),
            SimpleNamespace(
                id="older",
                name="a",
                identification={"id": 1},
                _effective_search_date=date(2024, 1, 1),
            ),
        ]
        uuid_rows = [
            SimpleNamespace(
                id="two",
                name="a",
                identification={"id": UUID("00000000-0000-0000-0000-000000000002")},
            ),
            SimpleNamespace(
                id="one",
                name="a",
                identification={"id": UUID("00000000-0000-0000-0000-000000000001")},
            ),
        ]
        float_rows = [
            SimpleNamespace(id="ten", name="a", identification={"id": 10.0}),
            SimpleNamespace(id="two", name="a", identification={"id": 2.0}),
        ]
        identity_less_rows = [
            SimpleNamespace(id=3, name="a"),
            SimpleNamespace(id=1, name="a"),
        ]

        ordered = sort_items(rows, normalize_ordering(("name",)))
        ordered_snapshots = sort_items(snapshot_rows, normalize_ordering(("name",)))
        ordered_uuids = sort_items(uuid_rows, normalize_ordering(("name",)))
        ordered_floats = sort_items(float_rows, normalize_ordering(("name",)))
        ordered_identity_less = sort_items(
            identity_less_rows, normalize_ordering(("name",))
        )

        self.assertLess(ordered.index(rows[1]), ordered.index(rows[0]))
        self.assertLess(ordered.index(rows[3]), ordered.index(rows[2]))
        self.assertEqual([row.id for row in ordered_snapshots], ["older", "newer"])
        self.assertEqual([row.id for row in ordered_uuids], ["one", "two"])
        self.assertEqual([row.id for row in ordered_floats], ["two", "ten"])
        self.assertEqual([row.id for row in ordered_identity_less], [3, 1])

    def test_rejects_plus_and_unknown_nested_paths_before_evaluation(self) -> None:
        bucket = self.DatabaseRow.all()[:0]
        with self.assertRaises(ValueError):
            self.CalculationRow.all().sort("+name")
        with self.assertRaises(ValueError):
            self.CalculationRow.all().sort("-")
        with self.assertRaises(ValueError):
            self.CalculationRow.all().sort("name", "-name")
        with self.assertRaisesRegex(Exception, "name__missing"):
            bucket.sort("name__missing")
