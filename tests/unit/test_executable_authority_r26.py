"""Unchanged callable identity cannot preserve changed public execution proof."""

from general_manager.chat.graphql_contract import clear_contract_cache
from copy import deepcopy
from datetime import date
from types import FunctionType

import pytest

from general_manager.api import graphql_search
from general_manager.api.graphql_resolvers import (
    create_list_resolver,
    create_root_list_resolver,
)
from general_manager.chat.planned.calculation_scope import (
    validate_calculation_binding_evidence,
)
from general_manager.chat.planned.calculations import (
    CalculationOperand,
    calculate_evidence,
)
from general_manager.chat.schema_inspection import inspect_manager_schema
from general_manager.manager.general_manager import GeneralManager
from general_manager.manager.input import Input
from tests.unit.test_chat_cross_query_scope import (
    public_population as _population_fixture,
    _setup,
)
from tests.unit.test_chat_category_value_binding_r26 import (
    category_schema as _category_fixture,
    _public_runner,
    _lookup,
    _call,
    _binding,
)
from tests.unit.test_filter_execution_authority_r26 import _reject_net

public_population = _population_fixture
category_schema = _category_fixture


def _clone(function):
    result = FunctionType(
        function.__code__,
        function.__globals__,
        function.__name__,
        function.__defaults__,
        function.__closure__,
    )
    result.__kwdefaults__ = function.__kwdefaults__
    return result


@pytest.mark.parametrize(
    "seam",
    [
        "all",
        "filter",
        "normalizer",
        "id_helper",
        "native_id_helper",
        "root",
        "factory_code",
        "interface",
        "date",
        "container",
    ],
)
def test_code_drift_revokes_old_and_fresh_complete_cross_population(
    public_population, monkeypatch, seam
):
    schema, managers, rows = public_population
    rows["Gross"].append(
        {
            **deepcopy(rows["Gross"][0]),
            "id": "requested-january",
            "amount": 200,
            "event_date": date(2025, 1, 15),
        }
    )
    baseline = _setup(public_population)
    runner, runtime, binding = baseline
    validate_calculation_binding_evidence(runtime.task, "net", binding, runner.evidence)
    result = calculate_evidence(
        "positive",
        "t",
        "difference",
        [CalculationOperand(name, ("value",)) for name in ("gross", "returns")],
        runner.evidence,
        require_linked=True,
        binding=binding,
    )
    assert result.payload()["value"] == 310
    before = inspect_manager_schema("Gross", view="full")["snapshot"]
    native = schema.graphql_schema
    if seam == "all":
        target = GeneralManager.all.__func__

        def changed(cls):
            return cls.Interface.filter(amount__lt=200)
    elif seam == "filter":
        target = GeneralManager.filter.__func__

        def changed(cls, **kwargs):
            return cls.Interface.filter(**{**kwargs, "amount__lt": 200})
    elif seam == "id_helper":
        target = graphql_search.normalize_id_filter_value

        def changed(manager, lookup, value):
            if manager.__name__ == "Gross" and lookup == "event_date__gte":
                return value.replace(month=2)
            return value
    elif seam == "native_id_helper":
        target = graphql_search._cast_identity_scalar

        def changed(converter, value):
            return "tampered-id"
    elif seam == "normalizer":
        target = graphql_search.normalize_filter_input
        monkeypatch.setitem(
            target.__globals__, "_r26_original_for_contract", _clone(target)
        )

        def changed(manager, filters):
            result = globals()["_r26_original_for_contract"](manager, filters)
            if manager.__name__ == "Gross":
                result["filter"]["amount__lt"] = 200
            return result
    elif seam == "interface":
        target = managers["Gross"].Interface.filter.__func__
        monkeypatch.setitem(
            target.__globals__, "_r26_original_for_contract", _clone(target)
        )

        def changed(cls, **kwargs):
            result = globals()["_r26_original_for_contract"](cls, **kwargs)
            return (
                result.filter(amount__lt=200)
                if cls._parent_class.__name__ == "Gross"
                else result
            )
    elif seam in ("root", "factory_code"):
        target = native.query_type.fields["grossList"].resolve
        monkeypatch.setitem(
            target.__globals__, "_r26_original_for_contract", _clone(target)
        )

        def changed_factory(
            base_getter, fallback_manager_class, filter_normalizer, grouping_validator
        ):
            def changed(*args, **kwargs):
                bindings = (
                    base_getter,
                    fallback_manager_class,
                    filter_normalizer,
                    grouping_validator,
                )
                assert len(bindings) == 4
                result = globals()["_r26_original_for_contract"](*args, **kwargs)
                result["items"] = [row for row in result["items"] if row.amount < 200]
                result["pageInfo"]["total_count"] = len(result["items"])
                return result

            return changed

        changed = changed_factory(None, None, None, None)
        assert changed.__code__.co_freevars == target.__code__.co_freevars
        if seam == "factory_code":

            def corrupt_factory(getter, owner):
                common = create_list_resolver(
                    getter, owner, graphql_search.normalize_filter_input
                )
                common.__code__ = changed.__code__
                return common

            target = create_root_list_resolver(
                managers["Gross"], resolver_factory=corrupt_factory
            )
            monkeypatch.setattr(
                native.query_type.fields["grossList"], "resolve", target
            )
    elif seam == "date":
        target = native.get_type("Date").parse_value

        def changed(value):
            return (
                __import__("datetime").date(2025, 2, 1)
                if value == "2025-01-01"
                else __import__("datetime").date.fromisoformat(value)
            )
    else:
        target = native.get_type("GrossFilterTypeDepth1").out_type.__init__

        def changed(self, *args, **kwargs):
            dict.__init__(self, *args, **kwargs)
            if "event_date__gte" in self:
                self["event_date__gte"] = self["event_date__gte"].replace(month=2)
            for key in self._meta.fields:
                setattr(self, key, self.get(key))

    monkeypatch.setattr(target, "__code__", changed.__code__)
    clear_contract_cache()
    assert inspect_manager_schema("Gross", view="full")["snapshot"] != before
    _reject_net(*baseline)
    _reject_net(*_setup(public_population))


@pytest.mark.parametrize("component", ["cast", "normalize", "keyword_default"])
def test_same_identifier_coercer_code_revokes_observed_category_membership(
    category_schema, monkeypatch, component
):
    schema = category_schema
    runner = _public_runner(schema.manager)
    _, valid = _lookup(runner, schema)
    assert valid
    metadata = _binding(field="id", path=["filters", "id_Exact"])
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"id_Exact": 1},
        category_bindings=[metadata],
    )
    assert valid and [row["id"] for row in result["data"]] == [1]
    before = inspect_manager_schema(schema.manager, view="full")["snapshot"]
    target = Input.cast if component == "keyword_default" else getattr(Input, component)

    def changed(self, value, identification=None, *, cache_context=None):
        return self.type(2 if value in (1, "1") else value)

    if component == "keyword_default":
        monkeypatch.setitem(
            target.__kwdefaults__, "cache_context", {"proof": "changed"}
        )
    else:
        monkeypatch.setattr(target, "__code__", changed.__code__)
    clear_contract_cache()
    assert inspect_manager_schema(schema.manager, view="full")["snapshot"] != before
    calls = len(schema.calls)
    result, valid = _call(
        runner,
        "query",
        manager=schema.manager,
        fields=["id", "family"],
        filters={"id_Exact": 1},
        category_bindings=[metadata],
    )
    assert not valid and result == {
        "status": "error",
        "code": "invalid_category_binding",
    }
    assert len(schema.calls) == calls
