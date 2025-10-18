# type: ignore

from django.contrib.auth import get_user_model
from django.db.models import CharField
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.measurement import MeasurementField, Measurement
from general_manager.manager.input import Input
from general_manager.api.property import graphQlProperty


class CustomMutationTest(GeneralManagerTransactionTestCase):

    @classmethod
    def setUpClass(cls):
        """
        Initializes test-specific `Employee` and `TaxCalculation` manager classes with their interfaces for use in integration tests.

        Defines an `Employee` class with database fields for name and salary (in EUR), and a `TaxCalculation` class that references an employee and exposes a calculation property for computing 20% tax on the employee's salary. Assigns these classes to class variables for use in test methods.
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

            @graphQlProperty(sortable=True)
            def calculatedTax(self) -> Measurement:
                """
                calculatedTaxs 20% of the associated employee's salary as tax.

                Returns:
                    Measurement: The calculatedTaxd tax amount based on the employee's salary.
                """
                return self.employee.salary * 0.2

        cls.Employee = Employee
        cls.TaxCalculation = TaxCalculation

        cls.general_manager_classes = [Employee, TaxCalculation]

    def setUp(self):
        """
        Prepares the test environment by creating and logging in a test user, and defines the GraphQL query for tax calculation.
        """
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="secret")
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

    def test_calculatedTax_tax(self):
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
        Tests that the tax calculation can be sorted by the employee's name.
        """
        employee1 = self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        employee2 = self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        employee3 = self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket = self.TaxCalculation.all()
        self.assertEqual(len(tax_calculation_bucket), 3)
        self.assertEqual(tax_calculation_bucket[0].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket[1].employee.name, "Bob")
        self.assertEqual(tax_calculation_bucket[2].employee.name, "Tim")

        tax_calculation_bucket_sorted = tax_calculation_bucket.sort("calculatedTax")
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Bob")

        tax_calculation_bucket_sorted = tax_calculation_bucket.sort(
            "calculatedTax", reverse=True
        )
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Bob")

    def test_sort_by_calculation_property_and_name(self):
        """
        Tests that the tax calculation can be sorted by the employee's name.
        """
        employee1 = self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        employee2 = self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        employee3 = self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )
        employee4 = self.Employee.create(
            name="Tina", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket_sorted = self.TaxCalculation.all().sort(
            ("calculatedTax", "employee.name"), reverse=False
        )
        self.assertEqual(tax_calculation_bucket_sorted[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_sorted[1].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_sorted[2].employee.name, "Tina")
        self.assertEqual(tax_calculation_bucket_sorted[3].employee.name, "Bob")

    def test_filter_by_calculation_property(self):
        """
        Tests that the tax calculation can be filtered by the employee's name.
        """
        employee1 = self.Employee.create(
            name="Alice", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )
        employee2 = self.Employee.create(
            name="Bob", salary=Measurement(4000, "EUR"), creator_id=self.user.id
        )
        employee3 = self.Employee.create(
            name="Tim", salary=Measurement(2000, "EUR"), creator_id=self.user.id
        )
        employee4 = self.Employee.create(
            name="Tina", salary=Measurement(3000, "EUR"), creator_id=self.user.id
        )

        tax_calculation_bucket_filtered1 = self.TaxCalculation.filter(
            employee__name__startswith="T"
        )
        self.assertEqual(len(tax_calculation_bucket_filtered1), 2)
        self.assertEqual(tax_calculation_bucket_filtered1[0].employee.name, "Tim")
        self.assertEqual(tax_calculation_bucket_filtered1[1].employee.name, "Tina")

        tax_calculation_bucket_filtered2 = tax_calculation_bucket_filtered1.filter(
            calculatedTax=Measurement(3000, "EUR") * 0.2
        )
        self.assertEqual(len(tax_calculation_bucket_filtered2), 1)
        self.assertEqual(tax_calculation_bucket_filtered2[0].employee.name, "Tina")

        tax_calculation_bucket_filtered3 = self.TaxCalculation.filter(
            calculatedTax=Measurement(3000, "EUR") * 0.2
        )
        self.assertEqual(len(tax_calculation_bucket_filtered3), 2)
        self.assertEqual(tax_calculation_bucket_filtered3[0].employee.name, "Alice")
        self.assertEqual(tax_calculation_bucket_filtered3[1].employee.name, "Tina")

        tax_calculation_bucket_filtered4 = tax_calculation_bucket_filtered1.filter(
            calculatedTax=Measurement(3000, "EUR") * 0.2
        )

        self.assertEqual(
            tax_calculation_bucket_filtered4, tax_calculation_bucket_filtered2
        )

        self.assertNotEqual(
            tax_calculation_bucket_filtered4, tax_calculation_bucket_filtered1
        )
