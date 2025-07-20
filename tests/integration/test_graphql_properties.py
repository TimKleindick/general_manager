from django.db.models import CharField, IntegerField, functions
from general_manager.api.property import graphQlProperty
from general_manager.manager.generalManager import GeneralManager
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.manager.input import Input
from general_manager.utils.testing import GeneralManagerTransactionTestCase
from general_manager.permission.managerBasedPermission import ManagerBasedPermission


class GraphQLPropertyDatabaseTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class Person(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                age = IntegerField()

                class Meta:
                    app_label = "general_manager"

            class Permission(ManagerBasedPermission):
                __create__ = ["public"]
                __read__ = ["public"]

            @graphQlProperty(filterable=True, sortable=True, query_annotation=functions.Length("name"))
            def name_length(self) -> int:
                return len(self.name)

            @graphQlProperty(filterable=True, sortable=True)
            def age_plus_length(self) -> int:
                return self.age + len(self.name)

        cls.Person = Person
        cls.general_manager_classes = [Person]

    def setUp(self):
        super().setUp()
        self.Person.create(name="Bob", age=30, creator_id=None)
        self.Person.create(name="Alice", age=25, creator_id=None)
        self.Person.create(name="Eve", age=40, creator_id=None)

    def test_filter_and_sort_properties(self):
        query = """
        query {
            personList(filter:{nameLength:3}, sortBy: age_plus_length) {
                name
                nameLength
                agePlusLength
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["personList"]
        self.assertEqual(len(data), 2)
        names = [d["name"] for d in data]
        # sorted by agePlusLength: Bob (30+3=33) then Eve (40+3=43)
        self.assertEqual(names, ["Bob", "Eve"])


class GraphQLPropertyCalculationTest(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SumCalc(GeneralManager):
            class Interface(CalculationInterface):
                a = Input(int, possible_values=[1, 2])
                b = Input(int, possible_values=[2, 3])

            @graphQlProperty(filterable=True, sortable=True)
            def sum_val(self) -> int:
                return self.a + self.b

        cls.SumCalc = SumCalc
        cls.general_manager_classes = [SumCalc]

    def test_filter_and_sort_properties(self):
        query = """
        query {
            sumcalcList(filter:{sumVal:4}, sortBy: sum_val) {
                a
                b
                sumVal
            }
        }
        """
        response = self.query(query)
        self.assertResponseNoErrors(response)
        data = response.json()["data"]["sumcalcList"]
        combos = {(d["a"], d["b"]) for d in data}
        self.assertEqual(combos, {(1,3), (2,2)})

