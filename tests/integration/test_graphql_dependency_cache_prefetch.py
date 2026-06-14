from typing import ClassVar
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.utils.crypto import get_random_string

from general_manager.api.property import graph_ql_property
from general_manager.cache.dependency_cache import DependencyCacheHit
from general_manager.interface import CalculationInterface, DatabaseInterface
from general_manager.manager import GeneralManager, Input
from general_manager.measurement import Measurement, MeasurementField
from general_manager.permission.base_permission import ReadPermissionPlan
from general_manager.permission.manager_based_permission import ManagerBasedPermission
from general_manager.permission.permission_checks import register_permission
from general_manager.utils.make_cache_key import make_cache_key
from general_manager.utils.testing import GeneralManagerTransactionTestCase


@register_permission("denyCalculatedTax")
def _deny_calculated_tax(_instance, _user, _config):
    return False


class TestGraphQLDependencyCachePrefetch(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class PrefetchEmployee(GeneralManager):
            name: str
            salary: Measurement

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                salary = MeasurementField(base_unit="EUR")

        class PrefetchCalculation(GeneralManager):
            employee: PrefetchEmployee
            dependency_calls: ClassVar[int] = 0
            run_calls: ClassVar[int] = 0

            class Interface(CalculationInterface):
                employee = Input(
                    PrefetchEmployee,
                    possible_values=lambda: PrefetchEmployee.all(),
                )

            @graph_ql_property(cache="dependency")
            def calculated_tax(self) -> Measurement:
                type(self).dependency_calls += 1
                return self.employee.salary * 0.2

            @graph_ql_property(cache="run")
            def run_scoped_value(self) -> int:
                type(self).run_calls += 1
                return int(self.employee.salary.quantity.magnitude)

        cls.Employee = PrefetchEmployee
        cls.Calculation = PrefetchCalculation
        cls.general_manager_classes = [PrefetchEmployee, PrefetchCalculation]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_superuser(
            username="prefetcher",
            password=password,
        )
        self.client.login(username="prefetcher", password=password)
        self.alice = self.Employee.create(
            name="Alice",
            salary=Measurement(3000, "EUR"),
            ignore_permission=True,
        )
        self.bob = self.Employee.create(
            name="Bob",
            salary=Measurement(4000, "EUR"),
            ignore_permission=True,
        )
        self.Calculation.dependency_calls = 0
        self.Calculation.run_calls = 0

    def _list_query(self, extra_fields: str = "") -> str:
        return f"""
        query {{
            prefetchCalculationList(page: 1, pageSize: 1) {{
                items {{
                    employee {{ name }}
                    calculatedTax {{ value unit }}
                    {extra_fields}
                }}
                pageInfo {{ totalCount currentPage totalPages }}
            }}
        }}
        """

    def _single_query(self) -> str:
        return """
        query($employeeId: ID!) {
            prefetchCalculation(employeeId: $employeeId) {
                employee { name }
                calculatedTax { value unit }
            }
        }
        """

    def test_single_and_list_queries_return_same_dependency_cached_value(self):
        single_response = self.query(
            self._single_query(),
            variables={"employeeId": self.alice.id},
        )
        list_response = self.query(self._list_query())

        self.assertResponseNoErrors(single_response)
        self.assertResponseNoErrors(list_response)
        single_item = single_response.json()["data"]["prefetchCalculation"]
        list_item = response_item(list_response)

        self.assertEqual(single_item["employee"]["name"], list_item["employee"]["name"])
        self.assertEqual(single_item["calculatedTax"], list_item["calculatedTax"])

    def test_only_selected_dependency_cached_fields_are_planned(self):
        query = self._list_query(extra_fields="runScopedValue")

        with patch(
            "general_manager.api.graphql_resolvers.prefetch_dependency_cache_hits",
            return_value={},
        ) as prefetch:
            response = self.query(query)

        self.assertResponseNoErrors(response)
        plans = prefetch.call_args.args[0]
        planned_names = {plan.property_name for plan in plans.values()}
        self.assertEqual(planned_names, {"calculated_tax"})

    def test_prefetch_plans_only_paginated_items(self):
        with patch(
            "general_manager.api.graphql_resolvers.prefetch_dependency_cache_hits",
            return_value={},
        ) as prefetch:
            response = self.query(self._list_query())

        self.assertResponseNoErrors(response)
        plans = prefetch.call_args.args[0]
        self.assertEqual(len(plans), 1)
        planned_instance = next(iter(plans.values())).instance
        self.assertEqual(planned_instance.employee.name, "Alice")

    def test_cache_miss_computes_through_existing_property_path(self):
        response = self.query(self._list_query())

        self.assertResponseNoErrors(response)
        item = response_item(response)
        self.assertEqual(item["calculatedTax"]["value"], 600)
        self.assertEqual(self.Calculation.dependency_calls, 1)

    def test_hot_prefetch_uses_bulk_hit_without_per_field_cache_read(self):
        instance = self.Calculation(employee=self.alice)
        prop = self.Calculation.Interface.get_graph_ql_properties()["calculated_tax"]
        cache_key = make_cache_key(prop._get_cached_fget(), (instance,), {})
        hit = DependencyCacheHit(
            value=Measurement(600, "EUR"),
            dependencies=frozenset(
                {
                    (
                        "PrefetchEmployee",
                        "identification",
                        f'{{"id": {self.alice.id}}}',
                    )
                }
            ),
        )

        def fake_reader(_cache_backend, cache_keys):
            self.assertEqual(tuple(cache_keys), (cache_key,))
            return {cache_key: hit}

        with (
            patch(
                "general_manager.api.graphql_prefetch.read_many_dependency_cache_hits",
                side_effect=fake_reader,
            ) as bulk_read,
            patch(
                "general_manager.cache.cache_decorator.read_dependency_cache_hit",
                side_effect=AssertionError("single-key dependency cache read used"),
            ),
        ):
            response = self.query(self._list_query())

        self.assertResponseNoErrors(response)
        item = response_item(response)
        self.assertEqual(item["calculatedTax"]["value"], 600)
        self.assertEqual(item["calculatedTax"]["unit"], "EUR")
        self.assertEqual(self.Calculation.dependency_calls, 0)
        bulk_read.assert_called_once()

    def test_hot_list_uses_get_many_for_selected_dependency_cached_fields(self):
        first = self.query(self._list_query())
        self.assertResponseNoErrors(first)
        self.assertEqual(self.Calculation.dependency_calls, 1)

        self.reset_cache_ops()
        self.Calculation.dependency_calls = 0

        second = self.query(self._list_query())

        self.assertResponseNoErrors(second)
        self.assertEqual(self.Calculation.dependency_calls, 0)
        ops = self.cache_ops()
        get_many_ops = [op for op in ops if op[0] == "get_many"]
        self.assertTrue(get_many_ops)


class TestGraphQLDependencyCachePrefetchPermissions(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class SecureEmployee(GeneralManager):
            name: str
            salary: Measurement

            class Interface(DatabaseInterface):
                name = CharField(max_length=100)
                salary = MeasurementField(base_unit="EUR")

        class SecureCalculation(GeneralManager):
            employee: SecureEmployee
            calls: ClassVar[int] = 0

            class Interface(CalculationInterface):
                employee = Input(
                    SecureEmployee,
                    possible_values=lambda: SecureEmployee.all(),
                )

            class Permission(ManagerBasedPermission):
                __read__: ClassVar[list[str]] = ["public"]
                calculated_tax: ClassVar[dict[str, list[str]]] = {
                    "read": ["denyCalculatedTax"]
                }

                def get_read_permission_plan(self) -> ReadPermissionPlan:
                    return ReadPermissionPlan(
                        filters=[{"filter": {}, "exclude": {}}],
                        requires_instance_check=False,
                    )

            @graph_ql_property(cache="dependency")
            def calculated_tax(self) -> Measurement:
                type(self).calls += 1
                return self.employee.salary * 0.2

        cls.Employee = SecureEmployee
        cls.Calculation = SecureCalculation
        cls.general_manager_classes = [SecureEmployee, SecureCalculation]
        super().setUpClass()

    def setUp(self):
        super().setUp()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="limited",
            password=password,
        )
        self.client.login(username="limited", password=password)
        self.employee = self.Employee.create(
            name="Denied",
            salary=Measurement(5000, "EUR"),
            ignore_permission=True,
        )
        self.Calculation.calls = 0

    def test_denied_field_is_not_prefetched_or_computed(self):
        query = """
        query {
            secureCalculationList {
                items {
                    calculatedTax { value unit }
                }
            }
        }
        """

        with patch(
            "general_manager.api.graphql_resolvers.prefetch_dependency_cache_hits",
            return_value={},
        ) as prefetch:
            response = self.query(query)

        self.assertResponseNoErrors(response)
        payload = response.json()["data"]["secureCalculationList"]["items"][0]
        self.assertIsNone(payload["calculatedTax"])
        self.assertEqual(prefetch.call_args.args[0], {})
        self.assertEqual(self.Calculation.calls, 0)


def response_item(response):
    return response.json()["data"]["prefetchCalculationList"]["items"][0]
