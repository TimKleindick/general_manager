# type: ignore[file-ignores]

import asyncio
import os
from types import SimpleNamespace

import graphene
from django.contrib.auth import get_user_model
from django.db.models import CharField, FloatField
from typing import ClassVar

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graphQlProperty
from general_manager.interface.calculationInterface import CalculationInterface
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager.generalManager import GeneralManager
from general_manager.manager.input import Input
from general_manager.measurement import Measurement, MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class TestGraphQLCalculationSubscriptions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        class Employee(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=120)
                salary = MeasurementField(base_unit="EUR")

        class TaxRule(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=120)
                multiplier = FloatField()

        class TaxCalculation(GeneralManager):
            default_rule_id: int | None = None
            access_log: ClassVar[list[str]] = []

            class Interface(CalculationInterface):
                employee = Input(Employee, possible_values=lambda: Employee.all())

            @graphQlProperty()
            def calculatedTax(self) -> Measurement:
                self.__class__.access_log.append("calculatedTax")
                return self.employee.salary * 0.2

            @graphQlProperty()
            def configuredTax(self) -> Measurement:
                self.__class__.access_log.append("configuredTax")
                rule_id = self.__class__.default_rule_id
                if rule_id is None:
                    raise ValueError("No tax rule configured for TaxCalculation.")
                rule = TaxRule(id=rule_id)
                return self.employee.salary * rule.multiplier

            @graphQlProperty()
            def unusedTax(self) -> Measurement:
                self.__class__.access_log.append("unusedTax")
                return self.employee.salary * 0.5

        cls.TaxRule = TaxRule
        cls.Employee = Employee
        cls.TaxCalculation = TaxCalculation
        cls.general_manager_classes = [Employee, TaxCalculation, TaxRule]

    def setUp(self) -> None:
        super().setUp()
        User = get_user_model()
        self.user = User.objects.create_user(username="bob", password="secret")
        self.client.force_login(self.user)
        self._async_env_original = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        self.tax_rule = self.TaxRule.create(
            name="default",
            multiplier=0.2,
            creator_id=self.user.id,
        )
        self.TaxCalculation.default_rule_id = self.tax_rule.id
        self.TaxCalculation.access_log = []

    def tearDown(self) -> None:
        self.TaxCalculation.default_rule_id = None
        self.TaxCalculation.access_log = []
        if self._async_env_original is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = self._async_env_original
        super().tearDown()

    def _build_schema(self) -> graphene.Schema:
        schema_kwargs: dict[str, object] = {"query": GraphQL._query_class}
        if GraphQL._mutation_class is not None:
            schema_kwargs["mutation"] = GraphQL._mutation_class
        if GraphQL._subscription_class is not None:
            schema_kwargs["subscription"] = GraphQL._subscription_class
        return graphene.Schema(**schema_kwargs)

    def test_calculation_subscription_reacts_to_dependency(self) -> None:
        employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )
        schema = self._build_schema()
        context = SimpleNamespace(user=self.user)
        subscription = """
            subscription ($employeeId: ID!) {
                onTaxcalculationChange(employeeId: $employeeId) {
                    action
                    item {
                        calculatedTax {
                            value
                            unit
                        }
                    }
                }
            }
        """

        async def run_subscription() -> tuple[object, object]:
            generator = await schema.subscribe(
                subscription,
                variable_values={"employeeId": employee.id},
                context_value=context,
            )
            if hasattr(generator, "errors"):
                raise AssertionError(generator.errors)
            try:
                first = await generator.__anext__()
                await asyncio.to_thread(
                    lambda: employee.update(
                        salary=Measurement(4000, "EUR"),
                        creator_id=self.user.id,
                        ignore_permission=True,
                    )
                )
                second = await generator.__anext__()
            finally:
                await generator.aclose()
            return first, second

        first_event, second_event = asyncio.run(run_subscription())

        self.assertIsNone(first_event.errors)
        snapshot = first_event.data["onTaxcalculationChange"]
        self.assertEqual(snapshot["action"], "snapshot")
        self.assertAlmostEqual(snapshot["item"]["calculatedTax"]["value"], 600)
        self.assertEqual(snapshot["item"]["calculatedTax"]["unit"], "EUR")

        self.assertIsNone(second_event.errors)
        update = second_event.data["onTaxcalculationChange"]
        self.assertEqual(update["action"], "update")
        self.assertAlmostEqual(update["item"]["calculatedTax"]["value"], 800)
        self.assertEqual(update["item"]["calculatedTax"]["unit"], "EUR")
        self.assertIn("calculatedTax", self.TaxCalculation.access_log)

    def test_calculation_subscription_reacts_to_graphql_property_dependency_with_fragment(self) -> None:
        employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )
        schema = self._build_schema()
        context = SimpleNamespace(user=self.user)
        subscription = """
            subscription ($employeeId: ID!) {
                onTaxcalculationChange(employeeId: $employeeId) {
                    action
                    item {
                        ...ConfiguredTaxFields
                    }
                }
            }

            fragment ConfiguredTaxFields on TaxCalculationType {
                configuredTax {
                    value
                    unit
                }
            }
        """

        async def run_subscription() -> tuple[object, object]:
            generator = await schema.subscribe(
                subscription,
                variable_values={"employeeId": employee.id},
                context_value=context,
            )
            if hasattr(generator, "errors"):
                raise AssertionError(generator.errors)
            try:
                first = await generator.__anext__()
                await asyncio.to_thread(
                    lambda: self.tax_rule.update(
                        multiplier=0.25,
                        creator_id=self.user.id,
                        ignore_permission=True,
                    )
                )
                second = await generator.__anext__()
            finally:
                await generator.aclose()
            return first, second

        first_event, second_event = asyncio.run(run_subscription())

        self.assertIsNone(first_event.errors)
        snapshot = first_event.data["onTaxcalculationChange"]
        self.assertEqual(snapshot["action"], "snapshot")
        self.assertAlmostEqual(snapshot["item"]["configuredTax"]["value"], 600)
        self.assertEqual(snapshot["item"]["configuredTax"]["unit"], "EUR")

        self.assertIsNone(second_event.errors)
        update = second_event.data["onTaxcalculationChange"]
        self.assertEqual(update["action"], "update")
        self.assertAlmostEqual(update["item"]["configuredTax"]["value"], 750)
        self.assertEqual(update["item"]["configuredTax"]["unit"], "EUR")
        self.assertIn("configuredTax", self.TaxCalculation.access_log)
        self.assertNotIn("unusedTax", self.TaxCalculation.access_log)

    def test_calculation_subscription_reacts_to_graphql_property_dependency(self) -> None:
        employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )
        schema = self._build_schema()
        context = SimpleNamespace(user=self.user)
        subscription = """
            subscription ($employeeId: ID!) {
                onTaxcalculationChange(employeeId: $employeeId) {
                    action
                    item {
                        configuredTax {
                            value
                            unit
                        }
                    }
                }
            }
        """

        async def run_subscription() -> tuple[object, object]:
            generator = await schema.subscribe(
                subscription,
                variable_values={"employeeId": employee.id},
                context_value=context,
            )
            if hasattr(generator, "errors"):
                raise AssertionError(generator.errors)
            try:
                first = await generator.__anext__()
                await asyncio.to_thread(
                    lambda: self.tax_rule.update(
                        multiplier=0.25,
                        creator_id=self.user.id,
                        ignore_permission=True,
                    )
                )
                second = await generator.__anext__()
            finally:
                await generator.aclose()
            return first, second

        first_event, second_event = asyncio.run(run_subscription())

        self.assertIsNone(first_event.errors)
        snapshot = first_event.data["onTaxcalculationChange"]
        self.assertEqual(snapshot["action"], "snapshot")
        self.assertAlmostEqual(snapshot["item"]["configuredTax"]["value"], 600)
        self.assertEqual(snapshot["item"]["configuredTax"]["unit"], "EUR")

        self.assertIsNone(second_event.errors)
        update = second_event.data["onTaxcalculationChange"]
        self.assertEqual(update["action"], "update")
        self.assertAlmostEqual(update["item"]["configuredTax"]["value"], 750)
        self.assertEqual(update["item"]["configuredTax"]["unit"], "EUR")
        self.assertIn("configuredTax", self.TaxCalculation.access_log)
        self.assertNotIn("unusedTax", self.TaxCalculation.access_log)

    def test_calculation_subscription_handles_property_aliases(self) -> None:
        employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )
        schema = self._build_schema()
        context = SimpleNamespace(user=self.user)
        subscription = """
            subscription ($employeeId: ID!) {
                onTaxcalculationChange(employeeId: $employeeId) {
                    action
                    item {
                        primaryTax: configuredTax {
                            value
                            unit
                        }
                    }
                }
            }
        """

        async def run_subscription() -> tuple[object, object]:
            generator = await schema.subscribe(
                subscription,
                variable_values={"employeeId": employee.id},
                context_value=context,
            )
            if hasattr(generator, "errors"):
                raise AssertionError(generator.errors)
            try:
                first = await generator.__anext__()
                await asyncio.to_thread(
                    lambda: self.tax_rule.update(
                        multiplier=0.3,
                        creator_id=self.user.id,
                        ignore_permission=True,
                    )
                )
                second = await generator.__anext__()
            finally:
                await generator.aclose()
            return first, second

        first_event, second_event = asyncio.run(run_subscription())

        self.assertIsNone(first_event.errors)
        snapshot = first_event.data["onTaxcalculationChange"]
        self.assertEqual(snapshot["action"], "snapshot")
        self.assertAlmostEqual(snapshot["item"]["primaryTax"]["value"], 600)
        self.assertEqual(snapshot["item"]["primaryTax"]["unit"], "EUR")

        self.assertIsNone(second_event.errors)
        update = second_event.data["onTaxcalculationChange"]
        self.assertEqual(update["action"], "update")
        self.assertAlmostEqual(update["item"]["primaryTax"]["value"], 900)
        self.assertEqual(update["item"]["primaryTax"]["unit"], "EUR")
        self.assertIn("configuredTax", self.TaxCalculation.access_log)
        self.assertNotIn("unusedTax", self.TaxCalculation.access_log)

    def test_calculation_subscription_without_item_payload(self) -> None:
        employee = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            creator_id=self.user.id,
        )
        schema = self._build_schema()
        context = SimpleNamespace(user=self.user)
        subscription = """
            subscription ($employeeId: ID!) {
                onTaxcalculationChange(employeeId: $employeeId) {
                    action
                }
            }
        """

        async def run_subscription() -> tuple[object, object]:
            generator = await schema.subscribe(
                subscription,
                variable_values={"employeeId": employee.id},
                context_value=context,
            )
            if hasattr(generator, "errors"):
                raise AssertionError(generator.errors)
            try:
                first = await generator.__anext__()
                await asyncio.to_thread(
                    lambda: employee.update(
                        salary=Measurement(3500, "EUR"),
                        creator_id=self.user.id,
                        ignore_permission=True,
                    )
                )
                second = await generator.__anext__()
            finally:
                await generator.aclose()
            return first, second

        first_event, second_event = asyncio.run(run_subscription())

        self.assertIsNone(first_event.errors)
        snapshot = first_event.data["onTaxcalculationChange"]
        self.assertEqual(snapshot["action"], "snapshot")

        self.assertIsNone(second_event.errors)
        update = second_event.data["onTaxcalculationChange"]
        self.assertEqual(update["action"], "update")
        self.assertEqual(self.TaxCalculation.access_log, [])
