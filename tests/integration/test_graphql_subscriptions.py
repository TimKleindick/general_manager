# type: ignore[file-ignores]

import asyncio
import os
from types import SimpleNamespace

import graphene
from django.contrib.auth import get_user_model
from django.db.models import CharField, FloatField
from django.utils.crypto import get_random_string
from typing import ClassVar

from general_manager.api.graphql import GraphQL
from general_manager.api.property import graphQlProperty
from general_manager.interface.calculation_interface import CalculationInterface
from general_manager.interface.database_interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from general_manager.measurement import Measurement, MeasurementField
from general_manager.utils.testing import GeneralManagerTransactionTestCase


class MissingTaxRuleConfigurationError(ValueError):
    """Raised when a tax calculation is requested without a configured rule."""

    def __init__(self) -> None:
        """
        Initialize MissingTaxRuleConfigurationError with the standard message indicating no tax rule is configured for TaxCalculation.
        """
        super().__init__("No tax rule configured for TaxCalculation.")


class TestGraphQLCalculationSubscriptions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        """
        Register three in-test GeneralManager models (Employee, TaxRule, TaxCalculation) on the test class for subscription tests.

        Creates:
        - Employee: DatabaseInterface with `name` and `salary` (EUR) fields.
        - TaxRule: DatabaseInterface with `name` and `multiplier` fields.
        - TaxCalculation: CalculationInterface with an `employee` input, class-level configuration `default_rule_id` (int | None) and `access_log` (list[str]), and three GraphQL properties used by tests:
          - `calculatedTax`: records access and returns employee.salary * 0.2.
          - `configuredTax`: records access and returns employee.salary * rule.multiplier using the rule identified by `default_rule_id`; raises MissingTaxRuleConfigurationError if no rule is configured.
          - `unusedTax`: records access and returns employee.salary * 0.5.

        Side effects:
        - Attaches `TaxRule`, `Employee`, `TaxCalculation`, and `general_manager_classes` to the test class (`cls`) for use by test methods.
        """

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
                """
                Compute the calculated tax for this calculation based on the associated employee's salary.

                Appends "calculatedTax" to the class-level `access_log`.

                Returns:
                    Measurement: Tax amount equal to `employee.salary * 0.2`.
                """
                self.__class__.access_log.append("calculatedTax")
                return self.employee.salary * 0.2

            @graphQlProperty()
            def configuredTax(self) -> Measurement:
                """
                Compute the tax for this calculation using the currently configured TaxRule.

                Returns:
                    Measurement: Tax amount computed as employee.salary multiplied by the configured TaxRule.multiplier.

                Raises:
                    MissingTaxRuleConfigurationError: If no tax rule is configured for this TaxCalculation.
                """
                self.__class__.access_log.append("configuredTax")
                rule_id = self.__class__.default_rule_id
                if rule_id is None:
                    raise MissingTaxRuleConfigurationError()
                rule = TaxRule(id=rule_id)
                return self.employee.salary * rule.multiplier

            @graphQlProperty()
            def unusedTax(self) -> Measurement:
                """
                Compute an unused tax equal to half of the employee's salary and record that the property was accessed.

                Appends "unusedTax" to the class-level `access_log`.

                Returns:
                    Measurement: A measurement equal to 50% of the employee's salary (same unit as the salary).
                """
                self.__class__.access_log.append("unusedTax")
                return self.employee.salary * 0.5

        cls.TaxRule = TaxRule
        cls.Employee = Employee
        cls.TaxCalculation = TaxCalculation
        cls.general_manager_classes = [Employee, TaxCalculation, TaxRule]

    def setUp(self) -> None:
        """
        Prepare test fixtures for each test: create and log in a test user, enable Django async operations for the test, and initialize a default TaxRule and TaxCalculation state.

        This sets:
        - self.user: a newly created and authenticated test user.
        - the environment variable DJANGO_ALLOW_ASYNC_UNSAFE to "true" for the duration of the test.
        - self.tax_rule: a TaxRule instance used as the default rule.
        - TestGraphQLCalculationSubscriptions.TaxCalculation.default_rule_id to the created rule's id.
        - TestGraphQLCalculationSubscriptions.TaxCalculation.access_log to an empty list.
        """
        super().setUp()
        User = get_user_model()
        password = get_random_string(12)
        self.user = User.objects.create_user(username="bob", password=password)
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
        """
        Restore test-class state and environment variables after each test.

        Resets TaxCalculation class-level configuration (clears default_rule_id and access_log), restores the DJANGO_ALLOW_ASYNC_UNSAFE environment setting to its original value (removing it if it was not set), and then invokes the superclass teardown.
        """
        self.TaxCalculation.default_rule_id = None
        self.TaxCalculation.access_log = []
        if self._async_env_original is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = self._async_env_original
        super().tearDown()

    def _build_schema(self) -> graphene.Schema:
        """
        Build a Graphene schema using the test GraphQL query class and any configured mutation or subscription classes.

        Returns:
            graphene.Schema: Schema composed of GraphQL._query_class and, when set, GraphQL._mutation_class and GraphQL._subscription_class.
        """
        schema_kwargs: dict[str, object] = {"query": GraphQL._query_class}
        if GraphQL._mutation_class is not None:
            schema_kwargs["mutation"] = GraphQL._mutation_class
        if GraphQL._subscription_class is not None:
            schema_kwargs["subscription"] = GraphQL._subscription_class
        return graphene.Schema(**schema_kwargs)

    def test_calculation_subscription_reacts_to_dependency(self) -> None:
        """
        Verify a calculation GraphQL subscription updates when an underlying dependency changes.

        Subscribes to `onTaxcalculationChange` for a created employee, asserts the initial snapshot contains the expected calculated tax, updates the employee's salary to provoke a recalculation, and asserts the subsequent update event reflects the new calculated tax and that `calculatedTax` was accessed.
        """
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
            """
            Subscribe to the given GraphQL subscription, apply an employee salary update, and return the subscription's initial snapshot and the subsequent update event.

            Raises:
                AssertionError: If the subscription generator exposes `errors` before iteration begins.

            Returns:
                tuple[first_event, second_event]: `first_event` is the initial snapshot event object from the subscription; `second_event` is the follow-up update event object produced after the employee salary change.
            """
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

    def test_calculation_subscription_reacts_to_graphql_property_dependency_with_fragment(
        self,
    ) -> None:
        """
        Verify that a GraphQL subscription using a fragment updates calculated GraphQL properties when their backend dependency changes.

        Subscribes to onTaxcalculationChange for a created employee with a fragment that selects `configuredTax`, asserts the initial snapshot value and unit (reflecting the initial TaxRule multiplier), updates the TaxRule multiplier, and asserts the subsequent update event reflects the new computed `configuredTax` value and unit. Also verifies that `TaxCalculation.access_log` records access to `configuredTax` and does not contain `unusedTax`.
        """
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
            """
            Subscribe to the prepared GraphQL subscription, consume the initial snapshot and the subsequent event produced after mutating the tax rule.

            Returns:
                tuple[first_event, second_event]: The first subscription event (snapshot) and the following event (update).
            """
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

    def test_calculation_subscription_reacts_to_graphql_property_dependency(
        self,
    ) -> None:
        """
        Verify that a GraphQL subscription updates when a calculation's GraphQL property depends on another model.

        Subscribes to onTaxcalculationChange for a created employee and asserts the initial snapshot and subsequent update reflect changes to the TaxRule multiplier used by the `configuredTax` calculation. Confirms the reported `value` and `unit` for `configuredTax` in the snapshot and update events and that `configuredTax` was accessed while `unusedTax` was not recorded in the calculation access log.
        """
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
            """
            Subscribe to the prepared GraphQL subscription, consume the initial snapshot and the subsequent event produced after mutating the tax rule.

            Returns:
                tuple[first_event, second_event]: The first subscription event (snapshot) and the following event (update).
            """
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
        """
        Verify GraphQL subscription handles aliases for calculation properties.

        Subscribes to onTaxcalculationChange requesting `configuredTax` aliased as `primaryTax`, asserts the initial snapshot and subsequent update reflect the TaxRule multiplier change (values 600 → 900 with unit "EUR"), and verifies that `configuredTax` was accessed while `unusedTax` was not.
        """
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
            """
            Advance a GraphQL subscription to capture an initial snapshot event and a subsequent update event, and return both.

            The subscription is executed for the current employee context and a tax-rule change is triggered to produce the update event.

            Returns:
                tuple[object, object]: The first (snapshot) event and the second (update) event produced by the subscription.

            Raises:
                AssertionError: If the subscription generator exposes an `errors` attribute.
            """
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
        """
        Verifies that a subscription requesting only the `action` field emits a snapshot and an update without resolving item payloads.

        Subscribes to `onTaxcalculationChange` for an employee, receives an initial snapshot event, updates the employee's salary to trigger a change event, and asserts:
        - the first event's action is "snapshot",
        - the second event's action is "update",
        - no calculation GraphQL properties were accessed (TaxCalculation.access_log remains empty).
        """
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
            """
            Subscribe to the GraphQL subscription for the current employee and return the initial snapshot and the subsequent update events.

            The coroutine consumes the first emitted event (snapshot), performs an update to the employee's salary, then consumes the next emitted event (update) before closing the subscription.

            Returns:
                tuple[first_event, second_event]: `first_event` is the initial snapshot event object; `second_event` is the subsequent update event object.

            Raises:
                AssertionError: If the subscription generator reports errors on creation.
            """
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
