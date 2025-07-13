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

            @graphQlProperty
            def calculate(self) -> Measurement:
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
        Prepares the test environment by creating and logging in a test user, and defines the GraphQL query for tax calculation.
        """
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", password="secret")
        self.client.force_login(self.user)
        self.mutation = """
        query($employeeId: Int!) {
            taxcalculation(employeeId: $employeeId) {
                calculate {
                    value
                    unit
                }
            }
        }
        """

    def test_calculate_tax(self):
        """
        Tests the tax calculation GraphQL mutation for an employee.
        
        Creates an employee with a specified salary, executes the tax calculation mutation, and verifies that the calculated tax value and unit in the response are correct.
        """
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id  # type: ignore
        )
        variables = {"employeeId": employee.id}
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["taxcalculation"]
        self.assertEqual(data["calculate"]["value"], 600)
        self.assertEqual(data["calculate"]["unit"], "EUR")
