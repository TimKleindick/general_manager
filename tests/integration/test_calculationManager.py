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
                return self.employee.salary * 0.2

        cls.Employee = Employee
        cls.TaxCalculation = TaxCalculation

        cls.general_manager_classes = [Employee, TaxCalculation]

    def setUp(self):
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
        employee = self.Employee.create(
            name="John Doe", salary=Measurement(3000, "EUR"), creator_id=self.user.id  # type: ignore
        )
        variables = {"employeeId": employee.id}
        response = self.query(self.mutation, variables=variables)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["taxcalculation"]
        self.assertEqual(data["calculate"]["value"], 600)
        self.assertEqual(data["calculate"]["unit"], "EUR")
