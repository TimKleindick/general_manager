# type: ignore

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string
from general_manager.manager.general_manager import GeneralManager
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.interface.calculation_interface import CalculationInterface
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.measurement import MeasurementField, Measurement
from general_manager.manager.input import Input
from general_manager.api.property import graph_ql_property


class CustomMutationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        """
        Prepare test manager classes used across tests.

        Defines two inner manager classes on the test class:
        - Employee: a database-backed manager with `name` and `salary` fields (salary measured in EUR).
        - TaxCalculation: a manager that references an Employee and exposes a sortable GraphQL property `calculated_tax` that computes 20% of the referenced employee's salary.

        After definition, assigns `Employee`, `TaxCalculation`, and `general_manager_classes` to the test class for use in test methods.
        """

        class Employee(GeneralManager):
            id: int
            name: str
            salary: Measurement

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                salary = MeasurementField(base_unit="EUR")

        class TaxCalculation(GeneralManager):
            employee: Employee

            class Interface(CalculationInterface):
                employee = Input(Employee, possible_values=lambda: Employee.all())

            @graph_ql_property(sortable=True)
            def calculated_tax(self) -> Measurement:
                """
                Calculates 20% of the associated employee's salary as tax.

                Returns:
                    Measurement: The calculated tax amount based on the employee's salary.
                """
                return self.employee.salary * 0.2

        cls.Employee = Employee
        cls.TaxCalculation = TaxCalculation

        cls.general_manager_classes = [Employee, TaxCalculation]

    def setUp(self):
        """
        Set up a test user and the GraphQL query used by tests.

        Creates a test user and logs them in, then assigns:
        - self.user: the created user instance
        - self.mutation: GraphQL query string for retrieving a TaxCalculation's `calculatedTax` (value and unit)
        """
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="tester", password=password)
        self.client.force_login(self.user)
        self.mutation = """
        query($employeeId: ID!) {
            taxcalculation(employeeId: $employeeId) {
                calculatedTax {
                    value
                    unit
                }
            }
        }
        """

    def test_calculated_tax_tax(self):
        """
        Tests the tax calculation GraphQL mutation for an employee.

        Creates an employee with a specified salary, executes the tax calculation mutation, and verifies that the calculatedTaxd tax value and unit in the response are correct.
        """
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        variables = {"employeeId": employee.id}
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["taxcalculation"]
        self.assertEqual(data["calculatedTax"]["value"], 600)
        self.assertEqual(data["calculatedTax"]["unit"], "EUR")

    def test_sort_by_calculation_property(self):
        """
        Verify TaxCalculation entries can be ordered by the `calculated_tax` property.

        Asserts the initial retrieval order is by employee name, then checks that sorting by
        `calculated_tax` yields employees ordered by their salary-derived tax (ascending),
        and that sorting with `reverse=True` yields the reverse order.
        """
        self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket = self.TaxCalculation.all()
        self.assertEqual(len(tax_calculation_bucket), 3)
        self.assertEqual(tax_calculation_bucket[0].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket[1].employee.name, "Bob")
        self.assertEqual(tax_calculation_bucket[2].employee.name, "Tim")

        tax_calculation_bucket_sorted = tax_calculation_bucket.sort("calculated_tax")
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Bob")

        tax_calculation_bucket_sorted = tax_calculation_bucket.sort(
            "calculated_tax", reverse=True
        )
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Bob")

    def test_sort_by_calculation_property_and_name(self):
        """
        Verifies that TaxCalculation entries are ordered by `calculated_tax` and then by `employee.name`, both in ascending order.

        Creates employees with different salaries, sorts the TaxCalculation bucket by the tuple ("calculated_tax", "employee.name") ascending, and asserts the resulting employee name order is: "Tim", "Alice", "Tina", "Bob".
        """
        self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tina", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket_sorted = self.TaxCalculation.all().sort(
            ("calculated_tax", "employee.name"), reverse=False
        )
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Tina")
        self.assertEqual(tax_calculation_bucket_sorted[3].employee.name, "Bob")

    def test_filter_by_calculation_property(self):
        """
        Verifies that TaxCalculation entries can be filtered by employee name prefix and by calculated_tax, and that combined filters produce the expected subsets and ordering.

        Checks:
        - Filtering by employee name prefix "T" yields two entries ordered by employee name: Tim, Tina.
        - Further filtering that subset by calculated_tax == 3000 EUR * 0.2 yields a single entry (Tina).
        - Filtering all TaxCalculation entries by calculated_tax == 3000 EUR * 0.2 yields two entries ordered: Alice, Tina.
        - Applying the calculated_tax filter to the name-prefixed subset produces the same result as filtering the subset directly.
        - The filtered-by-both bucket is not equal to the original name-prefixed bucket.
        """
        self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )
        self.Employee.create(
            name="Tina", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket_filtered1 = self.TaxCalculation.filter(
            employee__name__startswith="T"
        )
        self.assertEqual(len(tax_calculation_bucket_filtered1), 2)
        self.assertEqual(tax_calculation_bucket_filtered1[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_filtered1[1].employee.name, "Tina")

        tax_calculation_bucket_filtered2 = tax_calculation_bucket_filtered1.filter(
            calculated_tax=Measurement(3000, "EUR") * 0.2
        )
        self.assertEqual(len(tax_calculation_bucket_filtered2), 1)
        self.assertEqual(tax_calculation_bucket_filtered2[0].employee.name, "Tina")

        tax_calculation_bucket_filtered3 = self.TaxCalculation.filter(
            calculated_tax=Measurement(3000, "EUR") * 0.2
        )
        self.assertEqual(len(tax_calculation_bucket_filtered3), 2)
        self.assertEqual(tax_calculation_bucket_filtered3[0].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_filtered3[1].employee.name, "Tina")

        tax_calculation_bucket_filtered4 = tax_calculation_bucket_filtered1.filter(
            calculated_tax=Measurement(3000, "EUR") * 0.2
        )

        self.assertEqual(
            tax_calculation_bucket_filtered4, tax_calculation_bucket_filtered2
        )

        self.assertNotEqual(
            tax_calculation_bucket_filtered4, tax_calculation_bucket_filtered1
        )
