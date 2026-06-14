from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from django.test import SimpleTestCase
from graphql import parse

from general_manager.api.graphql_prefetch import (
    DependencyCachePrefetchPlan,
    collect_selected_graphql_property_names,
    plan_dependency_cache_prefetches,
    prefetch_dependency_cache_hits,
)
from general_manager.cache.dependency_cache import DependencyCacheHit
from general_manager.cache.run_context import CalculationRunContext
from general_manager.api.property import GraphQLProperty, graph_ql_property
from general_manager.utils.make_cache_key import make_cache_key


def _prop() -> GraphQLProperty:
    def getter(_self: object) -> int:
        return 1

    return GraphQLProperty(getter)


class FakeInterface:
    @staticmethod
    def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
        return {
            "calculated_tax": _prop(),
            "budget_used": _prop(),
            "hidden_cost": _prop(),
        }


class FakeManager:
    Interface = FakeInterface


def _info(query: str) -> Any:
    document = parse(query)
    operation = next(
        definition
        for definition in document.definitions
        if definition.kind == "operation_definition"
    )
    fragments = {
        definition.name.value: definition
        for definition in document.definitions
        if definition.kind == "fragment_definition"
    }
    return SimpleNamespace(
        field_nodes=list(operation.selection_set.selections),
        fragments=fragments,
    )


class GraphQLPrefetchSelectionTests(SimpleTestCase):
    def test_collects_selected_properties_under_items(self) -> None:
        info = _info(
            """
            query {
                taxCalculationList {
                    items {
                        id
                        calculatedTax { value unit }
                        ...TaxFields
                    }
                }
            }
            fragment TaxFields on TaxCalculationType {
                budgetUsed { value unit }
            }
            """
        )

        selected = collect_selected_graphql_property_names(
            info,
            FakeManager,
            root_field="items",
        )

        self.assertEqual(selected, {"calculated_tax", "budget_used"})

    def test_does_not_collect_nested_relation_properties(self) -> None:
        info = _info(
            """
            query {
                taxCalculationList {
                    items {
                        employee {
                            hiddenCost
                        }
                    }
                }
            }
            """
        )

        selected = collect_selected_graphql_property_names(
            info,
            FakeManager,
            root_field="items",
        )

        self.assertEqual(selected, set())


class PlannedObject:
    def __init__(self, value: int) -> None:
        self.value = value

    @graph_ql_property(cache="dependency")
    def computed_value(self) -> int:
        return self.value * 2

    @graph_ql_property(cache="run")
    def run_value(self) -> int:
        return self.value * 3

    class Interface:
        @staticmethod
        def get_graph_ql_properties() -> dict[str, GraphQLProperty]:
            return {
                "computed_value": PlannedObject.computed_value,
                "run_value": PlannedObject.run_value,
            }


class GraphQLPrefetchPlanningTests(SimpleTestCase):
    def test_plans_dependency_cached_selected_properties_with_real_keys(self) -> None:
        instance = PlannedObject(4)

        plans = plan_dependency_cache_prefetches(
            [instance],
            PlannedObject,
            {"computed_value", "run_value"},
            can_read_field=lambda _instance, _field_name: True,
        )

        prop = PlannedObject.Interface.get_graph_ql_properties()["computed_value"]
        expected_key = make_cache_key(prop._get_cached_fget(), (instance,), {})
        self.assertEqual(set(plans), {expected_key})
        self.assertEqual(plans[expected_key].property_name, "computed_value")
        self.assertIs(plans[expected_key].instance, instance)

    def test_skips_permission_denied_property_instances(self) -> None:
        first = PlannedObject(1)
        second = PlannedObject(2)

        plans = plan_dependency_cache_prefetches(
            [first, second],
            PlannedObject,
            {"computed_value"},
            can_read_field=lambda instance, _field_name: instance is first,
        )

        self.assertEqual(len(plans), 1)
        self.assertIs(next(iter(plans.values())).instance, first)


class GraphQLPrefetchExecutionTests(SimpleTestCase):
    def test_prefetch_reads_many_and_stores_hits_in_current_context(self) -> None:
        hit = DependencyCacheHit(
            value=42,
            dependencies=frozenset({("Project", "identification", '{"id": 1}')}),
        )
        reader = Mock(return_value={"cache-key": hit})
        cache_backend = object()
        plan = DependencyCachePrefetchPlan(
            cache_key="cache-key",
            instance=PlannedObject(1),
            property_name="computed_value",
        )

        with CalculationRunContext() as context:
            hits = prefetch_dependency_cache_hits(
                {"cache-key": plan},
                cache_backend=cache_backend,
                reader=reader,
            )

            self.assertEqual(hits, {"cache-key": hit})
            self.assertEqual(context.get_dependency_cache_hit("cache-key"), hit)

        reader.assert_called_once_with(cache_backend, ("cache-key",))

    def test_prefetch_without_context_skips_reader(self) -> None:
        reader = Mock(return_value={})
        plan = DependencyCachePrefetchPlan(
            cache_key="cache-key",
            instance=PlannedObject(1),
            property_name="computed_value",
        )

        hits = prefetch_dependency_cache_hits(
            {"cache-key": plan},
            cache_backend=object(),
            reader=reader,
        )

        self.assertEqual(hits, {})
        reader.assert_not_called()


class GraphQLPrefetchGroupedSkipTests(SimpleTestCase):
    def test_planner_can_be_called_with_empty_materialized_items(self) -> None:
        plans = plan_dependency_cache_prefetches(
            [],
            PlannedObject,
            {"computed_value"},
            can_read_field=lambda _instance, _field_name: True,
        )

        self.assertEqual(plans, {})
