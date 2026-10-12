"""Copied function metadata never proves executable population semantics."""

from copy import deepcopy
from dataclasses import replace
from functools import wraps

import pytest

from general_manager.api.graphql import GraphQL
from general_manager.api.graphql_filter_contract import public_population_contract
from general_manager.api.graphql_resolvers import (
    create_list_resolver,
    create_root_list_resolver,
)
from general_manager.api.graphql_search import normalize_filter_input
from general_manager.chat.graphql_contract import clear_contract_cache
from general_manager.chat.planned.calculation_scope import (
    validate_calculation_binding_evidence,
)
from general_manager.chat.planned.calculations import (
    CalculationError,
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.planned.evidence_selection import selected_evidence
from general_manager.chat.schema_inspection import inspect_manager_schema
from tests.unit.test_chat_cross_query_scope import (
    public_population as _population_fixture,
    _setup,
)
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
    _public_runner,
    _lookup,
)

public_population = _population_fixture
category_schema = _category_fixture


def _wrapped_subset(common):
    @wraps(common)
    def custom(*args, **kwargs):
        result = common(*args, **kwargs)
        result["items"] = result["items"][:1]
        result["pageInfo"]["total_count"] = len(result["items"])
        return result

    return custom


def _custom_factory(base_getter, owner):
    return _wrapped_subset(
        create_list_resolver(base_getter, owner, normalize_filter_input)
    )


def test_factory_wraps_cannot_copy_common_population_authority(public_population):
    manager = public_population[1]["Gross"]
    resolver = create_root_list_resolver(manager, resolver_factory=_custom_factory)
    field = type("Field", (), {"resolve": staticmethod(resolver)})()
    assert public_population_contract(field, {"Gross": manager}) is None


@pytest.mark.parametrize("factory_wrapper", [False, True])
def test_fresh_wrapper_queries_cannot_supply_cross_query_population(
    public_population, factory_wrapper
):
    schema, managers, rows = public_population
    rows["Gross"].append({**deepcopy(rows["Gross"][0]), "id": "g2", "amount": 200})
    field = schema.graphql_schema.query_type.fields["grossList"]
    field.resolve = (
        create_root_list_resolver(managers["Gross"], resolver_factory=_custom_factory)
        if factory_wrapper
        else _wrapped_subset(field.resolve)
    )
    runner, runtime, binding = _setup(public_population)
    assert public_population_contract(field, managers) is None
    assert runner.evidence.get("gross").payload()["value"] == 120
    assert len(rows["Gross"]) == 2
    with pytest.raises(CalculationError, match=r"root.*population"):
        validate_calculation_binding_evidence(
            runtime.task, "net", binding, runner.evidence
        )
    with pytest.raises(CalculationError, match=r"root.*population"):
        calculate_evidence(
            "invalid-net",
            "t",
            "difference",
            [CalculationOperand(id, ("value",)) for id in ("gross", "returns")],
            runner.evidence,
            require_linked=True,
            binding=binding,
        )


def test_wrapping_root_after_calculation_revokes_completion_proof(public_population):
    runner, runtime, binding = _setup(public_population)
    net = calculate_evidence(
        "net",
        "t",
        "difference",
        [CalculationOperand(id, ("value",)) for id in ("gross", "returns")],
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    runtime.task = replace(
        runtime.task,
        requirements=(
            *runtime.task.requirements[:-1],
            replace(runtime.task.requirements[-1], binding=binding),
        ),
    )
    runner.evidence.add(net, requirement=runtime.task.requirements[-1])
    assert selected_evidence(runner.evidence, "t", ["net"])
    field = public_population[0].graphql_schema.query_type.fields["grossList"]
    field.resolve = _wrapped_subset(field.resolve)
    clear_contract_cache()
    assert public_population_contract(field, public_population[1]) is None
    with pytest.raises(CalculationError):
        selected_evidence(runner.evidence, "t", ["net"])


@pytest.mark.parametrize("factory_wrapper", [False, True])
def test_wrapped_root_cannot_authorize_category_lookup(
    category_schema, factory_wrapper
):
    manager = GraphQL.manager_registry[category_schema.manager]
    root = inspect_manager_schema(category_schema.manager)["roots"][0]
    field = GraphQL.get_schema().graphql_schema.query_type.fields[root]
    field.resolve = (
        create_root_list_resolver(manager, resolver_factory=_custom_factory)
        if factory_wrapper
        else _wrapped_subset(field.resolve)
    )
    clear_contract_cache()
    assert public_population_contract(field, GraphQL.manager_registry) is None
    runner = _public_runner(category_schema.manager)
    result, valid = _lookup(runner, category_schema)
    assert result == {"status": "error", "code": "invalid_category_binding"}
    assert not valid and len(runner.evidence.records) == 1
    assert category_schema.calls == []
