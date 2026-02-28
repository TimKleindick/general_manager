# type: ignore

from datetime import date

from django.contrib.auth import get_user_model
from django.db.models import CASCADE, CharField, ForeignKey
from django.test import override_settings
from django.utils.crypto import get_random_string

from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import DateRangeDomain, Input, NumericRangeDomain
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@override_settings(GENERAL_MANAGER_VALIDATE_INPUT_VALUES=True)
class TestGraphQLCalculationInputOptions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class Department(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        class Employee(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                department = ForeignKey(
                    "general_manager.Department",
                    on_delete=CASCADE,
                )

        class OptionalInputCalculation(GeneralManager):
            class Interface(CalculationInterface):
                employee = Input(Employee, possible_values=lambda: Employee.all())
                as_of = Input(date, required=False)

        class MinValueCalculation(GeneralManager):
            class Interface(CalculationInterface):
                quantity = Input(int, min_value=2)

        class MaxValueCalculation(GeneralManager):
            class Interface(CalculationInterface):
                quantity = Input(int, max_value=5)

        class ValidatorCalculation(GeneralManager):
            class Interface(CalculationInterface):
                code = Input(str, validator=lambda value: value.startswith("OK-"))

        class NormalizerCalculation(GeneralManager):
            class Interface(CalculationInterface):
                code = Input(str, normalizer=lambda value: value.upper())

        class DateRangeCalculation(GeneralManager):
            class Interface(CalculationInterface):
                as_of = Input.date_range(
                    start=date(2024, 1, 1),
                    end=date(2024, 1, 31),
                )

        class MonthlyDateCalculation(GeneralManager):
            class Interface(CalculationInterface):
                as_of = Input.monthly_date(
                    start=date(2024, 1, 1),
                    end=date(2024, 3, 31),
                    anchor="end",
                )

        class YearlyDateCalculation(GeneralManager):
            class Interface(CalculationInterface):
                as_of = Input.yearly_date(
                    start=date(2024, 1, 1),
                    end=date(2025, 12, 31),
                    anchor="end",
                )

        class DateDomainCalculation(GeneralManager):
            class Interface(CalculationInterface):
                as_of = Input(
                    date,
                    possible_values=DateRangeDomain(
                        date(2024, 1, 1),
                        date(2024, 3, 31),
                        frequency="month_end",
                    ),
                )

        class NumericDomainCalculation(GeneralManager):
            class Interface(CalculationInterface):
                amount = Input(
                    int,
                    possible_values=NumericRangeDomain(1, 5, step=2),
                )

        class ManagerQueryCalculation(GeneralManager):
            class Interface(CalculationInterface):
                department = Input(
                    Department,
                    possible_values=lambda: Department.all(),
                )
                employee = Input.from_manager_query(
                    Employee,
                    query=lambda department: Employee.filter(
                        department_id=department.id
                    ),
                    depends_on=["department"],
                )

        cls.Department = Department
        cls.Employee = Employee
        cls.OptionalInputCalculation = OptionalInputCalculation
        cls.MinValueCalculation = MinValueCalculation
        cls.MaxValueCalculation = MaxValueCalculation
        cls.ValidatorCalculation = ValidatorCalculation
        cls.NormalizerCalculation = NormalizerCalculation
        cls.DateRangeCalculation = DateRangeCalculation
        cls.MonthlyDateCalculation = MonthlyDateCalculation
        cls.YearlyDateCalculation = YearlyDateCalculation
        cls.DateDomainCalculation = DateDomainCalculation
        cls.NumericDomainCalculation = NumericDomainCalculation
        cls.ManagerQueryCalculation = ManagerQueryCalculation

        cls.general_manager_classes = [
            Department,
            Employee,
            OptionalInputCalculation,
            MinValueCalculation,
            MaxValueCalculation,
            ValidatorCalculation,
            NormalizerCalculation,
            DateRangeCalculation,
            MonthlyDateCalculation,
            YearlyDateCalculation,
            DateDomainCalculation,
            NumericDomainCalculation,
            ManagerQueryCalculation,
        ]

    def setUp(self) -> None:
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="calc-inputs",
            password=password,
        )
        self.client.force_login(self.user)
        self.department_a = self.Department.create(
            name="Alpha",
            creator_id=self.user.id,
        )
        self.department_b = self.Department.create(
            name="Beta",
            creator_id=self.user.id,
        )
        self.employee_a = self.Employee.create(
            name="Alice",
            department=self.department_a,
            creator_id=self.user.id,
        )
        self.employee_b = self.Employee.create(
            name="Bob",
            department=self.department_b,
            creator_id=self.user.id,
        )

    def _assert_error_contains(self, response, text: str) -> None:
        self.assertResponseHasErrors(response)
        errors = response.json()["errors"]
        self.assertTrue(any(text in error["message"] for error in errors), errors)

    def test_optional_input_is_nullable_in_graphql_queries(self) -> None:
        query = """
        query($employeeId: ID!) {
            optionalinputcalculation(employeeId: $employeeId) {
                employee {
                    name
                }
                asOf
            }
        }
        """

        response = self.query(query, variables={"employeeId": self.employee_a.id})

        self.assertResponseNoErrors(response)
        data = response.json()["data"]["optionalinputcalculation"]
        self.assertEqual(data["employee"]["name"], "Alice")
        self.assertIsNone(data["asOf"])

    def test_optional_input_accepts_explicit_value_in_graphql_queries(self) -> None:
        query = """
        query($employeeId: ID!, $asOf: Date) {
            optionalinputcalculation(employeeId: $employeeId, asOf: $asOf) {
                employee {
                    name
                }
                asOf
            }
        }
        """

        response = self.query(
            query,
            variables={"employeeId": self.employee_a.id, "asOf": "2024-02-10"},
        )

        self.assertResponseNoErrors(response)
        data = response.json()["data"]["optionalinputcalculation"]
        self.assertEqual(data["employee"]["name"], "Alice")
        self.assertEqual(data["asOf"], "2024-02-10")

    def test_min_value_constraint_is_enforced_via_graphql(self) -> None:
        query = """
        query($quantity: Int!) {
            minvaluecalculation(quantity: $quantity) {
                quantity
            }
        }
        """

        response = self.query(query, variables={"quantity": 2})
        self.assertResponseNoErrors(response)
        self.assertEqual(response.json()["data"]["minvaluecalculation"]["quantity"], 2)

        invalid_response = self.query(query, variables={"quantity": 1})
        self._assert_error_contains(invalid_response, "Invalid value for quantity")

    def test_max_value_constraint_is_enforced_via_graphql(self) -> None:
        query = """
        query($quantity: Int!) {
            maxvaluecalculation(quantity: $quantity) {
                quantity
            }
        }
        """

        response = self.query(query, variables={"quantity": 5})
        self.assertResponseNoErrors(response)
        self.assertEqual(response.json()["data"]["maxvaluecalculation"]["quantity"], 5)

        invalid_response = self.query(query, variables={"quantity": 6})
        self._assert_error_contains(invalid_response, "Invalid value for quantity")

    def test_validator_is_enforced_via_graphql(self) -> None:
        query = """
        query($code: String!) {
            validatorcalculation(code: $code) {
                code
            }
        }
        """

        response = self.query(query, variables={"code": "OK-123"})
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["validatorcalculation"]["code"], "OK-123"
        )

        invalid_response = self.query(query, variables={"code": "BAD-123"})
        self._assert_error_contains(invalid_response, "Invalid value for code")

    def test_normalizer_is_applied_via_graphql(self) -> None:
        query = """
        query($code: String!) {
            normalizercalculation(code: $code) {
                code
            }
        }
        """

        response = self.query(query, variables={"code": "abc"})

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["normalizercalculation"]["code"], "ABC"
        )

    def test_date_range_helper_is_enforced_via_graphql(self) -> None:
        query = """
        query($asOf: Date!) {
            daterangecalculation(asOf: $asOf) {
                asOf
            }
        }
        """

        response = self.query(query, variables={"asOf": "2024-01-15"})
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["daterangecalculation"]["asOf"], "2024-01-15"
        )

        invalid_response = self.query(query, variables={"asOf": "2024-02-01"})
        self._assert_error_contains(invalid_response, "Invalid value for as_of")

    def test_monthly_date_helper_normalizes_via_graphql(self) -> None:
        query = """
        query($asOf: Date!) {
            monthlydatecalculation(asOf: $asOf) {
                asOf
            }
        }
        """

        response = self.query(query, variables={"asOf": "2024-02-10"})

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["monthlydatecalculation"]["asOf"],
            "2024-02-29",
        )

    def test_yearly_date_helper_normalizes_via_graphql(self) -> None:
        query = """
        query($asOf: Date!) {
            yearlydatecalculation(asOf: $asOf) {
                asOf
            }
        }
        """

        response = self.query(query, variables={"asOf": "2024-06-10"})

        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["yearlydatecalculation"]["asOf"],
            "2024-12-31",
        )

    def test_date_range_domain_is_enforced_via_graphql(self) -> None:
        query = """
        query($asOf: Date!) {
            datedomaincalculation(asOf: $asOf) {
                asOf
            }
        }
        """

        response = self.query(query, variables={"asOf": "2024-02-10"})
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["datedomaincalculation"]["asOf"],
            "2024-02-29",
        )

        invalid_response = self.query(query, variables={"asOf": "2024-04-10"})
        self._assert_error_contains(invalid_response, "Invalid value for as_of")

    def test_numeric_range_domain_is_enforced_via_graphql(self) -> None:
        query = """
        query($amount: Int!) {
            numericdomaincalculation(amount: $amount) {
                amount
            }
        }
        """

        response = self.query(query, variables={"amount": 3})
        self.assertResponseNoErrors(response)
        self.assertEqual(
            response.json()["data"]["numericdomaincalculation"]["amount"], 3
        )

        invalid_response = self.query(query, variables={"amount": 4})
        self._assert_error_contains(invalid_response, "Invalid value for amount")

    def test_from_manager_query_is_enforced_via_graphql(self) -> None:
        query = """
        query($departmentId: ID!, $employeeId: ID!) {
            managerquerycalculation(
                departmentId: $departmentId
                employeeId: $employeeId
            ) {
                department {
                    name
                }
            }
        }
        """

        response = self.query(
            query,
            variables={
                "departmentId": self.department_a.id,
                "employeeId": self.employee_a.id,
            },
        )
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["managerquerycalculation"]
        self.assertEqual(data["department"]["name"], "Alpha")

        invalid_response = self.query(
            query,
            variables={
                "departmentId": self.department_a.id,
                "employeeId": self.employee_b.id,
            },
        )
        self._assert_error_contains(invalid_response, "Invalid value for employee")
