"""GraphQL grouping parity across the native data interfaces."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar, cast

from django.contrib.auth import get_user_model
from django.db.models import CASCADE, BooleanField, CharField, DecimalField, DateField
from django.db.models import ForeignKey
from django.utils.crypto import get_random_string
from openpyxl import Workbook

from general_manager.api.property import graph_ql_property
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.interface import ExcelInterface
from general_manager.interface.excel import (
    ExcelCharField,
    ExcelDecimalField,
    ExcelField,
    ExcelIntegerField,
)
from general_manager.manager import GeneralManager, Input
from general_manager.measurement import Measurement
from general_manager.measurement.measurement_field import MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase


_PARITY_ROWS: tuple[dict[str, object], ...] = (
    {
        "id": 1,
        "status": "alpha",
        "label": "first",
        "amount": Decimal("1.25"),
        "optional_amount": None,
        "enabled": False,
        "occurred": date(2024, 1, 1),
        "distance": Measurement(1, "meter"),
    },
    {
        "id": 2,
        "status": "alpha",
        "label": "second",
        "amount": Decimal("2.75"),
        "optional_amount": Decimal("0.25"),
        "enabled": True,
        "occurred": date(2024, 2, 1),
        "distance": Measurement(2, "meter"),
    },
    {
        "id": 3,
        "status": "alpha",
        "label": "first",
        "amount": Decimal("0.50"),
        "optional_amount": None,
        "enabled": False,
        "occurred": date(2024, 1, 15),
        "distance": Measurement(3, "meter"),
    },
    {
        "id": 4,
        "status": "beta",
        "label": "third",
        "amount": Decimal("4.00"),
        "optional_amount": None,
        "enabled": False,
        "occurred": date(2024, 3, 1),
        "distance": Measurement(4, "meter"),
    },
    {
        "id": 5,
        "status": "beta",
        "label": "third",
        "amount": Decimal("1.00"),
        "optional_amount": None,
        "enabled": False,
        "occurred": date(2024, 3, 2),
        "distance": Measurement(5, "meter"),
    },
)


def _parse_excel_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _parse_excel_measurement(value: object) -> Measurement:
    if isinstance(value, Measurement):
        return value
    return Measurement.from_string(str(value))


def _calculation_values(field_name: str):
    def values(row_id: int) -> list[object]:
        row = next(row for row in _PARITY_ROWS if row["id"] == row_id)
        value = row[field_name]
        if value is None:
            raise AssertionError(field_name)
        return [value]

    return values


class TestGraphQLGroupingParity(GeneralManagerTransactionTestCase):
    """Equivalent records expose equivalent grouped GraphQL pages."""

    _tempdir: ClassVar[TemporaryDirectory[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = TemporaryDirectory()
        cls.addClassCleanup(cls._tempdir.cleanup)
        workbook_path = Path(cls._tempdir.name) / "parity.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Rows"
        sheet.append(
            [
                "id",
                "status",
                "label",
                "amount",
                "optional_amount",
                "enabled",
                "occurred",
                "distance",
            ]
        )
        for row in _PARITY_ROWS:
            sheet.append(
                [
                    row["id"],
                    row["status"],
                    row["label"],
                    row["amount"],
                    row["optional_amount"],
                    row["enabled"],
                    row["occurred"],
                    str(row["distance"]),
                ]
            )
        workbook.save(workbook_path)
        workbook.close()

        class ParityDatabaseRow(GeneralManager):
            class Interface(DatabaseInterface):
                status = CharField(max_length=20)
                label = CharField(max_length=20)
                amount = DecimalField(max_digits=10, decimal_places=2)
                optional_amount = DecimalField(
                    max_digits=10,
                    decimal_places=2,
                    null=True,
                    blank=True,
                )
                enabled = BooleanField()
                occurred = DateField()
                distance = MeasurementField(base_unit="meter")

        class ParityExcelRow(GeneralManager):
            class Interface(ExcelInterface):
                id = ExcelIntegerField(unique=True)
                status = ExcelCharField()
                label = ExcelCharField()
                amount = ExcelDecimalField(decimal_places=2)
                optional_amount = ExcelDecimalField(
                    decimal_places=2,
                    required=False,
                )
                enabled = ExcelField(bool)
                occurred = ExcelField(date, parser=_parse_excel_date)
                distance = ExcelField(
                    Measurement,
                    parser=_parse_excel_measurement,
                )

                class Meta:
                    workbook = str(workbook_path)
                    sheet = "Rows"
                    header_row = 1
                    key = "id"

        class ParityRelationParent(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=20)

        class ParityCalculationRow(GeneralManager):
            class Interface(CalculationInterface):
                parent = Input(
                    ParityRelationParent,
                    possible_values=lambda: ParityRelationParent.all(),
                )
                row_id = Input(int, possible_values=tuple(range(1, 6)))
                status = Input(
                    str,
                    possible_values=_calculation_values("status"),
                    depends_on=["row_id"],
                )
                label = Input(
                    str,
                    possible_values=_calculation_values("label"),
                    depends_on=["row_id"],
                )
                amount = Input(
                    Decimal,
                    possible_values=_calculation_values("amount"),
                    depends_on=["row_id"],
                )
                enabled = Input(
                    bool,
                    possible_values=_calculation_values("enabled"),
                    depends_on=["row_id"],
                )
                occurred = Input(
                    date,
                    possible_values=_calculation_values("occurred"),
                    depends_on=["row_id"],
                )
                distance = Input(
                    Measurement,
                    possible_values=_calculation_values("distance"),
                    depends_on=["row_id"],
                )

            @graph_ql_property
            def optional_amount(self) -> Decimal | None:
                row_id = cast(int, self.row_id)
                return cast(
                    Decimal | None,
                    next(row for row in _PARITY_ROWS if row["id"] == row_id)[
                        "optional_amount"
                    ],
                )

        class ParityRelationChild(GeneralManager):
            class Interface(DatabaseInterface):
                status = CharField(max_length=20)
                amount = DecimalField(max_digits=10, decimal_places=2)
                parent = ForeignKey(
                    "general_manager.ParityRelationParent",
                    on_delete=CASCADE,
                )

        cls.DatabaseRow = ParityDatabaseRow
        cls.ExcelRow = ParityExcelRow
        cls.CalculationRow = ParityCalculationRow
        cls.RelationParent = ParityRelationParent
        cls.RelationChild = ParityRelationChild
        cls.general_manager_classes = [
            ParityDatabaseRow,
            ParityExcelRow,
            ParityCalculationRow,
            ParityRelationParent,
            ParityRelationChild,
        ]

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="graphql-grouping-parity",
            password=password,
            is_superuser=True,
        )
        self.client.force_login(self.user)

        self.calculation_parent = self.RelationParent.create(
            name="Calculation Parent",
            creator_id=self.user.id,
        )

        for row in _PARITY_ROWS:
            values = dict(row)
            values.pop("id")
            self.DatabaseRow.create(creator_id=self.user.id, **values)
        self.ExcelRow.Interface.sync_from_excel(force=True)

    @staticmethod
    def _query_fields(
        manager_field: str,
        *,
        grouped: bool,
        page: int | None = None,
        group_by: tuple[str, ...] | None = None,
        filter_expression: str | None = None,
        order_by: tuple[str, ...] = (),
        selection: str | None = None,
    ) -> str:
        field_name = f"{manager_field}{'Groups' if grouped else 'List'}"
        if grouped:
            keys = group_by or ("status",)
            quoted_keys = ", ".join(f'"{key}"' for key in keys)
            group_argument = f"groupBy: [{quoted_keys}]"
        else:
            group_argument = ""
        page_arguments = {
            1: "page: 1, pageSize: 1",
            2: "page: 2, pageSize: 1",
        }.get(page, "")
        filter_argument = (
            f"filter: {filter_expression}" if filter_expression is not None else ""
        )
        ordering_argument = (
            "orderBy: ["
            + ", ".join(
                f"{{field: {field.removeprefix('-')}, direction: {'DESC' if field.startswith('-') else 'ASC'}}}"
                for field in order_by
            )
            + "]"
            if order_by
            else ""
        )
        arguments = ", ".join(
            argument for argument in (group_argument, page_arguments) if argument
        )
        arguments = ", ".join(
            argument
            for argument in (arguments, filter_argument, ordering_argument)
            if argument
        )
        argument_text = f"({arguments})" if arguments else ""
        item_selection = (
            selection
            or """
                    __typename
                    status
                    label
                    amount
                    optionalAmount
                    enabled
                    occurred
                    distance(targetUnit: \"centimeter\") { value unit }
                """
        )
        return f"""
            {field_name}{argument_text} {{
                __typename
                items {{
                    {item_selection}
                }}
                pageInfo {{ __typename totalCount pageSize currentPage totalPages }}
            }}
        """

    def _assert_parity_payload(self, manager_field: str) -> None:
        response = self.query(
            f"""
            query {{
                ordinary: {self._query_fields(manager_field, grouped=False)}
                grouped: {self._query_fields(manager_field, grouped=True)}
            }}
            """
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]
        ordinary = payload["ordinary"]
        grouped = payload["grouped"]

        self.assertNotEqual(ordinary["__typename"], grouped["__typename"])
        self.assertEqual(
            ordinary["pageInfo"]["__typename"], grouped["pageInfo"]["__typename"]
        )
        self.assertNotEqual(
            {item["__typename"] for item in ordinary["items"]},
            {item["__typename"] for item in grouped["items"]},
        )
        self.assertEqual(ordinary["pageInfo"]["totalCount"], 5)
        self.assertEqual(grouped["pageInfo"]["totalCount"], 2)
        self.assertEqual(
            sorted(ordinary["items"], key=lambda item: (item["status"], item["label"])),
            [
                {
                    "__typename": ordinary["items"][0]["__typename"],
                    "status": "alpha",
                    "label": "first",
                    "amount": 1.25,
                    "optionalAmount": None,
                    "enabled": False,
                    "occurred": "2024-01-01",
                    "distance": {"value": 100.0, "unit": "centimeter"},
                },
                {
                    "__typename": ordinary["items"][0]["__typename"],
                    "status": "alpha",
                    "label": "first",
                    "amount": 0.5,
                    "optionalAmount": None,
                    "enabled": False,
                    "occurred": "2024-01-15",
                    "distance": {"value": 300.0, "unit": "centimeter"},
                },
                {
                    "__typename": ordinary["items"][0]["__typename"],
                    "status": "alpha",
                    "label": "second",
                    "amount": 2.75,
                    "optionalAmount": 0.25,
                    "enabled": True,
                    "occurred": "2024-02-01",
                    "distance": {"value": 200.0, "unit": "centimeter"},
                },
                {
                    "__typename": ordinary["items"][0]["__typename"],
                    "status": "beta",
                    "label": "third",
                    "amount": 4.0,
                    "optionalAmount": None,
                    "enabled": False,
                    "occurred": "2024-03-01",
                    "distance": {"value": 400.0, "unit": "centimeter"},
                },
                {
                    "__typename": ordinary["items"][0]["__typename"],
                    "status": "beta",
                    "label": "third",
                    "amount": 1.0,
                    "optionalAmount": None,
                    "enabled": False,
                    "occurred": "2024-03-02",
                    "distance": {"value": 500.0, "unit": "centimeter"},
                },
            ],
        )
        self.assertEqual(
            sorted(grouped["items"], key=lambda item: item["status"]),
            [
                {
                    "__typename": grouped["items"][0]["__typename"],
                    "status": "alpha",
                    "label": "first, second",
                    "amount": 4.5,
                    "optionalAmount": 0.25,
                    "enabled": True,
                    "occurred": "2024-02-01",
                    "distance": {"value": 600.0, "unit": "centimeter"},
                },
                {
                    "__typename": grouped["items"][0]["__typename"],
                    "status": "beta",
                    "label": "third",
                    "amount": 5.0,
                    "optionalAmount": None,
                    "enabled": False,
                    "occurred": "2024-03-02",
                    "distance": {"value": 900.0, "unit": "centimeter"},
                },
            ],
        )

    def test_database_excel_and_calculation_grouping_are_schema_and_value_parity(
        self,
    ) -> None:
        for manager_field in (
            "parityDatabaseRow",
            "parityExcelRow",
            "parityCalculationRow",
        ):
            with self.subTest(manager_field=manager_field):
                self._assert_parity_payload(manager_field)

    def test_grouped_pagination_counts_groups_after_aggregation(self) -> None:
        for manager_field in (
            "parityDatabaseRow",
            "parityExcelRow",
            "parityCalculationRow",
        ):
            with self.subTest(manager_field=manager_field):
                response = self.query(
                    f"""
                    query {{
                        grouped: {self._query_fields(manager_field, grouped=True, page=2)}
                    }}
                    """
                )
                self.assertResponseNoErrors(response)
                page = response.json()["data"]["grouped"]
                self.assertEqual(page["pageInfo"]["totalCount"], 2)
                self.assertEqual(page["pageInfo"]["pageSize"], 1)
                self.assertEqual(page["pageInfo"]["currentPage"], 2)
                self.assertEqual(page["pageInfo"]["totalPages"], 2)
                self.assertEqual([item["status"] for item in page["items"]], ["beta"])

    def test_multi_key_grouping_has_equivalent_keys_and_aggregates(self) -> None:
        selection = """
            __typename
            status
            enabled
            amount
        """
        expected = [
            ("alpha", False, 1.75),
            ("alpha", True, 2.75),
            ("beta", False, 5.0),
        ]
        for manager_field in (
            "parityDatabaseRow",
            "parityExcelRow",
            "parityCalculationRow",
        ):
            with self.subTest(manager_field=manager_field):
                response = self.query(
                    f"""
                    query {{
                        grouped: {
                        self._query_fields(
                            manager_field,
                            grouped=True,
                            group_by=("status", "enabled"),
                            order_by=("status", "enabled"),
                            selection=selection,
                        )
                    }
                    }}
                    """
                )
                self.assertResponseNoErrors(response)
                page = response.json()["data"]["grouped"]
                self.assertEqual(page["pageInfo"]["totalCount"], 3)
                self.assertEqual(
                    [
                        (item["status"], item["enabled"], item["amount"])
                        for item in page["items"]
                    ],
                    expected,
                )

    def test_empty_filtered_grouped_page_is_empty_for_each_backend(self) -> None:
        selection = """
            __typename
            status
            amount
        """
        for manager_field in (
            "parityDatabaseRow",
            "parityExcelRow",
            "parityCalculationRow",
        ):
            with self.subTest(manager_field=manager_field):
                response = self.query(
                    f"""
                    query {{
                        empty: {
                        self._query_fields(
                            manager_field,
                            grouped=True,
                            page=1,
                            filter_expression='{status: "missing"}',
                            selection=selection,
                        )
                    }
                    }}
                    """
                )
                self.assertResponseNoErrors(response)
                page = response.json()["data"]["empty"]
                self.assertEqual(page["items"], [])
                self.assertEqual(page["pageInfo"]["totalCount"], 0)
                self.assertEqual(page["pageInfo"]["pageSize"], 1)
                self.assertEqual(page["pageInfo"]["currentPage"], 1)
                self.assertEqual(page["pageInfo"]["totalPages"], 0)

    def test_groups_require_a_nonempty_group_by(self) -> None:
        for manager_field in (
            "parityDatabaseRow",
            "parityExcelRow",
            "parityCalculationRow",
        ):
            with self.subTest(manager_field=manager_field):
                response = self.query(
                    f"""
                    query {{
                        {manager_field}Groups {{ items {{ status }} }}
                    }}
                    """
                )
                self.assertResponseHasErrors(response)
                self.assertIn("groupBy", response.json()["errors"][0]["message"])

    def test_grouped_aggregate_ordering_is_supported_for_each_backend(self) -> None:
        selection = """
            __typename
            status
            amount
        """
        for manager_field in (
            "parityDatabaseRow",
            "parityExcelRow",
            "parityCalculationRow",
        ):
            with self.subTest(manager_field=manager_field):
                response = self.query(
                    f"""
                    query {{
                        grouped: {
                        self._query_fields(
                            manager_field,
                            grouped=True,
                            order_by=("-amount",),
                            selection=selection,
                        )
                    }
                    }}
                    """
                )
                self.assertResponseNoErrors(response)
                page = response.json()["data"]["grouped"]
                self.assertEqual(
                    [(item["status"], item["amount"]) for item in page["items"]],
                    [("beta", 5.0), ("alpha", 4.5)],
                )

    def test_calculation_manager_input_relation_is_recursive(self) -> None:
        response = self.query(
            """
            query {
              parityCalculationRowGroups(groupBy: ["status"]) {
                items {
                  status
                  parentList(orderBy: [{field: name}]) {
                    items { id name }
                    pageInfo { totalCount }
                  }
                  parentGroups(groupBy: ["name"]) {
                    items { name }
                    pageInfo { totalCount }
                  }
                }
              }
            }
            """
        )

        self.assertResponseNoErrors(response)
        groups = response.json()["data"]["parityCalculationRowGroups"]["items"]
        self.assertEqual([group["status"] for group in groups], ["alpha", "beta"])
        for group in groups:
            self.assertEqual(
                group["parentList"],
                {
                    "items": [
                        {
                            "id": self.calculation_parent.identification["id"],
                            "name": "Calculation Parent",
                        }
                    ],
                    "pageInfo": {"totalCount": 1},
                },
            )
            self.assertEqual(
                group["parentGroups"]["items"], [{"name": "Calculation Parent"}]
            )
            self.assertEqual(group["parentGroups"]["pageInfo"]["totalCount"], 1)

    def test_relation_list_and_groups_have_independent_page_shapes(self) -> None:
        parent = self.RelationParent.create(name="Parent", creator_id=self.user.id)
        for status, amount in (
            ("alpha", Decimal("2.00")),
            ("alpha", Decimal("3.00")),
            ("beta", Decimal("4.00")),
        ):
            self.RelationChild.create(
                status=status,
                amount=amount,
                parent=parent,
                creator_id=self.user.id,
            )

        response = self.query(
            """
            query {
                parityRelationParentList(filter: {name: "Parent"}) {
                    items {
                        ordinary: parityRelationChildList(orderBy: [{field: status}]) {
                            __typename
                            items { __typename status amount }
                            pageInfo { __typename totalCount }
                        }
                        grouped: parityRelationChildGroups(
                            groupBy: ["status"]
                            orderBy: [{field: status}]
                        ) {
                            __typename
                            items { __typename status amount }
                            pageInfo { __typename totalCount }
                        }
                    }
                }
            }
            """
        )
        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["parityRelationParentList"]["items"][0]
        ordinary = payload["ordinary"]
        grouped = payload["grouped"]
        self.assertNotEqual(ordinary["__typename"], grouped["__typename"])
        self.assertEqual(
            ordinary["pageInfo"]["__typename"],
            "PageInfo",
        )
        self.assertEqual(ordinary["pageInfo"]["totalCount"], 3)
        self.assertEqual(grouped["pageInfo"]["totalCount"], 2)
        self.assertEqual(
            grouped["items"],
            [
                {
                    "__typename": grouped["items"][0]["__typename"],
                    "status": "alpha",
                    "amount": 5.0,
                },
                {
                    "__typename": grouped["items"][0]["__typename"],
                    "status": "beta",
                    "amount": 4.0,
                },
            ],
        )
